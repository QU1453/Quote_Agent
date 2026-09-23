# -*- coding: utf-8 -*-
"""技能提炼提示词与工具函数：复盘文本 → 技能四要素（事/的/痛/解）。

设计（见 docs/memory-system-design.md §3.5）：
- 输入：失败/成功复盘文本（transcript），可为谈话原文或人工复盘记录；
- 输出：0~N 条四要素技能条目（situation/goal/pain/solution + tags + trigger）；
- 无 Key / LLM 不可用 / 产出非法时返回 []（宁可空，绝不写坏数据）。
"""
from __future__ import annotations

import json
import re

from config import API_KEY, BASE_URL, SUMMARIZER_MODEL

__all__ = ["SKILL_WRITER_PROMPT", "build_skill_entries"]

# 四要素范式说明（与 memory/skill/memory.py 表结构一一对应）
_SKILL_SCHEMA = {
    "situation": "str",   # 遇到了什么事 / 什么场景
    "goal": "str",        # 当时想达成什么目的
    "pain": "str",        # 卡在什么痛点 / 失败原因
    "solution": "str",    # 最后怎么解决的（可复用的做法）
    "tags": ["str"],      # 检索标签（中文关键词）
    "trigger": "str",     # 触发词：下次遇到类似情况时用来唤起的短语
}

SKILL_WRITER_PROMPT = (
    "你是 Quote Agent 卖家工作台的技能提炼员（权限 L2）。"
    "请阅读下面的复盘文本，从中提炼可复用的技能条目，输出 JSON 数组，每项符合：\n"
    + json.dumps(_SKILL_SCHEMA, ensure_ascii=False)
    + "\n规则：\n"
    "1. situation 概述遇到的事；goal 说明当时目的；pain 写明痛点/失败原因；\n"
    "2. solution 必须可复用、可操作（不是空话），是技能的核心；\n"
    "3. trigger 写一句将来能唤起该技能的短触发词（3~12 个字）；\n"
    "4. 没有可提炼的技能时输出 []；只输出 JSON 数组本体，不要任何解释或 markdown 标记。\n\n"
    "复盘文本：\n{transcript}"
)


def build_skill_entries(transcript: str, api_key: str | None = None,
                        base_url: str | None = None,
                        model: str | None = None) -> list[dict]:
    """复盘文本 → 技能条目列表（LLM 提炼）。

    api_key/base_url/model 缺省走 config 槽位；无 Key 或产出非法时返回 []。
    返回项字段：situation/goal/pain/solution/tags/trigger（可直接喂 SkillMemory.add_skill）。
    """
    text = (transcript or "").strip()
    if not text:
        return []
    key = api_key if api_key is not None else API_KEY
    if not key:
        return []
    try:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=model or SUMMARIZER_MODEL,
            api_key=key,
            base_url=base_url or BASE_URL or None,
            temperature=0.2,
        )
        resp = llm.invoke(SKILL_WRITER_PROMPT.format(transcript=text[-12000:]))
        content = getattr(resp, "content", "") or ""
    except Exception:  # noqa: BLE001 - 缺依赖/网络/额度异常 → 返回空
        return []
    if not isinstance(content, str):
        return []
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        solution = str(it.get("solution", "")).strip()
        situation = str(it.get("situation", "")).strip()
        if not solution:  # solution 是技能核心，缺失即整条作废
            continue
        tags = it.get("tags")
        out.append({
            "situation": situation[:200] or "（未说明）",
            "goal": str(it.get("goal", "")).strip()[:200],
            "pain": str(it.get("pain", "")).strip()[:200],
            "solution": solution[:800],
            "tags": [str(t) for t in tags][:8] if isinstance(tags, list) else [],
            "trigger": str(it.get("trigger", "")).strip()[:60],
        })
    return out