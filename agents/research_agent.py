# -*- coding: utf-8 -*-
"""选品智能体（Research Agent）：需求/竞争/利润四步验证 + 供应商寻找。

卖家工作台视角：服务对象是跨境卖家（不是买家）——帮卖家判断类目能不能做、
在哪里进货。继承 agents/base.py 的 ReActAgentBase，只定义提示词 / 工具 / 兜底。
"""
from __future__ import annotations

import json
import re

from agents.base import ReActAgentBase
from memory.access import MemoryCaller
from tools.research import (
    calc_freight,
    calc_landed_cost,
    calc_profit,
    check_competition,
    check_compliance,
    check_demand,
    research_keywords,
    run_product_research,
)
from tools.supplier import compare_supplier, search_supplier

# ---- 系统提示词：卖家工作台视角 ------------------------------------------------
SYSTEM_PROMPT = """你是「Quote Agent」卖家工作台的选品智能体，服务对象是跨境电商卖家。

工作方式：根据卖家的问题，自主调用工具获取事实，再给出可执行的选品/采购建议：
- 判断类目能不能做（需求/竞争/利润）→ 优先调用 run_product_research 一步出结论，
  或分步调用 check_demand / check_competition / calc_profit；
- 找货源/供应商 → 调用 search_supplier 或 compare_supplier；
- 卖家没说具体品类：主动询问想做的品类/关键词，不要瞎猜。

回复要求：
1. 结论先行：先给“能做/慎入”的判断与一句话理由，再给数据明细；
2. 引用工具返回的搜索量、评分、净利率等真实数字，不得虚构；
3. 建议要可执行（采购口径、询价要点、下一步动作）；
4. 不向卖家暴露工具名称与 JSON 内部细节。"""


class ResearchAgent(ReActAgentBase):
    """选品智能体：需求 / 竞争 / 利润 / 供应商工具集（L0 工具，agent 直调）。"""

    system_prompt = SYSTEM_PROMPT
    tools = [run_product_research, check_demand, check_competition, calc_profit,
             research_keywords, check_compliance, calc_freight, calc_landed_cost,
             search_supplier, compare_supplier]
    caller = MemoryCaller("research_agent", "L1")


# ============================================================================
# 本地兜底：未配置 Key / 后端异常时给出可演示的完整链路
# ============================================================================
_SUPPLIER_RE = re.compile(r"供应商|1688|阿里|货源|进货|采购|起订量|moq", re.I)
# 演示品类关键词（与 tools/research.py 的演示市场库一致）
_KNOWN_CATEGORIES = ["充电宝", "蓝牙耳机", "保温杯", "机械键盘", "云感耳机"]


def classic_research_reply(q: str) -> dict:
    """选品/供应商兜底（无 LLM）：规则路由 → 工具直调 → 结构化卡片数据。"""
    # 供应商链路：搜索 + 对比
    if _SUPPLIER_RE.search(q):
        matched = next((c for c in _KNOWN_CATEGORIES if c in q), "保温杯")
        found = json.loads(search_supplier(matched))
        comp = json.loads(compare_supplier(matched))
        rows = comp["comparison"]
        best = comp["recommend"]
        lines = "；".join(
            f"{r['name']}（{r['city']}）MOQ{r['moq']}件 / ¥{r['unit_price']} / {r['lead_days']}天"
            for r in rows
        )
        return {
            "reply": (
                f"已为您找到「<span class='em'>{matched}</span>」的候选供应商（演示数据）：<br>"
                f"{lines}<br>综合推荐 <span class='em'>{best}</span>——{comp['reason']}"
                "<br>提示：首批建议 50-100 件试水，下单前务必索要样品与认证证书。"
            ),
            "intent": "research",
            "data": {"type": "supplier_compare", "keyword": matched,
                     "rows": rows, "recommend": best, "reason": comp["reason"]},
        }
    # 选品四步链路
    matched = next((c for c in _KNOWN_CATEGORIES if c in q), "充电宝")
    r = json.loads(run_product_research(matched))
    if not r["found"]:
        return {"reply": f"演示库暂无该品类数据，可尝试：{' / '.join(_KNOWN_CATEGORIES)}。",
                "intent": "research", "data": None}
    steps = r["steps"]
    verdict = "建议推进" if r["score"] == "3/3" else "谨慎推进"
    return {
        "reply": (
            f"「<span class='em'>{matched}</span>」选品四步结论：<b>{verdict}</b>（{r['score']}）。<br>"
            f"需求{'✓' if steps['需求']['passed'] else '✗'} / "
            f"竞争{'✓' if steps['竞争']['passed'] else '✗'} / "
            f"利润{'✓' if steps['利润']['passed'] else '✗'}（净利率 "
            f"{round(steps['利润']['margin'] * 100, 1)}%）。"
        ),
        "intent": "research",
        "data": {"type": "research_report", "category": matched,
                 "verdict": verdict, "score": r["score"], "steps": steps},
    }