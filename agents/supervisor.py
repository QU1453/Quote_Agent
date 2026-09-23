# -*- coding: utf-8 -*-
"""主控智能体（Supervisor）：对外统一入口，对内调度专职智能体。

当前注册：
- customer_service：客服智能体（订单 / 物流 / 售后）；
- presales：售前导购智能体（商品咨询 / 推荐）。

多智能体扩展方法：
1. 在 agents/ 下新增专职智能体文件（参考 presales.py，继承 agents/base.py）；
2. 在下方 specialists 注册表加一行；
3. 在 _route() 中补充该智能体的路由规则（现为规则路由，后续可升级 LLM 路由）。
"""
from __future__ import annotations

import json
import re

from config import MODEL_ID, SUMMARIZER_MODEL, L2_SUMMARY_INTERVAL
from memory import agent_session_id, get_short_term
from memory.access import AuditLogger

from .customer_service import CustomerServiceAgent, classic_reply
from .listing_agent import ListingAgent, classic_listing_reply
from .presales import PreSalesAgent
from .research_agent import ResearchAgent, classic_research_reply
from .summarizer import SummarizerAgent

# 售前导购的路由关键词（中日双语）：命中即分流给 presales
# 注意：订单 / 物流 / 售后关键词要放在客服路由里优先判断（见 _route），
# 避免"订单多少钱""商品什么时候发货"这类混合问句被误分给导购
_PRE_SALES_PATTERN = re.compile(
    r"推荐|想买|哪款|什么好|好物|比价|耳机|键盘|保温杯|充电宝"
    r"|おすすめ|推薦|薦め|どれ|いくら|価格|値段|商品案内|イヤホン|ヘッドホン|キーボード|マグボトル|水筒|バッテリー"
)
# 客服关键词优先级更高（中日双语）：含订单 / 物流 / 售后语义时一律先走客服
_CS_PATTERN = re.compile(
    r"物流|快递|到哪|发货|订单|单号|签收|退|换|退款|售后|质量|坏了"
    r"|配送|荷物|注文|追跡|届く|返品|交換|返金|キャンセル|不良"
)
# 选品智能体路由：选品/供应商/采购语义（卖家工作台视角）
_RESEARCH_PATTERN = re.compile(
    r"选品|选个|选什么|帮我选|热销|爆款|搜索量|利润|竞争|供应商|1688|阿里"
    r"|采购|起订量|moq|货源|进货|找货源|需求"
)
# Listing 智能体路由：上架语义（优先级高于选品，见 _route）
_LISTING_PATTERN = re.compile(
    r"listing|上架|标题|五点|描述|图片|主图|定价|售价|fba|fbm"
    r"|关键词布局|search\s*terms", re.I
)


class Supervisor:
    """多智能体协作主控。available/reason/model 透传主智能体（客服）状态。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = MODEL_ID,  # 默认值统一来自 config.py（glm-5.3-flash），不在各处硬编码
    ):
        # 专职智能体注册表：新增智能体在此登记
        self.customer_service = CustomerServiceAgent(api_key=api_key, base_url=base_url, model=model)
        self.presales = PreSalesAgent(api_key=api_key, base_url=base_url, model=model)
        self.research = ResearchAgent(api_key=api_key, base_url=base_url, model=model)
        self.listing = ListingAgent(api_key=api_key, base_url=base_url, model=model)
        self.specialists: dict[str, CustomerServiceAgent | PreSalesAgent] = {
            "customer_service": self.customer_service,
            "presales": self.presales,
            "research": self.research,
            "listing": self.listing,
        }
        # 专职总结智能体（权限 L1）：谈话结束 → 一级总结；每 M 个 → 二级总结
        self.summarizer = SummarizerAgent(api_key=api_key, base_url=base_url, model=SUMMARIZER_MODEL)

    @property
    def available(self) -> bool:
        """任一专职智能体可用（LLM 模式）即视为可用。"""
        return any(agent.available for agent in self.specialists.values())

    @property
    def reason(self) -> str | None:
        return self.customer_service.reason

    @property
    def model(self) -> str:
        return self.customer_service.model

    def answer(self, question: str, session_id: str = "default",
               extra_system: list[str] | None = None,
               request_id: str = "") -> dict | None:
        """按意图路由到专职智能体作答；多轮记忆由 memory/ 的 checkpointer 托管。

        被选智能体不可用 / 失败时，退而尝试客服智能体；仍失败返回 None，
        由调用方（orchestrator / server）走本地规则兜底。
        extra_system：编排层注入的五模块记忆上下文块（见 core/perception/context.py）。
        request_id：本轮运行态标识（思考流展示 + 打断检查点）。
        """
        name = self._route(question)
        # 登记该谈话最近一轮由谁处理（谈话注册表，供结束总结 / 窗口原文取回）
        try:
            get_short_term().record_turn(session_id, name)
        except Exception:  # noqa: BLE001 - 注册表故障不影响本轮回复
            pass
        # 会话 ID 按智能体隔离（presales:xxx / customer_service:xxx，经 agent_session_id
        # 唯一出口生成），避免多个智能体共享同一 thread_id 导致记忆互相串台
        result = self.specialists[name].answer(
            question, session_id=agent_session_id(name, session_id),
            extra_system=extra_system, request_id=request_id)
        if result is not None:
            result["route"] = name
        if result is None and name != "customer_service":
            # 主选智能体不可用（如未配 Key），退回客服智能体再试一次
            result = self.customer_service.answer(
                question, session_id=agent_session_id("customer_service", session_id),
                extra_system=extra_system, request_id=request_id)
            if result is not None:
                result["route"] = "customer_service"
        return result

    def end_conversation(self, session_id: str, user_id: str = "") -> dict:
        """结束谈话：取回原文 → 一级总结（LLM/规则兜底）→ 落注册表 → 触发二级总结。

        返回 {ok, summarized, summary, second_stage}；second_stage=True 表示
        本次结束触发了一批二级总结（长期记忆已更新）。
        """
        stm = get_short_term()
        rec = stm.registry.get(session_id)
        if rec is None:
            return {"ok": False, "reason": "谈话不存在"}
        # 原文取回：按注册表记录的 agent_name 定位 checkpoint 线程
        raw_text = ""
        if rec.get("agent_name"):
            try:
                sid = agent_session_id(rec["agent_name"], session_id)
                msgs = stm.get_messages(sid)
                lines = []
                for m in msgs:
                    content = str(getattr(m, "content", "") or "").strip()
                    if content:
                        role = {"human": "用户", "ai": "客服", "system": "系统"}.get(
                            getattr(m, "type", "human"), "用户")
                        lines.append(f"{role}: {content}")
                raw_text = "\n".join(lines)
            except Exception:  # noqa: BLE001 - 线程不存在时 raw 为空，仅按空对话总结
                raw_text = ""
        summary = self.summarizer.summarize(raw_text, session_id=session_id, user_id=user_id)
        stm.registry.set_summary(session_id, json.dumps(summary, ensure_ascii=False))

        # 二级总结触发游标：未合并的已结束谈话 ≥ M 时执行（P3 实现）
        second_stage = False
        try:
            pending = stm.registry.pending_rollup(L2_SUMMARY_INTERVAL)
            if len(pending) >= L2_SUMMARY_INTERVAL:
                second_stage = self._run_second_stage(user_id, pending)
        except Exception:  # noqa: BLE001 - 二级总结失败不阻断谈话结束
            second_stage = False
        AuditLogger.log(
            actor=self.summarizer.caller.name, actor_level=self.summarizer.caller.level,
            action="summarize", module="short_term", target_id=session_id,
            session_id=session_id, result="ok",
        )
        return {"ok": True, "summarized": True, "summary": summary,
                "second_stage": second_stage}

    def _run_second_stage(self, user_id: str, pending: list[dict]) -> bool:
        """二级总结：把 M 个一级总结滚动合并 → 长期记忆范式条目（caller L1）。

        长期记忆不可用（hnswlib 缺失）时返回 False 且不标记 rolled_up，
        待依赖就绪后同一批谈话会被再次拾起，不丢数据。
        """
        from memory import get_memory
        from memory.short_term.registry import parse_summary

        mm = get_memory()
        if mm is None:
            return False
        entries: list[dict] = []
        for c in pending:
            summary = parse_summary(c.get("summary_json", "")) or {}
            summary["conversation_id"] = c["session_id"]
            entries.append(summary)
        previous = mm.long_term.get_long_term_entries(user_id, limit=10)
        merged = self.summarizer.second_stage(entries, previous)
        if not merged:
            return False
        for it in merged:
            mm.long_term.save_long_term_entry(
                user_id=user_id or "default",
                entry_type=it["entry_type"],
                content=it["content"],
                confidence=it.get("confidence", 0.5),
                tags=it.get("tags") or [],
                source_conversations=it.get("source_conversations") or [],
                caller=self.summarizer.caller,
            )
        stm = get_short_term()
        stm.registry.mark_rolled_up([c["session_id"] for c in pending])
        AuditLogger.log(
            actor=self.summarizer.caller.name, actor_level=self.summarizer.caller.level,
            action="second_stage", module="long_term",
            target_id=",".join(c["session_id"] for c in pending),
            session_id="", result="ok",
        )
        return True

    @staticmethod
    def _route(question: str) -> str:
        """规则意图路由（优先级）：listing > research > customer_service > presales。

        客服关键词在导购前判断，防止"订单多少钱"误分导购；
        选品/上架语义领先于旧客服/售前（卖家工作台转型后为最高频入口）；
        后续可替换为 LLM 路由（Roadmap），对外接口不变。
        """
        s = question.lower()
        if _LISTING_PATTERN.search(s):
            return "listing"
        if _RESEARCH_PATTERN.search(s):
            return "research"
        if _CS_PATTERN.search(s):
            return "customer_service"
        if _PRE_SALES_PATTERN.search(s):
            return "presales"
        return "customer_service"
