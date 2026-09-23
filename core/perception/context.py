# -*- coding: utf-8 -*-
"""感知-上下文管理：五模块记忆 → 每次推理必带的系统上下文块（assemble_context）。

原则（见 docs/memory-system-design.md §7）：
- 死规则（状态记忆）必须每次都注入；
- 中断续跑提示（中断日志）优先注入：上一次被打断的活要能接着干；
- 长期记忆条目取最近 N 条（精确事实优先）；
- 知识库只注入「索引简述」（第一阶段），正文由智能体按需 fetch_knowledge；
- 短期窗口只注入最近谈话的「话题列表」行（细节留给 checkpointer 历史）；
- 热技能预注入 TOP 条（高分技能优先）；
- 每块独立开关由 config 控制，任何一块损坏都不影响其它块（旁路 try/except）。

返回 dict：
    {"blocks": {"rules": str, "resume": str, "facts": str, "kb_index": str,
                "window_topics": str, "hot_skills": str},
     "estimated_tokens": int, "nonempty": [块名列表]}
"""
from __future__ import annotations

import config
from memory.long_term.chunker import estimate_tokens

__all__ = ["assemble_context"]

# 长期记忆条目 / 知识库索引 / 短期窗口 的默认条数（config 未设时使用）
_LT_ENTRIES = 10
_KB_INDEX_LIMIT = 10


def assemble_context(mm, user_id: str, session_id: str = "",
                     agent_name: str = "", question: str = "") -> dict:
    """组装一次推理的全部系统上下文块（旁路容错：任何一块异常都置空，不抛错）。"""
    uid = str(user_id or session_id or "default")
    blocks: dict[str, str] = {"rules": "", "resume": "", "facts": "", "kb_index": "",
                              "window_topics": "", "hot_skills": ""}

    # 1) 死规则注入（状态记忆）：每次必带；总开关关闭时返回空串
    try:
        agent_type = (agent_name or "").replace("_agent", "")
        blocks["rules"] = mm.inject_rules(agent_type=agent_type or None, user_id=uid)
    except Exception:  # noqa: BLE001
        blocks["rules"] = ""

    # 2) 中断续跑提示（中断日志）：上次被打断到哪一步、哪个工具是被用户打断的
    try:
        if config.INTERRUPT_RESUME_INJECT:
            blocks["resume"] = mm.interrupt_block(session_id or uid)
    except Exception:  # noqa: BLE001
        blocks["resume"] = ""

    # 3) 长期记忆条目（最近 N 条范式条目：画像/事实/待办/偏好）
    try:
        entries = mm.long_term.get_long_term_entries(uid, limit=_LT_ENTRIES)
        if entries:
            lines = []
            for e in entries:
                tag = {"user_profile": "画像", "fact": "事实",
                       "pending_item": "待办", "preference": "偏好"}.get(e["entry_type"], "事实")
                lines.append(f"[{tag}·{int(e['confidence'] * 100)}%] {e['content']}")
            blocks["facts"] = "【长期记忆】\n" + "\n".join(lines)
    except Exception:  # noqa: BLE001
        blocks["facts"] = ""

    # 4) 知识库索引简述（第一阶段：只给索引，省 token）
    try:
        idx = mm.get_index(user_id=uid, limit=_KB_INDEX_LIMIT)
        if idx:
            lines = [f"· {i['title'] or '文档'}｜{i['brief']}" for i in idx]
            blocks["kb_index"] = (
                "【知识库索引】需要正文时调用知识库取块工具（chunk_id 如下）：\n"
                + "\n".join(lines)
            )
    except Exception:  # noqa: BLE001
        blocks["kb_index"] = ""

    # 5) 短期窗口：最近已结束谈话的话题列表（细节留在 checkpoint 历史里）
    try:
        window = mm.window_context(user_id=uid)
        topics = [
            (b.get("summary") or {}).get("topic") for b in window
            if (b.get("summary") or {}).get("topic")
        ]
        if topics:
            blocks["window_topics"] = "【近期谈话】" + "；".join(topics[:5])
    except Exception:  # noqa: BLE001
        blocks["window_topics"] = ""

    # 6) 热技能预注入（技能记忆 TOP 条：遇到类似情况可直接复用）
    try:
        hot = mm.hot_skills(top=config.SKILL_HOT_INJECT_TOP)
        if hot:
            lines = []
            for h in hot:
                lines.append(f"· 触发「{h['trigger'] or h['situation'][:12]}」→ {h['solution'][:60]}")
            blocks["hot_skills"] = "【可复用技能】\n" + "\n".join(lines)
    except Exception:  # noqa: BLE001
        blocks["hot_skills"] = ""

    # 7) 五段预算封顶（预算熔断·上下文分配）：输出预留 15% 不注入，其余各段按归属裁剪；
    #    纯程序裁剪（零 token / 零 LLM），故障时退回未裁剪块（旁路容错）
    try:
        from core.constraint import get_constraint_layer

        blocks = get_constraint_layer().budget.trim_context_blocks(blocks)
    except Exception:  # noqa: BLE001
        pass

    nonempty = [name for name, text in blocks.items() if text.strip()]
    total_tokens = sum(estimate_tokens(t) for t in blocks.values() if t)
    return {"blocks": blocks, "estimated_tokens": total_tokens, "nonempty": nonempty}