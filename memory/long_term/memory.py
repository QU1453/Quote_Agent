# -*- coding: utf-8 -*-
"""长期记忆存储：facts（结构化 KV，精确读写）+ documents/chunks（RAG 语料）+ ANN 检索编排。

存储链：add_document → chunker 分块 → EmbeddingCache 嵌入 → chunks 落库（label=chunk_id）
→ hnswlib ANN 入索引 → 持久化 data/memories.hnsw。
检索链：recall → 查询向量化 → ANN 粗排（user 过滤 + 超取）→ 缓存向量精确 cosine 重排 → top-k。

单进程假设；结构化问题优先走 facts 精确 KV（不进向量管道，天然高准确率）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from ..access import AccessError, MemoryCaller, SYSTEM_CALLER, guard
from .ann import AnnIndex, HNSWLIB_AVAILABLE, cosine
from .chunker import chunk_text
from .rag import EmbeddingCache, EmbeddingProvider, pick_provider, text_hash

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT,
  source TEXT DEFAULT 'backend',
  updated_at TEXT DEFAULT (datetime('now','localtime')),
  UNIQUE(user_id, key)
);
CREATE INDEX IF NOT EXISTS idx_facts_user ON facts(user_id);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  title TEXT,
  raw_text TEXT NOT NULL,
  source TEXT DEFAULT 'backend',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  text TEXT NOT NULL,
  text_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_user ON chunks(user_id);
CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(text_hash);
CREATE TABLE IF NOT EXISTS ann_meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS long_term_entries(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  entry_type TEXT NOT NULL,          -- user_profile / fact / pending_item / preference
  content TEXT NOT NULL,
  confidence REAL DEFAULT 0.5,
  source_conversations TEXT DEFAULT '[]',  -- JSON 数组（来源谈话 ID，可回溯）
  tags TEXT DEFAULT '[]',
  status TEXT DEFAULT 'active',
  created_by TEXT DEFAULT '',
  created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_lte_user ON long_term_entries(user_id);
"""


class LongTermMemory:
    """长期记忆 = facts（精确）+ documents/chunks + ANN 索引（语义召回）。"""

    def __init__(
        self,
        db_path: str | Path,
        ann_path: str | Path,
        provider: EmbeddingProvider | None = None,
        chunk_size: int = 300,
        chunk_overlap: int = 50,
        M: int = 32,
        ef_construction: int = 200,
        ef_search: int = 64,
        overfetch: int = 50,
        auto_save: bool = True,
    ):
        self.db_path = Path(db_path)
        self.ann_path = Path(ann_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.provider = provider or pick_provider()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.auto_save = auto_save
        self._M, self._efc, self._efs, self._ovf = M, ef_construction, ef_search, overfetch
        self._wlock = threading.Lock()
        self._cache: EmbeddingCache | None = None
        self._ann: AnnIndex | None = None
        self._label_user: dict[int, str] = self._load_label_user()
        self._ann = self._load_ann()

    # ---------- 内部 ----------
    @property
    def cache(self) -> EmbeddingCache:
        """嵌入缓存（懒加载单例）：text_hash → 向量，同文本零重复计算。"""
        if self._cache is None:
            self._cache = EmbeddingCache(self._conn, self.provider)
        return self._cache

    def _stored_dim(self) -> int | None:
        """读取 ann_meta 中已落盘的向量维度；未建过索引返回 None。"""
        row = self._conn.execute("SELECT value FROM ann_meta WHERE key='dim'").fetchone()
        return int(row[0]) if row else None

    def _set_meta(self, key: str, value: str) -> None:
        """写 ann_meta 元数据（KV upsert），目前只存向量维度。"""
        self._conn.execute(
            "INSERT INTO ann_meta(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self._conn.commit()

    def _load_label_user(self) -> dict[int, str]:
        """chunk_id → user_id 映射（从 chunks 表重建），供 ANN 检索时做用户分区过滤。"""
        return {int(cid): uid for cid, uid in self._conn.execute("SELECT id, user_id FROM chunks")}

    def _load_ann(self) -> AnnIndex | None:
        """重启恢复：索引文件与维度元数据齐备则从磁盘加载 .hnsw，否则视为空索引。"""
        if not HNSWLIB_AVAILABLE:
            return None
        dim = self._stored_dim()
        if self.ann_path.exists() and dim:
            try:
                return AnnIndex.load(self.ann_path, dim, ef_search=self._efs, overfetch=self._ovf)
            except Exception:  # noqa: BLE001 - 索引损坏/不可读时退化为无索引，不影响其余记忆功能
                return None
        return None

    def _ensure_ann(self, dim: int) -> AnnIndex | None:
        """按需建索引；无 hnswlib 时返回 None（调用方走暴力检索降级）。

        维度与既有索引不一致直接报错（换 Provider 需先清库）。
        """
        if not HNSWLIB_AVAILABLE:
            return None
        stored = self._stored_dim()
        if stored is not None and stored != dim:
            raise ValueError(f"嵌入维度不一致：既有索引 {stored}，当前 Provider {dim}。请清空长期库或更换 Provider。")
        if self._ann is None:
            self._ann = AnnIndex(
                dim, M=self._M, ef_construction=self._efc,
                ef_search=self._efs, overfetch=self._ovf,
            )
            self._set_meta("dim", str(dim))
        return self._ann

    # ---------- 后端数据输入接口：结构化事实（精确 KV） ----------
    def save_fact(self, user_id: str, key: str, value, source: str = "backend") -> None:
        """结构化事实 upsert（同 user 同 key 覆盖更新）：订单号 / 偏好 / 状态等。"""
        with self._wlock:
            self._conn.execute(
                "INSERT INTO facts(user_id, key, value, source) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value,"
                " source=excluded.source, updated_at=datetime('now','localtime')",
                (str(user_id), key, str(value), source),
            )
            self._conn.commit()

    def get_facts(self, user_id: str) -> list[dict]:
        """取该用户全部事实，按更新时间升序；每条 {key, value, source, updated_at}。"""
        return [
            {"key": k, "value": v, "source": s, "updated_at": ts}
            for k, v, s, ts in self._conn.execute(
                "SELECT key, value, source, updated_at FROM facts WHERE user_id=? ORDER BY updated_at",
                (str(user_id),),
            )
        ]

    def delete_fact(self, user_id: str, key: str) -> bool:
        """删除一条事实；返回是否确实删除（key 不存在返回 False）。"""
        with self._wlock:
            cur = self._conn.execute("DELETE FROM facts WHERE user_id=? AND key=?", (str(user_id), key))
            self._conn.commit()
        return cur.rowcount > 0

    # ---------- 长期记忆范式条目（二级总结产物） ----------
    def save_long_term_entry(
        self,
        user_id: str,
        entry_type: str,
        content: str,
        confidence: float = 0.5,
        tags: list[str] | None = None,
        source_conversations: list[str] | None = None,
        created_by: str = "",
        caller: MemoryCaller | None = None,
    ) -> int:
        """写入一条长期记忆范式条目；写权限 L1（总结管线），默认拒绝、显式授权。"""
        caller = caller or SYSTEM_CALLER
        guard("long_term", "write", caller)
        if entry_type not in ("user_profile", "fact", "pending_item", "preference"):
            raise ValueError(f"非法 entry_type：{entry_type}")
        with self._wlock:
            cur = self._conn.execute(
                "INSERT INTO long_term_entries"
                "(user_id, entry_type, content, confidence, source_conversations, tags, created_by)"
                " VALUES(?,?,?,?,?,?,?)",
                (
                    str(user_id), entry_type, str(content), float(confidence),
                    json.dumps(source_conversations or [], ensure_ascii=False),
                    json.dumps(tags or [], ensure_ascii=False),
                    created_by or caller.name,
                ),
            )
            self._conn.commit()
        return int(cur.lastrowid)

    def get_long_term_entries(self, user_id: str, entry_type: str | None = None,
                              limit: int = 20, status: str = "active") -> list[dict]:
        """读该用户长期记忆条目（读权限全员开放）。"""
        sql = ("SELECT id, entry_type, content, confidence, source_conversations, tags,"
               " created_by, created_at FROM long_term_entries"
               " WHERE user_id=? AND status=?")
        args: list = [str(user_id), status]
        if entry_type:
            sql += " AND entry_type=?"
            args.append(entry_type)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        out = []
        for row in self._conn.execute(sql, args):
            try:
                sources = json.loads(row[4])
            except (json.JSONDecodeError, TypeError):
                sources = []
            try:
                tags = json.loads(row[5])
            except (json.JSONDecodeError, TypeError):
                tags = []
            out.append({
                "id": row[0], "entry_type": row[1], "content": row[2],
                "confidence": row[3], "source_conversations": sources,
                "tags": tags, "created_by": row[6], "created_at": row[7],
            })
        return out

    def set_entry_status(self, entry_id: int, status: str, caller: MemoryCaller | None = None) -> None:
        """改/删条目状态（active → archived）；改权限 L3 / L9。"""
        caller = caller or SYSTEM_CALLER
        guard("long_term", "modify", caller)
        with self._wlock:
            self._conn.execute(
                "UPDATE long_term_entries SET status=? WHERE id=?", (status, int(entry_id)))
            self._conn.commit()

    # ---------- 后端数据输入接口：自由文本/文档（分块 → ANN 入库） ----------
    def add_document(self, user_id: str, text: str, title: str | None = None,
                     source: str = "backend") -> dict:
        """长文本入库：分块 → 向量化 → chunks 落库 → ANN 入索引；返回 {doc_id, chunks}。"""
        text = (text or "").strip()
        if not text:
            raise ValueError("text 不能为空")
        with self._wlock:
            cur = self._conn.execute(
                "INSERT INTO documents(user_id, title, raw_text, source) VALUES(?,?,?,?)",
                (str(user_id), title, text, source),
            )
            doc_id = cur.lastrowid
            pieces = chunk_text(text, max_tokens=self.chunk_size, overlap=self.chunk_overlap)
            vecs = self.cache.embed(pieces)
            ann = self._ensure_ann(len(vecs[0]))
            labels: list[int] = []
            for seq, piece in enumerate(pieces):
                c = self._conn.execute(
                    "INSERT INTO chunks(doc_id, user_id, seq, text, text_hash) VALUES(?,?,?,?,?)",
                    (doc_id, str(user_id), seq, piece, text_hash(piece)),
                )
                labels.append(c.lastrowid)
            self._label_user.update({lb: str(user_id) for lb in labels})
            if ann is not None:          # 无 hnswlib 时不建图索引：chunks 已落库，检索走暴力精确路径
                ann.add(labels, vecs)
            self._conn.commit()
            if ann is not None and self.auto_save:
                ann.save(self.ann_path)
        return {"doc_id": doc_id, "chunks": len(pieces)}

    # ---------- RAG 检索：ANN 速度优先 + 精确重排 ----------
    def recall(self, user_id: str, query: str, top_k: int = 5, with_rerank: bool = True) -> list[dict]:
        """语义召回：查询向量化 → ANN 粗排（仅本用户分区）→ 精确 cosine 重排 → top-k 块。

        无 hnswlib（图索引不可用）时退回暴力精确检索，功能不缺失、只是慢；
        该用户没有入库任何文档时直接返回空，保持"空库零开销"。
        """
        if self._ann is None or len(self._ann) == 0:
            has_chunks = self._conn.execute(
                "SELECT 1 FROM chunks WHERE user_id=? LIMIT 1", (str(user_id),)).fetchone()
            return self.brute_force_recall(user_id, query, top_k=top_k) if has_chunks else []
        uid = str(user_id)
        qvec = self.cache.embed([query.strip()])[0]
        ann = self._ann

        def label_filter(label: int) -> bool:  # 万人隔离：只在本用户分区内命中
            return self._label_user.get(label) == uid

        candidates = ann.query(qvec, k=top_k, label_filter=label_filter)
        if with_rerank and candidates:  # 两阶段：缓存原始向量做精确 cosine 重排
            rescored = []
            for label, _sim in candidates:
                row = self._conn.execute(
                    "SELECT text_hash FROM chunks WHERE id=?", (label,)).fetchone()
                vec = self.cache.get_by_hash(row[0]) if row else None
                if vec is not None:
                    rescored.append((label, cosine(qvec, vec)))
            candidates = sorted(rescored, key=lambda x: -x[1])
        out: list[dict] = []
        for label, score in candidates[:top_k]:
            row = self._conn.execute(
                "SELECT c.doc_id, c.seq, c.text, d.title FROM chunks c "
                "JOIN documents d ON d.id=c.doc_id WHERE c.id=?", (label,)).fetchone()
            if row:
                out.append({
                    "chunk_id": label, "doc_id": row[0], "seq": row[1],
                    "text": row[2], "title": row[3],
                    "score": round(float(score), 4), "user_id": uid,
                })
        return out

    def brute_force_recall(self, user_id: str, query: str, top_k: int = 5) -> list[dict]:
        """暴力精确检索（对照基准，用于验证 ANN 准确率；生产不调用）。"""
        uid = str(user_id)
        qvec = self.cache.embed([query.strip()])[0]
        scored: list[tuple[int, float]] = []
        for cid, h in self._conn.execute(
                "SELECT id, text_hash FROM chunks WHERE user_id=?", (uid,)):
            vec = self.cache.get_by_hash(h)
            if vec is not None:
                scored.append((int(cid), cosine(qvec, vec)))
        scored.sort(key=lambda x: -x[1])
        out: list[dict] = []
        for cid, score in scored[:top_k]:
            row = self._conn.execute(
                "SELECT c.doc_id, c.seq, c.text, d.title FROM chunks c "
                "JOIN documents d ON d.id=c.doc_id WHERE c.id=?", (cid,)).fetchone()
            if row:
                out.append({
                    "chunk_id": cid, "doc_id": row[0], "seq": row[1],
                    "text": row[2], "title": row[3],
                    "score": round(float(score), 4), "user_id": uid,
                })
        return out

    # ---------- 统计 / 维护 ----------
    def count(self, user_id: str | None = None) -> dict:
        """统计文档与分块数；传 user_id 只统计该用户。"""
        if user_id is None:
            docs = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chs = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        else:
            docs = self._conn.execute(
                "SELECT COUNT(*) FROM documents WHERE user_id=?", (str(user_id),)).fetchone()[0]
            chs = self._conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE user_id=?", (str(user_id),)).fetchone()[0]
        return {"documents": docs, "chunks": chs}

    def clear(self, user_id: str | None = None) -> None:
        """清空记忆：不传 user_id 全清（含索引与缓存重建）；传则只删该用户数据并软删其索引项。"""
        with self._wlock:
            if user_id is None:  # 全清：重建空索引
                for table in ("facts", "chunks", "documents", "embed_cache", "ann_meta"):
                    self._conn.execute(f"DELETE FROM {table}")
                self._conn.commit()
                self._label_user.clear()
                self._ann = None
                self._cache = None  # 丢弃内存缓存（含旧 provider 的维度），否则换 Provider 会误报维度不一致
                if self.ann_path.exists():
                    self.ann_path.unlink()
            else:
                uid = str(user_id)
                labels = [lb for lb, u in self._label_user.items() if u == uid]
                for table in ("facts", "chunks", "documents"):
                    self._conn.execute(f"DELETE FROM {table} WHERE user_id=?", (uid,))
                self._conn.commit()
                if self._ann is not None:
                    for lb in labels:
                        self._ann.mark_deleted(lb)
                    if self.auto_save:
                        self._ann.save(self.ann_path)
                for lb in labels:
                    self._label_user.pop(lb, None)

    def save(self) -> None:
        """手动持久化 ANN 索引到磁盘（auto_save=True 时无需调用）。"""
        if self._ann is not None:
            self._ann.save(self.ann_path)

    def close(self) -> None:
        """关闭连接；auto_save 开启时先落盘索引再关。"""
        try:
            if self.auto_save:
                self.save()
            self._conn.close()
        except Exception:
            pass
