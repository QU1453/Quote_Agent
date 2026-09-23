# -*- coding: utf-8 -*-
"""供应商寻找工具（tools.supplier）：供应商搜索 + 供货条件对比。

演示数据版本：内置深圳/东莞/义乌等模拟供应商（MOQ/单价/交期），
真实版替换为 1688 / 阿里国际站开放接口（接入槽位见 SUPPLIER_SOURCE）。
所有工具经 P0 双层注册表登记：模块局部表 tools.supplier + 全局表同步。
"""
from __future__ import annotations

import json

from core.dispatch.registry import LocalRegistry

# ---- 局部注册表（owner=tools.supplier）-------------------------------------------
registry = LocalRegistry("tools.supplier")

# 真实供应商数据源接入槽位（演示版：内置模拟数据；真实版接 1688/阿里开放 API）
SUPPLIER_SOURCE = "demo"  # demo | alibaba1688


# ---- 演示供应商库：关键词 → 3 家候选（首批 50-100 件试水口径）---------------------
_SUPPLIERS = {
    "charger": [
        {"name": "深圳锂能电子", "city": "深圳", "moq": 50, "unit_price": 38.0,
         "lead_days": 7, "cert": "CE/FCC/PSE", "rating": 4.8},
        {"name": "东莞宏锐能源", "city": "东莞", "moq": 100, "unit_price": 33.5,
         "lead_days": 10, "cert": "CE/FCC", "rating": 4.6},
        {"name": "义乌小商品城·联航", "city": "义乌", "moq": 30, "unit_price": 42.0,
         "lead_days": 5, "cert": "CE", "rating": 4.5},
    ],
    "earphone": [
        {"name": "深圳声学智造", "city": "深圳", "moq": 60, "unit_price": 45.0,
         "lead_days": 9, "cert": "CE/FCC/TELEC", "rating": 4.9},
        {"name": "东莞音频电子", "city": "东莞", "moq": 100, "unit_price": 39.0,
         "lead_days": 12, "cert": "CE/FCC", "rating": 4.5},
        {"name": "义乌数码港·鑫声", "city": "义乌", "moq": 40, "unit_price": 48.0,
         "lead_days": 6, "cert": "CE", "rating": 4.4},
    ],
    "tumbler": [
        {"name": "永康保温科技", "city": "永康", "moq": 100, "unit_price": 21.0,
         "lead_days": 8, "cert": "FDA/LFGB", "rating": 4.7},
        {"name": "义乌杯业工贸", "city": "义乌", "moq": 50, "unit_price": 24.5,
         "lead_days": 5, "cert": "FDA", "rating": 4.5},
        {"name": "潮州瓷器集团·杯具部", "city": "潮州", "moq": 200, "unit_price": 19.5,
         "lead_days": 15, "cert": "FDA/LFGB", "rating": 4.6},
    ],
}

# 键盘 / 云感耳机 等其他类目未命中时的通用候选（演示兜底，展示对比表结构）
_GENERIC_SUPPLIERS = _SUPPLIERS["tumbler"]

# 从 category 中文名 → 供应商库 key（保持与 tools/research.py 演示类目对齐）
_CATEGORY_KEYS = {"充电宝": "charger", "蓝牙耳机": "earphone", "保温杯": "tumbler"}


def _items(keyword: str) -> tuple[bool, list[dict]]:
    for name, key in _CATEGORY_KEYS.items():
        if name in keyword or keyword in name:
            return True, _SUPPLIERS.get(key, _GENERIC_SUPPLIERS)
    return False, _GENERIC_SUPPLIERS


@registry.register(
    name="search_supplier",
    description=(
        "何时使用：用户要找货源/供应商/工厂（1688/阿里上面进货），"
        "询问“哪里进货/有哪些供货商”时调用，返回候选供应商列表。\n"
        '调用格式：{"tool": "search_supplier", "parameters": {"product_keyword": "<商品关键词，字符串类型>"}}\n'
        "参数说明：\n"
        '- product_keyword：要找货源的商品关键词，字符串类型（string），如 "充电宝"。'
    ),
    schema={"product_keyword": "string 商品关键词（中文）"},
    level="L0",
    cost="medium",
)
def search_supplier(product_keyword: str) -> str:
    """供应商搜索：返回候选供应商（名称/城市/MOQ/单价/交期/认证/评分）。"""
    exact, items = _items(str(product_keyword or "").strip())
    return json.dumps({
        "found": True,
        "exact_match": exact,
        "source": SUPPLIER_SOURCE,
        "suppliers": items,
        "hint": "首批建议按 50-100 件试水口径询价；正式下单前务必索要样品与认证证书。",
    }, ensure_ascii=False)


@registry.register(
    name="compare_supplier",
    description=(
        "何时使用：需要对比多家供应商的供货条件（MOQ/单价/交期）做决策时调用，"
        "用户问“这俩供应商哪个划算/帮我对比一下货源”时优先调用本工具。\n"
        '调用格式：{"tool": "compare_supplier", "parameters": {"product_keyword": "<商品关键词，字符串类型>"}}\n'
        "参数说明：\n"
        '- product_keyword：商品关键词，字符串类型（string），如 "蓝牙耳机"。'
    ),
    schema={"product_keyword": "string 商品关键词（中文）"},
    level="L0",
    cost="medium",
)
def compare_supplier(product_keyword: str) -> str:
    """供应商对比：MOQ / 单价 / 交期 / 综合推荐（按评分+价格加权排序）。"""
    _, items = _items(str(product_keyword or "").strip())
    rows = [
        {
            "name": s["name"], "city": s["city"], "moq": s["moq"],
            "unit_price": s["unit_price"], "lead_days": s["lead_days"],
            "cert": s["cert"], "rating": s["rating"],
            "score": round(s["rating"] * 10 - s["unit_price"] * 0.05, 2),
        }
        for s in items
    ]
    rows.sort(key=lambda r: -r["score"])
    best = rows[0]
    return json.dumps({
        "found": True,
        "comparison": rows,
        "recommend": best["name"],
        "reason": f"评分 {best['rating']} 且综合分最高；交期 {best['lead_days']} 天、MOQ {best['moq']} 件。",
    }, ensure_ascii=False)


# ---- 供应商工商风险演示数据：真实版接企查查/天眼查/裁判文书网 API --------------------
_RISK_NOTES = {
    "深圳锂能电子": {"legal": "正常", "lawsuits": 0, "penalties": 0, "note": "近 12 个月无涉诉/处罚记录"},
    "东莞宏锐能源": {"legal": "正常", "lawsuits": 1, "penalties": 0, "note": "1 起买卖合同纠纷（已和解）"},
    "义乌小商品城·联航": {"legal": "正常", "lawsuits": 0, "penalties": 1, "note": "1 次消防整改（已完成）"},
    "深圳声学智造": {"legal": "正常", "lawsuits": 0, "penalties": 0, "note": "国家级高新企业，经营稳定"},
    "东莞音频电子": {"legal": "正常", "lawsuits": 2, "penalties": 0, "note": "2 起劳动争议（已结案）"},
    "义乌数码港·鑫声": {"legal": "异常", "lawsuits": 0, "penalties": 0, "note": "注册地址异常（列入经营异常名录）"},
    "永康保温科技": {"legal": "正常", "lawsuits": 0, "penalties": 0, "note": "近 12 个月无涉诉/处罚记录"},
    "义乌杯业工贸": {"legal": "正常", "lawsuits": 1, "penalties": 0, "note": "1 起质量纠纷（调解结案）"},
    "潮州瓷器集团·杯具部": {"legal": "正常", "lawsuits": 0, "penalties": 0, "note": "集团子公司，资信良好"},
}


@registry.register(
    name="check_supplier_risk",
    description=(
        "何时使用：下单前的供应商风险背调——经营状态/涉诉/行政处罚/资质评级一次查清。"
        "用户问“这家供应商靠谱吗/有没有风险/要不要换一家”时调用。\n"
        '调用格式：{"tool": "check_supplier_risk", "parameters": {"supplier_name": "<供应商名称，字符串类型>"}}\n'
        "参数说明：\n"
        "- supplier_name：供应商名称（支持部分匹配），字符串类型（string），如\"深圳锂能电子\"；"
        "也可先 search_supplier 拿到候选名单再逐家核查。"
    ),
    schema={"supplier_name": "string 供应商名称"},
    level="L0",
    cost="low",
)
def check_supplier_risk(supplier_name: str) -> str:
    """供应商风险背调：经营状态 / 涉诉 / 处罚 / 评级建议。"""
    name = str(supplier_name or "").strip()
    hit = None
    for items in _SUPPLIERS.values():
        for s in items:
            if s["name"] in name or name in s["name"]:
                hit = s
                break
        if hit:
            break
    if hit is None:
        return json.dumps({"found": False,
                           "hint": "演示库无此供应商；可先用 search_supplier 查询候选名单再核查"},
                          ensure_ascii=False)
    ext = _RISK_NOTES.get(hit["name"],
                          {"legal": "未知", "lawsuits": 0, "penalties": 0, "note": "演示库暂无工商风险数据"})
    if ext["legal"] != "正常" or hit["rating"] < 4.5 or ext["lawsuits"] >= 2:
        level, advice = "高", "建议换备选供应商，或实地验厂后再谈"
    elif ext["lawsuits"] >= 1 or ext["penalties"] >= 1 or hit["rating"] < 4.7:
        level, advice = "中", "可合作，但首单建议小额试单并签质量协议"
    else:
        level, advice = "低", "可正常推进；仍需索要样品与认证原件"
    return json.dumps({
        "found": True,
        "supplier": hit["name"],
        "city": hit["city"],
        "rating": hit["rating"],
        "cert": hit["cert"],
        "legal_status": ext["legal"],
        "lawsuits": ext["lawsuits"],
        "penalties": ext["penalties"],
        "risk_level": level,
        "note": ext["note"],
        "advice": advice,
        "source": SUPPLIER_SOURCE,
    }, ensure_ascii=False)


__all__ = ["search_supplier", "compare_supplier", "check_supplier_risk",
           "registry", "SUPPLIER_SOURCE"]