# -*- coding: utf-8 -*-
"""状态记忆（StateMemory）：每次必须注入 LLM 的死规则（铁律）。

设计（见 docs/memory-system-design.md §3.4）：
- 写入权限：仅 L3（高权限 agent / supervisor）/ L9（人类）；普通智能体只能被动接收；
- scope 分组：global（全员）/ agent_type（定向 agent：如 "research"）/ user（指定用户），
  避免无关规则污染专项智能体上下文；
- 注入：assembler 每次组装上下文时调 inject_block()，拼成「铁律」块置于 system prompt；
- STATE_MEMORY_INJECT=False 时注入返回空串（总开关）。

种子死规则（5 条，插入方 = system/L9）：
1. 不得编造订单号、销量、供应商报价等任何数据；
2. 回复语言跟随用户输入语言（中文或日本語）；
3. 不得向用户暴露工具名称、参数 JSON 等内部实现细节；
4. 不得读写本机 C 盘文件；所有数据只落项目目录；
5. API Key 等密钥只从环境变量读取，绝不写入代码、日志或仓库。
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

import config
from ..access import MemoryCaller, SYSTEM_CALLER, guard

# 种子死规则（scope=global，优先级 1~5）
SEED_RULES = [
    "不得编造订单号、销量、供应商报价等任何数据，缺失信息必须如实说明并引导用户补充。",
    "回复语言必须跟随用户输入语言（中文或日本語），不得混用。",
    "不得向用户暴露工具名称、调用参数 JSON 等内部实现细节。",
    "不得读写本机 C 盘文件；所有读写只发生在项目目录内。",
    "API Key 等密钥只从环境变量读取，绝不写入代码、日志或仓库。",
]


class StateMemory:
    """死规则表 + 注入器。写 L3 / 改 L3 / 读（只经注入器自动发生）。"""

    def __init__(self, base_dir: str | Path | None = None):
        base = Path(base_dir) if base_dir else config.DATA_DIR / "memory"
        self.db_path = base / "state.sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._wlock = threading.Lock()
        # 列说明：scope ∈ global(全员) / agent_type(指定 agent) / user(指定用户)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS state_memory("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "rule_text TEXT NOT NULL,"
            "scope TEXT DEFAULT 'global',"
            "agent_types TEXT DEFAULT '',"
            "priority INTEGER DEFAULT 10,"
            "enabled INTEGER DEFAULT 1,"
            "created_by TEXT DEFAULT '',"
            "updated_at TEXT DEFAULT (datetime('now','localtime')))"
        )
        self._conn.commit()
        self._seed()

    def _seed(self) -> None:
        """预置种子死规则（幂等：表存在即跳过）。"""
        count = self._conn.execute("SELECT COUNT(*) FROM state_memory").fetchone()[0]
        if count:
            return
        with self._wlock:
            for i, rule in enumerate(SEED_RULES, start=1):
                self._conn.execute(
                    "INSERT INTO state_memory(rule_text, scope, priority, created_by) VALUES(?,?,?,?)",
                    (rule, "global", i, SYSTEM_CALLER.name),
                )
            self._conn.commit()

    # ---------- 写（L3）/ 改（L3） ----------
    def add_rule(self, rule_text: str, scope: str = "global",
                 agent_types: str | None = None,
                 priority: int = 10, caller: MemoryCaller | None = None) -> int:
        """新增死规则（guard L3）；agent_types：scope=agent_type 时传如 \"research,listing\"。"""
        caller = caller or SYSTEM_CALLER
        guard("state", "write", caller)
        with self._wlock:
            cur = self._conn.execute(
                "INSERT INTO state_memory(rule_text, scope, agent_types, priority, created_by)"
                " VALUES(?,?,?,?,?)",
                (str(rule_text).strip(), scope, agent_types or "", int(priority), caller.name),
            )
            self._conn.commit()
        return int(cur.lastrowid)

    def set_enabled(self, rule_id: int, enabled: bool, caller: MemoryCaller | None = None) -> None:
        """启用/停用一条规则（guard L3）。"""
        caller = caller or SYSTEM_CALLER
        guard("state", "modify", caller)
        with self._wlock:
            self._conn.execute(
                "UPDATE state_memory SET enabled=?, updated_at=? WHERE id=?",
                (1 if enabled else 0, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), int(rule_id)),
            )
            self._conn.commit()

    # ---------- 读（仅注入器；无逐条读取 API） ----------
    def get_rules(self, agent_type: str | None = None, user_id: str | None = None) -> list[dict]:
        """取生效规则（scope 匹配）：global 恒入，agent_type / user 按参数匹配。"""
        sql = ("SELECT id, rule_text, scope, agent_types, priority FROM state_memory"
               " WHERE enabled=1 AND (scope='global'")
        args: list = []
        if agent_type:
            sql += " OR (scope='agent_type' AND ',' || agent_types || ',' LIKE ?)"
            args.append(f"%,{agent_type},%")
        if user_id:
            sql += " OR (scope='user' AND agent_types=?)"
            args.append(str(user_id))
        sql += ") ORDER BY priority ASC, id ASC"
        rows = self._conn.execute(sql, args).fetchall()
        return [
            {"id": r[0], "rule_text": r[1], "scope": r[2], "agent_types": r[3], "priority": r[4]}
            for r in rows
        ]

    def inject_block(self, agent_type: str | None = None, user_id: str | None = None) -> str:
        """拼「铁律」块（每次上下文组装必调）；总开关关闭时返回空串。"""
        if not config.STATE_MEMORY_INJECT:
            return ""
        rules = self.get_rules(agent_type=agent_type, user_id=user_id)
        if not rules:
            return ""
        lines = [f"{i}. {r['rule_text']}" for i, r in enumerate(rules, start=1)]
        return "【铁律 · 每次必须遵守】\n" + "\n".join(lines)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass