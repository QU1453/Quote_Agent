# -*- coding: utf-8 -*-
"""知识库（Knowledge Base）：索引先行、两阶段检索（先读索引简述 → 再取正文）。

设计（见 docs/memory-system-design.md §3.3）：
- 大批有用文件（1688 页面、竞品 Listing、政策文档）分块入库，每块生成一条索引；
- Agent 先 get_index() 读简述（省 token），分析哪些块相关，再 fetch_knowledge() 只取被选中的块全文；
- 索引简述优先 LLM 生成，无 Key 降级"块前宁字 + 高频词"，保证演示链路可跑；
- v0.1 不做 ANN 语义粗筛（索引列表 + 关键词过滤已够用），接入点留 TODO。

权限：写入 L1（上传/入库管道），改/删 L3/L9，读 L0 全员开放。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import config

from ..access import MemoryCaller, SYSTEM_CALLER, guard
from .chunker import default_chunker

__all__ = ["KnowledgeBase"]

# 分块器：语义分块（long_term.chunker）优先，缺依赖时内置简单分段（见 chunker.py）


class KnowledgeBase:
    """知识库存储：docs → chunks → index 三表；检索走 get_index / fetch_knowledge。"""

    def __init__(self, base_dir: str | Path | None = None, llm=None,
                 chunk_size: int = 300, chunk_overlap: int = 50):
        base = Path(base_dir) if base_dir else config.DATA_DIR / "memory"
        self.db_path = base / "knowledge.sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")  # 级联删除 docs → chunks → index
        self._wlock = threading.Lock()
        self.llm = llm  # langchain BaseChatModel；None = 索引简述走规则降级
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kb_documents("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "user_id TEXT NOT NULL, title TEXT, raw_text TEXT NOT NULL,"
            "created_by TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now','localtime')))"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kb_chunks("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "doc_id INTEGER NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,"
            "user_id TEXT NOT NULL, seq INTEGER NOT NULL, text TEXT NOT NULL)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kb_index("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "chunk_id INTEGER NOT NULL REFERENCES kb_chunks(id) ON DELETE CASCADE,"
            "brief TEXT NOT NULL, keywords TEXT DEFAULT '[]', title TEXT DEFAULT '',"
            "created_by TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now','localtime')))"
        )
        self._conn.commit()

    # ---------- 入库（写 L1） ----------
    def ingest(self, user_id: str, title: str, text: str,
               caller: MemoryCaller | None = None) -> dict:
        """文档入库：分块 → 每块生成索引简述；返回 {doc_id, chunks}。"""
        caller = caller or SYSTEM_CALLER
        guard("knowledge", "write", caller)
        text = (text or "").strip()
        if not text:
            raise ValueError("text 不能为空")
        pieces = default_chunker(text, self.chunk_size, self.chunk_overlap)
        if not pieces:
            pieces = [text]
        with self._wlock:
            cur = self._conn.execute(
                "INSERT INTO kb_documents(user_id, title, raw_text, created_by) VALUES(?,?,?,?)",
                (str(user_id), title or "", text, caller.name),
            )
            doc_id = cur.lastrowid
            for seq, piece in enumerate(pieces):
                c = self._conn.execute(
                    "INSERT INTO kb_chunks(doc_id, user_id, seq, text) VALUES(?,?,?,?)",
                    (doc_id, str(user_id), seq, piece),
                )
                brief, keywords = self._make_index(piece, title)
                self._conn.execute(
                    "INSERT INTO kb_index(chunk_id, brief, keywords, title, created_by)"
                    " VALUES(?,?,?,?,?)",
                    (c.lastrowid, brief, json.dumps(keywords, ensure_ascii=False),
                     title or "", caller.name),
                )
            self._conn.commit()
        return {"doc_id": doc_id, "chunks": len(pieces)}

    # ---------- 检索（读 L0；两阶段：先索引后内容） ----------
    def get_index(self, user_id: str | None = None, filter_keywords: list[str] | None = None,
                  limit: int = 50) -> list[dict]:
        """第一步：只返回索引简述（brief / keywords / title / chunk_id），省 token。

        filter_keywords：粗过滤（keyword 或 brief 包含任一关键词）。
        """
        sql = (
            "SELECT i.chunk_id, i.brief, i.keywords, i.title, c.seq, d.user_id"
            " FROM kb_index i JOIN kb_chunks c ON c.id=i.chunk_id"
            " JOIN kb_documents d ON d.id=c.doc_id"
        )
        args: list = []
        if user_id:
            sql += " WHERE d.user_id=?"
            args.append(str(user_id))
        sql += " ORDER BY i.chunk_id ASC LIMIT ?"
        args.append(int(limit))
        out = []
        for row in self._conn.execute(sql, args):
            try:
                keywords = json.loads(row[2])
            except (json.JSONDecodeError, TypeError):
                keywords = []
            item = {"chunk_id": row[0], "brief": row[1], "keywords": keywords,
                    "title": row[3], "seq": row[4], "user_id": row[5]}
            if filter_keywords and not self._match(item, filter_keywords):
                continue
            out.append({k: v for k, v in item.items() if k != "user_id"})
        return out

    def fetch_knowledge(self, chunk_ids: list[int]) -> list[dict]:
        """第二步：只取被选中块的正文，返回 [{chunk_id, text, title, seq}]。"""
        if not chunk_ids:
            return []
        ids = [int(i) for i in chunk_ids]
        sql = (
            f"SELECT c.id, c.text, c.seq, d.title FROM kb_chunks c"
            f" JOIN kb_documents d ON d.id=c.doc_id"
            f" WHERE c.id IN ({','.join('?' * len(ids))}) ORDER BY c.id ASC"
        )
        rows = self._conn.execute(sql, ids).fetchall()
        return [{"chunk_id": r[0], "text": r[1], "seq": r[2], "title": r[3]} for r in rows]

    # ---------- 维护（改/删 L3） ----------
    def delete_document(self, doc_id: int, caller: MemoryCaller | None = None) -> bool:
        """删除文档（级联删 chunks/indexes）；改/删权限 L3。"""
        caller = caller or SYSTEM_CALLER
        guard("knowledge", "modify", caller)
        with self._wlock:
            cur = self._conn.execute("DELETE FROM kb_documents WHERE id=?", (int(doc_id),))
            self._conn.commit()
        return cur.rowcount > 0

    def count(self, user_id: str | None = None) -> dict:
        if user_id:
            docs = self._conn.execute(
                "SELECT COUNT(*) FROM kb_documents WHERE user_id=?", (str(user_id),)).fetchone()[0]
            chs = self._conn.execute(
                "SELECT COUNT(*) FROM kb_chunks WHERE user_id=?", (str(user_id),)).fetchone()[0]
        else:
            docs = self._conn.execute("SELECT COUNT(*) FROM kb_documents").fetchone()[0]
            chs = self._conn.execute("SELECT COUNT(*) FROM kb_chunks").fetchone()[0]
        return {"documents": docs, "chunks": chs}

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    # ---------- 内部 ----------
    def _make_index(self, text: str, title: str = "") -> tuple[str, list[str]]:
        """索引简述生成：LLM 优先（1–2 句描述 + 关键词），无 LLM 规则降级。"""
        if self.llm is not None:
            try:
                resp = self.llm.invoke(
                    "你是知识库索引员。为下面的文本块写一条索引："
                    "输出 JSON：{\"brief\": \"1-2句大致描述\", \"keywords\": [\"关键词1\", ...]}\n"
                    "只输出 JSON 本体，不要解释。\n\n文本块：\n" + text[:400]
                )
                content = str(getattr(resp, "content", "") or "").strip()
                data = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", content))
                brief = str(data.get("brief", "")).strip()
                kws = [str(k) for k in (data.get("keywords") or [])][:6]
                if brief and kws:
                    return brief, kws
            except Exception:  # noqa: BLE001 - LLM 失败 → 规则降级
                pass
        brief = text[:60].replace("\n", " ") + ("…" if len(text) > 60 else "")
        kws = [w for w, _ in _top_words(text, 6)]
        return brief, kws

    @staticmethod
    def _match(item: dict, filter_keywords: list[str]) -> bool:
        hay = (item.get("brief", "") + " " + " ".join(item.get("keywords", []))).lower()
        return any(k.lower() in hay for k in filter_keywords)


def _top_words(text: str, n: int) -> list[tuple[str, int]]:
    """CJK 两字词 / 拉丁单词频率 top-n（索引关键词降级用）。"""
    tokens = []
    for w in re.findall(r"[a-zA-Z0-9]{2,}", text):
        tokens.append(w.lower())
    for w in re.findall(r"[\u4e00-\u9fff]{2,4}", text):
        tokens.append(w)
    counter: dict[str, int] = {}
    for t in tokens:
        counter[t] = counter.get(t, 0) + 1
    return sorted(counter.items(), key=lambda x: -x[1])[:n]