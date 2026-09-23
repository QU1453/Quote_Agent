# -*- coding: utf-8 -*-
"""总结智能体（SummarizerAgent）：分层总结管线的一级 / 二级总结。

职责（见 docs/memory-system-design.md §4）：
- 一级总结：谈话结束后立即执行，按固定范式输出 JSON，保留细节；
- 二级总结：每 M 个一级总结滚动合并，废除无用信息，产出长期记忆范式条目。

安全约定：
- 写库 caller 固定为 MemoryCaller("summarizer", "L1", session_id)；
- LLM 输出必须先过范式校验（_parse_and_validate），坏产出不落库、走规则兜底；
- 无 Key / LLM 不可用时走 fallback 规则版本，保证演示链路始终可跑。
"""
from __future__ import annotations

import json
import re

from config import SUMMARIZER_MODEL, L2_SUMMARY_INTERVAL
from memory.access import MemoryCaller

# ---- 一级总结范式（十个字段，见 memory-system-design.md §4.2）----------------------
L1_SCHEMA = {
    "conversation_id": "str",
    "user_id": "str",
    "time_range": "str",
    "topic": "str",             # 对话主题（一句话）
    "user_requests": ["str"],   # 用户诉求列表
    "key_facts": [{"k": "str", "v": "str"}],  # 关键事实 KV
    "decisions": ["str"],       # 结论 / 决定
    "pending_items": ["str"],   # 未完成事项
    "entities": ["str"],        # 涉及的商品 / 供应商 / 文档
    "preferences": ["str"],     # 用户情绪与偏好观察
    "artifacts": ["str"],       # 产出物引用（表格 / 文档 ID）
    "tags": ["str"],
}

SUMMARIZER_PROMPT = (
    "你是 Quote Agent 卖家工作台的专职记忆总结员（权限 L1）。"
    "请把下面的对话总结为一级总结 JSON（保留细节，不得虚构），严格遵守以下 schema：\n"
    + json.dumps(L1_SCHEMA, ensure_ascii=False)
    + "\n规则：\n"
    "1. topic 用一句话概括本次谈话；user_requests 逐条列出用户诉求；\n"
    "2. key_facts 提取订单号 / 金额 / 商品 / 供应商等关键事实（k 为事实名，v 为值）；\n"
    "3. pending_items 只列未完成事项；preferences 记录情绪与偏好观察；\n"
    "4. 只输出 JSON 本体，不要任何解释、前言或 markdown 代码块标记。\n\n"
    "对话原文：\n{messages}"
)

# ---- 二级总结范式（长期记忆条目，见 memory-system-design.md §4.3）-------------------
L2_PROMPT = (
    "你是 Quote Agent 卖家工作台的长期记忆整理员（权限 L1）。"
    "下面是最近 {n} 个谈话的一级总结（JSON 列表）。请滚动合并，废除无用信息"
    "（闲聊、重复、已解决旧事），只保留关键信息，输出一个 JSON 数组，"
    "每项符合：\n"
    '{{"entry_type": "user_profile|fact|pending_item|preference", "content": "...",'
    ' "confidence": 0.0~1.0, "tags": ["..."], "source_conversations": ["session_id"]}}\n'
    "规则：\n"
    "1. 保留：用户画像、长期偏好、未完成事项、反复出现的关键事实、重要决策与理由；\n"
    "2. 丢弃：寒暄闲聊、一次性查询、已解决的临时问题；\n"
    "3. source_conversations 注明来源谈话 session_id（可多个）；\n"
    "4. 只输出 JSON 数组本体，不要任何解释或 markdown 标记。\n\n"
    "一级总结列表：\n{entries}\n\n已有长期记忆（避免重复）：\n{previous}"
)


def _parse_and_validate(text: str, schema: dict = L1_SCHEMA) -> dict | None:
    """范式校验器：解析 JSON 并按 schema 校验（坏产出返回 None，不落库）。

    列表字段宽容处理：非列表类型自动包裹；字符串字段非 str 转 str。
    """
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned)  # 容忍代码块包裹
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or "topic" not in data:
        return None
    out: dict = {}
    for key, spec in schema.items():
        val = data.get(key)
        if spec == "str":
            out[key] = str(val) if val is not None else ""
        elif spec == ["str"]:
            items = val if isinstance(val, list) else ([val] if val else [])
            out[key] = [str(x) for x in items][:20]
        elif isinstance(spec, list) and isinstance(spec[0], dict):  # key_facts
            items = val if isinstance(val, list) else ([val] if val else [])
            facts = []
            for it in items[:20]:
                if isinstance(it, dict) and "k" in it:
                    facts.append({"k": str(it["k"]), "v": str(it.get("v", ""))})
            out[key] = facts
    return out


class SummarizerAgent:
    """专职总结智能体。available=False（无 Key / 缺依赖）时自动走规则降级。"""

    caller = MemoryCaller("summarizer", "L1")

    def __init__(self, api_key: str | None, base_url: str | None = None,
                 model: str = SUMMARIZER_MODEL):
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self._llm = None
        self.reason = None
        if not api_key:
            self.reason = "未配置 OPENAI_API_KEY，总结走规则降级"
            return
        try:
            from langchain_openai import ChatOpenAI

            self._llm = ChatOpenAI(model=model, api_key=api_key,
                                   base_url=base_url or None, temperature=0.2)
        except Exception as exc:  # noqa: BLE001 - 缺依赖等
            self.reason = f"总结 LLM 初始化失败（{exc.__class__.__name__}: {exc}）"

    @property
    def available(self) -> bool:
        """LLM 总结是否可用。"""
        return self._llm is not None

    # ---------- 一级总结 ----------
    def summarize(self, messages: str, session_id: str = "", user_id: str = "") -> dict:
        """谈话原文 → 一级总结范式 dict。

        优先 LLM；输出坏掉或不可用时落回 fallback（保证总结永不落空）。
        """
        if self.available:
            try:
                resp = self._llm.invoke(SUMMARIZER_PROMPT.format(messages=messages[-16000:]))
                text = getattr(resp, "content", "") or ""
                parsed = _parse_and_validate(text) if isinstance(text, str) else None
                if parsed:
                    parsed["conversation_id"] = parsed.get("conversation_id") or session_id
                    parsed["user_id"] = parsed.get("user_id") or user_id
                    parsed["time_range"] = parsed.get("time_range") or (
                        self._time_range(messages))
                    return parsed
            except Exception:  # noqa: BLE001 - 网络/额度异常 → 规则降级
                pass
        return self.fallback_summary(messages, session_id=session_id, user_id=user_id)

    @staticmethod
    def fallback_summary(messages: str, session_id: str = "", user_id: str = "") -> dict:
        """无 LLM 兜底：首条用户消息做主题、截取高频词做 tags，其余字段留空。

        目的：保证演示链路（分层总结 → 长期记忆）在无 Key 时也能整链跑通。
        """
        first_user = ""
        for line in (messages or "").splitlines():
            line = line.strip()
            if not line:
                continue
            line = re.sub(r"^(用户|客服|系统|assistant|user|system)\s*[:：]\s*", "", line).strip()
            if not line:
                continue
            first_user = line[:80]
            break
        keywords = re.findall(r"选品|供应商|listing|上架|利润|竞争|进货|退款|退货|物流|订单|定价",
                              messages or "")
        return {
            "conversation_id": session_id,
            "user_id": user_id,
            "time_range": SummarizerAgent._time_range(messages),
            "topic": first_user or "（无主题）",
            "user_requests": [first_user] if first_user else [],
            "key_facts": [],
            "decisions": [],
            "pending_items": [],
            "entities": [],
            "preferences": [],
            "artifacts": [],
            "tags": sorted(set(keywords)),
        }

    @staticmethod
    def _time_range(messages: str) -> str:
        """无时间戳时返回空串（LLM 路径会自填；规则兜底不伪造时间）。"""
        return ""

    # ---------- 二级总结（P3 由 supervisor 触发） ----------
    def second_stage(self, entries: list[dict], previous: list[dict] | None = None) -> list[dict]:
        """M 个一级总结 → 长期记忆范式条目列表（LLM 失败走 fallback）。"""
        if self.available:
            try:
                payload = json.dumps(entries, ensure_ascii=False)
                prev = json.dumps(previous or [], ensure_ascii=False)
                resp = self._llm.invoke(L2_PROMPT.format(
                    n=len(entries), entries=payload[-12000:], previous=prev[-4000:],
                ))
                text = getattr(resp, "content", "") or ""
                parsed = self._parse_entries(text)
                if parsed:
                    return parsed
            except Exception:  # noqa: BLE001
                pass
        return self.fallback_second_stage(entries)

    def _parse_entries(self, text: str) -> list[dict]:
        """二级总结输出校验：JSON 数组 → 每项 entry_type 合法且 content 非空。"""
        if not text:
            return []
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        out = []
        for it in data:
            if not isinstance(it, dict):
                continue
            etype = str(it.get("entry_type", ""))
            content = str(it.get("content", "")).strip()
            if etype not in ("user_profile", "fact", "pending_item", "preference") or not content:
                continue
            out.append({
                "entry_type": etype,
                "content": content[:400],
                "confidence": float(it.get("confidence", 0.5)),
                "tags": [str(t) for t in (it.get("tags") or [])][:8],
                "source_conversations": [str(s) for s in (it.get("source_conversations") or [])][:16],
            })
        return out

    @staticmethod
    def fallback_second_stage(entries: list[dict]) -> list[dict]:
        """无 LLM 兜底：pending_items 全部进长期记忆、topic 聚合为一条 fact。

        规则版本刻意保守——只搬"未完成事项"和"主题"，绝不臆造画像/偏好。
        """
        out: list[dict] = []
        pend = []
        topics = []
        sources: list[str] = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            sid = str(e.get("conversation_id", ""))
            if sid:
                sources.append(sid)
            for item in e.get("pending_items", []) or []:
                if str(item).strip():
                    pend.append(str(item).strip())
            if e.get("topic"):
                topics.append(str(e["topic"]).strip())
        for item in pend[:10]:
            out.append({
                "entry_type": "pending_item",
                "content": item,
                "confidence": 0.8,
                "tags": [],
                "source_conversations": sources[-L2_SUMMARY_INTERVAL:],
            })
        if topics:
            out.append({
                "entry_type": "fact",
                "content": "近期谈话主题：" + "；".join(topics[-L2_SUMMARY_INTERVAL:]),
                "confidence": 0.6,
                "tags": [],
                "source_conversations": sources[-L2_SUMMARY_INTERVAL:],
            })
        return out


def summarize_ad_hoc(messages: str, api_key: str | None, base_url: str | None = None) -> dict:
    """便捷入口：临时造一个总结智能体做一次一级总结（demo / 测试用）。"""
    return SummarizerAgent(api_key, base_url).summarize(messages)