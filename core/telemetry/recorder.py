# -*- coding: utf-8 -*-
"""遥测记录器（TelemetryRecorder）：智能体运行观测，调试后台（/debug）的数据源。

字段设计参考 OpenInference 语义约定（llm.token_count.prompt/completion/total、
tool.name/parameters、openinference.span.kind）+ 本项目特有维度（route / llm_mode /
约束拦截 / 上下文 token 估算），见 docs 计划《agent-telemetry-debug-console.md》。

旁路容错（fail-open）：所有写入/查询方法内部吞异常——
遥测是观测工具，任何故障都不得影响问答主链路；查询失败返回空结果。

存储：data/telemetry/telemetry.sqlite（WAL，单进程假设，与审计库同模式）；
目录已被 .gitignore 拦截，运行时数据绝不入库。

数据模型：
- traces：一轮问答一条（含路由/模式/token/耗时/状态/错误）
- events：trace 内的分步事件（stage/llm/tool/constraint 四类，按 seq 排序）
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

import config

# 遥测库路径走 config.DATA_DIR（不能用 __file__：打包后会落到解包临时目录，重启即丢）
_DB_PATH = config.DATA_DIR / "telemetry" / "telemetry.sqlite"

# 当前线程活跃的 trace（GLOBAL_REGISTRY.call 等无 trace 上下文的埋点方挂靠用；
# orchestrator 在 answer() 开头 set、结尾 clear，旁路埋点据此归并到正确 trace）
_current = threading.local()

# 截断上限（防库膨胀；调试够用）
_MAX_QUESTION = 500
_MAX_REPLY = 500
_MAX_ERROR = 500
_MAX_PARAMS = 800
_MAX_DETAIL = 300


def _cut(text: Any, limit: int) -> str:
    """任意值 → 截断字符串（None → 空串）。"""
    s = "" if text is None else str(text)
    return s[:limit]


class TelemetryRecorder:
    """遥测读写门面：写入（埋点方）+ 查询（/api/telemetry）。线程安全。"""

    def __init__(self, db_path: str | Path = _DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_tables()

    # ---- 建表（幂等）--------------------------------------------------------------
    def _init_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS traces(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT DEFAULT (datetime('now','localtime')),
                session_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                route TEXT DEFAULT '',
                agent TEXT DEFAULT '',
                intent TEXT DEFAULT '',
                question TEXT DEFAULT '',
                reply_snippet TEXT DEFAULT '',
                llm_mode TEXT DEFAULT '',
                status TEXT DEFAULT 'ok',
                latency_ms INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                tokens_source TEXT DEFAULT 'none',
                context_tokens INTEGER DEFAULT 0,
                error TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_traces_session ON traces(session_id);
            CREATE INDEX IF NOT EXISTS idx_traces_ts ON traces(ts);

            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id INTEGER NOT NULL,
                seq INTEGER NOT NULL DEFAULT 0,
                ts TEXT DEFAULT (datetime('now','localtime')),
                kind TEXT NOT NULL DEFAULT 'stage',
                name TEXT NOT NULL DEFAULT '',
                params_json TEXT DEFAULT '',
                status TEXT DEFAULT 'ok',
                duration_ms INTEGER,
                detail TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id);
            """
        )
        self._conn.commit()

    # ---- 写入 API（埋点方调用；fail-open 吞异常）-----------------------------------
    def start_trace(self, session_id: str, user_id: str, question: str) -> int:
        """开一条 trace（一轮问答开始）；返回 trace_id（失败返回 0，调用方零感知）。"""
        try:
            with self._lock:
                cur = self._conn.execute(
                    "INSERT INTO traces(session_id, user_id, question) VALUES(?,?,?)",
                    (str(session_id or ""), str(user_id or ""), _cut(question, _MAX_QUESTION)),
                )
                self._conn.commit()
                return int(cur.lastrowid or 0)
        except Exception:  # noqa: BLE001
            return 0

    def add_event(self, trace_id: int, kind: str, name: str,
                  params: dict | None = None, status: str = "ok",
                  duration_ms: int | None = None, detail: str = "") -> None:
        """追加一条分步事件（stage/llm/tool/constraint）。"""
        if not trace_id:
            return
        try:
            params_json = ""
            if params:
                params_json = _cut(json.dumps(params, ensure_ascii=False), _MAX_PARAMS)
            with self._lock:
                # seq = 该 trace 已有事件数 + 1（时间线排序用）
                seq = self._conn.execute(
                    "SELECT COUNT(*) FROM events WHERE trace_id=?", (trace_id,)
                ).fetchone()[0] + 1
                self._conn.execute(
                    "INSERT INTO events(trace_id, seq, kind, name, params_json, status,"
                    " duration_ms, detail) VALUES(?,?,?,?,?,?,?,?)",
                    (trace_id, seq, str(kind or "stage"), str(name or ""),
                     params_json, str(status or "ok"), duration_ms,
                     _cut(detail, _MAX_DETAIL)),
                )
                self._conn.commit()
        except Exception:  # noqa: BLE001
            pass

    def close_trace(self, trace_id: int, **fields: Any) -> None:
        """结束 trace：补 route/intent/reply/llm_mode/latency/status/error 等。"""
        if not trace_id:
            return
        try:
            # 白名单字段（防 SQL 注入式拼接；question 截断在 start_trace 已做）
            allow = {"route", "agent", "intent", "reply_snippet", "llm_mode",
                     "status", "latency_ms", "error", "context_tokens"}
            sets, vals = [], []
            for key, val in fields.items():
                if key not in allow or val is None:
                    continue
                sets.append(f"{key}=?")
                # 字符串按字段语义截断（error 500 / reply 500 / 其余短标识 300）；
                # 数值直接 int 化——不能拿 val 当 limit（字符串 val 会 TypeError）
                limit = (_MAX_ERROR if key == "error"
                         else _MAX_REPLY if key == "reply_snippet"
                         else _MAX_DETAIL)
                vals.append(_cut(val, limit) if isinstance(val, str) else int(val))
            if not sets:
                return
            with self._lock:
                self._conn.execute(
                    f"UPDATE traces SET {', '.join(sets)} WHERE id=?", (*vals, trace_id)
                )
                self._conn.commit()
        except Exception:  # noqa: BLE001
            pass

    def set_tokens(self, trace_id: int, input_tokens: int, output_tokens: int,
                   source: str) -> None:
        """写入本轮真实 token（API usage）与来源标注（api/estimated/none）。"""
        if not trace_id:
            return
        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE traces SET input_tokens=?, output_tokens=?, total_tokens=?,"
                    " tokens_source=? WHERE id=?",
                    (int(input_tokens or 0), int(output_tokens or 0),
                     int(input_tokens or 0) + int(output_tokens or 0), str(source), trace_id),
                )
                self._conn.commit()
        except Exception:  # noqa: BLE001
            pass

    def clear(self) -> None:
        """清空两表（调试后台「清空数据」按钮）。"""
        with self._lock:
            self._conn.execute("DELETE FROM events")
            self._conn.execute("DELETE FROM traces")
            self._conn.commit()

    # ---- 查询 API（/api/telemetry 数据源；失败返回空）------------------------------
    def list_traces(self, limit: int = 50, session_id: str = "",
                    route: str = "") -> list[dict]:
        """近期 trace 列表（倒序；带每条的工具调用数，供表格展示）。"""
        try:
            sql = ("SELECT t.*, (SELECT COUNT(*) FROM events e"
                   " WHERE e.trace_id=t.id AND e.kind='tool') AS tool_count"
                   " FROM traces t WHERE 1=1")
            args: list[Any] = []
            if session_id:
                sql += " AND t.session_id=?"
                args.append(session_id)
            if route:
                sql += " AND t.route=?"
                args.append(route)
            sql += " ORDER BY t.id DESC LIMIT ?"
            args.append(max(1, min(int(limit or 50), 500)))
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]
        except Exception:  # noqa: BLE001
            return []

    def get_trace(self, trace_id: int) -> dict | None:
        """单条 trace + 事件明细（按 seq 排序的时间线）。"""
        try:
            row = self._conn.execute(
                "SELECT * FROM traces WHERE id=?", (int(trace_id),)
            ).fetchone()
            if row is None:
                return None
            trace = dict(row)
            trace["events"] = [dict(r) for r in self._conn.execute(
                "SELECT * FROM events WHERE trace_id=? ORDER BY seq", (int(trace_id),)
            ).fetchall()]
            return trace
        except Exception:  # noqa: BLE001
            return None

    def summary(self) -> dict:
        """聚合统计：概览卡 + 工具排行 + 智能体分布 + 最近错误。"""
        out: dict = {"total_traces": 0, "total_tokens": 0, "fallback_rate": 0.0,
                     "constraint_count": 0, "avg_latency_ms": 0, "tools": [],
                     "routes": [], "recent_errors": []}
        try:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n,"
                " SUM(COALESCE(total_tokens,0)) AS tok,"
                " SUM(CASE WHEN llm_mode='fallback' THEN 1 ELSE 0 END) AS fb,"
                " SUM(CASE WHEN llm_mode='constraint' THEN 1 ELSE 0 END) AS c,"
                " AVG(COALESCE(latency_ms,0)) AS lat"
                " FROM traces"
            ).fetchone()
            out["total_traces"] = row["n"] or 0
            out["total_tokens"] = row["tok"] or 0
            out["fallback_rate"] = round((row["fb"] or 0) / row["n"], 3) if row["n"] else 0.0
            out["constraint_count"] = row["c"] or 0
            out["avg_latency_ms"] = int(row["lat"] or 0)
            # 工具排行（events 表 kind='tool'，含 duration 的平均耗时）
            out["tools"] = [dict(r) for r in self._conn.execute(
                "SELECT name, COUNT(*) AS calls,"
                " AVG(COALESCE(duration_ms,0)) AS avg_ms"
                " FROM events WHERE kind='tool' GROUP BY name ORDER BY calls DESC"
            ).fetchall()]
            # 智能体分布（route × llm_mode）
            out["routes"] = [dict(r) for r in self._conn.execute(
                "SELECT route, llm_mode, COUNT(*) AS calls FROM traces"
                " GROUP BY route, llm_mode ORDER BY calls DESC"
            ).fetchall()]
            # 最近 5 条错误 trace
            out["recent_errors"] = [dict(r) for r in self._conn.execute(
                "SELECT id, ts, session_id, route, error FROM traces"
                " WHERE status='error' AND error<>'' ORDER BY id DESC LIMIT 5"
            ).fetchall()]
        except Exception:  # noqa: BLE001
            pass
        return out

    def token_trend(self, session_id: str, limit: int = 50) -> list[dict]:
        """某会话逐轮 input_tokens 序列（正序；诊断上下文膨胀的核心视图）。"""
        try:
            return [dict(r) for r in self._conn.execute(
                "SELECT id, ts, input_tokens, output_tokens, context_tokens,"
                " tokens_source FROM traces WHERE session_id=?"
                " ORDER BY id ASC LIMIT ?",
                (str(session_id or ""), max(1, min(int(limit or 50), 200))),
            ).fetchall()]
        except Exception:  # noqa: BLE001
            return []


# 进程级单例（与 memory 的 get_memory / 约束层 get_constraint_layer 同模式）
_RECORDER: TelemetryRecorder | None = None


def get_recorder() -> TelemetryRecorder:
    """遥测记录器单例入口（orchestrator / registry / server 统一从这里取）。"""
    global _RECORDER
    if _RECORDER is None:
        _RECORDER = TelemetryRecorder()
    return _RECORDER


def set_current_trace(trace_id: int) -> None:
    """登记当前线程活跃 trace（orchestrator 问答开始时调用）。"""
    _current.trace_id = trace_id or 0


def get_current_trace() -> int:
    """取当前线程活跃 trace_id（无则 0；registry 等旁路埋点方用）。"""
    return int(getattr(_current, "trace_id", 0) or 0)
