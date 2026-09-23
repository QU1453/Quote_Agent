# -*- coding: utf-8 -*-
"""售前导购智能体（Pre-Sales Agent）：商品咨询、多款对比、按需求推荐。

继承 agents/base.py 的 ReActAgentBase，只定义自己的提示词、工具与兜底逻辑：
- 配置了 OPENAI_API_KEY 时：走真实 LLM，调用推荐工具后结合商品特点作答；
- 未配置 Key 或调用失败时：返回 None，由 supervisor 转入本地规则兜底
  （classic_presales_reply）。

与客服智能体的分工：客服专管订单 / 物流 / 售后；售前专管买前咨询与推荐。
"""
from __future__ import annotations

import re

from .base import ReActAgentBase, card_items, is_japanese
from tools import recommend_for, recommend_products

# 售前导购智能体的系统提示词：主管买前咨询与商品推荐
SYSTEM_PROMPT = """你是「Quote Agent」跨境电商售前导购 Agent，面向海外（日本）消费者、当前以中文演示。

工作方式：根据用户需求，调用 recommend_products 工具获取候选商品，再结合商品特点作答：
- 用户描述需求/场景（如"通勤听歌""送礼""续航久"）→ 提炼关键词调用工具；
- 用户问某类商品有哪些 → 直接按品类调用工具并逐款给出推荐理由；
- 用户比价 → 引用工具返回的真实价格，客观对比优缺点。

回复要求：
1. 简洁、热情、像真人导购；自动跟随用户语言——中文用热情口语，日语用丁寧語・敬語（です・ます調、称呼「お客様」、推荐用「おすすめいたします」）；
2. 每款商品给出具体推荐理由（结合工具返回的 feature），不得编造价格、库存、优惠；
3. 一次最多推荐 3 款，并明确指出最推荐哪一款及原因；
4. 不要向用户暴露工具名称、JSON 等内部实现细节。"""


class PreSalesAgent(ReActAgentBase):
    """售前导购智能体：只提供提示词 / 工具 / 兜底，通用逻辑全部在基类。"""

    system_prompt = SYSTEM_PROMPT
    # 导购只需要商品推荐工具
    tools = [recommend_products]


# ============================================================================
# 本地兜底路由：未配置 Key / 后端异常时，售前导购的本地规则回复
# ============================================================================
def classic_presales_reply(q: str) -> dict:
    """关键词路由 + 结构化推荐卡数据，返回 {reply, intent, data}（日语用户回复日语敬体）。"""
    s = q.lower()
    ja = is_japanese(q)  # 日语用户：回复文案切换为日语敬体（卡片商品名由前端 localize 转日文）

    # 价格 / 比价类问题（中日双语）：给出推荐并说明价格口径
    if re.search(r"多少钱|价格|价位|比价|便宜|贵|いくら|価格|値段|安い|高い", s):
        items = recommend_for(["耳机", "键盘", "充电宝"])
        return {
            "reply": (
                "为您挑了 3 款高性价比好物（价格以商品页实时为准）："
                "最推荐第一款，同价位里口碑最好、功能最均衡～"
                if not ja
                else "コストパフォーマンスの高い3商品をご提案いたします（価格は商品ページの実時価格が基準）：イチオシは1つ目、同価格帯で最も人気とバランスが取れております～"
            ),
            "intent": "recommend",
            "data": {"items": card_items(items)},
        }

    # 品类咨询：按提及的品类推荐（中日关键词均支持）
    category = None
    for kw, tags in (
        ("耳机", ["耳机"]), ("イヤホン", ["耳机"]), ("ヘッドホン", ["耳机"]),
        ("键盘", ["键盘"]), ("キーボード", ["键盘"]),
        ("保温杯", ["保温杯"]), ("マグボトル", ["保温杯"]), ("水筒", ["保温杯"]),
        ("充电宝", ["充电宝"]), ("バッテリー", ["充电宝"]),
    ):
        if kw in s:
            category = tags
            break
    if re.search(r"推荐|想买|哪款|什么好|好物|商品|选|看看|おすすめ|推薦|どれ|選ぶ|見たい", s) or category:
        items = recommend_for(category or ["耳机", "键盘", "充电宝"])
        if ja:
            name = {"耳机": "イヤホン", "键盘": "キーボード", "保温杯": "マグボトル", "充电宝": "モバイルバッテリー"}.get(
                category[0] if category else "", "人気商品"
            )
            reply = f"ご要望に合わせて{name}を3商品ご提案いたします。イチオシは1つ目、コスパと口コミともに自信ありでございます～"
        else:
            name = category[0] if category else "好物"
            reply = f"根据您的需求推荐这 3 款{name}，最推荐第一款，性价比和口碑都在线～"
        return {"reply": reply, "intent": "recommend", "data": {"items": card_items(items)}}

    # 问候语
    if re.search(r"在吗|你好|哈喽|hi|hello|こんにちは", s):
        if ja:
            return {
                "reply": "こんにちは～Quote Agent のプリセールス（ご案内担当）でございます。ご予算やご要望をお聞かせいただければ、最適な商品をご提案いたします～",
                "intent": "none",
                "data": None,
            }
        return {
            "reply": "您好呀～我是 Quote Agent 的售前导购，想买什么告诉我需求或预算，帮您挑最合适的～",
            "intent": "none",
            "data": None,
        }

    # 默认引导
    if ja:
        return {
            "reply": "何かお探しでございますか？<br>「イヤホンのおすすめ / マグボトルどれ / バッテリーの価格」などとお試しください。",
            "intent": "none",
            "data": None,
        }
    return {
        "reply": "想找点什么好物呢？<br>可以试试问我：<span class='em'>推荐一款耳机 / 保温杯哪款好 / 充电宝价格</span>。",
        "intent": "none",
        "data": None,
    }
