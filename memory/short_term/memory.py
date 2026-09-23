# -*- coding: utf-8 -*-
"""短期记忆：LangGraph SqliteSaver（thread_id="session:{会话ID}"，万人即万 thread，互不串扰）。

接入方式（不改本模块）：

    from memory import MemoryManager
    mm = MemoryManager()
    graph = create_react_agent(..., checkpointer=mm.short_term.saver)
    result = graph.invoke({"messages": [...]}, mm.chat_config(session_id))

约定：消息状态 channel 名为 "messages"（langgraph MessagesState）。
压缩摘要存本库 summaries 表（不动接入方 state schema），get_history 自动注入 system 消息；
压缩触发时摘要会以 SystemMessage 回流到该 thread（标记 [早前对话摘要]），
create_react_agent 等接入方下一轮 invoke 自动携带，不丢被压缩的上下文；
get_history 已对线程内摘要消息去重，表注入是唯一摘要出口。
单进程假设；更大并发时换 Postgres checkpointer 即可（接口不变）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph

import config  # 记忆参数槽位（K 窗口 / token 守卫），读取环境变量与默认值
from .compress import Compressor
from .projects import ProjectRegistry
from .registry import ConversationRegistry, parse_summary

# ---- token 估算（窗口组装守卫用）----
# 优先复用 long_term.chunker（同口径）；hnswlib 不可用（Windows 无编译环境）时降级本地实现
try:  # pragma: no cover - 依赖分支
    from ..long_term.chunker import estimate_tokens as _estimate_tokens
except Exception:  # noqa: BLE001
    def _estimate_tokens(text: str) -> int:
        cjk = sum(
            1 for c in text
            if "\u2e80" <= c <= "\u9fff" or "\u3040" <= c <= "\u30ff" or "\uff00" <= c <= "\uffef"
        )
        return cjk + (len(text) - cjk) // 4

_ROLE_MAP = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}


def _to_message(role: str, content: str) -> BaseMessage:
    """角色字符串 → LangChain 消息对象（user/assistant/system，未知按 user）。"""
    role = (role or "user").lower()
    cls = {"user": HumanMessage, "assistant": AIMessage, "system": SystemMessage}.get(role, HumanMessage)
    return cls(content)


def thread_id_for(session_id: str) -> str:
    """会话 ID → checkpointer 线程命名空间（兼容原 memory/short_term.py 的 "session:" 前缀）。"""
    return f"session:{session_id}"


def agent_session_id(agent: str, session_id: str) -> str:
    """复合会话 ID 唯一出口：agent 维度隔离（"customer_service:demo"）。

    约定：推理侧（supervisor）与任何想对接 agent 会话的接入方，
    一律用本函数生成会话 ID，再交给 thread_id_for/chat_config/MemoryManager，
    禁止手拼 f"{agent}:{sid}"。
    """
    return f"{agent}:{session_id}"


_SUMMARY_MARKER = "[早前对话摘要]"  # 回流摘要消息的统一标记（get_history 注入前缀同源）


def _is_summary_msg(m: BaseMessage) -> bool:
    """回流进线程的滚动摘要消息（SystemMessage 且带标记前缀）。"""
    return isinstance(m, SystemMessage) and str(getattr(m, "content", "")).startswith(_SUMMARY_MARKER)


class ShortTermMemory:
    """短期记忆 = LangGraph checkpoint（会话内消息）+ summaries 表（压缩摘要）。"""

    def __init__(self, db_path: str | Path, window_size: int = 20, compressor: Compressor | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.window_size = max(2, window_size)
        self.compressor = compressor or Compressor()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS summaries("
            "thread_id TEXT PRIMARY KEY, summary TEXT NOT NULL DEFAULT '',"
            "updated_at TEXT DEFAULT (datetime('now','localtime')))"
        )
        self._conn.commit()
        self._saver = SqliteSaver(self._conn)
        self._writer = self._compile_writer()
        # 谈话注册表（同连接）：谈话生命周期 + 一级总结归属 + 二级总结游标
        self.registry = ConversationRegistry(self._conn)
        # 项目注册表（同连接）：一级「项目」容器 + 会话的 project_id / feature_key 归属
        self.projects = ProjectRegistry(self._conn)

    # ---- 内部：一个挂在同一 saver 上的最小写图（invoke/update_state 即写入 checkpoint）----
    def _compile_writer(self):
        g = StateGraph(MessagesState)
        g.add_node("noop", lambda state: {})
        g.add_edge(START, "noop")
        g.add_edge("noop", END)
        return g.compile(checkpointer=self._saver)

    @property
    def saver(self) -> SqliteSaver:
        """暴露给接入方：create_react_agent(..., checkpointer=stm.saver)。"""
        return self._saver

    def chat_config(self, session_id: str) -> dict:
        """LangGraph 调用配置：thread_id="session:{会话ID}"，一人一会话一线程。"""
        return {"configurable": {"thread_id": thread_id_for(session_id)}}

    # ---------- LLM 输入接口 ----------
    def add_message(self, session_id: str, role: str, content: str) -> None:
        """写入一条对话消息到该会话 checkpoint（role: user/assistant/system）。"""
        self._writer.invoke({"messages": [_to_message(role, content)]}, self.chat_config(session_id))

    # ---------- 谈话维度（窗口组装 / 分层总结用） ----------
    def record_turn(self, session_id: str, agent_name: str, user_id: str = "") -> None:
        """登记该谈话最近一轮由哪个智能体处理（总结/原文取回依赖此记录）。

        supervisor 每次路由后调用；谈话未登记时自动补登记（open 状态）。
        """
        reg = self.registry
        reg.start(session_id, user_id=user_id, agent_name=agent_name)
        reg.update_agent(session_id, agent_name)

    def window_context(self, user_id: str = "", k: int | None = None,
                       token_guard: int | None = None, include_open: bool = True) -> list[dict]:
        """拼装短期窗口：最近 K 个已结束谈话的「一级总结 + 原文」+ 进行中的谈话。

        返回块列表（时间升序）：每块 {conversation_id, agent, topic, summary, raw, raw_dropped}；
        token 守卫：总估算超过 token_guard 时按最新优先丢最旧谈话的 raw（绝不丢 summary），
        并在列表头部插入标记块（conversation_id="__window_marker__"）说明省略次数。
        """
        k = int(k) if k else config.SHORT_TERM_CONVERSATIONS
        limit = config.CONTEXT_TOKEN_GUARD if token_guard is None else int(token_guard)

        blocks: list[dict] = []
        for c in reversed(self.registry.recent_closed(k, user_id=user_id)):  # 倒序→升序
            blocks.append(self._conversation_block(c))
        if include_open:
            for c in self.registry.open_conversations(user_id=user_id):
                blocks.append(self._conversation_block(c))

        # token 守卫：超限时从最旧块开始丢 raw（不丢 summary），并记录省略数
        dropped = 0
        total = sum(self._block_tokens(b) for b in blocks)
        idx = 0
        while total > limit and idx < len(blocks):
            b = blocks[idx]
            if b["raw"]:  # 只丢原文；全窗口只剩 summary 时停止
                total -= self._block_tokens(b)
                b["raw"] = []
                b["raw_dropped"] = True
                dropped += 1
                total += self._block_tokens(b)
            idx += 1
        if dropped:
            blocks.insert(0, {
                "conversation_id": "__window_marker__",
                "agent": "", "topic": f"已省略更早谈话 {dropped} 次（原文退役，摘要已接力）",
                "summary": {}, "raw": [], "raw_dropped": False,
            })
        return blocks

    def _conversation_block(self, c: dict) -> dict:
        """单个谈话 → 窗口块：解析一级总结，并按 agent_name 取回 checkpoint 原文。"""
        summary = parse_summary(c.get("summary_json", ""))
        raw: list[dict] = []
        agent = c.get("agent_name") or ""
        if agent:
            try:
                sid = agent_session_id(agent, c["session_id"])
                raw = [
                    {
                        "role": _ROLE_MAP.get(getattr(m, "type", "human"), "user"),
                        "content": getattr(m, "content", ""),
                    }
                    for m in self.get_messages(sid)
                    if not _is_summary_msg(m)  # 回流的压缩摘要不进窗口原文（summary 字段才是口径）
                ]
            except Exception:  # noqa: BLE001 - 线程不存在等场景，块只带总结
                raw = []
        return {
            "conversation_id": c["session_id"],
            "agent": agent,
            "topic": (summary or {}).get("topic", "") or "",
            "summary": summary or {},
            "raw": raw,
            "raw_dropped": False,
        }

    @staticmethod
    def _block_tokens(b: dict) -> int:
        """单块 token 估算（用于守卫裁剪）。"""
        n = _estimate_tokens(b.get("topic", ""))
        for k in ("user_requests", "decisions", "pending_items", "tags"):
            n += _estimate_tokens(" ".join(map(str, b.get("summary", {}).get(k, []) or [])))
        for m in b.get("raw", []):
            n += _estimate_tokens(str(m.get("content", "")))
        return n

    # ---------- 读 ----------
    def get_messages(self, session_id: str) -> list[BaseMessage]:
        """读该会话 checkpoint 内的全部消息（含回流的摘要 SystemMessage）。"""
        state = self._writer.get_state(self.chat_config(session_id))
        return [m for m in ((state.values or {}).get("messages") or []) if isinstance(m, BaseMessage)]

    def get_history(self, session_id: str, with_summary: bool = True) -> list[dict]:
        """取会话上下文：[压缩摘要(如有)] + 当前窗口消息，供 LLM 输入组装。"""
        out: list[dict] = []
        summary = self.get_summary(session_id) if with_summary else ""
        if summary:
            out.append({"role": "system", "content": f"[早前对话摘要]\n{summary}"})
        for m in self.get_messages(session_id):
            if _is_summary_msg(m):
                continue  # 线程内回流摘要去重：表注入是唯一摘要出口，避免双份
            out.append({
                "role": _ROLE_MAP.get(getattr(m, "type", "human"), "user"),
                "content": getattr(m, "content", ""),
            })
        return out

    def count_messages(self, session_id: str) -> int:
        """当前会话消息条数（压缩阈值判断用）。"""
        return len(self.get_messages(session_id))

    def get_summary(self, session_id: str) -> str:
        """读该会话的滚动摘要；未压缩过返回空串。"""
        row = self._conn.execute(
            "SELECT summary FROM summaries WHERE thread_id=?", (thread_id_for(session_id),)
        ).fetchone()
        return row[0] if row else ""

    # ---------- 压缩 ----------
    def maybe_compress(self, session_id: str) -> dict:
        """超过阈值触发：旧消息裁剪 + 滚动摘要写回线程（无 LLM 降级为纯裁剪）。

        摘要回流：裁剪旧消息的同时，把摘要作为 SystemMessage 写回该 thread，
        create_react_agent 等接入方下一轮 invoke 自动携带，不丢被压缩的上下文。
        """
        msgs = self.get_messages(session_id)
        total = len(msgs)
        if not self.compressor.needs_compress(total):
            return {"compressed": False, "messages": total,
                    "reason": f"{total} <= 阈值 {self.compressor.compress_threshold}"}
        keep = self.compressor.keep_recent
        old_all = msgs[:-keep]                                  # 本轮全部待裁剪消息（含旧摘要消息）
        old = [m for m in old_all if not _is_summary_msg(m)]    # 摘要消息不参与摘要输入（避免重复拼 prompt）
        prev = self.get_summary(session_id)
        new_summary = self.compressor.summarize(old, prev)
        summary_updated = bool(new_summary and new_summary != prev)
        if summary_updated:                                     # 摘要表照旧落库（手动组装路径的唯一来源）
            self._conn.execute(
                "INSERT INTO summaries(thread_id, summary) VALUES(?,?) "
                "ON CONFLICT(thread_id) DO UPDATE SET summary=excluded.summary,"
                " updated_at=datetime('now','localtime')",
                (thread_id_for(session_id), new_summary),
            )
            self._conn.commit()
        # 裁剪 + 摘要回流：一个 checkpoint step 原子提交（先删旧摘要消息再写新摘要）
        removes = Compressor.messages_to_remove(old_all)
        carried = (new_summary or prev or "").strip()  # 未更新时回填旧摘要，防止二轮压缩丢摘要
        if carried:
            writes = removes + [SystemMessage(content=f"{_SUMMARY_MARKER}\n{carried}")]
        else:
            writes = removes
        if writes:
            try:
                self._writer.update_state(self.chat_config(session_id), {"messages": writes})
            except Exception:  # 兜底：同样走 reducer 的 invoke 路径
                self._writer.invoke({"messages": writes}, self.chat_config(session_id))
        return {
            "compressed": True,
            "messages_before": total,
            "removed": len(removes),
            "summary_updated": summary_updated,
            "summary_injected": bool(carried),
            "messages_after": self.count_messages(session_id),  # 回流时 = keep_recent + 1（含摘要）
        }

    # ---------- 维护 ----------
    def clear(self, session_id: str) -> None:
        # thread_id 带命名空间前缀（与 chat_config 一致），否则删不到 checkpoint；
        # 摘要表主键即 thread_id，与线程同键同删
        self._saver.delete_thread(thread_id_for(session_id))
        self._conn.execute("DELETE FROM summaries WHERE thread_id=?", (thread_id_for(session_id),))
        self._conn.commit()

    def close(self) -> None:
        """关闭 SQLite 连接（checkpointer 随连接失效）。"""
        try:
            self._conn.close()
        except Exception:
            pass
