# -*- coding: utf-8 -*-
"""输出格式化：wrap——统一输出契约 {reply, intent, data, route}。

- reply：清洗后的自然语言回复；
- intent：业务意图标签（research / listing / order / recommend / interrupted / none）；
- data：结构化卡片数据（透传，前端按 intent 渲染）；
- route：实际处理的智能体名（调试与 /api/status 展示用）。
"""
from __future__ import annotations

from .validator import sanitize_reply

__all__ = ["wrap"]

_VALID_INTENTS = {"research", "listing", "order", "recommend", "interrupted", "none"}


def wrap(raw: dict | None, route: str = "") -> dict:
    """把智能体原始结果包装为标准输出契约；raw 为空时输出空回复。"""
    raw = raw or {}
    reply = sanitize_reply(raw.get("reply", ""))
    intent = str(raw.get("intent", "none") or "none")
    if intent not in _VALID_INTENTS:
        intent = "none"
    return {
        "reply": reply,
        "intent": intent,
        "data": raw.get("data"),
        "route": route or raw.get("route", ""),
    }