# -*- coding: utf-8 -*-
"""Quote Agent 工具包：选品/上架（供应商/Listing）+ 客服（商品库/订单物流/售后/推荐）。

新增工具：在包内加一个模块文件（可用 P0 双层注册表 register），并在下方 re-export。
"""
from .after_sales import classify_after_sale, draft_reply, handle_return, register_return
from .catalog import PRODUCTS
from .order import ORDERS, lookup_order, query_order_info, track_logistics
from .recommend import recommend_for, recommend_products
from .research import (
    calc_freight,
    calc_landed_cost,
    calc_profit,
    check_competition,
    check_compliance,
    check_demand,
    research_keywords,
    run_product_research,
)
from .supplier import check_supplier_risk, compare_supplier, search_supplier
from .listing import (
    audit_listing,
    check_images,
    draft_listing,
    price_strategy,
    recommend_fulfillment,
)

__all__ = [
    "PRODUCTS",
    "ORDERS",
    "lookup_order",
    "query_order_info",
    "track_logistics",
    "register_return",
    "handle_return",
    "classify_after_sale",
    "draft_reply",
    "recommend_for",
    "recommend_products",
    "check_demand",
    "check_competition",
    "check_compliance",
    "calc_profit",
    "calc_freight",
    "calc_landed_cost",
    "research_keywords",
    "run_product_research",
    "search_supplier",
    "compare_supplier",
    "check_supplier_risk",
    "draft_listing",
    "check_images",
    "audit_listing",
    "price_strategy",
    "recommend_fulfillment",
]
