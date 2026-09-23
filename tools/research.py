# -*- coding: utf-8 -*-
"""选品研究工具（tools.research）：需求 / 竞争 / 利润 / 四步合验 + 关键词 / 合规 / 物流 / 落地成本。

演示数据版本：内置 5 个类目的模拟市场数据（搜索量/BSR/售价/重量），
真实版替换为卖家精灵 / Jungle Scout 等数据源 API 调用（接入点见各函数注释）。
所有工具经 P0 双层注册表登记：模块局部表 tools.research + 全局表同步。
"""
from __future__ import annotations

import json

from core.dispatch.registry import LocalRegistry

# ---- 局部注册表（owner=tools.research；high 频直连 + 全局登记）----------------------
registry = LocalRegistry("tools.research")

# ---- 演示市场库：类目 → 需求与成本参数（真实版换数据源 API）-----------------------
_MARKET = {
    "charger": {"category": "充电宝", "search_volume": 8500, "bsr": 320, "avg_price": 37.5,
                "weight_lb": 0.9, "purchase": 25.0, "compet_rating": 4.2, "compet_reviews": 386},
    "earphone": {"category": "蓝牙耳机", "search_volume": 12000, "bsr": 210, "avg_price": 45.0,
                 "weight_lb": 0.5, "purchase": 30.0, "compet_rating": 4.4, "compet_reviews": 612},
    "tumbler": {"category": "保温杯", "search_volume": 6200, "bsr": 450, "avg_price": 28.0,
                "weight_lb": 1.1, "purchase": 18.0, "compet_rating": 4.1, "compet_reviews": 298},
    "keyboard": {"category": "机械键盘", "search_volume": 4100, "bsr": 780, "avg_price": 89.0,
                 "weight_lb": 2.6, "purchase": 60.0, "compet_rating": 4.3, "compet_reviews": 941},
    "earbuds": {"category": "云感耳机", "search_volume": 3900, "bsr": 260, "avg_price": 52.0,
                "weight_lb": 0.6, "purchase": 33.0, "compet_rating": 4.0, "compet_reviews": 254},
}

# 判定阈值（选品四步标准，可调）
DEMAND_MIN = 3000            # 月搜索量下限
PRICE_RANGE = (20.0, 70.0)   # 售价美元区间
WEIGHT_MAX_LB = 2.0          # 重量上限（控制头程成本）
MARGIN_MIN = 0.30            # 净利率下限

# ---- 成本费率（演示值；真实版接物流/费率 API）----------------------------------
_FREIGHT_PER_LB = 15.0   # 头程
_FBA_FEE_MIN = 2.9       # FBA 配送费下限
_FBA_RATE = 0.08         # FBA 配送费率
_COMMISSION = 0.15       # 平台佣金
_AD_RATE = 0.10          # 广告预算占比
_RETURN_RATE = 0.04      # 退货损耗
_STORAGE_RATE = 0.03     # 仓储占比

# ---- 长尾关键词演示库：类目 → 长尾词行情（真实版接卖家精灵 / Helium 10 API）---------
_KEYWORDS = {
    "充电宝": [
        {"kw": "power bank", "volume": 8500, "cpc_usd": 0.62, "competition": "高"},
        {"kw": "20000mah power bank", "volume": 5400, "cpc_usd": 0.48, "competition": "中"},
        {"kw": "fast charging power bank", "volume": 3200, "cpc_usd": 0.39, "competition": "低"},
        {"kw": "slim power bank usb c", "volume": 1900, "cpc_usd": 0.31, "competition": "低"},
    ],
    "蓝牙耳机": [
        {"kw": "bluetooth earphones", "volume": 12000, "cpc_usd": 0.71, "competition": "高"},
        {"kw": "wireless earbuds anc", "volume": 6600, "cpc_usd": 0.55, "competition": "中"},
        {"kw": "open ear earbuds", "volume": 4100, "cpc_usd": 0.42, "competition": "低"},
        {"kw": "earbuds for small ears", "volume": 2300, "cpc_usd": 0.33, "competition": "低"},
    ],
    "保温杯": [
        {"kw": "insulated tumbler", "volume": 6200, "cpc_usd": 0.44, "competition": "中"},
        {"kw": "tumbler with handle and lid", "volume": 3900, "cpc_usd": 0.36, "competition": "低"},
        {"kw": "40 oz tumbler", "volume": 2800, "cpc_usd": 0.51, "competition": "中"},
        {"kw": "leak proof travel mug", "volume": 1600, "cpc_usd": 0.29, "competition": "低"},
    ],
    "机械键盘": [
        {"kw": "mechanical keyboard", "volume": 4100, "cpc_usd": 0.83, "competition": "高"},
        {"kw": "75 percent keyboard", "volume": 2900, "cpc_usd": 0.61, "competition": "中"},
        {"kw": "hot swappable keyboard", "volume": 2200, "cpc_usd": 0.47, "competition": "低"},
        {"kw": "cream mechanical keyboard", "volume": 1300, "cpc_usd": 0.35, "competition": "低"},
    ],
    "云感耳机": [
        {"kw": "comfortable earbuds", "volume": 3900, "cpc_usd": 0.40, "competition": "低"},
        {"kw": "earbuds for sleeping", "volume": 2600, "cpc_usd": 0.38, "competition": "低"},
        {"kw": "lightweight wireless earbuds", "volume": 2100, "cpc_usd": 0.45, "competition": "中"},
        {"kw": "earbuds all day comfort", "volume": 1100, "cpc_usd": 0.27, "competition": "低"},
    ],
}

# ---- 类目合规演示库：类目 → 认证 / 运输限制 / 平台规则（真实版接合规数据库 API）----
_COMPLIANCE = {
    "充电宝": {
        "required_certs": ["UN38.3", "MSDS", "CE", "FCC"],
        "shipping_note": "内置锂电池：空运需危险品申报，≤100Wh 可随身登机；海运需电池专柜",
        "platform_rules": ["上架需上传 UN38.3 摘要", "认证文件型号须与产品一致", "禁用夸大容量宣传"],
        "risk_level": "高",
    },
    "蓝牙耳机": {
        "required_certs": ["FCC", "RoHS", "BQB", "CE"],
        "shipping_note": "含锂电池，可正常空运；无需危险品申报（小容量电芯）",
        "platform_rules": ["蓝牙标志需 BQB 认证背书", "无线频段须符合目的国法规"],
        "risk_level": "中",
    },
    "保温杯": {
        "required_certs": ["FDA", "LFGB"],
        "shipping_note": "普货，任意渠道可发；注意食品接触材料标识",
        "platform_rules": ["食品接触类需在详情页标注材质", "杯盖密封性差易差评"],
        "risk_level": "低",
    },
    "机械键盘": {
        "required_certs": ["FCC", "CE", "RoHS"],
        "shipping_note": "普货；带锂电池版本（三模）按带电产品申报",
        "platform_rules": ["键位布局需标注兼容系统", "驱动/改键软件需提供下载渠道"],
        "risk_level": "低",
    },
    "云感耳机": {
        "required_certs": ["FCC", "RoHS", "CE"],
        "shipping_note": "含锂电池，可正常空运；无需危险品申报（小容量电芯）",
        "platform_rules": ["入耳类目建议标注佩戴舒适度数据来源", "无线频段须符合目的国法规"],
        "risk_level": "中",
    },
}

# ---- 头程物流费率演示：方式 → 费率/时效（真实版接货代报价 API，体积重取大者）--------
_FREIGHT_MODES = {
    "sea": {"rate_per_lb": 15.0, "eta_days": "25~35", "note": "海运整柜/拼箱：最便宜、时效最慢，适合大批补货"},
    "air": {"rate_per_lb": 28.0, "eta_days": "8~12", "note": "空运专线：适合旺季补货与轻小件首发"},
    "express": {"rate_per_lb": 45.0, "eta_days": "5~7", "note": "商业快递（DHL/UPS）：样品与紧急件"},
}

# ---- 目的国关税率演示：类目 → 从价税（真实版接 HTS 编码查询）-----------------------
_TARIFF = {"充电宝": 0.08, "蓝牙耳机": 0.05, "保温杯": 0.10, "机械键盘": 0.04, "云感耳机": 0.05}


def _find(keyword: str) -> dict | None:
    """关键词 → 命中一个演示类目（未命中返回 None）。"""
    for data in _MARKET.values():
        name = data["category"]
        if name in keyword or keyword in name:
            return dict(data)
    return None


@registry.register(
    name="check_demand",
    description=(
        "何时使用：选品第一步——判断某个品类的市场需求是否达标（搜索量、"
        "价格区间、重量）。用户问“这个类目好不好做/需求大不大”时调用。\n"
        '调用格式：{"tool": "check_demand", "parameters": {"keyword": "<品类关键词，字符串类型>"}}\n'
        "参数说明：\n"
        '- keyword：品类/商品关键词，字符串类型（string），如 "充电宝"、"蓝牙耳机"。'
    ),
    schema={"keyword": "string 品类关键词（中文）"},
    level="L0",
    cost="medium",
)
def check_demand(keyword: str) -> str:
    """需求验证：搜索量 / BSR / 售价 / 重量 四指标判定。"""
    hit = _find(str(keyword or "").strip())
    if hit is None:
        return json.dumps({"found": False, "hint": "演示库暂无该类目数据（含：充电宝/蓝牙耳机/保温杯/机械键盘/云感耳机）"},
                          ensure_ascii=False)
    p = hit
    price_ok = PRICE_RANGE[0] <= p["avg_price"] <= PRICE_RANGE[1]
    weight_ok = p["weight_lb"] < WEIGHT_MAX_LB
    demand_ok = p["search_volume"] >= DEMAND_MIN
    return json.dumps({
        "found": True,
        "category": p["category"],
        "search_volume": p["search_volume"],
        "bsr": p["bsr"],
        "avg_price_usd": p["avg_price"],
        "weight_lb": p["weight_lb"],
        "checks": {
            "搜索量≥3000": demand_ok,
            "售价$20~$70": price_ok,
            "重量<2lb": weight_ok,
        },
        "passed": demand_ok and price_ok and weight_ok,
    }, ensure_ascii=False)


@registry.register(
    name="check_competition",
    description=(
        "何时使用：选品第二步——判断该关键词首页前 10 名竞品的竞争强度"
        "（平均评分/评论数）。用户问“竞争激烈吗/好入场吗”时调用。\n"
        '调用格式：{"tool": "check_competition", "parameters": {"keyword": "<品类关键词，字符串类型>"}}\n'
        "参数说明：\n"
        '- keyword：品类/商品关键词，字符串类型（string），如 "充电宝"。'
    ),
    schema={"keyword": "string 品类关键词（中文）"},
    level="L0",
    cost="medium",
)
def check_competition(keyword: str) -> str:
    """竞争验证：首页前 10 平均评分 / 评论数 → 低竞争判定（≤4.3 且 ≤500）。"""
    hit = _find(str(keyword or "").strip())
    if hit is None:
        return json.dumps({"found": False, "hint": "演示库暂无该类目数据"}, ensure_ascii=False)
    rating, reviews = hit["compet_rating"], hit["compet_reviews"]
    low_rating = rating <= 4.3
    low_reviews = reviews <= 500
    return json.dumps({
        "found": True,
        "category": hit["category"],
        "avg_rating_top10": rating,
        "avg_reviews_top10": reviews,
        "checks": {"首页评分≤4.3": low_rating, "平均评论≤500": low_reviews},
        "passed": low_rating and low_reviews,
    }, ensure_ascii=False)


@registry.register(
    name="calc_profit",
    description=(
        "何时使用：选品第三步——按采购/头程/FBA/佣金/广告/退货/仓储全链路"
        "费率测算给定售价的净利率，判断是否≥30%。用户问“卖多少钱有利润/这单赚多少”时调用。\n"
        '调用格式：{"tool": "calc_profit", "parameters": {"category": "<品类名，字符串类型>",'
        ' "sell_price": "<售价美元，浮点数类型>"}}\n'
        "参数说明：\n"
        "- category：品类名，字符串类型（string），如“充电宝”；\n"
        "- sell_price：计划售价（美元），浮点数类型（float），如 37.5。"
    ),
    schema={"category": "string 品类名", "sell_price": "float 售价（美元）"},
    level="L0",
    cost="low",
)
def calc_profit(category: str, sell_price: float) -> str:
    """利润测算：全链路费率逐项扣减 → 净利率。"""
    hit = _find(str(category or "").strip())
    if hit is None:
        return json.dumps({"found": False, "hint": "演示库暂无该类目数据"}, ensure_ascii=False)
    try:
        price = float(sell_price)
    except (TypeError, ValueError):
        price = hit["avg_price"]
    freight = hit["weight_lb"] * _FREIGHT_PER_LB
    fba = max(_FBA_FEE_MIN, price * _FBA_RATE)
    commission = price * _COMMISSION
    ads = price * _AD_RATE
    ret = price * _RETURN_RATE
    storage = price * _STORAGE_RATE
    cost = hit["purchase"] + freight + fba + commission + ads + ret + storage
    profit = price - cost
    margin = round(profit / price, 4) if price else 0.0
    return json.dumps({
        "found": True,
        "category": hit["category"],
        "sell_price": price,
        "cost_breakdown": {
            "采购": round(hit["purchase"], 2),
            "头程": round(freight, 2),
            "FBA配送": round(fba, 2),
            "佣金": round(commission, 2),
            "广告": round(ads, 2),
            "退货损耗": round(ret, 2),
            "仓储": round(storage, 2),
        },
        "total_cost": round(cost, 2),
        "profit_per_unit": round(profit, 2),
        "margin": margin,
        "passed": margin >= MARGIN_MIN,
    }, ensure_ascii=False)


@registry.register(
    name="run_product_research",
    description=(
        "何时使用：选品四步合验——需求 + 竞争 + 利润一步出结论。用户直接问"
        "“帮我选个品/这个产品能不能做/帮我看看XX类目”时优先调用本工具（代替分步调用）。\n"
        '调用格式：{"tool": "run_product_research", "parameters": {"keyword": "<品类关键词，字符串类型>"}}\n'
        "参数说明：\n"
        "- keyword：品类/商品关键词，字符串类型（string），如“充电宝”。"
    ),
    schema={"keyword": "string 品类关键词（中文）"},
    level="L0",
    cost="medium",
)
def run_product_research(keyword: str) -> str:
    """四步选品编排：需求 + 竞争 + 利润 + 总分结论。"""
    hit = _find(str(keyword or "").strip())
    if hit is None:
        return json.dumps({"found": False, "hint": "演示库暂无该类目数据（含：充电宝/蓝牙耳机/保温杯/机械键盘/云感耳机）"},
                          ensure_ascii=False)
    demand = json.loads(check_demand(hit["category"]))
    competition = json.loads(check_competition(hit["category"]))
    profit = json.loads(calc_profit(hit["category"], hit["avg_price"]))
    score = sum([demand["passed"], competition["passed"], profit["passed"]])
    return json.dumps({
        "found": True,
        "category": hit["category"],
        "steps": {
            "需求": {"passed": demand["passed"], **demand["checks"]},
            "竞争": {"passed": competition["passed"], **competition["checks"]},
            "利润": {"passed": profit["passed"], "margin": profit["margin"],
                    "profit_per_unit": profit["profit_per_unit"]},
        },
        "score": f"{score}/3",
        "verdict": (
            "建议推进（需求/竞争/利润全部达标）" if score == 3
            else "谨慎推进（部分指标未达标，见 steps 明细）"
        ),
    }, ensure_ascii=False)


@registry.register(
    name="research_keywords",
    description=(
        "何时使用：选品辅助——查某类目的长尾关键词行情（搜索量/CPC/竞争度），"
        "给 Listing 埋词和广告选词做参考。用户问“这个类目什么词好做/关键词竞争大吗”时调用。\n"
        '调用格式：{"tool": "research_keywords", "parameters": {"keyword": "<品类关键词，字符串类型>"}}\n'
        "参数说明：\n"
        '- keyword：品类/商品关键词，字符串类型（string），如 "充电宝"。'
    ),
    schema={"keyword": "string 品类关键词（中文）"},
    level="L0",
    cost="low",
)
def research_keywords(keyword: str) -> str:
    """关键词调研：按类目返回长尾词行情，按 CPC 从低到高排序。"""
    hit = _find(str(keyword or "").strip())
    if hit is None:
        return json.dumps({"found": False,
                           "hint": "演示库暂无该类目数据（含：充电宝/蓝牙耳机/保温杯/机械键盘/云感耳机）"},
                          ensure_ascii=False)
    rows = sorted(_KEYWORDS.get(hit["category"], []), key=lambda r: r["cpc_usd"])
    return json.dumps({
        "found": True,
        "category": hit["category"],
        "keywords": rows,
        "hint": "优先选 competition=低 且 volume≥1500 的词做 Listing 埋词；CPC 结合广告预算评估。",
    }, ensure_ascii=False)


@registry.register(
    name="check_compliance",
    description=(
        "何时使用：选品/上架前的合规审查——查某类目的认证要求、运输限制与平台规则。"
        "用户问“这个品类要什么认证/能不能空运/平台有什么限制”时调用。\n"
        '调用格式：{"tool": "check_compliance", "parameters": {"keyword": "<品类关键词，字符串类型>"}}\n'
        "参数说明：\n"
        '- keyword：品类/商品关键词，字符串类型（string），如 "充电宝"。'
    ),
    schema={"keyword": "string 品类关键词（中文）"},
    level="L0",
    cost="low",
)
def check_compliance(keyword: str) -> str:
    """合规审查：认证清单 / 运输限制 / 平台规则 / 风险等级。"""
    hit = _find(str(keyword or "").strip())
    if hit is None:
        return json.dumps({"found": False,
                           "hint": "演示库暂无该类目数据（含：充电宝/蓝牙耳机/保温杯/机械键盘/云感耳机）"},
                          ensure_ascii=False)
    rule = _COMPLIANCE.get(hit["category"], {})
    return json.dumps({
        "found": True,
        "category": hit["category"],
        "required_certs": rule.get("required_certs", []),
        "shipping_note": rule.get("shipping_note", ""),
        "platform_rules": rule.get("platform_rules", []),
        "risk_level": rule.get("risk_level", "未知"),
        "hint": "风险等级为 高/中 的类目，下单前先与货代和平台确认认证与运输条件。",
    }, ensure_ascii=False)


@registry.register(
    name="calc_freight",
    description=(
        "何时使用：测算头程运费——按重量与运输方式（海运/空运/快递）估算头程成本。"
        "用户问“头程多少钱/海运空运怎么选”时调用。\n"
        '调用格式：{"tool": "calc_freight", "parameters": {"weight_lb": "<重量（磅），浮点数类型>",'
        ' "mode": "<运输方式 sea/air/express，字符串类型，可省略默认 sea>"}}\n'
        "参数说明：\n"
        "- weight_lb：货物重量（磅），浮点数类型（float），如 0.9；\n"
        '- mode：运输方式，字符串类型（string），sea=海运 / air=空运 / express=快递，'
        '可省略，默认 "sea"。'
    ),
    schema={"weight_lb": "float 重量（磅）", "mode": "string sea/air/express"},
    level="L0",
    cost="low",
)
def calc_freight(weight_lb: float, mode: str = "sea") -> str:
    """头程运费估算：重量 × 各渠道费率（含时效说明）。"""
    key = str(mode or "sea").strip().lower()
    conf = _FREIGHT_MODES.get(key)
    if conf is None:
        return json.dumps({"found": False, "hint": "运输方式支持 sea/air/express"}, ensure_ascii=False)
    try:
        w = max(0.0, float(weight_lb))
    except (TypeError, ValueError):
        return json.dumps({"found": False, "hint": f"weight_lb 需为数字，收到：{weight_lb!r}"},
                          ensure_ascii=False)
    return json.dumps({
        "found": True,
        "mode": key,
        "weight_lb": w,
        "rate_per_lb": conf["rate_per_lb"],
        "freight_usd": round(w * conf["rate_per_lb"], 2),
        "eta_days": conf["eta_days"],
        "note": conf["note"],
    }, ensure_ascii=False)


@registry.register(
    name="calc_landed_cost",
    description=(
        "何时使用：测算到岸落地成本——在利润口径上叠加目的国关税，得出更真实的到手成本。"
        "用户问“加上关税还赚多少/落地成本多少”时调用。\n"
        '调用格式：{"tool": "calc_landed_cost", "parameters": {"category": "<品类名，字符串类型>",'
        ' "sell_price": "<售价美元，浮点数类型>"}}\n'
        "参数说明：\n"
        "- category：品类名，字符串类型（string），如“保温杯”；\n"
        "- sell_price：计划售价（美元），浮点数类型（float），如 28.0。"
    ),
    schema={"category": "string 品类名", "sell_price": "float 售价（美元）"},
    level="L0",
    cost="low",
)
def calc_landed_cost(category: str, sell_price: float) -> str:
    """落地成本测算：calc_profit 口径 + 目的国从价关税（按 采购价+头程 计税）。"""
    base = json.loads(calc_profit(category, sell_price))
    if not base.get("found"):
        return json.dumps(base, ensure_ascii=False)
    price = base["sell_price"]
    tariff_rate = _TARIFF.get(base["category"], 0.0)
    customs_value = base["cost_breakdown"]["采购"] + base["cost_breakdown"]["头程"]
    tariff = round(customs_value * tariff_rate, 2)
    landed = round(base["total_cost"] + tariff, 2)
    profit = round(price - landed, 2)
    margin = round(profit / price, 4) if price else 0.0
    return json.dumps({
        "found": True,
        "category": base["category"],
        "sell_price": price,
        "customs_value": customs_value,
        "tariff_rate": tariff_rate,
        "tariff_usd": tariff,
        "landed_cost": landed,
        "profit_per_unit": profit,
        "margin": margin,
        "passed": margin >= MARGIN_MIN,
        "hint": (f"关税按货值（采购+头程 {customs_value:.2f}）× {tariff_rate:.0%} 计征；"
                 f"叠加后净利率 {margin:.1%}，"
                 + ("仍达标。" if margin >= MARGIN_MIN else "已跌破 30% 红线，建议提价或压采购成本。")),
    }, ensure_ascii=False)


__all__ = ["check_demand", "check_competition", "calc_profit", "run_product_research",
           "research_keywords", "check_compliance", "calc_freight", "calc_landed_cost",
           "registry", "DEMAND_MIN", "PRICE_RANGE", "WEIGHT_MAX_LB", "MARGIN_MIN"]