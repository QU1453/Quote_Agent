# -*- coding: utf-8 -*-
"""智能体公共基类（ReActAgentBase）：封装 LangGraph ReAct 智能体的通用脚手架。

各专职智能体（agents/ 下文件）继承本类，只需提供：
- system_prompt：本智能体的系统提示词；
- tools：本智能体可调用的工具列表（来自 tools/ 包）。

兜底说明：无 Key / 调用失败时各智能体模块提供模块级兜底函数
（如 customer_service.classic_reply），由 supervisor / server 调用，
不走本基类。

通用能力（本类实现）：
- LLM 初始化与失败降级（无 Key / 未装依赖 / 异常 → 记录 reason，退兜底模式）；
- 多轮记忆挂载：memory/ 提供的 checkpointer 按 thread_id 隔离会话；
- answer()：调用 LLM 并把运行结果整理为 {reply, intent, data}（含卡片信息提取）。
"""
from __future__ import annotations

import functools
import inspect

import config
from config import MODEL_ID
from core.cognition.react import (  # ReAct 引擎提炼到认知层（思考推理），此处薄封装重导出
    _HAS_LANGGRAPH,
    _JA_REPLY_HINT,
    _build_react_agent,
    create_react_agent,
    is_japanese,
)
from memory import get_memory, get_short_term, thread_id_for
from memory.access import MemoryCaller
from core.telemetry import current_cancelled, get_live_bus
from tools import lookup_order, recommend_for

# 兼容旧引用（如 agents/customer_service.py 的 from .base import is_japanese）
JA_REPLY_HINT = _JA_REPLY_HINT
ChatOpenAI = None
try:
    from langchain_openai import ChatOpenAI as _ChatOpenAI  # 兼容旧引用（基类初始化用）
    ChatOpenAI = _ChatOpenAI
except Exception:  # pragma: no cover - 离线环境 / 未安装依赖时
    pass


# 被打断时工具的占位返回前缀：上层据此识别「该工具是用户打断、并未执行」。
# 必须返回一条**正常的工具消息**而不是抛错/跳过——assistant 的 tool_calls 必须有
# 对应的 tool 结果，否则该 agent 线程的 checkpoint 会留下残破历史，下一轮调模型
# 直接被接口以「tool_calls 缺后续 tool 消息」拒绝，整个专职智能体就废了。
INTERRUPT_SKIP_PREFIX = "[已打断]"
_INTERRUPT_SKIP_REPLY = INTERRUPT_SKIP_PREFIX + " 用户已打断本轮，该工具未执行。"


def _guard_tool(fn):
    """约束层挂载：agent 直连工具统一过验证层（权限/路径/网络/危险 + hook）与循环守卫。

    - 打断短路：本轮已被请求打断时**不执行**工具，直接返回占位结果（零副作用）；
    - 拦截：不执行原函数，把错误提示文本作为工具结果返回（随 ToolMessage 进入
      消息列表，agent 读到后可自行调整策略）；
    - 软干预：工具结果末尾追加 <system-reminder>（相同调用提醒 / [A,B]×3 交替提醒）；
    - 记账：成功/失败都计入循环守卫（总量 / 连败 / 完全相同签名）；
    - 会话归并：caller.session_id 留空，循环守卫键回落到线程变量（编排器设原始会话 ID），
      避免按 agent_session_id 拆散同一会话的计数。
    functools.wraps 保留函数名/签名/文档，LangGraph 工具节点与遥测提取按原名工作。
    """
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        from core.constraint.layer import get_constraint_layer

        # 打断优先：已请求打断 → 工具不执行（不记账、不落副作用），只留占位工具消息
        try:
            if current_cancelled():
                return _INTERRUPT_SKIP_REPLY
        except Exception:  # noqa: BLE001 - 打断判定故障不阻断工具调用
            pass

        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            params = dict(bound.arguments)
        except TypeError as exc:
            return f"参数错误：{exc}"
        layer = get_constraint_layer()
        caller = MemoryCaller(f"agent:{fn.__name__}", "L1")
        verdict = layer.check_tool_call(fn.__name__, params, caller)
        if not verdict.allowed:
            return verdict.message
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - 失败也记账（连败熔断依据），错误文本返回 agent
            reminder = layer.record_tool_result(
                fn.__name__, params, False, f"{exc.__class__.__name__}: {exc}", caller)
            return f"工具执行失败（{exc.__class__.__name__}）：{exc}{reminder}"
        reminder = layer.record_tool_result(fn.__name__, params, True, str(result or ""), caller)
        if reminder and isinstance(result, str):
            return result + reminder
        return result

    return wrapper


def _extract_telemetry(result: dict, prev_count: int, model: str) -> dict:
    """从本轮新增消息提取遥测数据（供 orchestrator 消费后剥离）。

    - input_tokens / output_tokens：累加各 AIMessage 的 usage_metadata
      （OpenInference 口径 llm.token_count.prompt/completion；与 API 计费一致）；
    - tool_calls：全部工具调用（name + args，OpenInference 口径 tool_call.function.*）；
    - model：本轮使用的模型 ID。
    """
    msgs = (result.get("messages") or [])[prev_count:]
    input_tokens = output_tokens = 0
    tool_calls: list[dict] = []
    for m in msgs:
        usage = getattr(m, "usage_metadata", None) or {}
        input_tokens += int(usage.get("input_tokens") or 0)
        output_tokens += int(usage.get("output_tokens") or 0)
        for c in getattr(m, "tool_calls", None) or []:
            tool_calls.append({"name": c.get("name", ""),
                               "args": c.get("args") or {}})
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "tool_calls": tool_calls, "model": model}


def _args_summary(args: object, limit: int = 90) -> str:
    """工具入参 → 一行摘要（思考小字展示用；只给数据不给措辞，措辞由前端本地化）。"""
    if not isinstance(args, dict) or not args:
        return ""
    parts = []
    for key, val in list(args.items())[:4]:
        s = "" if val is None else str(val).replace("\n", " ")
        parts.append(f"{key}={s[:40]}")
    return "、".join(parts)[:limit]


def _result_summary(content: object, limit: int = 90) -> str:
    """工具返回 → 一行摘要（同上，截断防界面过长）。"""
    s = "" if content is None else str(content).replace("\n", " ").strip()
    return s[:limit]


def build_memory_context(mm, uid: str, question: str,
                         fact_limit: int = 10, top_k: int = 3) -> str | None:
    """组装长期记忆上下文文本（用户事实 + RAG 召回）；无任何数据返回 None（零开销路径）。

    uid 约定：演示期用复合会话 ID（agent_session_id 结果）兜底用户维度，
    与 MemoryManager.build_context 的回落规则一致；接入账号体系后换成真实 user_id 即可。
    """
    try:
        facts = mm.get_facts(uid)[:fact_limit]
        recalled = []
        if mm.long_term.count(uid)["chunks"]:
            recalled = mm.recall(uid, question, top_k=top_k)
        if not facts and not recalled:
            return None
        lines = []
        if facts:
            kv = "；".join(f"{f['key']}={f['value']}" for f in facts)
            lines.append(f"[用户事实] {kv}")
        if recalled:
            hits = " / ".join(f"『{r['title'] or '知识'}』{r['text'][:80]}" for r in recalled)
            lines.append(f"[相关知识] {hits}")
        return ("以下是该用户的长期记忆，回答时可作为事实依据（不得虚构）；\n" + "\n".join(lines))
    except Exception:  # noqa: BLE001 - 长期记忆故障不影响本轮推理
        return None


class ReActAgentBase:
    """ReAct 智能体基类。graph 为 None 表示运行在本地兜底模式。"""

    # 子类需覆盖：系统提示词 / 工具列表（类属性声明，构造时传入）
    system_prompt: str = ""
    tools: list = []

    def __init__(
        self,
        api_key: str | None,
        base_url: str | None = None,
        model: str = MODEL_ID,  # 默认值统一来自 config.py（glm-5.3-flash），不在各处硬编码
    ):
        self.model = model
        self.reason = None  # 初始化失败/未配置的原因（供日志展示）
        self._graph = None
        self._stm = None    # 进程级单例短期记忆（压缩与推理共享同一 saver）
        self._dynamic_system: list[str] = []  # 每轮推理前重建的动态系统上下文（不落 checkpoint）
        if not api_key:
            self.reason = "未配置 GLM_API（或 OPENAI_API_KEY），运行于本地规则兜底模式"
            return
        if not _HAS_LANGGRAPH:
            self.reason = "未安装 langgraph/langchain-openai，运行于本地规则兜底模式"
            return
        try:
            # 温度取运行时配置（网页「设置」可调；reload 后按新值重建）
            llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url or None,
                             temperature=config.LLM_TEMPERATURE)
            # 多轮记忆：memory/ 提供的 checkpointer 按 thread_id 隔离会话；
            # 压缩器注入同一 llm，超阈值裁剪时滚动摘要会回流进线程（不丢上下文）；
            # prompt 用动态 callable：长期记忆/日语提示每轮重建，不进 checkpoint 历史
            self._stm = get_short_term(llm=llm)
            # 约束层挂载：直连工具逐个包上验证层 + 循环守卫（不改类属性，实例级包装）
            self._guarded_tools = [_guard_tool(t) for t in (self.tools or [])]
            self._graph = _build_react_agent(
                llm, self._guarded_tools, self._dynamic_prompt, checkpointer=self._stm.saver
            )
        except Exception as exc:  # noqa: BLE001
            self.reason = f"Agent 初始化失败（{exc.__class__.__name__}: {exc}）"

    @property
    def available(self) -> bool:
        """LLM 模式是否可用。"""
        return self._graph is not None

    def answer(self, question: str, session_id: str = "default",
               extra_system: list[str] | None = None,
               request_id: str = "") -> dict | None:
        """调用 LLM Agent，返回 {reply, intent, data}；失败返回 None（交由兜底）。

        多轮记忆：checkpointer 按 thread_id（= session_id）自动携带历史上下文，
        无需手动拼接 history。extra_system：编排层（orchestrator）注入的
        每轮动态上下文块（死规则/长期记忆/知识库索引/短窗口话题/热技能）。

        流式驱动（stream_mode="updates"）：边跑边把「思考步骤」上报运行态总线，
        供聊天框展示小字；同时在各检查点检查打断信号，实现协作式打断。
        request_id：本轮运行态标识；留空 = 不展示思考过程、不支持打断（零开销）。
        """
        if not self.available:
            return None
        try:
            cfg = {"configurable": {"thread_id": thread_id_for(session_id)}}
            # 循环守卫会话归并：编排器未设置线程变量时（直调 agent）兜底为本次会话 ID
            try:
                from core.constraint.layer import ensure_current_session

                ensure_current_session(session_id)
            except Exception:  # noqa: BLE001
                pass
            # 每轮重建动态系统上下文（经 _dynamic_prompt 组装，不落 checkpoint 历史）：
            # 1) 长期记忆（用户事实 + RAG 召回）——空库/hnswlib 不可用时为零开销路径；
            # 2) 编排层注入块（extra_system）：五模块记忆的系统上下文；
            # 3) 日语提示（含假名即视为日语用户；按当前输入语言逐轮生效，历史零残留）
            mm = get_memory()
            ctx = build_memory_context(mm, session_id, question) if mm is not None else None
            self._dynamic_system = (
                ([ctx] if ctx else [])
                + (extra_system or [])
                + ([_JA_REPLY_HINT] if is_japanese(question) else [])
            )
            # 流式驱动：既拿到本轮全部新增消息，也顺路完成思考上报与打断检查
            msgs, interrupt = self._stream_run(cfg, question, request_id)
            result = {"messages": msgs}
            formatted = self._format_result(result, 0)
            # 遥测提取：本轮新增消息里的 usage_metadata（真实 token）与 tool_calls
            # （ReAct 每步模型调用各有一条 AIMessage，逐条累加 = 本轮总消耗）
            try:
                from core.telemetry import get_recorder

                get_recorder()  # 触发单例初始化（fail-open，见 recorder）
                formatted["_telemetry"] = _extract_telemetry(result, 0, self.model)
            except Exception:  # noqa: BLE001 - 遥测故障不影响回复
                pass
            if interrupt:
                # 打断标记：编排器据此落中断日志（memory/interrupt/）并停止兜底
                formatted["_interrupt"] = interrupt
            try:
                # 每轮推理后触发压缩检查：阈值内只多一次 get_state，零 LLM 开销；
                # 超阈值时裁剪旧消息并把滚动摘要回流进线程，下一轮自动携带
                self._stm.maybe_compress(session_id)
            except Exception:  # noqa: BLE001 - 压缩失败不影响本轮回复
                pass
            return formatted
        except Exception:  # noqa: BLE001 - 网络/额度/格式异常都交给兜底
            return None

    def _stream_run(self, cfg: dict, question: str,
                    request_id: str = "") -> tuple[list, dict | None]:
        """流式跑完 ReAct：逐步上报思考步骤 + 在检查点响应打断。

        返回 `(本轮新增消息, 打断信息)`；打断信息为 None 表示正常跑完。

        打断是**协作式**的（单次 LLM 请求发出后无法从外部掐断，这是物理限制）：
        - 模型已决定调工具时命中打断 → `_guard_tool` 把工具短路成占位返回（真不执行、
          零副作用），tools 节点照常产出 tool 消息，**消息历史保持完整**；
        - 每个 chunk 收完才判定停止，绝不中途丢弃同一批消息——否则会留下
          「有 tool_calls 却无 tool 结果」的残破 checkpoint，该 agent 线程下一轮
          调模型会直接被接口拒绝（这是必须避开的真坑）。
        """
        bus = get_live_bus() if request_id else None
        msgs: list = []
        completed_tools: list[str] = []
        skipped_tools: list[str] = []      # 被用户打断、未执行的工具
        pending_tool = ""
        step_no = 0

        # 自愈：上一轮可能留下「有 tool_calls 却无 tool 结果」的残缺尾巴
        # （打断、进程被强杀导致最后一次写入丢失等都可能造成）。这种尾巴会让模型接口
        # 直接拒绝整条历史，该专职智能体从此静默不可用（表现为悄悄退化到客服智能体），
        # 所以每轮开跑前先补齐占位工具结果。
        self._heal_dangling_tool_calls(cfg)

        if bus is not None:
            bus.add(request_id, "llm", "think", detail="0")
            # 检查点⓪：还没开跑就已被打断 → 连模型都不调用（零成本停止）
            if bus.is_cancelled(request_id):
                return msgs, self._interrupt_info(
                    "perception", 0, [], "", False, msgs, bus.cancel_reason(request_id))

        gen = self._graph.stream(
            {"messages": [{"role": "user", "content": question}]},
            cfg, stream_mode="updates",
        )
        interrupt: dict | None = None
        try:
            for chunk in gen:
                for payload in (chunk or {}).values():
                    for m in (payload or {}).get("messages") or []:
                        msgs.append(m)
                        calls = getattr(m, "tool_calls", None) or []
                        if calls:
                            step_no += 1
                            if bus is not None:
                                bus.add(request_id, "llm", "think", detail=str(step_no))
                            for call in calls:
                                name = str(call.get("name") or "")
                                pending_tool = name
                                if bus is not None:
                                    bus.add(request_id, "tool", name,
                                            detail=_args_summary(call.get("args")),
                                            status="pending")
                        elif getattr(m, "type", "") == "tool":
                            name = str(getattr(m, "name", "") or pending_tool)
                            content = str(getattr(m, "content", "") or "")
                            status = str(getattr(m, "status", "success") or "").lower()
                            ok = not status.startswith("error")
                            if content.startswith(INTERRUPT_SKIP_PREFIX):
                                # 用户打断：工具未执行（既不算成功，也不算失败）
                                if name:
                                    skipped_tools.append(name)
                                if bus is not None:
                                    bus.add(request_id, "tool", name or pending_tool,
                                            detail="", status="skipped")
                            elif ok:
                                if name:
                                    completed_tools.append(name)
                                if bus is not None:
                                    bus.add(request_id, "tool", name or pending_tool,
                                            detail=_result_summary(content), status="ok")
                            elif bus is not None:
                                bus.add(request_id, "tool", name or pending_tool,
                                        detail=_result_summary(content), status="error")
                            if name and name == pending_tool:
                                pending_tool = ""

                # 一个 chunk 收完才判定停止（同一批消息必须全部进 msgs）
                if bus is None or not bus.is_cancelled(request_id):
                    continue
                if pending_tool and not skipped_tools:
                    # 检查点①命中：模型刚决定调工具。这里**不能** break——
                    # 要让 tools 节点把工具短路成占位返回，历史才完整；
                    # 循环会在 tools 那个 chunk 之后停住。
                    continue
                if pending_tool or completed_tools or skipped_tools:
                    # 检查点②：工具已返回（或已短路），不再推进下一步推理
                    stop_tool = skipped_tools[0] if skipped_tools else pending_tool
                    interrupt = self._interrupt_info(
                        "tool" if stop_tool else "thinking", step_no, completed_tools,
                        stop_tool, bool(skipped_tools), msgs,
                        bus.cancel_reason(request_id))
                    break
        finally:
            # 显式关闭生成器：break/return 跳出后立刻停止图执行，不等 GC
            close = getattr(gen, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
        return msgs, interrupt

    def _heal_dangling_tool_calls(self, cfg: dict) -> None:
        """修补残缺历史：清掉「有 tool_calls 却没有任何 tool 结果」的 AI 消息。

        为什么必须有这一手：OpenAI 兼容接口要求 assistant 的每个 tool_call 都有对应的
        tool 消息，否则整条历史被拒 → 该专职智能体每轮都失败。上层只看到它"不可用"，
        会静默退化到客服智能体，用户完全无从察觉（表现为"选品智能体好像失灵了"）。

        产生原因：打断、进程被强杀导致最后一次 checkpoint 写入丢失等。
        注意不能只看最后一条：失败轮次会继续往历史里追加用户消息，残缺的 AI 消息
        会沉到中间，所以这里全量扫描。

        失败一律静默放过（最坏情况退回改造前行为，不影响本轮）。
        """
        try:
            state = self._graph.get_state(cfg)
            msgs = list((state.values or {}).get("messages") or []) if (state and state.values) else []
            if not msgs:
                return
            from langchain_core.messages import RemoveMessage

            answered = {str(getattr(m, "tool_call_id", "")) for m in msgs
                        if getattr(m, "tool_call_id", "")}
            doomed: list[str] = []
            orphan_ids: set[str] = set()
            for m in msgs:
                calls = getattr(m, "tool_calls", None) or []
                if not calls:
                    continue
                pending = [c for c in calls if str(c.get("id") or "") not in answered]
                mid = str(getattr(m, "id", "") or "")
                if pending and mid:
                    doomed.append(mid)
                    orphan_ids.update(str(c.get("id") or "") for c in calls)
            if not doomed:
                return
            # 顺带清掉指向被删 AI 消息的孤儿 tool 结果，避免留下无主的工具消息
            for m in msgs:
                mid = str(getattr(m, "id", "") or "")
                if mid and str(getattr(m, "tool_call_id", "") or "") in orphan_ids:
                    doomed.append(mid)
            self._graph.update_state(
                cfg, {"messages": [RemoveMessage(id=i) for i in dict.fromkeys(doomed)]})
        except Exception:  # noqa: BLE001 - 自愈失败不阻断本轮
            pass

    @staticmethod
    def _interrupt_info(stage: str, step_index: int, completed_tools: list[str],
                        pending_tool: str, tool_interrupted_by_user: bool,
                        msgs: list, reason: str) -> dict:
        """汇总打断现场：停在哪一步 / 已完成与被打断的工具 / 已产出的部分内容。"""
        partial = ""
        for m in reversed(msgs):
            if getattr(m, "type", "") in ("tool", "human"):
                continue
            content = str(getattr(m, "content", "") or "").strip()
            if content:
                partial = content[:400]
                break
        return {
            "stage": stage,                                   # tool / thinking
            "step_index": int(step_index),
            "completed_tools": list(completed_tools),
            "pending_tool": pending_tool,
            "tool_interrupted_by_user": bool(tool_interrupted_by_user),
            "partial_reply": partial,
            "interrupted_by": "user",
            "reason": str(reason or "user"),
        }

    def _dynamic_prompt(self, state):
        """动态 prompt：固定系统提示 + 每轮最新动态上下文 + 历史消息。

        create_react_agent 每次调用模型前都会执行本 callable，因此上下文永远取
        当前数据（长期记忆/日语提示），历史 checkpoint 中不留任何注入残留。
        兼容不同版本的 callable 签名：state 可能为 dict（prompt=）或消息列表
        （messages_modifier/state_modifier=）。
        """
        from langchain_core.messages import SystemMessage  # 延迟导入：仅 graph 存在时才会调用

        msgs = state.get("messages", []) if isinstance(state, dict) else list(state)
        out: list = []
        if self.system_prompt:
            out.append(SystemMessage(content=self.system_prompt))
        out.extend(SystemMessage(content=t) for t in self._dynamic_system)
        out.extend(msgs)
        return out

    # ---------- 内部：把 Agent 运行结果整理为结构化回复 ----------
    @staticmethod
    def _format_result(result: dict, prev_count: int = 0) -> dict:
        msgs = (result.get("messages") or [])[prev_count:]
        reply = ""
        intent, data = "none", None
        for m in reversed(msgs):
            if getattr(m, "content", ""):
                reply = m.content or ""
                break

        # 从本轮工具调用中还原"卡片信息"，让前端渲染订单 / 推荐卡片
        for m in msgs:
            calls = getattr(m, "tool_calls", None) or []
            for c in calls:
                name, args = c.get("name", ""), c.get("args") or {}
                if name == "recommend_products":
                    intent = "recommend"
                    data = {"items": card_items(recommend_for(args.get("keywords") or []))}
                elif name in ("query_order_info", "track_logistics"):
                    order = lookup_order(args.get("order_no", ""))
                    if order:
                        intent = "order"
                        data = order_card(order)
        return {"reply": reply, "intent": intent, "data": data}


# ---- 卡片数据结构化（与 ui/app.js 的渲染约定保持一致，各智能体兜底共用）-------------
def card_items(items: list[dict]) -> list[dict]:
    """商品列表 → 前端推荐卡片结构。"""
    return [{"name": p["name"], "price": p["price"], "img": p["img"]} for p in items]


def order_card(order: dict) -> dict:
    """订单详情 → 前端订单卡片结构。"""
    p = order["product"]
    return {
        "order_no": order["order_no"],
        "carrier": order["carrier"],
        "paid_at": order["paid_at"],
        "qty": order["qty"],
        "total": order["total"],
        "steps": order["steps"],
        "product": {"name": p["name"], "price": p["price"], "img": p["img"]},
    }
