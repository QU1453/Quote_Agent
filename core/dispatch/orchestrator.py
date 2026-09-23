# -*- coding: utf-8 -*-
"""调度控制-编排器（Orchestrator）：Server 的 /api/ask 统一入口。

流程（一次完整回答 = 认知层流水线 + 约束层护栏）：
  constraint.pre_check（规则红线 → 框架预算 → 循环守卫；命中即否决）
  → perception.input（清洗 + 意图初判）
  → perception.context（五模块记忆 + 约束层提示 → 上下文块注入）
  → supervisor.answer（规则路由 → 专职智能体 ReAct；熔断会话直接跳过）
  → 失败时按路由落本地规则兜底（research/listing 各自 classic_reply）
  → constraint.post_check（输出脱敏 + 会话记账）
  → output.validator / output.formatter（清洗 + {reply, intent, data, route}）

supervisor 仍可直接被调用（agents 层独立可用），本编排器是认知层整合入口。
"""
from __future__ import annotations

import time

from memory import get_memory
from memory.access import MemoryCaller
from agents import classic_reply
from agents.research_agent import classic_research_reply
from agents.listing_agent import classic_listing_reply
from agents.supervisor import Supervisor
from core.constraint import get_constraint_layer, set_current_session
from core.output.formatter import wrap
from core.perception.context import assemble_context
from core.perception.input import clean_input, intent_score
from core.telemetry import (
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_INTERRUPTED,
    get_live_bus,
    get_recorder,
    set_current_request,
    set_current_trace,
)

__all__ = ["Orchestrator"]

# 路由 → 本地兜底函数（无 Key / LLM 失败时保证演示链路完整）
_FALLBACKS = {
    "research": classic_research_reply,
    "listing": classic_listing_reply,
    "customer_service": classic_reply,
    "presales": classic_reply,
}

# 兜底模式：结果卡 data.type → 实际用到的工具（推断记录，供调试面板展示）
_FALLBACK_TOOL_MAP = {
    "research_report": ["run_product_research"],
    "supplier_compare": ["search_supplier", "compare_supplier"],
    "listing_draft": ["draft_listing"],
    "pricing": ["suggest_price"],
    "fulfillment": ["suggest_fulfillment"],
    "logistics": ["track_logistics"],
    "order": ["query_order_info"],
    "recommend": ["recommend_products"],
}


class Orchestrator:
    """认知层编排入口：问答流水线 + 上下文组装 + 兜底闭环。"""

    def __init__(self, supervisor: Supervisor | None = None):
        self.supervisor = supervisor

    def answer(self, question: str, session_id: str = "default",
               user_id: str = "default", request_id: str = "") -> dict:
        """完整问答流水线；返回 {reply, intent, data, route}（永不抛错）。

        request_id：本轮运行态标识（前端生成）。给了就在运行态总线上开一条记录，
        用于「思考过程小字」实时展示与「打断」检查点；留空 = 全部旁路，
        行为与改造前完全一致（零开销）。
        """
        q = clean_input(question)
        if not q:
            return wrap({"reply": ""}, route="")

        # 遥测：一轮问答 = 一条 trace（含各阶段事件；fail-open 不影响主链路）
        rec = get_recorder()
        live = get_live_bus()
        t0 = time.perf_counter()
        trace_id = rec.start_trace(session_id, user_id, q)
        set_current_trace(trace_id)  # registry 等旁路埋点据此归并到本 trace
        set_current_request(request_id)  # 工具包装器据此判断"本轮已打断"→ 短路不执行
        live.begin(request_id, session_id=session_id)
        live.add(request_id, "stage", "input")

        mm = None
        route = ""
        # 0) 约束层·输入前置：规则红线 → 框架预算 → 循环守卫（命中即否决本轮）
        constraint = get_constraint_layer()
        verdict = constraint.pre_check(q, session_id)
        if not verdict.passed:
            # 被否决也要记账（频率/轮次守卫的语义是“请求发生”而非“请求成功”）；
            # fallback=False：否决不是 LLM 失败，不计入兜底熔断
            try:
                constraint.post_check(verdict.reply, session_id, q, fallback=False)
            except Exception:  # noqa: BLE001
                pass
            rec.add_event(trace_id, kind="constraint", name=verdict.kind,
                          status="deny", detail=verdict.reply)
            live.add(request_id, "stage", "constraint", detail=verdict.kind, status="deny")
            rec.close_trace(trace_id, route="constraint", intent="constraint_denied",
                            reply_snippet=verdict.reply, llm_mode="constraint",
                            status="ok", latency_ms=int((time.perf_counter() - t0) * 1000))
            set_current_trace(0)  # 结束：旁路埋点不再归并到本 trace
            set_current_session("")  # 结束：工具循环守卫的会话键归并停止
            set_current_request("")  # 结束：工具不再按本轮打断标记短路
            live.finish(request_id, STATUS_DONE, detail="constraint")
            return wrap({"reply": verdict.reply, "intent": "constraint_denied",
                         "data": {"constraint": verdict.kind}}, route="constraint")

        # 会话线程变量：agent 直连工具包装器据此把循环守卫计数归并到本会话
        set_current_session(session_id)

        # 1) 感知：意图初判（与 supervisor 规则路由同口径，供上下文组装与统计）
        scores = intent_score(q)
        route = (self.supervisor or Supervisor)._route(q)  # 规则路由唯一权威
        rec.add_event(trace_id, kind="stage", name="route", detail=route)
        live.add(request_id, "stage", "route", detail=route)
        # 登记该谈话最近一轮由谁处理（LLM 路径 supervisor 内部会再记一次，幂等）
        try:
            from memory import get_short_term

            get_short_term().record_turn(session_id, route, user_id=user_id)
        except Exception:  # noqa: BLE001 - 注册表故障不影响本轮回复
            pass

        # 2) 上下文组装：六模块记忆 + 约束层提示 → 系统上下文块（任一模块故障只降级该块）
        extra: list[str] = [constraint.extra_system()]  # 约束层软约束块（红线行为提示）
        context_tokens = 0
        try:
            mm = get_memory()
            if mm is not None:
                ac = assemble_context(mm, user_id=user_id, session_id=session_id,
                                      agent_name=f"{route}_agent", question=q)
                for key in ("rules", "resume", "facts", "kb_index", "window_topics", "hot_skills"):
                    if ac["blocks"].get(key):
                        extra.append(ac["blocks"][key])
                context_tokens = int(ac.get("estimated_tokens") or 0)
                rec.add_event(trace_id, kind="stage", name="context",
                              detail=f"{len(ac.get('nonempty', []))}块/{context_tokens}tok")
                live.add(request_id, "stage", "context",
                         detail=f"{len(ac.get('nonempty', []))}块 · {context_tokens}tok")
                # 续跑提示已注入 → 前端提示"检测到上次未完成"
                if ac["blocks"].get("resume"):
                    live.add(request_id, "note", "resume_found", detail="")
        except Exception:  # noqa: BLE001 - 上下文组装失败不影响本轮问答
            pass

        # 3) 思考：主控调度 → 专职智能体 ReAct（失败自动退客服再试）
        #    循环守卫已熔断的会话直接跳过 LLM（连续失败止损）
        result = None
        used_fallback = False
        llm_error = ""
        tripped = constraint.llm_blocked(session_id)
        try:
            if not tripped and self.supervisor is not None and self.supervisor.available:
                t_llm = time.perf_counter()
                # 会话空（无 Key 模式）由 supervisor 内部回落；回答挂 route 标签
                result = self.supervisor.answer(q, session_id, extra_system=extra,
                                                request_id=request_id)
                rec.add_event(trace_id, kind="llm", name=route,
                              duration_ms=int((time.perf_counter() - t_llm) * 1000),
                              status="ok")
        except Exception as exc:  # noqa: BLE001 - 网络/额度异常 → 本地兜底
            result = None
            llm_error = f"{exc.__class__.__name__}: {exc}"
            rec.add_event(trace_id, kind="llm", name=route, status="error",
                          detail=llm_error[:300])
        # 遥测闭环①：LLM 模式工具调用落库（base._telemetry 的 tool_calls 逐条入 events）
        if result and isinstance(result.get("_telemetry"), dict):
            tel = result.pop("_telemetry")  # 消费即剥离，对外契约不变
            try:
                if tel.get("input_tokens") or tel.get("output_tokens"):
                    rec.set_tokens(trace_id, tel["input_tokens"], tel["output_tokens"],
                                   source="api")
                    # 预算熔断记账：真实 usage 累加进会话预算（token + 单价折算金额）
                    constraint.record_llm_usage(
                        session_id, int(tel.get("input_tokens") or 0),
                        int(tel.get("output_tokens") or 0))
                for tc in tel.get("tool_calls") or []:
                    rec.add_event(trace_id, kind="tool", name=tc.get("name", ""),
                                  params=tc.get("args"))
                if tel.get("model"):
                    rec.close_trace(trace_id, agent=tel["model"])
            except Exception:  # noqa: BLE001
                pass

        # 3.4) 打断处理：本轮被用户打断 → 落中断日志（记忆层第六模块）并直接返回，
        #      不走兜底（打断不等于失败，套一段规则话术反而误导用户）
        interrupt = result.pop("_interrupt", None) if isinstance(result, dict) else None
        if isinstance(interrupt, dict):
            entry_id = self._record_interrupt(session_id, user_id, request_id, route, q, interrupt)
            reply = str(interrupt.get("partial_reply") or "")
            try:
                reply = constraint.post_check(reply, session_id, q, fallback=False)
            except Exception:  # noqa: BLE001
                pass
            rec.add_event(trace_id, kind="stage", name="interrupt", detail=(
                f"stage={interrupt.get('stage')} step={interrupt.get('step_index')}"
                f" pending={interrupt.get('pending_tool') or '-'}"))
            live.add(request_id, "note", "interrupt", detail=(
                f"{interrupt.get('stage')}|{interrupt.get('pending_tool') or ''}"),
                status="deny")
            rec.close_trace(trace_id, route=route, intent="interrupted",
                            reply_snippet=reply, llm_mode="interrupted", status="ok",
                            latency_ms=int((time.perf_counter() - t0) * 1000),
                            context_tokens=context_tokens)
            set_current_trace(0)
            set_current_session("")
            set_current_request("")
            live.finish(request_id, STATUS_INTERRUPTED, detail="interrupt")
            return wrap({
                "reply": reply,
                "intent": "interrupted",
                "data": {
                    "type": "interrupted",
                    "interrupted": True,
                    "interrupt_id": entry_id,
                    "stage": interrupt.get("stage") or "",
                    "step_index": int(interrupt.get("step_index") or 0),
                    "completed_tools": interrupt.get("completed_tools") or [],
                    "pending_tool": interrupt.get("pending_tool") or "",
                    "tool_interrupted_by_user": bool(interrupt.get("tool_interrupted_by_user")),
                },
            }, route=route)

        # 3.5) 打断信号晚到（本轮其实已跑完）：只留一条提示，不落可续跑台账
        #      （活已经干完了，不该让下一轮再去"接着做"）
        if live.is_cancelled(request_id):
            rec.add_event(trace_id, kind="stage", name="interrupt_late", status="ok")
            live.add(request_id, "note", "interrupt_late", detail="")

        if not result or not (result.get("reply") or "").strip():
            result = self._fallback(q, route)  # 本地规则兜底
            used_fallback = True
            if tripped:
                # 熔断会话：兜底回复前附熔断说明，提示用户排查
                result = dict(result)
                result["reply"] = f"{constraint.trip_reply()}\n{result.get('reply', '')}"
            # 兜底模式下也把本轮对话写入短期记忆线程，保证「结束谈话」总结有原文可依
            try:
                self._record_fallback_turn(route, session_id, q, result.get("reply", ""))
            except Exception:  # noqa: BLE001
                pass
            # 遥测闭环②：兜底工具使用推断（data.type → 工具名，与 LLM 路数合并进 events）
            rec.add_event(trace_id, kind="stage", name="fallback", detail=route)
            live.add(request_id, "stage", "fallback", detail=route)
            data = result.get("data")
            data_type = (data or {}).get("type") if isinstance(data, dict) else None
            for tool_name in _FALLBACK_TOOL_MAP.get(data_type or "", []):
                rec.add_event(trace_id, kind="tool", name=tool_name,
                              params={"keyword": (data or {}).get("keyword")
                                      or (data or {}).get("category"),
                                      "inferred": True})
        # 3.6) 约束层·输出复查：脱敏（密钥/内部信息）+ 会话记账（轮次/循环信号）
        try:
            if result and result.get("reply"):
                result["reply"] = constraint.post_check(
                    result["reply"], session_id, q, fallback=used_fallback
                )
        except Exception:  # noqa: BLE001 - 约束层故障不影响已生成的回复
            pass

        # 3.7) 本轮正常完成 → 该会话的待续跑记录收尾（断点已被接手，不必再注入）
        if not used_fallback and mm is not None:
            try:
                n = mm.resolve_interrupt(
                    session_id, note=f"已由本轮回答接手（{route}）",
                    caller=MemoryCaller("orchestrator", "L0", session_id))
                if n:
                    live.add(request_id, "note", "resume_closed", detail=str(n))
            except Exception:  # noqa: BLE001 - 收尾失败只影响下一轮是否重复注入
                pass

        # 4) 输出：清洗 + 标准契约
        rec.close_trace(
            trace_id, route=route, intent=(result or {}).get("intent") or "none",
            reply_snippet=(result or {}).get("reply") or "",
            llm_mode="fallback" if used_fallback else "llm",
            status="error" if llm_error else "ok",
            latency_ms=int((time.perf_counter() - t0) * 1000),
            error=llm_error, context_tokens=context_tokens,
        )
        live.add(request_id, "stage", "output")
        set_current_trace(0)  # 结束：旁路埋点不再归并到本 trace
        set_current_session("")  # 结束：工具循环守卫的会话键归并停止
        set_current_request("")  # 结束：工具不再按本轮打断标记短路
        live.finish(request_id, STATUS_ERROR if llm_error else STATUS_DONE,
                    detail=llm_error[:120])
        return wrap(result, route=route if result is None or not result.get("route") else result.get("route"))

    @staticmethod
    def _record_interrupt(session_id: str, user_id: str, request_id: str, route: str,
                          question: str, info: dict) -> int:
        """把打断现场写进中断日志（记忆层第六模块），返回记录 id（失败返回 0）。

        caller 用 L0（本人会话）——中断日志是会话级交接班记录，与短期记忆同档。
        """
        try:
            mm = get_memory()
            if mm is None:
                return 0
            return mm.log_interrupt(
                session_id,
                user_id=user_id,
                request_id=request_id,
                route=route,
                stage=str(info.get("stage") or "thinking"),
                step_index=int(info.get("step_index") or 0),
                completed_steps=int(info.get("step_index") or 0),
                completed_tools=info.get("completed_tools") or [],
                pending_tool=str(info.get("pending_tool") or ""),
                tool_interrupted_by_user=bool(info.get("tool_interrupted_by_user")),
                partial_reply=str(info.get("partial_reply") or ""),
                question=question,
                interrupted_by=str(info.get("interrupted_by") or "user"),
                reason=str(info.get("reason") or ""),
                caller=MemoryCaller("orchestrator", "L0", session_id),
            )
        except Exception:  # noqa: BLE001 - 落库失败不影响本轮返回
            return 0

    @staticmethod
    def _record_fallback_turn(route: str, session_id: str, question: str, reply: str) -> None:
        """把兜底模式的问答写入该智能体的 checkpoint 线程（与 LLM 模式同口径）。"""
        from memory import agent_session_id, get_short_term

        stm = get_short_term()
        sid = agent_session_id(route, session_id)
        stm.add_message(sid, "user", question)
        if reply:
            stm.add_message(sid, "assistant", reply)

    @staticmethod
    def _fallback(q: str, route: str) -> dict:
        """按路由取对应智能体的本地规则兜底（research/listing 有专属 classic_reply）。"""
        fn = _FALLBACKS.get(route) or classic_reply
        try:
            out = fn(q)
        except Exception:  # noqa: BLE001 - 兜底异常再退通用兜底
            out = classic_reply(q)
        out = dict(out)
        out.setdefault("route", route)
        return out