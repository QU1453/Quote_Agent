# -*- coding: utf-8 -*-
"""MemoryManager：Agent 记忆统一入口。

两类输入接口（对接入方零侵入，模块可整体搬移）：
- LLM 输入接口（对话消息）：add_message / chat_config / get_history / maybe_compress / saver
- 后端数据输入接口：save_fact（结构化 KV）/ add_document（长文本 → 分块 → ANN 入库）

检索：recall（ANN 速度优先 + 精确重排，高准确率）；组装：build_context 四段上下文。

换库扩展：替换 EmbeddingProvider / AnnIndex / 存储实现即可，本类签名不变
（如未来切 Postgres/pgvector / Redis / 分片索引）。
"""
from __future__ import annotations

from pathlib import Path

import config  # 记忆参数槽位（SKILL_HOT_INJECT_TOP 等）

from .long_term.memory import LongTermMemory
from .long_term.rag import pick_provider
from .knowledge import KnowledgeBase
from .short_term.compress import Compressor
from .short_term.memory import ShortTermMemory

__all__ = ["MemoryManager"]


class MemoryManager:
    def __init__(
        self,
        base_dir: str | Path | None = None,
        *,
        short_term: ShortTermMemory | None = None,
        long_term: LongTermMemory | None = None,
        knowledge: KnowledgeBase | None = None,
        state: "object | None" = None,
        skill: "object | None" = None,
        window_size: int = 20,
        compress_threshold: int = 30,
        keep_recent: int = 10,
        llm=None,                        # langchain BaseChatModel（如 ChatOpenAI），None=压缩降级
        embedding_provider=None,         # EmbeddingProvider，None=按环境自动选择
        chunk_size: int = 300,
        chunk_overlap: int = 50,
        ann_M: int = 32,
        ann_ef_construction: int = 200,
        ann_ef_search: int = 64,
        ann_overfetch: int = 50,
        auto_save: bool = True,
    ):
        # 所有记忆库都收在 data/memory/ 下（config.DATA_DIR 是运行期数据根目录）
        base = Path(base_dir) if base_dir else config.DATA_DIR / "memory"
        # 实例注入优先（如 get_memory() 复用 get_short_term() 单例，全程单连接）；
        # 未注入时按 base_dir 自建（原行为不变）
        self.short_term = short_term or ShortTermMemory(
            base / "short_term" / "short_term.sqlite",
            window_size=window_size,
            compressor=Compressor(llm=llm, compress_threshold=compress_threshold, keep_recent=keep_recent),
        )
        self.long_term = long_term or LongTermMemory(
            base / "long_term" / "long_term.sqlite",
            base / "long_term" / "memories.hnsw",
            provider=embedding_provider or pick_provider(),
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            M=ann_M,
            ef_construction=ann_ef_construction,
            ef_search=ann_ef_search,
            overfetch=ann_overfetch,
            auto_save=auto_save,
        )
        # 知识库 / 状态记忆 / 技能记忆：各自独立 SQLite，都落在 base（= data/memory）下，
        # 均可注入实例（复用连接）；未注入时按 base_dir 懒加载（见 state / skill 属性）
        self._base = base
        self.knowledge = knowledge or KnowledgeBase(base, llm=llm, chunk_size=chunk_size)
        self._state = state
        self._skill = skill
        self._interrupt = None

    @property
    def state(self):
        """状态记忆（死规则）懒加载单例（P5 提供）。"""
        if self._state is None:
            from .state.memory import StateMemory

            self._state = StateMemory(self._base)
        return self._state

    @property
    def skill(self):
        """技能记忆（事/的/痛/解）懒加载单例（P6 提供）。"""
        if self._skill is None:
            from .skill.memory import SkillMemory

            self._skill = SkillMemory(self._base)
        return self._skill

    @property
    def interrupt(self):
        """中断日志（被打断交互的可续跑台账）懒加载单例。"""
        if self._interrupt is None:
            from .interrupt.memory import InterruptLog

            self._interrupt = InterruptLog(self._base)
        return self._interrupt

    # ================= 谈话维度（分层总结管线） =================
    @property
    def registry(self):
        """谈话注册表：生命周期 + 一级总结归属 + 二级总结游标。"""
        return self.short_term.registry

    def start_conversation(self, session_id: str, user_id: str = "",
                           scope: str = "") -> None:
        """登记谈话开始（幂等）；scope 为业务线（research/listing/global，可选）。"""
        self.short_term.registry.start(session_id, user_id=user_id, scope=scope)

    def end_conversation(self, session_id: str, summary_json: str = "") -> None:
        """结束谈话；带总结时标记 summarized。"""
        self.short_term.registry.end(session_id, summary_json=summary_json)

    def record_turn(self, session_id: str, agent_name: str, user_id: str = "") -> None:
        """登记该谈话最近一轮由哪个智能体处理（supervisor 路由后调用）。"""
        self.short_term.record_turn(session_id, agent_name, user_id=user_id)

    def window_context(self, user_id: str = "", k: int | None = None,
                       token_guard: int | None = None) -> list[dict]:
        """短期窗口：最近 K 个谈话的总结+原文（token 守卫裁剪）。"""
        return self.short_term.window_context(user_id=user_id, k=k, token_guard=token_guard)

    # ================= 知识库（索引先行两阶段） =================
    def ingest_knowledge(self, user_id: str, title: str, text: str, caller=None) -> dict:
        """文档入库（写 L1）：分块 + 每块生成索引简述。"""
        return self.knowledge.ingest(user_id, title, text, caller=caller)

    def get_index(self, user_id: str | None = None, filter_keywords: list[str] | None = None,
                  limit: int = 50) -> list[dict]:
        """索引（简述/关键词），agent 先看索引再取正文。"""
        return self.knowledge.get_index(user_id=user_id, filter_keywords=filter_keywords, limit=limit)

    def fetch_knowledge(self, chunk_ids: list[int]) -> list[dict]:
        """按 chunk_id 取块正文。"""
        return self.knowledge.fetch_knowledge(chunk_ids)

    # ================= 状态记忆（死规则） =================
    def add_rule(self, rule_text: str, scope: str = "global", agent_types: str | None = None,
                 priority: int = 10, caller=None) -> int:
        """新增死规则（写 L3）。"""
        return self.state.add_rule(rule_text, scope=scope, agent_types=agent_types,
                                   priority=priority, caller=caller)

    def inject_rules(self, agent_type: str | None = None, user_id: str | None = None) -> str:
        """死规则注入块（每次上下文组装必调）。"""
        return self.state.inject_block(agent_type=agent_type, user_id=user_id)

    # ================= 技能记忆（事/的/痛/解 + 反馈闭环） =================
    def add_skill(self, situation: str, goal: str, pain: str, solution: str,
                  tags: list[str] | None = None, trigger: str = "", caller=None) -> int:
        """新增一条技能记忆（写 L2）。"""
        return self.skill.add_skill(situation, goal, pain, solution,
                                    tags=tags, trigger=trigger, caller=caller)

    def search_skills(self, text: str, top_k: int = 5,
                      include_archived: bool = False) -> list[dict]:
        """关键词/tags 检索技能（读 L0；向量检索接入位留 TODO）。"""
        return self.skill.search_skills(text, top_k=top_k, include_archived=include_archived)

    def skill_feedback(self, skill_id: int, success: bool) -> dict:
        """技能采用结果回报：更新 score，连续失败自动归档。"""
        return self.skill.feedback(skill_id, success)

    def hot_skills(self, top: int | None = None) -> list[dict]:
        """热技能（上下文预注入用），条数默认取 config.SKILL_HOT_INJECT_TOP。"""
        if top is None:
            top = config.SKILL_HOT_INJECT_TOP
        return self.skill.hot_skills(top=int(top))

    # ================= 中断日志（被打断交互的可续跑台账） =================
    def log_interrupt(self, session_id: str, **fields) -> int:
        """落一条中断记录：停在哪一步 / 已完成与被打断的工具 / 已产出的部分内容（写 L0）。"""
        return self.interrupt.log_interrupt(session_id, **fields)

    def interrupt_block(self, session_id: str) -> str:
        """续跑提示块（「上次未完成」）；无待续跑记录返回空串。"""
        return self.interrupt.inject_block(session_id)

    def latest_interrupt(self, session_id: str) -> dict | None:
        """该会话最近一条待续跑记录（open）。"""
        return self.interrupt.latest_open(session_id)

    def resolve_interrupt(self, session_id: str, note: str = "", caller=None) -> int:
        """本轮正常完成 → 把该会话待续跑记录标记为已续跑，返回条数。"""
        return self.interrupt.resolve_open(session_id, note=note, caller=caller)

    def list_interrupts(self, limit: int = 50, session_id: str = "",
                        status: str = "") -> list[dict]:
        """近期中断记录（倒序）——调试后台面板数据源。"""
        return self.interrupt.list_recent(limit=limit, session_id=session_id, status=status)

    # ================= LLM 输入接口（对话消息） =================
    @property
    def saver(self):
        """接入方挂载点：create_react_agent(..., checkpointer=mm.saver)。"""
        return self.short_term.saver

    def chat_config(self, session_id: str) -> dict:
        """graph.invoke 的配置：thread_id=会话ID（一会话一线程，万人隔离）。"""
        return self.short_term.chat_config(session_id)

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """记录一条对话消息（role: user/assistant/system）。"""
        self.short_term.add_message(session_id, role, content)

    def get_history(self, session_id: str, with_summary: bool = True) -> list[dict]:
        """短期上下文：[压缩摘要] + 窗口内消息，供 LLM 输入组装。"""
        return self.short_term.get_history(session_id, with_summary=with_summary)

    def maybe_compress(self, session_id: str) -> dict:
        """超过阈值触发压缩：LLM 滚动摘要 + 旧消息裁剪（无 LLM 自动降级）。"""
        return self.short_term.maybe_compress(session_id)

    # ================= 后端数据输入接口 =================
    def save_fact(self, user_id: str, key: str, value, source: str = "backend") -> None:
        """结构化事实（订单号/偏好/状态等）：精确 KV upsert，天然高准确率。"""
        self.long_term.save_fact(user_id, key, value, source=source)

    def get_facts(self, user_id: str) -> list[dict]:
        """取用户全部结构化事实 [{key, value, source, updated_at}]。"""
        return self.long_term.get_facts(user_id)

    def delete_fact(self, user_id: str, key: str) -> bool:
        """删除一条事实；key 不存在返回 False。"""
        return self.long_term.delete_fact(user_id, key)

    def add_document(self, user_id: str, text: str, title: str | None = None,
                     source: str = "backend") -> dict:
        """长文本/知识入库：自动分块 → 向量化 → ANN 索引。返回 {doc_id, chunks}。"""
        return self.long_term.add_document(user_id, text, title=title, source=source)

    # ================= RAG 检索 =================
    def recall(self, user_id: str, query: str, top_k: int = 5) -> list[dict]:
        """语义召回长期记忆：ANN 粗排（user 分区过滤）→ 精确 cosine 重排 → top-k。"""
        return self.long_term.recall(user_id, query, top_k=top_k)

    # ================= 组装与维护 =================
    def build_context(self, session_id: str, user_id: str | None = None,
                      query: str | None = None, top_k: int = 5) -> dict:
        """四段组装：summary / history / facts / recalled。user_id 缺省回落 session_id。"""
        uid = str(user_id or session_id)
        return {
            "summary": self.short_term.get_summary(session_id),
            "history": self.short_term.get_history(session_id, with_summary=False),
            "facts": self.long_term.get_facts(uid),
            "recalled": self.recall(uid, query, top_k=top_k) if query else [],
        }

    def clear(self, session_id: str | None = None, user_id: str | None = None) -> None:
        """清除记忆：session_id 清短期会话，user_id 清长期记忆；都不传则无操作。"""
        if session_id:
            self.short_term.clear(session_id)
        if user_id:
            self.long_term.clear(user_id)

    def close(self) -> None:
        """关闭短期 / 长期存储连接（auto_save 时长期索引先落盘）。"""
        self.short_term.close()
        self.long_term.close()
