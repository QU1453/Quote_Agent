# -*- coding: utf-8 -*-
"""记忆权限体系：等级定义（L0~L9） / 读写鉴权 guard / 审计留痕 AuditLogger。

设计（见 docs/memory-system-design.md §6）：
- 默认拒绝、显式授权：所有记忆写/改/删操作必须先过 guard()；
- 读操作 L0 全员开放（状态记忆除外——它只做自动注入，不走读 API）；
- 越权即抛 AccessError，且无论成败都落 memory_audit 审计表；
- 审计库独立于各记忆模块库（data/memory/access.sqlite），数据文件均被 .gitignore 拦截。

用法：
    from memory.access import MemoryCaller, guard, AccessError

    caller = MemoryCaller("summarizer", "L1", session_id="s1")
    guard("long_term", "write", caller)      # 越权会抛 AccessError
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime

import config

# ---- 等级定义（L9=人类用户，全权限）--------------------------------------------------
LEVEL_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L9": 9}

# 权限矩阵：{op: {module: 最低等级}}
# 写 = 新增条目；改/删 = 修改或删除既有条目。读权限 L0 全员开放，无需矩阵。
_WRITE_LEVEL = {
    "short_term": "L0",  # 仅本人会话（会话归属校验由业务层完成）
    "long_term": "L1",
    "knowledge": "L1",
    "skill": "L2",
    "state": "L3",
    "interrupt": "L0",  # 中断日志：会话级交接班记录，与短期记忆同档（仅本人会话）
}
_MODIFY_LEVEL = {
    "short_term": "L0",
    "long_term": "L3",
    "knowledge": "L3",
    "skill": "L2",  # 技能晋升/降级由 L2 专门 agent 自理
    "state": "L3",
    "interrupt": "L0",  # 续跑标记（open → resumed）由本人会话收尾时写
}


class AccessError(PermissionError):
    """记忆权限不足（越权操作被拒绝）。"""


@dataclass(frozen=True)
class MemoryCaller:
    """一次记忆调用的发起方身份：所有记忆 API 必须携带。"""

    name: str                  # 智能体 / 模块名（如 "summarizer"、"research_agent"）
    level: str                 # L0~L3 / L9
    session_id: str = ""       # 所属会话（审计追溯用）

    def __post_init__(self) -> None:
        if self.level not in LEVEL_ORDER:
            raise ValueError(f"非法权限等级：{self.level}（可选 {sorted(LEVEL_ORDER)}）")


# 系统内部调用方（初始化种子数据等）——最高权限，等同人类
SYSTEM_CALLER = MemoryCaller("system", "L9")


def guard(module: str, op: str, caller: MemoryCaller) -> None:
    """鉴权入口：默认拒绝，显式授权。

    module ∈ {short_term, long_term, knowledge, skill, state}；op ∈ {read, write, modify}。
    越权抛 AccessError；无论成败均写审计。
    """
    if op == "read":
        allowed = True  # 读操作 L0 全员开放
        required = "L0"
    else:
        matrix = _WRITE_LEVEL if op == "write" else _MODIFY_LEVEL
        required = matrix.get(module)
        if required is None:
            raise AccessError(f"模块 {module} 未在权限矩阵登记（op={op}）")
        allowed = LEVEL_ORDER.get(caller.level, -1) >= LEVEL_ORDER[required]
    AuditLogger.log(
        actor=caller.name, actor_level=caller.level,
        action=op, module=module, target_id="",
        session_id=caller.session_id, result="allow" if allowed else "deny",
    )
    if not allowed:
        raise AccessError(
            f"{caller.name}({caller.level}) 无权对记忆模块 [{module}] 执行 {op}（需要 ≥ {required}）"
        )


class AuditLogger:
    """审计日志：独立 SQLite 单例（data/memory/access.sqlite），写失败不阻断主流程。"""

    _lock = threading.Lock()
    _conn: sqlite3.Connection | None = None

    # 审计库路径走 config.DATA_DIR（不能用 __file__：打包后会落到解包临时目录，重启即丢）
    _DB_PATH = config.DATA_DIR / "memory" / "access.sqlite"

    @classmethod
    def _get_conn(cls) -> sqlite3.Connection | None:
        if cls._conn is None:
            with cls._lock:
                if cls._conn is None:
                    try:
                        cls._DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                        conn = sqlite3.connect(cls._DB_PATH, check_same_thread=False)
                        conn.execute(
                            "CREATE TABLE IF NOT EXISTS memory_audit("
                            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                            "ts TEXT, actor TEXT, actor_level TEXT,"
                            "action TEXT, module TEXT, target_id TEXT,"
                            "session_id TEXT, result TEXT)"
                        )
                        conn.commit()
                        cls._conn = conn
                    except Exception:  # noqa: BLE001 - 审计不可用不影响业务
                        return None
        return cls._conn

    @classmethod
    def log(cls, actor: str, actor_level: str, action: str, module: str,
            target_id: str = "", session_id: str = "", result: str = "allow") -> None:
        """写一条审计记录；任何异常静默吞掉（审计是旁路，绝不阻断业务）。"""
        try:
            conn = cls._get_conn()
            if conn is None:
                return
            conn.execute(
                "INSERT INTO memory_audit(ts, actor, actor_level, action, module, target_id, session_id, result) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), actor, actor_level,
                 action, module, str(target_id), session_id, result),
            )
            conn.commit()
        except Exception:  # noqa: BLE001
            pass

    @classmethod
    def recent(cls, limit: int = 20) -> list[dict]:
        """读取最近审计记录（L9 排查用）。"""
        conn = cls._get_conn()
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT ts, actor, actor_level, action, module, target_id, session_id, result "
            "FROM memory_audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        keys = ("ts", "actor", "actor_level", "action", "module", "target_id", "session_id", "result")
        return [dict(zip(keys, row)) for row in rows]

    @classmethod
    def close(cls) -> None:
        """关闭审计连接（进程退出 / demo 收尾时调用）。"""
        with cls._lock:
            if cls._conn is not None:
                try:
                    cls._conn.close()
                except Exception:  # noqa: BLE001
                    pass
                cls._conn = None