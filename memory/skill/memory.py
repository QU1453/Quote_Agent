# -*- coding: utf-8 -*-
"""技能记忆（SkillMemory）：事情 / 目的 / 痛点 / 解法 四要素 + 使用反馈闭环。

设计（见 docs/memory-system-design.md §3.5）：
- 写入：仅 L2 专门智能体（技能提炼/复盘）；supervisor 授权下也可入库；
- 检索：agent 遇难点时 search 关键词/tags（v0.1 不上向量检索，ANN 接入点留 TODO）；
- 反馈闭环：技能被采用后回报成败 → score = successes/(successes+failures)，
  连续 3 次失败自动 archived（防止坏技能污染检索结果）；
- 热技能：hot_skills 按 score 排序，前 TOP 条由上下文组装器预注入。

四要素：situation（什么事）/ goal（目的）/ pain（痛点）/ solution（解法）。
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

__all__ = ["SkillMemory"]

# 连续失败 3 次即归档
_ARCHIVE_FAILS = 3


class SkillMemory:
    """技能库：四要素表 + 检索 + feedback + 热技能。"""

    def __init__(self, base_dir: str | Path | None = None):
        base = Path(base_dir) if base_dir else config.DATA_DIR / "memory"
        self.db_path = base / "skill.sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._wlock = threading.Lock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS skill_memory("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "situation TEXT NOT NULL,"
            "goal TEXT DEFAULT '',"
            "pain TEXT DEFAULT '',"
            "solution TEXT NOT NULL,"
            "tags TEXT DEFAULT '[]',"
            "trigger TEXT DEFAULT '',"
            "successes INTEGER DEFAULT 0,"
            "failures INTEGER DEFAULT 0,"
            "score REAL DEFAULT 0,"
            "status TEXT DEFAULT 'active',"
            "created_by TEXT DEFAULT '',"
            "updated_at TEXT DEFAULT (datetime('now','localtime')))"
        )
        self._conn.commit()

    # ---------- 写（L2） ----------
    def add_skill(self, situation: str, goal: str, pain: str, solution: str,
                  tags: list[str] | None = None, trigger: str = "",
                  caller: MemoryCaller | None = None) -> int:
        """新增一条技能记忆（guard L2）。"""
        caller = caller or SYSTEM_CALLER
        guard("skill", "write", caller)
        with self._wlock:
            cur = self._conn.execute(
                "INSERT INTO skill_memory(situation, goal, pain, solution, tags, trigger, created_by)"
                " VALUES(?,?,?,?,?,?,?)",
                (str(situation).strip(), str(goal).strip(), str(pain).strip(),
                 str(solution).strip(), json.dumps(tags or [], ensure_ascii=False),
                 str(trigger).strip(), caller.name),
            )
            self._conn.commit()
        return int(cur.lastrowid)

    # ---------- 检索（读 L0） ----------
    def search_skills(self, text: str, top_k: int = 5, include_archived: bool = False) -> list[dict]:
        """按关键词/tags 匹配 + score 排序（v0.1；# TODO: 向量检索接入位）。"""
        words = set(re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]{2,4}", (text or "").lower()))
        sql = "SELECT id, situation, goal, pain, solution, tags, trigger, successes, failures," \
              " score, status FROM skill_memory"
        if not include_archived:
            sql += " WHERE status='active'"
        rows = self._conn.execute(sql).fetchall()
        scored: list[tuple[float, dict]] = []
        for r in rows:
            tags = self._load_tags(r[5])
            hay = (r[1] + r[2] + r[3] + r[4] + " ".join(tags)).lower()
            hits = sum(1 for w in words if w and w in hay)
            scored.append((hits + r[9], self._row(r, tags)))
        scored.sort(key=lambda x: -x[0])
        return [item for _, item in scored[:top_k]]

    def get_skill(self, skill_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT id, situation, goal, pain, solution, tags, trigger, successes, failures,"
            " score, status FROM skill_memory WHERE id=?", (int(skill_id),)).fetchone()
        return self._row(row, self._load_tags(row[5])) if row else None

    def hot_skills(self, top: int = 3) -> list[dict]:
        """热技能：active 状态按 score 降序（上下文预注入用）。"""
        rows = self._conn.execute(
            "SELECT id, situation, goal, pain, solution, tags, trigger, successes, failures,"
            " score, status FROM skill_memory WHERE status='active'"
            " ORDER BY score DESC, successes DESC LIMIT ?", (int(top),)).fetchall()
        return [self._row(r, self._load_tags(r[5])) for r in rows]

    # ---------- 反馈闭环 ----------
    def feedback(self, skill_id: int, success: bool) -> dict:
        """技能被采用后的成败回报：更新 score；连续失败 3 次自动归档。

        反馈属于技能体系的运行机制（不涉及改写技能内容），由使用者直接回报。
        """
        with self._wlock:
            row = self._conn.execute(
                "SELECT successes, failures, status FROM skill_memory WHERE id=?",
                (int(skill_id),)).fetchone()
            if row is None:
                return {"ok": False, "reason": "技能不存在"}
            ok, fails, status = row
            if success:
                ok += 1
                # 成功只会激活/保持，绝不触发归档（含 archived 技能成功复出）
                new_status = "active" if status == "archived" else status
            else:
                fails += 1
                new_status = "archived" if fails >= _ARCHIVE_FAILS and fails > ok else status
            total = ok + fails
            score = round(ok / total, 4) if total else 0.0
            self._conn.execute(
                "UPDATE skill_memory SET successes=?, failures=?, score=?, status=?,"
                " updated_at=? WHERE id=?",
                (ok, fails, score, new_status,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"), int(skill_id)),
            )
            self._conn.commit()
        return {"ok": True, "successes": ok, "failures": fails, "score": score, "status": new_status}

    def set_status(self, skill_id: int, status: str, caller: MemoryCaller | None = None) -> None:
        """晋升/降级（active/archived）——改权限 L2 或 L3。"""
        caller = caller or SYSTEM_CALLER
        guard("skill", "modify", caller)
        with self._wlock:
            self._conn.execute(
                "UPDATE skill_memory SET status=?, updated_at=? WHERE id=?",
                (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), int(skill_id)),
            )
            self._conn.commit()

    # ---------- 内部 ----------
    @staticmethod
    def _load_tags(raw) -> list[str]:
        try:
            tags = json.loads(raw)
            return [str(t) for t in tags] if isinstance(tags, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def _row(r, tags: list[str]) -> dict:
        return {
            "id": r[0], "situation": r[1], "goal": r[2], "pain": r[3], "solution": r[4],
            "tags": tags, "trigger": r[6], "successes": r[7], "failures": r[8],
            "score": r[9], "status": r[10],
        }

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass