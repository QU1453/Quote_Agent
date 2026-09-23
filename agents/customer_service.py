# -*- coding: utf-8 -*-
"""客服智能体（Customer Service Agent）：订单 / 物流 / 售后 / 推荐的通用客服。

继承 agents/base.py 的 ReActAgentBase，只定义自己的提示词、工具与兜底逻辑：
- 配置了 OPENAI_API_KEY 时：走真实 LLM，自主调用工具后生成回复；
- 未配置 Key 或调用失败时：由 supervisor / server 转入本地规则兜底（classic_reply）。

安全约定：本模块从不保存密钥，OPENAI_API_KEY 只从环境变量 / .env 读取。
"""
from __future__ import annotations

import re

from .base import ReActAgentBase, is_japanese, order_card
from tools import (
    classify_after_sale,
    draft_reply,
    handle_return,
    lookup_order,
    query_order_info,
    recommend_for,
    recommend_products,
    register_return,
    track_logistics,
)

# 客服智能体的系统提示词：主管订单 / 物流 / 售后，兼顾推荐
SYSTEM_PROMPT = """你是「Quote Agent」跨境电商智能客服 Agent，面向海外（日本）消费者、当前以中文演示。

工作方式：根据用户问题，自主决定是否调用工具获取事实，再用自然语言作答：
- 查订单/物流 → 先调用 query_order_info / track_logistics（订单号形如 2026081200012）；
- 办退换货/退款 → 先调用 handle_return；
- 求推荐商品 → 调用 recommend_products，并结合工具返回的商品特点给出理由；
- 用户没说订单号：礼貌询问，不要编造单号。

回复要求：
1. 简洁、友好、像真人客服；自动跟随用户语言——中文用亲和口语，日语用丁寧語・敬語（です・ます調、称呼「お客様」）；
2. 引用工具返回的金额、地点、时间等事实，不得虚构；
3. 若工具返回未找到，如实说明并引导用户核对订单号；
4. 不要向用户暴露工具名称、JSON 等内部实现细节。"""


class CustomerServiceAgent(ReActAgentBase):
    """客服智能体：只提供提示词 / 工具 / 兜底，通用逻辑全部在基类。"""

    system_prompt = SYSTEM_PROMPT
    # 客服可调用：订单查询 / 物流跟踪 / 退换货办理 / 售后分类与回复 / 商品推荐
    tools = [query_order_info, track_logistics, handle_return,
             classify_after_sale, draft_reply, recommend_products]


# ============================================================================
# 本地兜底路由：未配置 Key / 后端异常时，给出与前端演示一致的体验
# ============================================================================
def classic_reply(q: str) -> dict:
    """关键词路由 + 结构化卡片数据，返回 {reply, intent, data}（日语用户回复日语敬体）。"""
    s = q.lower()
    ja = is_japanese(q)  # 日语用户：回复文案切换为日语敬体（卡片数据由前端 localize 处理）

    # 分支关键词中日双语（日语用户经上面 ja 分支回复日语敬体）
    if re.search(r"物流|快递|到哪|发货|订单|单号|签收|配送|荷物|注文|追跡|届く|輸送", s):
        order = lookup_order("2026081200012")
        if ja:
            reply = (
                f"お客様のご注文は現在 <span class='em'>「{order['location']}」</span> にございます。"
                f"{order['eta']}に到着予定でございます。配達時にはSMSでお知らせいたします～"
            )
        else:
            reply = (
                f"您的订单当前处于 <span class='em'>「{order['location']}」</span>，"
                f"预计{order['eta']}，到达派送点会有短信提醒～"
            )
        return {"reply": reply, "intent": "order", "data": order_card(order)}

    if re.search(r"退|换|退款|售后|质量|坏了|返品|交換|返金|キャンセル|不良|壊れ", s):
        r = register_return("2026081200012", "质量问题")
        if ja:
            reply = (
                f"かしこまりました～ご注文は <span class='em'>7日間の無条件返品</span> 期間内でございます。"
                f"アフター受付 <span class='em'>{r['receipt']}</span> を発行いたしました。<br>"
                "① 返金のみ（元の支払い方法へ）　② 交換（送料無料・優先発送）　③ 返品・返金"
            )
        else:
            reply = (
                f"可以的～该订单在 <span class='em'>7 天无理由</span> 期限内，"
                f"已为您登记售后单 <span class='em'>{r['receipt']}</span>。<br>"
                "① 仅退款（原路退回）　② 换新（免运费优先发）　③ 退货退款"
            )
        return {"reply": reply, "intent": "none", "data": None}

    if re.search(r"推荐|耳机|键盘|保温杯|充电宝|什么好|哪款|おすすめ|イヤホン|キーボード|マグボトル|バッテリー|どれ|いくら|価格|値段", s):
        # 推荐类兜底：委托给售前导购的兜底逻辑，保持口径一致
        from .presales import classic_presales_reply

        return classic_presales_reply(q)

    if re.search(r"在吗|你好|哈喽|hi|hello|こんにちは", s):
        if ja:
            return {
                "reply": "こんにちは～Quote Agent の AI スマートカスタマーでございます。配送照会・返品交換・商品のおすすめが可能です。ご用件をお聞かせください～",
                "intent": "none",
                "data": None,
            }
        return {
            "reply": "您好呀～我是 Quote Agent 的 AI 智能客服，可以帮您查物流、办退换、挑商品，请直接告诉我需求即可～",
            "intent": "none",
            "data": None,
        }

    if ja:
        return {
            "reply": "かしこまりました～こちらで対応可能でございます。<br>「注文はどこ / 返品方法 / イヤホンのおすすめ」などとお試しください。",
            "intent": "none",
            "data": None,
        }
    return {
        "reply": "收到～这个问题我可以处理。<br>您可以试试问我：<span class='em'>订单到哪了 / 怎么退货 / 推荐一款耳机</span>。",
        "intent": "none",
        "data": None,
    }
