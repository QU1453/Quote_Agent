# -*- coding: utf-8 -*-
"""中断日志（InterruptLog）：被打断的交互「停在哪一步」的可续跑台账。

定位（记忆层第六个模块）：
用户的诉求是「智能体需要一个打断交互的功能，打断时要记录这段对话/思考进行到哪了、
工具是否是被用户打断的，方便在下一次对话中继续进行」。因此本模块不是调试日志，
而是**交接班记录**——上一次没干完的活，下一次要能接着干。

一条记录回答四个问题：
1. 停在哪一步？        → stage（感知/上下文/思考/工具/输出）+ step_index + completed_steps
2. 干到哪了？          → completed_tools（已成功执行完的工具列表）
3. 被打断的是谁？      → pending_tool + tool_interrupted_by_user（工具是否被用户打断）
4. 已经说了什么？      → partial_reply（已产出的部分内容，便于接话）

权限：会话级数据，与短期记忆同档 —— 写 L0（仅本人会话）/ 改 L0 / L9（见 memory/access.py）。

生命周期：
    open ──（下一次对话正常完成）──► resumed     # 已被续跑消费
         └─（用户结束谈话 / 人工关闭）──► closed  # 不再注入

注入：`inject_block(session_id)` 产出「上次未完成」提示块，由感知层
core/perception/context.py 组装进上下文，实现「自动续跑」。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import config

from ..access import MemoryCaller, SYSTEM_CALLER, guard

__all__ = ["InterruptLog"]

# 续跑提示块长度上限（防止历史长文挤占上下文预算）
_MAX_BLOCK_CHARS = 900
_MAX_PARTIAL = 600
_MAX_QUESTION = 300

_SCHEMA = """
CREATE TABLE IF NOT EXISTS interrupt_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    route TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT '',
    step_index INTEGER DEFAULT 0,
    completed_steps INTEGER DEFAULT 0,
    completed_tools TEXT DEFAULT '[]',
    pending_tool TEXT DEFAULT '',
    tool_interrupted_by_user INTEGER DEFAULT 0,
    partial_reply TEXT DEFAULT '',
    question TEXT DEFAULT '',
    interrupted_by TEXT DEFAULT 'user',
    reason TEXT DEFAULT '',
    status TEXT DEFAULT 'open',
    resumed_at TEXT DEFAULT '',
    resumed_note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_interrupt_session ON interrupt_log(session_id);
CREATE INDEX IF NOT EXISTS idx_interrupt_status ON interrupt_log(status);
CREATE INDEX IF NOT EXISTS idx_interrupt_created ON interrupt_log(created_at);
"""

_LIST_COLS = ("id", "session_id", "user_id", "request_id", "route", "stage",
              "step_index", "completed_steps", "completed_tools", "pending_tool",
              "tool_interrupted_by_user", "partial_reply", "question",
              "interrupted_by", "reason", "status", "resumed_at", "resumed_note",
              "created_at")


def _cut(text: object, limit: int) -> str:
    s = "" if text is None else str(text).strip()
    return s[:limit]


class InterruptLog:
    """中断日志：写入（打断时）/ 查询 / 续跑标记 / 续跑提示块生成。"""

    def __init__(self, base_dir: str | Path | None = None):
        base = Path(base_dir) if base_dir else config.DATA_DIR / "memory"
        self.db_path = base / "interrupt.sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._wlock = threading.Lock()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------------- 写（打断发生时） ----------------
    def log_interrupt(
        self,
        session_id: str,
        *,
        user_id: str = "",
        request_id: str = "",
        route: str = "",
        stage: str = "thinking",
        step_index: int = 0,
        completed_steps: int = 0,
        completed_tools: list[str] | None = None,
        pending_tool: str = "",
        tool_interrupted_by_user: bool = False,
        partial_reply: str = "",
        question: str = "",
        interrupted_by: str = "user",
        reason: str = "",
        caller: MemoryCaller | None = None,
    ) -> int:
        """落一条中断记录，返回记录 id（写权限 L0，本人会话）。"""
        caller = caller or SYSTEM_CALLER
        guard("interrupt", "write", caller)
        tools = [str(t) for t in (completed_tools or []) if str(t).strip()]
        with self._wlock:
            cur = self._conn.execute(
                "INSERT INTO interrupt_log(session_id, user_id, request_id, route, stage,"
                " step_index, completed_steps, completed_tools, pending_tool,"
                " tool_interrupted_by_user, partial_reply, question, interrupted_by, reason)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(session_id or ""), str(user_id or ""), str(request_id or ""),
                 str(route or ""), str(stage or ""), int(step_index or 0),
                 int(completed_steps or 0), json.dumps(tools, ensure_ascii=False),
                 str(pending_tool or ""), 1 if tool_interrupted_by_user else 0,
                 _cut(partial_reply, _MAX_PARTIAL), _cut(question, _MAX_QUESTION),
                 str(interrupted_by or "user"), str(reason or "")),
            )
            self._conn.commit()
        return int(cur.lastrowid or 0)

    # ---------------- 读（续跑注入 / 排查） ----------------
    def latest_open(self, session_id: str) -> dict | None:
        """该会话最近一条待续跑记录（status=open），没有则 None。"""
        row = self._conn.execute(
            f"SELECT {', '.join(_LIST_COLS)} FROM interrupt_log"
            " WHERE session_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (str(session_id or ""),),
        ).fetchone()
        return self._row(row) if row else None

    def get(self, entry_id: int) -> dict | None:
        row = self._conn.execute(
            f"SELECT {', '.join(_LIST_COLS)} FROM interrupt_log WHERE id=?",
            (int(entry_id),),
        ).fetchone()
        return self._row(row) if row else None

    def list_recent(self, limit: int = 50, session_id: str = "",
                    status: str = "") -> list[dict]:
        """近期中断记录（倒序；可按会话/状态过滤）——调试后台面板数据源。"""
        sql = f"SELECT {', '.join(_LIST_COLS)} FROM interrupt_log WHERE 1=1"
        args: list[object] = []
        if session_id:
            sql += " AND session_id=?"
            args.append(str(session_id))
        if status:
            sql += " AND status=?"
            args.append(str(status))
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(max(1, min(int(limit or 50), 500)))
        return [self._row(r) for r in self._conn.execute(sql, args).fetchall()]

    def open_by_session(self) -> dict:
        """按会话聚合未完成中断：{session_id: {count, pending_tool, stage, step_index,
        question, created_at}}。

        只统计 status='open' 的记录，每个会话取最新一条（最大 id）的明细；一次 SQL
        完成（先按会话分组取 MAX(id) 与条数，再 join 回原表），不做 N+1 查询。
        台账不可用时返回 {}（fail-open，会话清单仍可展示）。
        """
        try:
            cols = ", ".join(f"i.{c}" for c in _LIST_COLS)
            rows = self._conn.execute(
                f"SELECT g.n, {cols} FROM ("
                " SELECT session_id, MAX(id) AS max_id, COUNT(*) AS n"
                " FROM interrupt_log WHERE status='open' GROUP BY session_id"
                ") g JOIN interrupt_log i ON i.id = g.max_id"
                " ORDER BY g.max_id DESC"
            ).fetchall()
        except Exception:  # noqa: BLE001 - 台账故障不阻断清单展示
            return {}
        out: dict[str, dict] = {}
        for row in rows:
            rec = self._row(row[1:])
            sid = str(rec.get("session_id") or "")
            if not sid:
                continue
            out[sid] = {
                "count": int(row[0] or 0),
                "pending_tool": str(rec.get("pending_tool") or ""),
                "stage": str(rec.get("stage") or ""),
                "step_index": int(rec.get("step_index") or 0),
                "question": str(rec.get("question") or ""),
                "created_at": str(rec.get("created_at") or ""),
            }
        return out

    def inject_block(self, session_id: str) -> str:
        """生成「上次未完成」续跑提示块；无待续跑记录返回空串（零开销）。"""
        rec = self.latest_open(session_id)
        if not rec:
            return ""
        stage_cn = {"perception": "输入解析", "context": "记忆装配", "thinking": "思考推理",
                    "tool": "工具调用", "output": "输出整理"}.get(rec["stage"], rec["stage"] or "思考推理")
        lines = [
            "【上次未完成 · 可续跑】",
            f"- 上一轮被用户打断的位置：第 {rec['step_index'] or rec['completed_steps']} 步（{stage_cn}）",
        ]
        if rec["question"]:
            lines.append(f"- 用户上次的诉求：{rec['question']}")
        if rec["completed_tools"]:
            lines.append(f"- 已执行完的工具：{'、'.join(rec['completed_tools'])}")
        if rec["pending_tool"]:
            who = "用户打断，未继续" if rec["tool_interrupted_by_user"] else "系统中断"
            lines.append(f"- 被打断的工具：{rec['pending_tool']}（{who}）")
        if rec["partial_reply"]:
            lines.append(f"- 已产出的部分内容：{rec['partial_reply']}")
        lines.append(
            "若用户本轮意图与该未完成事项相关，请直接从断点继续，不要重复已完成的步骤；"
            "无关则忽略本条，按用户本轮诉求正常处理。"
        )
        return "\n".join(lines)[:_MAX_BLOCK_CHARS]

    # ---------------- 续跑标记 ----------------
    def mark_resumed(self, entry_id: int, note: str = "",
                     caller: MemoryCaller | None = None) -> bool:
        """标记为已被续跑消费（改权限 L0，本人会话）。"""
        caller = caller or SYSTEM_CALLER
        guard("interrupt", "modify", caller)
        return self._set_status(int(entry_id), "resumed", note)

    def mark_closed(self, entry_id: int, note: str = "",
                    caller: MemoryCaller | None = None) -> bool:
        """人工关闭（不再注入）；改权限 L3 / L9。"""
        caller = caller or SYSTEM_CALLER
        guard("interrupt", "modify", caller)
        return self._set_status(int(entry_id), "closed", note)

    def resolve_open(self, session_id: str, note: str = "",
                     caller: MemoryCaller | None = None) -> int:
        """把该会话所有 open 记录一次性标记为已续跑，返回条数。

        用于「本轮正常完成」时收尾：断点已被接手，不必再注入。
        """
        caller = caller or SYSTEM_CALLER
        guard("interrupt", "modify", caller)
        with self._wlock:
            ids = [r[0] for r in self._conn.execute(
                "SELECT id FROM interrupt_log WHERE session_id=? AND status='open'",
                (str(session_id or ""),)).fetchall()]
            if not ids:
                return 0
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._conn.executemany(
                "UPDATE interrupt_log SET status='resumed', resumed_at=?, resumed_note=?"
                " WHERE id=?",
                [(now, _cut(note, 200), int(i)) for i in ids],
            )
            self._conn.commit()
        return len(ids)

    def stats(self) -> dict:
        """按状态计数（调试后台概览用）。"""
        try:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) FROM interrupt_log GROUP BY status").fetchall()
            out = {"open": 0, "resumed": 0, "closed": 0}
            for status, n in rows:
                out[str(status)] = int(n)
            out["total"] = sum(v for k, v in out.items() if k != "total")
            return out
        except Exception:  # noqa: BLE001
            return {"open": 0, "resumed": 0, "closed": 0, "total": 0}

    # ---------------- 维护 ----------------
    def clear(self, session_id: str = "") -> None:
        """清空中断日志（不传 session_id 全清）。"""
        with self._wlock:
            if session_id:
                self._conn.execute("DELETE FROM interrupt_log WHERE session_id=?",
                                   (str(session_id),))
            else:
                self._conn.execute("DELETE FROM interrupt_log")
            self._conn.commit()

    def _set_status(self, entry_id: int, status: str, note: str = "") -> bool:
        with self._wlock:
            cur = self._conn.execute(
                "UPDATE interrupt_log SET status=?, resumed_at=?, resumed_note=? WHERE id=?",
                (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 _cut(note, 200), int(entry_id)),
            )
            self._conn.commit()
        return cur.rowcount > 0

    @staticmethod
    def _row(row) -> dict:
        out = dict(zip(_LIST_COLS, row))
        try:
            tools = json.loads(out.get("completed_tools") or "[]")
        except (json.JSONDecodeError, TypeError):
            tools = []
        out["completed_tools"] = [str(t) for t in tools] if isinstance(tools, list) else []
        out["tool_interrupted_by_user"] = bool(out.get("tool_interrupted_by_user"))
        return out

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
