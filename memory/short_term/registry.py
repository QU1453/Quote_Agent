# -*- coding: utf-8 -*-
"""谈话注册表（ConversationRegistry）：以「谈话」为记忆粒度的生命周期管理。

设计（见 docs/memory-system-design.md §3.1）：
- 谈话 = 一次会话（session_id）的完整生命周期，替代旧"按消息条数"的压缩口径；
- 每次谈话结束后由总结管线写入一级总结（summary_json），再按 M 个一批做二级总结；
- 短期窗口只保留最近 K 个谈话的原文；更早谈话原文退役（rolled_up 标记，冷查询仍可回查）。

本类复用 ShortTermMemory 的同一 SQLite 连接（self._conn），不另开库、不加锁。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

# 统一 SELECT 列清单（get / recent_closed / open_conversations / pending_rollup /
# list_conversations 共用；顺序与 _row_to_dict 的位置索引一一对应）
_COLS = ("session_id, user_id, agent_name, status, started_at, ended_at,"
         " summary_json, rolled_up, scope, title, hidden, updated_at,"
         " project_id, feature_key")

# 迁移用新增列（列名 → DDL 片段；老库缺哪列补哪列，顺序与新库建表一致）
_NEW_COLUMNS = (
    ("scope", "TEXT DEFAULT ''"),
    ("title", "TEXT DEFAULT ''"),
    ("hidden", "INTEGER DEFAULT 0"),
    ("updated_at", "TEXT DEFAULT ''"),
    ("project_id", "TEXT DEFAULT ''"),
    ("feature_key", "TEXT DEFAULT ''"),
)


def _now() -> str:
    """当前时间字符串（库内既有格式，供排序/展示统一口径）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ConversationRegistry:
    """谈话注册表：start / update_agent / end / 总结归属 / 滚动合并游标 / 清单管理。"""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        # 说明：agent_name=该谈话最后处理的智能体（原文取回用）；rolled_up=是否已参与二级总结；
        # scope=区块标签（research/listing/global，界面分区用，不参与记忆隔离）；
        # title=可读标题（首条提问或用户改名）；hidden=隐藏标记（隐藏≠删除，数据仍在）；
        # updated_at=最近活动时间（清单排序用）；
        # project_id=所属项目（空=未分类）/ feature_key=细分功能板块 key（空=未分类）
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS conversation_registry("
            "session_id TEXT PRIMARY KEY,"
            "user_id TEXT DEFAULT '',"
            "agent_name TEXT DEFAULT '',"
            "status TEXT DEFAULT 'open',"
            "started_at TEXT, ended_at TEXT,"
            "summary_json TEXT DEFAULT '',"
            "rolled_up INTEGER DEFAULT 0,"
            "scope TEXT DEFAULT '',"
            "title TEXT DEFAULT '',"
            "hidden INTEGER DEFAULT 0,"
            "updated_at TEXT DEFAULT '',"
            "project_id TEXT DEFAULT '',"
            "feature_key TEXT DEFAULT ''"
            ")"
        )
        self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """老库补列（幂等）：缺哪列 ALTER 哪列，并在补过列时回填一次默认值。

        回填只在本次确实新增过列时执行，之后每次启动都是纯读（零写入）。
        """
        cols = {row[1] for row in self._conn.execute(
            "PRAGMA table_info(conversation_registry)").fetchall()}
        added: list[str] = []
        for name, ddl in _NEW_COLUMNS:
            if name not in cols:
                self._conn.execute(
                    f"ALTER TABLE conversation_registry ADD COLUMN {name} {ddl}")
                added.append(name)
        if not added:
            return
        if "scope" in added:
            # 按既有 agent_name 回填业务线（research* → research；listing* → listing）
            self._conn.execute(
                "UPDATE conversation_registry SET scope='research'"
                " WHERE COALESCE(scope,'')='' AND agent_name LIKE 'research%'")
            self._conn.execute(
                "UPDATE conversation_registry SET scope='listing'"
                " WHERE COALESCE(scope,'')='' AND agent_name LIKE 'listing%'")
        if "updated_at" in added:
            # 回填最近活动时间：结束时间 → 开始时间 → 空串
            self._conn.execute(
                "UPDATE conversation_registry SET updated_at="
                "COALESCE(NULLIF(ended_at,''), NULLIF(started_at,''), '')"
                " WHERE COALESCE(updated_at,'')=''")
        self._conn.commit()

    # ---------- 生命周期 ----------
    def start(self, session_id: str, user_id: str = "", agent_name: str = "",
              scope: str = "", project_id: str = "", feature_key: str = "") -> None:
        """登记谈话开始（幂等：已存在则不动）；scope 为区块标签（research/listing/global）。

        project_id / feature_key 为二级归属（所属项目 + 细分功能板块，空 = 未分类，
        前端会把空 feature_key 的会话显示为「未分类」）；已存在的行不改写（INSERT OR IGNORE）。
        """
        now = _now()
        self._conn.execute(
            "INSERT OR IGNORE INTO conversation_registry"
            "(session_id, user_id, agent_name, status, started_at, scope, title, hidden,"
            " updated_at, project_id, feature_key)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (str(session_id), str(user_id), agent_name, "open", now,
             str(scope or ""), "", 0, now, str(project_id or ""), str(feature_key or "")),
        )
        self._conn.commit()

    def update_agent(self, session_id: str, agent_name: str) -> None:
        """记录该谈话最近一次由哪个智能体处理（结束总结/原文取回都依赖它），并刷新活动时间。"""
        self._conn.execute(
            "UPDATE conversation_registry SET agent_name=?, updated_at=? WHERE session_id=?",
            (agent_name, _now(), str(session_id)),
        )
        self._conn.commit()

    def end(self, session_id: str, summary_json: str = "") -> None:
        """结束谈话：记录结束时间；带 summary 时直接标记 summarized。"""
        status = "summarized" if summary_json else "closed"
        self._conn.execute(
            "UPDATE conversation_registry SET status=?, ended_at=?, summary_json=? WHERE session_id=?",
            (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), summary_json, str(session_id)),
        )
        self._conn.commit()

    def set_summary(self, session_id: str, summary_json: str) -> None:
        """写入/覆盖一级总结（P2 总结管线调用），顺带用总结主题刷新标题与活动时间。

        标题优先级：一级总结的 topic > 首条提问（set_title_if_empty 写的占位）。
        总结主题是模型提炼的，比首条提问更能代表整段谈话，所以这里覆盖。
        """
        topic = summary_topic(summary_json).strip()
        if topic:
            self._conn.execute(
                "UPDATE conversation_registry SET summary_json=?, status='summarized',"
                " title=?, updated_at=? WHERE session_id=?",
                (summary_json, topic[:40], _now(), str(session_id)),
            )
        else:
            self._conn.execute(
                "UPDATE conversation_registry SET summary_json=?, status='summarized',"
                " updated_at=? WHERE session_id=?",
                (summary_json, _now(), str(session_id)),
            )
        self._conn.commit()

    def touch(self, session_id: str) -> None:
        """只刷新最近活动时间（清单排序用），不改其它字段。"""
        self._conn.execute(
            "UPDATE conversation_registry SET updated_at=? WHERE session_id=?",
            (_now(), str(session_id)),
        )
        self._conn.commit()

    def set_title(self, session_id: str, title: str) -> None:
        """覆盖谈话标题（用户改名入口）。"""
        self._conn.execute(
            "UPDATE conversation_registry SET title=? WHERE session_id=?",
            (str(title or ""), str(session_id)),
        )
        self._conn.commit()

    def set_title_if_empty(self, session_id: str, title: str) -> None:
        """标题为空时才写入（首条提问当标题用），已有标题不覆盖。"""
        self._conn.execute(
            "UPDATE conversation_registry SET title=? WHERE session_id=? AND COALESCE(title,'')=''",
            (str(title or ""), str(session_id)),
        )
        self._conn.commit()

    def set_hidden(self, session_id: str, hidden: bool) -> bool:
        """隐藏 / 取消隐藏谈话（隐藏≠删除：只改标记，不动注册表行与任何记忆数据）。

        返回是否有行被更新（会话不存在返回 False）。
        """
        cur = self._conn.execute(
            "UPDATE conversation_registry SET hidden=? WHERE session_id=?",
            (1 if hidden else 0, str(session_id)),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def set_owner(self, session_id: str, project_id: str, feature_key: str,
                  scope: str | None = None) -> bool:
        """改写会话归属（项目 / 细分板块 / 区块）——「移动到其他板块」的唯一入口。

        只改这三列：消息、总结、断点、隐藏标记一律不动（**移动 ≠ 重建会话**）。
        scope=None 表示不改区块标签（区块与板块是两套 key，后端不反推，由调用方一起给）。
        返回是否有行被更新（会话不存在返回 False）。
        """
        sql = "UPDATE conversation_registry SET project_id=?, feature_key=?"
        args: list = [str(project_id or ""), str(feature_key or "")]
        if scope is not None:
            sql += ", scope=?"
            args.append(str(scope))
        sql += " WHERE session_id=?"
        args.append(str(session_id))
        cur = self._conn.execute(sql, args)
        self._conn.commit()
        return cur.rowcount > 0

    def claim_owner(self, session_id: str, project_id: str, feature_key: str,
                    scope: str = "") -> bool:
        """首次归属回填：**仅当会话还没有项目归属时**才写入（`/api/ask` 带上项目/板块时调用）。

        已有归属的会话绝不改写 —— 这是「会话归属固定为创建时所在的板块」的落点，
        避免用户只是进某个板块看一眼就把老会话的 L2 记忆层带漂移。
        返回是否真的写了（已归属 / 会话不存在 → False）。
        """
        cur = self._conn.execute(
            "UPDATE conversation_registry SET project_id=?, feature_key=?, scope=?"
            " WHERE session_id=? AND COALESCE(project_id,'')='' AND ?<>''",
            (str(project_id or ""), str(feature_key or ""), str(scope or ""),
             str(session_id), str(project_id or "")),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_conversations(self, user_id: str = "", scope: str = "",
                           include_hidden: bool = False, limit: int = 100,
                           project_id: str = "", feature_key: str = "") -> list[dict]:
        """会话清单（按最近活动倒序）：可选按用户 / 区块 / 项目 / 板块过滤，默认不含隐藏项。

        排序键取 updated_at → ended_at → started_at 中第一个非空值，同值用 rowid 兜底。
        project_id / feature_key 为空串表示不过滤（语义与 scope 过滤一致）。
        """
        sql = f"SELECT {_COLS} FROM conversation_registry WHERE 1=1"
        args: list = []
        if user_id:
            sql += " AND user_id=?"
            args.append(str(user_id))
        if scope:
            sql += " AND scope=?"
            args.append(str(scope))
        if project_id:
            sql += " AND COALESCE(project_id,'')=?"
            args.append(str(project_id))
        if feature_key:
            sql += " AND COALESCE(feature_key,'')=?"
            args.append(str(feature_key))
        if not include_hidden:
            sql += " AND COALESCE(hidden,0)=0"
        sql += (" ORDER BY COALESCE(NULLIF(updated_at,''), NULLIF(ended_at,''),"
                " NULLIF(started_at,''), '') DESC, rowid DESC LIMIT ?")
        args.append(max(1, min(int(limit or 100), 500)))
        return [self._row_to_dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def count_by_feature(self, project_id: str, user_id: str = "") -> dict:
        """按「区块 scope → 板块 feature_key」两级聚合会话数（项目总览树一次取全）。

        返回 {scope: {feature_key: {"sessions": n, "last_at": "..."}}}；
        **一次 SQL 聚合完成**，不做每板块一条查询的 N+1 写法。
        feature_key / scope 为空的老会话不丢弃，归到 "" 这个 key 下（前端显示为「未分类」）；
        last_at 取该板块最近活动时间（updated_at → ended_at → started_at 首个非空）。
        """
        sql = ("SELECT scope, feature_key, COUNT(*), MAX(COALESCE(NULLIF(updated_at,''),"
               " NULLIF(ended_at,''), NULLIF(started_at,''), ''))"
               " FROM conversation_registry WHERE COALESCE(project_id,'')=?")
        args: list = [str(project_id or "")]
        if user_id:
            sql += " AND user_id=?"
            args.append(str(user_id))
        sql += " GROUP BY scope, feature_key"
        out: dict[str, dict] = {}
        for scope, feature, n, last_at in self._conn.execute(sql, args).fetchall():
            out.setdefault(str(scope or ""), {})[str(feature or "")] = {
                "sessions": int(n or 0), "last_at": str(last_at or ""),
            }
        return out

    def get(self, session_id: str) -> dict | None:
        """读单个谈话记录；不存在返回 None。"""
        row = self._conn.execute(
            f"SELECT {_COLS} FROM conversation_registry WHERE session_id=?",
            (str(session_id),),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    # ---------- 窗口组装 / 二级总结游标 ----------
    def recent_closed(self, k: int, user_id: str = "") -> list[dict]:
        """最近 k 个已结束的谈话（按结束时间倒序，供 K 窗口组装）。"""
        sql = (f"SELECT {_COLS} FROM conversation_registry"
               " WHERE status IN ('closed','summarized')")
        args: list = []
        if user_id:
            sql += " AND user_id=?"
            args.append(str(user_id))
        sql += " ORDER BY COALESCE(ended_at, started_at) DESC, rowid DESC LIMIT ?"
        args.append(int(k))
        rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def open_conversations(self, user_id: str = "") -> list[dict]:
        """当前进行中的谈话（窗口组装时附在已结束谈话之后）。"""
        sql = f"SELECT {_COLS} FROM conversation_registry WHERE status='open'"
        args: list = []
        if user_id:
            sql += " AND user_id=?"
            args.append(str(user_id))
        rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def pending_rollup(self, limit: int = 0) -> list[dict]:
        """未做二级总结的已结束谈话（按结束时间升序，满 M 个触发合并）。"""
        n = int(limit) if limit else -1
        rows = self._conn.execute(
            f"SELECT {_COLS} FROM conversation_registry"
            " WHERE rolled_up=0 AND status IN ('closed','summarized')"
            " ORDER BY COALESCE(ended_at, started_at) ASC, rowid ASC LIMIT ?",
            (n,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def mark_rolled_up(self, session_ids: list[str]) -> int:
        """标记一批谈话已参与二级总结；返回实际更新的行数。"""
        if not session_ids:
            return 0
        cur = self._conn.execute(
            f"UPDATE conversation_registry SET rolled_up=1 WHERE session_id IN "
            f"({','.join('?' * len(session_ids))})",
            [str(s) for s in session_ids],
        )
        self._conn.commit()
        return cur.rowcount

    # ---------- 内部 ----------
    @staticmethod
    def _row_to_dict(row) -> dict:
        """按 _COLS 位置索引解析一行（扩展列后返回值只多不少，调用方按 key 取值不受影响）。"""
        return {
            "session_id": row[0], "user_id": row[1], "agent_name": row[2],
            "status": row[3], "started_at": row[4], "ended_at": row[5],
            "summary_json": row[6] or "", "rolled_up": bool(row[7]),
            "scope": row[8] or "", "title": row[9] or "",
            "hidden": bool(row[10]), "updated_at": row[11] or "",
            "project_id": row[12] or "", "feature_key": row[13] or "",
        }


def parse_summary(summary_json: str) -> dict | None:
    """一级总结 JSON 反序列化（坏数据返回 None，调用方按无总结处理）。"""
    if not summary_json:
        return None
    try:
        return json.loads(summary_json)
    except (json.JSONDecodeError, TypeError):
        return None


def summary_topic(summary_json: str) -> str:
    """从一级总结 JSON 中提取主题（窗口摘要行展示用；无总结返回空串）。"""
    data = parse_summary(summary_json)
    return (data or {}).get("topic", "")