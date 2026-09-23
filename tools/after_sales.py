# -*- coding: utf-8 -*-
"""售后工具：退换货 / 退款受理登记（演示数据，真实版对接售后工单系统）。"""
from __future__ import annotations

import json

from .order import lookup_order

_REFUND_SEQ = [2033]  # 售后单号自增，演示用


def register_return(order_no: str, reason: str) -> dict:
    """登记售后单，返回受理信息（演示）。"""
    n = _REFUND_SEQ[-1] + 1
    _REFUND_SEQ.append(n)
    order = lookup_order(order_no)
    return {
        "receipt": f"#SA-{n}",
        "order_no": str(order_no).strip(),
        "product_name": order["product"]["name"] if order else None,
        "refund_amount": order["total"] if order else None,
        "reason": reason or "未填写",
        "options": ["仅退款（原路退回）", "换新（免运费优先发）", "退货退款"],
    }


def handle_return(order_no: str, reason: str = "") -> str:
    """办理退换货 / 退款：登记售后单并给出可选方案。

    何时使用：用户想退货、换货、退款、报质量问题、商品坏了需要售后时调用；仅做意向咨询而未确认办理时，可先说明政策再调用本工具登记。

    调用格式（JSON）：
    {"tool": "handle_return", "parameters": {"order_no": "<订单号，字符串类型>", "reason": "<退货原因，字符串类型，可省略，默认空字符串>"}}

    参数说明：
    - order_no：订单号，字符串类型（string），例如 "2026081200012"，用户消息中形如 2026xxxxxxxx 的连续数字。
    - reason：退货原因，字符串类型（string），可省略；用户未说明时传空字符串 ""。
    """
    r = register_return(order_no, reason)
    return json.dumps(
        {
            "ok": True,
            "receipt": r["receipt"],
            "order_no": r["order_no"],
            "refund_amount": r["refund_amount"],
            "message": f"售后单 {r['receipt']} 已登记",
            "options": r["options"],
        },
        ensure_ascii=False,
    )


# ---- 售后分类演示规则：类型 → 命中词 / 处理口径 / 紧急度 --------------------------
_AFTER_SALE_TYPES = {
    "质量问题": {
        "keywords": ["坏了", "不能用", "破损", "异响", "失灵", "开裂", "没声音", "充不进电"],
        "policy": "质量问题包退换，往返运费我方承担",
        "urgency": "高",
    },
    "物流问题": {
        "keywords": ["没收到", "物流不动", "丢件", "迟迟不到", "一直显示揽收"],
        "policy": "物流超 7 天未更新可补发或全额退款",
        "urgency": "高",
    },
    "七天无理由": {
        "keywords": ["不想要了", "不合适", "后悔了", "七天无理由", "退货运费"],
        "policy": "未拆封不影响二次销售支持无理由退货，退回运费买家承担",
        "urgency": "中",
    },
    "咨询/其他": {
        "keywords": [],
        "policy": "按具体问题解答或转人工跟进",
        "urgency": "低",
    },
}


def classify_after_sale(description: str) -> str:
    """售后问题分类：按用户描述归类并给出处理口径（不登记售后单）。

    何时使用：用户描述了售后问题但还没明确要退货/退款时调用——先分类，再决定安抚话术与处理流程。

    调用格式（JSON）：
    {"tool": "classify_after_sale", "parameters": {"description": "<问题描述，字符串类型>"}}

    参数说明：
    - description：用户对售后问题的原始描述，字符串类型（string），例如 "耳机左边没声音了"。
    """
    text = str(description or "")
    best_type, hits = "咨询/其他", []
    for tname, conf in _AFTER_SALE_TYPES.items():
        matched = [w for w in conf["keywords"] if w in text]
        if len(matched) > len(hits):
            best_type, hits = tname, matched
    conf = _AFTER_SALE_TYPES[best_type]
    return json.dumps({
        "ok": True,
        "type": best_type,
        "matched_keywords": hits,
        "urgency": conf["urgency"],
        "policy": conf["policy"],
        "hint": "质量问题/物流问题优先安抚并主动给出方案；无理由退货按政策说明运费规则。",
    }, ensure_ascii=False)


def draft_reply(order_no: str, description: str) -> str:
    """起草客服回复：按售后分类生成安抚话术 + 处理方案（不自动登记售后单）。

    何时使用：需要给用户一段可直接发送的客服回复时调用；用户确认要退货/退款/换货后，再调用 handle_return 正式登记。

    调用格式（JSON）：
    {"tool": "draft_reply", "parameters": {"order_no": "<订单号，字符串类型>", "description": "<问题描述，字符串类型>"}}

    参数说明：
    - order_no：订单号，字符串类型（string），例如 "2026081200012"；查询不到时回复将不含订单与金额信息。
    - description：问题描述，字符串类型（string），例如 "充电宝外壳开裂了"。
    """
    cls = json.loads(classify_after_sale(description))
    order = lookup_order(str(order_no).strip())
    product = order["product"]["name"] if order else "您的商品"
    amount = order["total"] if order else None
    t = cls["type"]
    if t == "质量问题":
        reply = (f"非常抱歉给您带来不好的体验！{product} 出现质量问题，我们承担全部运费，"
                 "您可以放心申请退换，已为您登记优先处理通道。")
        actions = ["用户确认后调用 handle_return 登记售后单", "方案优先级：换新 → 仅退款"]
    elif t == "物流问题":
        reply = (f"抱歉让您久等了！已为您加急核实物流状态，若 7 天内仍未更新，"
                 "我们将直接为您补发或全额退款，无需您操作。")
        actions = ["先调用 track_logistics 查最新物流", "超时未更新再登记补发"]
    elif t == "七天无理由":
        reply = (f"没问题的！{product} 未拆封、不影响二次销售即可无理由退货，"
                 "商品全额退款，退回运费需买家承担。")
        actions = ["确认商品状态后调用 handle_return 登记", "提醒买家保留包装完整"]
    else:
        reply = f"您好！关于 {product} 的问题我已记录，稍后由专属客服为您详细解答。"
        actions = ["人工跟进", "补充问题描述后再分类"]
    return json.dumps({
        "ok": True,
        "type": t,
        "order_no": str(order_no).strip(),
        "product": product,
        "refund_amount": amount,
        "urgency": cls["urgency"],
        "reply": reply,
        "next_actions": actions,
        "policy": cls["policy"],
    }, ensure_ascii=False)
