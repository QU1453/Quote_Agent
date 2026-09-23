# -*- coding: utf-8 -*-
"""指导智能体（Guide Agent）：右下角悬浮窗里的「下一步该去哪儿」导航助手。

定位：
卖家问「我该去哪儿 / 下一步做什么」时，本模块读一遍记忆现状，只回答导航建议——
当前在哪条业务线、哪段谈话还有没跑完的活、下一步建议点哪里。

权限（由代码强制，不只靠提示词）：
- 身份固定为 `MemoryCaller("guide_agent", "L0")`（见 GUIDE_CALLER）：L0 只有读权限，
  memory/access.py 的权限矩阵里写（write）/改（modify）都要求更高等级，越权会被
  guard() 判 AccessError；本模块**不注册任何写工具**；
- 本模块只调用只读接口（assemble_context / registry.list_conversations /
  interrupt.open_by_session），**绝不**调用 log_interrupt / mark_resumed / mark_closed /
  resolve_open / set_summary / add_fact / add_rule / add_skill 等写入方法。

工作方式（单轮问答，不走 ReAct / LangGraph——本 agent 无工具）：
1. 读只读上下文（死规则 / 长期记忆 / 知识库索引 / 近期话题 / 热技能 / 续跑提示）
   + 导航状态（当前 scope + 最近会话清单 + 每段谈话的未完成断点）；
2. 交 LLM 产出 JSON（advice + actions）；无 Key / 调用失败 / 解析失败 → 用真实只读数据兜底；
3. actions 里的 session_id 必须真实存在于会话清单，模型编造的会话会被丢弃；最多 3 条。
"""
from __future__ import annotations

import json
import re

import config
from core.perception.context import assemble_context
from memory.access import AuditLogger, MemoryCaller

__all__ = ["GUIDE_CALLER", "guide_reply"]

# 只读身份：L0 只读（写/改在 guard() 里被判越权；本模块不注册任何写工具）
GUIDE_CALLER = MemoryCaller("guide_agent", "L0")

# 区块 → 中文名（前端动作按钮展示用）
_SCOPE_CN = {"research": "选品", "listing": "Listing", "global": "全局"}
# 状态 → 中文名
_STATUS_CN = {"open": "进行中", "closed": "已结束", "summarized": "已总结"}
_MAX_ACTIONS = 3          # actions 条数上限（避免模型罗列一堆）
_MAX_CONVERSATIONS = 20   # 导航状态里最多列出的谈话数
_MAX_PROJECTS = 10        # 导航状态里最多列出的项目数
_LLM_TIMEOUT = 20         # 单次 LLM 调用超时（秒）——超时即走真实数据兜底

SYSTEM_PROMPT = (
    "你是「Quote Agent」卖家工作台右下角悬浮窗里的指导智能体（权限 L0，只读）。\n"
    "层级：项目（用户创建，一级）→ 区块（选品 research / Listing listing，平级）→"
    " 细分板块 feature_key（二级）→ 板块窗口内的会话。\n"
    "职责：基于下面给出的只读记忆现状，告诉用户「现在在哪、下一步去哪儿、做什么」。\n"
    "硬约束：\n"
    "1. 你只能读信息、给建议，绝不能声称自己已执行任何写操作；\n"
    "2. 只能引用材料里出现过的 session_id / project_id，禁止编造；"
    "没有合适的会话就只给 project_id 建议（让用户进那个项目）；\n"
    "3. 建议要具体可执行（一句话说清下一步），不要复述整段材料。\n"
    "输出格式（必须是 JSON，最多 3 条 actions，可为空数组；scope ∈ research/listing/global）：\n"
    '{"advice": "给用户看的建议", "actions": [{"label": "去继续那段对话",'
    ' "project_id": "…", "scope": "research", "feature_key": "profit", "session_id": "abc"}]}'
)


def _scope_label(scope: str, agent_name: str = "") -> str:
    """scope（区块）→ 可读标签（未知 scope 用原值，再回落到 agent_name）。"""
    s = str(scope or "").strip().lower()
    if s in _SCOPE_CN:
        return _SCOPE_CN[s]
    return s or str(agent_name or "").strip()


def _proj_name(project_id: str, proj_names: dict) -> str:
    """项目名（查不到就回落到 project_id；空串 = 未分类）。"""
    pid = str(project_id or "").strip()
    if not pid:
        return "未分类"
    return str(proj_names.get(pid) or pid)


def _title_of(conv: dict) -> str:
    """谈话标题（空则按 scope/agent_name 拼一个可读默认值，不留空串）。"""
    title = str(conv.get("title") or "").strip()
    if title:
        return title
    label = _scope_label(conv.get("scope"), conv.get("agent_name"))
    return f"{label}谈话" if label else "(未命名谈话)"


def _navigation_block(scope: str, conversations: list[dict], open_map: dict,
                      projects: list[dict], project_id: str = "",
                      feature_key: str = "") -> str:
    """导航状态文本：当前所在项目 / 板块 + 项目清单 + 最近谈话（含未完成断点明细）。

    板块名称定义在前端（后端不复刻那份清单），所以这里只给 feature_key 原值。
    """
    proj_names = {str(p.get("project_id") or ""): str(p.get("name") or "")
                  for p in (projects or [])}
    here = _proj_name(project_id, proj_names)
    where = f"项目「{here}」" if project_id else "总控台（未进入项目）"
    if feature_key:
        where += f" · 板块 {feature_key}"
    lines = [f"【导航状态】用户当前在：{where}（区块：{_scope_label(scope) or '未指定'}）"]

    if projects:
        lines.append(f"- 项目列表（共 {len(projects)} 个）：")
        for p in projects[:_MAX_PROJECTS]:
            lines.append(f"  · {p.get('name') or '(未命名)'}"
                         f"（project_id={p.get('project_id')}，"
                         f"{int(p.get('session_count') or 0)} 段对话，"
                         f"{int(p.get('open_interrupts') or 0)} 个未完成断点）")
    else:
        lines.append("- 暂无项目（用户还没创建项目）")

    if not conversations:
        lines.append("- 暂无历史谈话记录")
        return "\n".join(lines)
    lines.append(f"- 最近 {len(conversations)} 段谈话：")
    for c in conversations:
        sid = str(c.get("session_id") or "")
        status = _STATUS_CN.get(str(c.get("status") or ""), str(c.get("status") or ""))
        label = _scope_label(c.get("scope"), c.get("agent_name")) or "未指定"
        loc = f"{_proj_name(c.get('project_id'), proj_names)}/" \
              f"{c.get('feature_key') or '未分类'}"
        line = f"  · [{loc} · {label}] {_title_of(c)}（{status}，session_id={sid}）"
        info = open_map.get(sid)
        if info:
            detail = f"有未完成断点：停在第 {int(info.get('step_index') or 0)} 步"
            if info.get("pending_tool"):
                detail += f"，工具 {info['pending_tool']} 未执行"
            if info.get("question"):
                detail += f"，上次诉求「{str(info['question'])[:60]}」"
            line += " —— " + detail
        lines.append(line)
    return "\n".join(lines)


def _user_prompt(message: str, scope: str, blocks: dict, navigation: str) -> str:
    """LLM 用户消息：问题 + 只读记忆块 + 导航状态。"""
    parts = [f"用户的问题：{message}",
             f"用户当前所在的业务线（scope）：{_scope_label(scope) or '未指定'}",
             navigation]
    labels = {"rules": "死规则", "facts": "长期记忆", "window_topics": "近期谈话",
              "hot_skills": "可复用技能", "kb_index": "知识库索引", "resume": "未完成续跑"}
    for key, name in labels.items():
        text = str((blocks or {}).get(key) or "").strip()
        if text:
            parts.append(f"【{name}】\n{text}")
    return "\n\n".join(parts)[:12000]


def _parse_llm_json(text: str) -> dict | None:
    """防御式解析：容忍 ```json 围栏与前后解释文字，抓第一个 {...} 对象。"""
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text).strip())
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _clean_actions(actions, valid_sids: set[str],
                   valid_pids: set[str]) -> list[dict]:
    """动作校验：label 非空、session_id / project_id 必须真实存在（编造的丢弃）、最多 3 条。"""
    out: list[dict] = []
    if not isinstance(actions, list):
        return out
    for item in actions:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        sid = str(item.get("session_id") or "").strip()
        if sid and sid not in valid_sids:
            continue  # 编造的会话：丢弃
        pid = str(item.get("project_id") or "").strip()
        if pid and pid not in valid_pids:
            continue  # 编造的项目：丢弃
        out.append({"label": label[:40],
                    "project_id": pid,
                    "feature_key": str(item.get("feature_key") or "").strip(),
                    "scope": str(item.get("scope") or "").strip(),
                    "session_id": sid})
        if len(out) >= _MAX_ACTIONS:
            break
    return out


def _fallback_plan(scope: str, conversations: list[dict], open_map: dict,
                   blocks: dict, projects: list[dict]) -> dict:
    """兜底建议（无 Key / LLM 失败）：全部来自真实只读数据，不硬编码假话。"""
    by_sid = {str(c.get("session_id") or ""): c for c in conversations}
    # 优先挑「清单里能看到」的断点；按断点创建时间取最新
    candidates = sorted(open_map.items(),
                        key=lambda kv: str((kv[1] or {}).get("created_at") or ""),
                        reverse=True)
    target = None
    for sid, info in candidates:
        if sid in by_sid:
            target = (sid, info, by_sid[sid])
            break
    if target is None and candidates:
        sid, info = candidates[0]
        target = (sid, info, {})

    proj_names = {str(p.get("project_id") or ""): str(p.get("name") or "")
                  for p in (projects or [])}

    if target is not None:
        sid, info, conv = target
        pid = str(conv.get("project_id") or "")
        label = _scope_label(conv.get("scope"), conv.get("agent_name")) or "当前板块"
        where = f"项目「{_proj_name(pid, proj_names)}」的「{label}」" if pid else f"「{label}」"
        advice = f"你上次在{where}里有一段「{_title_of(conv)}」没跑完，停在第 " \
                 f"{int(info.get('step_index') or 0)} 步"
        if info.get("pending_tool"):
            advice += f"（工具 {info['pending_tool']} 未执行）"
        advice += "，可以直接去继续。"
        if info.get("question"):
            advice += f"上次的诉求是「{str(info['question'])[:60]}」。"
        action = {"label": "去继续那段对话", "project_id": pid,
                  "feature_key": str(conv.get("feature_key") or ""),
                  "scope": str(conv.get("scope") or scope or "").strip(),
                  "session_id": sid}
        return {"advice": advice, "actions": [action]}

    if projects:
        names = "、".join(str(p.get("name") or "") for p in projects[:3] if p.get("name"))
        return {"advice": f"当前没有未完成的断点。你现在有项目：{names}。"
                          "进去挑一个细分板块（如选品的「竞争分析」或 Listing 的「定价策略」）"
                          "就能开始；不挂项目的通用问题可以在总控台的「全局对话」里问。",
                "actions": [{"label": f"进入「{p.get('name')}」",
                             "project_id": str(p.get("project_id") or ""),
                             "scope": "", "feature_key": "", "session_id": ""}
                            for p in projects[:2] if p.get("name")]}

    advice = ("还没有项目。先在总控台点「新建项目」（比如「水杯项目」），"
              "进去之后选品 / Listing 两块下面各有细分板块，点板块就能开始。")
    topics = str((blocks or {}).get("window_topics") or "").replace("【近期谈话】", "").strip()
    if topics:
        advice += f" 你最近聊过：{topics[:80]}。"
    return {"advice": advice, "actions": []}


def _read_only_state(user_id: str, session_id: str, question: str) -> tuple:
    """读只读状态：最近谈话清单 / 项目清单 / 中断断点聚合 / 上下文块（失败都降级为空）。"""
    conversations: list[dict] = []
    projects: list[dict] = []
    blocks: dict = {}
    open_map: dict = {}
    try:
        from memory import get_short_term

        stm = get_short_term()
        conversations = stm.registry.list_conversations(
            user_id=user_id, limit=_MAX_CONVERSATIONS)
        projects = stm.projects.list(user_id=user_id)
    except Exception:  # noqa: BLE001 - 注册表故障：只影响导航清单
        conversations, projects = [], []
    try:
        from memory import get_memory

        mm = get_memory()
        if mm is not None:
            blocks = assemble_context(mm, user_id, session_id=session_id,
                                      agent_name="guide", question=question).get("blocks", {})
            open_map = mm.interrupt.open_by_session()
        else:
            # 长期记忆不可用（如 hnswlib 缺失）时，中断台账仍可单独读
            from memory.interrupt.memory import InterruptLog

            open_map = InterruptLog(config.DATA_DIR / "memory").open_by_session()
    except Exception:  # noqa: BLE001 - 上下文/台账故障：按无断点兜底
        open_map = open_map or {}
    return conversations, blocks, open_map, projects


def guide_reply(message: str, session_id: str = "", scope: str = "",
                user_id: str = "default", project_id: str = "",
                feature_key: str = "") -> dict:
    """指导智能体入口：返回 {"ok", "mode": "llm|fallback", "reply", "actions"}。

    只读：全程只调用 registry.list_conversations / projects.list /
    interrupt.open_by_session / assemble_context 四个只读接口；
    任何异常都不向上抛（fail-open 给真实数据兜底）。
    """
    msg = str(message or "").strip()
    uid = str(user_id or "default")
    conversations, blocks, open_map, projects = _read_only_state(uid, session_id, msg)
    navigation = _navigation_block(scope, conversations, open_map, projects,
                                   project_id=project_id, feature_key=feature_key)
    fallback = _fallback_plan(scope, conversations, open_map, blocks, projects)

    # 审计留痕：以 GUIDE_CALLER（L0 只读）身份记一次导航问答（失败静默）
    try:
        AuditLogger.log(actor=GUIDE_CALLER.name, actor_level=GUIDE_CALLER.level,
                        action="guide:ask", module="memory", target_id=session_id,
                        session_id=session_id, result="ok")
    except Exception:  # noqa: BLE001
        pass

    if not msg:
        return {"ok": True, "mode": "fallback",
                "reply": fallback["advice"], "actions": fallback["actions"]}

    if config.API_KEY:
        try:
            from langchain_openai import ChatOpenAI

            llm = ChatOpenAI(model=config.MODEL_ID, api_key=config.API_KEY,
                             base_url=config.BASE_URL or None, temperature=0.2,
                             timeout=_LLM_TIMEOUT)
            resp = llm.invoke([("system", SYSTEM_PROMPT),
                               ("human", _user_prompt(msg, scope, blocks, navigation))])
            text = getattr(resp, "content", "") or ""
            if isinstance(text, str) and text.strip():
                data = _parse_llm_json(text)
                advice = str((data or {}).get("advice") or "").strip()
                if not advice:
                    advice = text.strip()  # 解析失败：整段文本当建议，actions 置空
                valid = {str(c.get("session_id") or "") for c in conversations} | set(open_map)
                valid_pids = {str(p.get("project_id") or "") for p in projects}
                return {"ok": True, "mode": "llm", "reply": advice,
                        "actions": _clean_actions((data or {}).get("actions"),
                                                  valid, valid_pids)}
        except Exception:  # noqa: BLE001 - 超时/网络/额度异常 → 真实数据兜底
            pass
    return {"ok": True, "mode": "fallback",
            "reply": fallback["advice"], "actions": fallback["actions"]}
