# -*- coding: utf-8 -*-
"""感知-会话生命周期：谈话开始 / 结束（接分层总结管线）。

- start_conversation(user_id, scope, project_id, feature_key) -> session_id：生成新谈话 ID
  并登记到谈话注册表（scope 为区块标签：research / listing / global；project_id /
  feature_key 为「项目 → 细分板块」二级归属，空 = 未分类）；
- end_conversation(mm, sup, session_id, user_id)：调 supervisor 结束管线
  （一级总结必做；达 M 个自动触发二级总结 → 长期记忆）。
"""
from __future__ import annotations

import uuid

from memory.access import AuditLogger

__all__ = ["start_conversation", "end_conversation"]


def start_conversation(user_id: str = "default", scope: str = "",
                       project_id: str = "", feature_key: str = "") -> str:
    """开会新谈话：生成 UUID session_id 并登记（幂等），返回 session_id。

    scope：区块标签（research=选品 / listing=上架 / global），用于会话清单分区；
    project_id / feature_key：二级归属（所属项目 + 细分功能板块，空 = 未分类）。
    """
    session_id = uuid.uuid4().hex
    try:
        from memory import get_short_term

        stm = get_short_term()
        stm.registry.start(session_id, user_id=user_id, scope=scope,
                           project_id=project_id, feature_key=feature_key)
        AuditLogger.log(
            actor="perception", actor_level="L0", action="conversation:start",
            module="short_term", target_id=session_id, session_id=session_id, result="ok",
        )
    except Exception:  # noqa: BLE001 - 注册表故障不阻塞会话创建
        pass
    return session_id


def end_conversation(sup, session_id: str, user_id: str = "default") -> dict:
    """结束谈话：supervisor 一级总结 + （视游标）二级总结；返回管线结果。

    sup 为 Supervisor 实例（agents/supervisor.py），mm 参数保留兼容空位
    （接入方可通过 MemoryManager.start_conversation 自行登记，不强制）。
    """
    result = sup.end_conversation(session_id, user_id=user_id)
    AuditLogger.log(
        actor="perception", actor_level="L0", action="conversation:end",
        module="short_term", target_id=session_id, session_id=session_id,
        result="ok" if result.get("ok") else "error",
    )
    return result