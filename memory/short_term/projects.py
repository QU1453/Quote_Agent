# -*- coding: utf-8 -*-
"""项目注册表（ProjectRegistry）：工作台一级「项目」的生命周期管理。

定位（见 .trae/documents/project-hierarchy-and-memory-layering.md §2 / §5.1）：
- **项目**是用户创建的一级容器（如「水杯项目」），其下固定挂 11 个细分功能板块；
- 会话（conversation_registry）通过 project_id / feature_key 归属到「项目 → 板块」，
  本表只管项目自身的元数据，不复制板块清单（板块名定义在前端 BOARDS）；
- **归档 ≠ 删除**：归档只把 status 置为 archived，项目行与其下所有会话、记忆、
  中断记录一律保留，只是默认不出现在常规项目列表（include_archived=True 仍可见）。

本类复用 ShortTermMemory 的同一 SQLite 连接（self._conn），不另开库、不加锁 ——
与 ConversationRegistry 完全同档：同库同连接、同样的建表/提交风格。
"""
from __future__ import annotations

import sqlite3
import uuid

from .registry import _now

# 建表 + 索引（与 ConversationRegistry 同样的幂等写法：IF NOT EXISTS）
_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
    project_id  TEXT PRIMARY KEY,
    user_id     TEXT DEFAULT '',
    name        TEXT DEFAULT '',
    desc        TEXT DEFAULT '',
    status      TEXT DEFAULT 'open',
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
"""

# 统一 SELECT 列清单（get / list 共用；顺序与 _row_to_dict 一一对应）
_COLS = "project_id, user_id, name, desc, status, created_at, updated_at"


class ProjectRegistry:
    """项目注册表：create / get / list / update（改名 / 改描述 / 归档）。"""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        # 说明：status=open（在use）/ archived（已归档，归档≠删除，数据全保留）；
        # created_at / updated_at = 创建与最近修改时间（列表按 updated_at 倒序）
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------- 写 ----------
    def create(self, user_id: str = "", name: str = "", desc: str = "") -> dict:
        """新建项目：生成 uuid4 hex 的 project_id，返回完整项目字典。"""
        now = _now()
        project_id = uuid.uuid4().hex
        self._conn.execute(
            "INSERT OR IGNORE INTO projects"
            "(project_id, user_id, name, desc, status, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (project_id, str(user_id or ""), str(name or "").strip(),
             str(desc or ""), "open", now, now),
        )
        self._conn.commit()
        return self.get(project_id) or {
            "project_id": project_id, "user_id": str(user_id or ""),
            "name": str(name or "").strip(), "desc": str(desc or ""),
            "status": "open", "created_at": now, "updated_at": now,
        }

    def update(self, project_id: str, name: str | None = None,
               desc: str | None = None, status: str | None = None) -> bool:
        """只改传入的字段并刷新 updated_at（改名 / 改描述 / 归档共用）。

        归档（status='archived'）**不等于删除**：只改状态位，项目行与其下会话、
        记忆、中断记录全部保留。未传的字段保持原值；无行被更新返回 False。
        """
        sets: list[str] = []
        args: list = []
        if name is not None:
            sets.append("name=?")
            args.append(str(name).strip())
        if desc is not None:
            sets.append("desc=?")
            args.append(str(desc))
        if status is not None:
            sets.append("status=?")
            args.append(str(status))
        if not sets:
            return self.get(project_id) is not None
        sets.append("updated_at=?")
        args.append(_now())
        args.append(str(project_id))
        cur = self._conn.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE project_id=?", args)
        self._conn.commit()
        return cur.rowcount > 0

    # ---------- 读 ----------
    def get(self, project_id: str) -> dict | None:
        """读单个项目；不存在返回 None。"""
        row = self._conn.execute(
            f"SELECT {_COLS} FROM projects WHERE project_id=?",
            (str(project_id or ""),),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def list(self, user_id: str = "", include_archived: bool = False) -> list[dict]:
        """项目列表（按最近修改倒序）：可按用户过滤，默认不含已归档项目。"""
        sql = f"SELECT {_COLS} FROM projects WHERE 1=1"
        args: list = []
        if user_id:
            sql += " AND user_id=?"
            args.append(str(user_id))
        if not include_archived:
            sql += " AND COALESCE(status,'open')!='archived'"
        sql += " ORDER BY COALESCE(NULLIF(updated_at,''), NULLIF(created_at,''), '') DESC, rowid DESC"
        return [self._row_to_dict(r) for r in self._conn.execute(sql, args).fetchall()]

    # ---------- 内部 ----------
    @staticmethod
    def _row_to_dict(row) -> dict:
        """按 _COLS 位置索引解析一行（扩展列后返回值只多不少，调用方按 key 取值不受影响）。"""
        return {
            "project_id": row[0], "user_id": row[1] or "", "name": row[2] or "",
            "desc": row[3] or "", "status": row[4] or "open",
            "created_at": row[5] or "", "updated_at": row[6] or "",
        }
