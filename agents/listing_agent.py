# -*- coding: utf-8 -*-
"""Listing 智能体（Listing Agent）：文案起草 / 图片合规 / 定价 / 履约建议。

卖家工作台视角：帮卖家完成 Listing 创建与上架前的自检。
继承 agents/base.py 的 ReActAgentBase，只定义提示词 / 工具 / 兜底。
"""
from __future__ import annotations

import json
import re

from agents.base import ReActAgentBase
from memory.access import MemoryCaller
from tools.listing import (
    audit_listing,
    check_images,
    draft_listing,
    price_strategy,
    recommend_fulfillment,
)

# ---- 系统提示词：卖家工作台视角 ------------------------------------------------
SYSTEM_PROMPT = """你是「Quote Agent」卖家工作台的 Listing 智能体，帮卖家完成 Listing 创建与上架。

工作方式：根据卖家的问题，自主调用工具获取结果，再给出可直接使用的 Listing 内容：
- 写标题/五点/Search Terms → 调用 draft_listing；
- 图片是否合规 → 调用 check_images；
- 定价决策 → 调用 price_strategy；
- 选 FBA 还是 FBM → 调用 recommend_fulfillment；
- 卖家没给商品名：按商品库编码（earbuds/keyboard/tumbler/power）询问确认。

回复要求：
1. 直接给出成品（标题、五点、关键词），卖家复制即可用；
2. 合规要点要提醒（标题 ≤75 字符、主图白底、价格保 30% 净利率）；
3. 引用工具返回的数据，不得虚构；
4. 不向卖家暴露工具名称与 JSON 内部细节。"""


class ListingAgent(ReActAgentBase):
    """Listing 智能体：文案/图片/定价/履约工具集。"""

    system_prompt = SYSTEM_PROMPT
    tools = [draft_listing, check_images, audit_listing, price_strategy, recommend_fulfillment]
    caller = MemoryCaller("listing_agent", "L1")


# ============================================================================
# 本地兜底：未配置 Key / 后端异常时给出可演示的完整链路
# ============================================================================
_PRICE_RE = re.compile(r"定价|售价|多少钱|价格策略", re.I)
_IMAGE_RE = re.compile(r"图片|主图|图片合规|白底", re.I)
_FULFILL_RE = re.compile(r"fba|fbm|履约|发货方式|首批|备货", re.I)
# 商品名 → 商品库编码（与 tools/catalog.py 对齐）
_CODE_MAP = {"云感耳机": "earbuds", "耳机": "earbuds", "键盘": "keyboard",
             "保温杯": "tumbler", "水杯": "tumbler", "充电宝": "power", "移动电源": "power"}


def _resolve_code(q: str, default: str = "earbuds") -> str:
    for name, code in _CODE_MAP.items():
        if name in q:
            return code
    return default


def classic_listing_reply(q: str) -> dict:
    """Listing 兜底（无 LLM）：规则路由 → 工具直调 → 结构化卡片数据。"""
    if _PRICE_RE.search(q):
        code = _resolve_code(q)
        r = json.loads(price_strategy(code))
        if not r["found"]:
            return {"reply": "商品库无此编码（可用：earbuds/keyboard/tumbler/power）。",
                    "intent": "listing", "data": None}
        return {
            "reply": (
                f"「{r['product']}」定价建议：<span class='em'>¥{r['suggested']}</span>"
                f"（略低于竞品 ¥{r['competitor_ref']} 做首发）。"
                f"保 30% 净利率的底价 <span class='em'>¥{r['min_break_even']}</span>，低于即亏利润。"
            ),
            "intent": "listing",
            "data": {"type": "price_strategy", **{k: v for k, v in r.items() if k != "found"}},
        }
    if _IMAGE_RE.search(q):
        r = json.loads(check_images([
            "https://img.demo.com/earbuds_main_rgb255_white.jpg",
            "https://img.demo.com/earbuds_lifestyle_text_banner.jpg",
        ]))
        return {
            "reply": (
                f"图片合规自检：{r['passed_count']}/{r['total']} 张通过——"
                "主图必须纯白底(255,255,255)、商品占比 ≥85%、无文字/水印。"
            ),
            "intent": "listing",
            "data": {"type": "image_check", "total": r["total"],
                     "passed_count": r["passed_count"], "detail": r["detail"]},
        }
    if _FULFILL_RE.search(q):
        code = _resolve_code(q)
        r = json.loads(recommend_fulfillment(code, stock=80))
        if not r["found"]:
            return {"reply": "商品库无此编码（可用：earbuds/keyboard/tumbler/power）。",
                    "intent": "listing", "data": None}
        return {
            "reply": (
                f"「{r['product']}」履约建议：首批 {r['first_batch']} 件走 "
                f"<span class='em'>{r['suggested_mode']}</span>。{r['hint']}"
            ),
            "intent": "listing",
            "data": {"type": "fulfillment", **{k: v for k, v in r.items() if k != "found"}},
        }
    # 默认：起草 Listing（标题 + 五点 + Search Terms）
    matched = next((name for name in _CODE_MAP if name in q), "云感耳机")
    r = json.loads(draft_listing(matched, keywords=[matched]))
    return {
        "reply": (
            f"「{matched}」Listing 草稿（可直接复制）：<br>"
            f"<b>标题</b>：{r['title']}<br>"
            f"<b>五点</b>：已生成 {len(r['bullets'])} 条（见下方卡片）；<br>"
            f"<b>Search Terms</b>：{r['search_terms']}"
            "<br><span class='em'>标题已按 75 字符新规截断</span>。"
        ),
        "intent": "listing",
        "data": {"type": "listing_draft", "product": matched, "title": r["title"],
                 "title_len": r["title_len"], "bullets": r["bullets"],
                 "search_terms": r["search_terms"], "tips": r["tips"]},
    }