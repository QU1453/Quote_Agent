# -*- coding: utf-8 -*-
"""记忆包（memory）：短期 + 长期记忆，LangGraph checkpointer + ANN-RAG + 记忆压缩。

职责边界：
- short_term/：多轮会话记忆，SqliteSaver 按 thread_id（= "session:{session_id}"）隔离，
  SQLite 落盘持久化（重启不丢），超阈值自动压缩（LLM 滚动摘要，无 Key 降级纯裁剪）；
- long_term/：长期记忆，facts 精确 KV + documents/chunks 分块 + hnswlib ANN 语义召回；
- manager.py：MemoryManager 统一入口（LLM 输入接口 / 后端数据输入接口 / build_context）。

兼容层（保持 agents/ 原有用法不变）：
- get_short_term(llm=None)：进程级单例短期记忆（含压缩器，llm 优先由接入方注入）；
- get_checkpointer()：进程级单例 checkpointer（由 MemorySaver 升级为 SQLite 持久化 SqliteSaver）；
- agent_session_id(agent, session_id)：复合会话 ID 唯一出口（"customer_service:sid"）；
- thread_id_for(session_id)：会话 ID → 线程命名空间（"session:{id}"）。

会话 ID 约定（唯一出口，禁止手拼）：
  agent_session_id("customer_service", sid)  → "customer_service:sid"         # agent 维度隔离
  thread_id_for(会话ID)                       → "session:customer_service:sid"  # checkpoint 线程
  MemoryManager.add_message/get_history/chat_config 直接传 agent_session_id(...) 的结果。

快速上手：

    from memory import MemoryManager
    mm = MemoryManager()                                  # 数据落 ./short_term/data 与 ./long_term/data
    mm.add_message("s1", "user", "你好")                   # LLM 输入接口（对话消息）
    mm.save_fact("u1", "last_order_no", "2026081200012")   # 后端数据输入接口（结构化事实）
    mm.add_document("u1", "退货政策：……")                   # 后端数据输入接口（长文本→分块→ANN）
    hits = mm.recall("u1", "退货流程")                      # ANN 速度优先 + 精确重排
    ctx = mm.build_context("s1", "u1", query="退货流程")     # summary/history/facts/recalled 四段组装

一键演示：python -m memory.demo
"""
from __future__ import annotations

import os

import config

# ---- 长期记忆为可选依赖：hnswlib 在 Windows 无预编译包，装不上时降级为"仅短期记忆" ----
# 短期记忆（SqliteSaver + 压缩）不依赖 hnswlib，agents/ 的核心链路不受影响；
# 需要长期记忆 / RAG 时：安装 VC++ Build Tools 后 pip install hnswlib，或改用 Linux / Docker。
try:
    from .long_term.ann import AnnIndex
    from .long_term.chunker import chunk_text
    from .long_term.rag import (
        EmbeddingProvider,
        HashEmbeddingProvider,
        OpenAIEmbeddingProvider,
    )
    from .manager import MemoryManager

    HAS_LONG_TERM = True  # hnswlib 可用，长期记忆功能完整
except ImportError as _lt_exc:  # pragma: no cover - Windows 无 C++ 编译环境时
    AnnIndex = None
    chunk_text = None
    EmbeddingProvider = None
    HashEmbeddingProvider = None
    OpenAIEmbeddingProvider = None
    MemoryManager = None
    HAS_LONG_TERM = False
    _LONG_TERM_HINT = (
        f"长期记忆不可用（导入失败：{_lt_exc}）。"
        "短期记忆不受影响；如需长期记忆 / RAG，请安装 Visual C++ Build Tools 后 "
        "pip install hnswlib，或改用 Linux / Docker 环境。"
    )
from .short_term import agent_session_id, thread_id_for
from .short_term.compress import Compressor

__all__ = [
    "MemoryManager",
    "Compressor",
    "AnnIndex",
    "chunk_text",
    "EmbeddingProvider",
    "HashEmbeddingProvider",
    "OpenAIEmbeddingProvider",
    "get_checkpointer",
    "get_short_term",
    "get_memory",
    "thread_id_for",
    "agent_session_id",
    "HAS_LONG_TERM",
]

_compat_stm = None  # 兼容层懒加载单例（只建短期记忆，长期记忆按需另建 MemoryManager）


def _env_llm():
    """未注入 llm 时按环境变量自建（OpenAI 兼容）；无 Key / 缺依赖 / 失败 → None（纯裁剪降级）。"""
    if not os.getenv("OPENAI_API_KEY", "").strip():
        return None
    try:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=os.getenv("OPENAI_MODEL", "glm-5.3-flash").strip() or "glm-5.3-flash",
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL") or None,
            temperature=0.3,
        )
    except Exception:
        return None


def get_short_term(llm=None):
    """进程级单例短期记忆（含压缩器）；llm 优先由接入方注入，首次调用定型。

    server.py 先 import config（已 load_dotenv）再 import agents/memory，
    因此服务路径下环境变量必然就绪；脱离 server 单独使用且无环境变量时
    退化为纯裁剪模式（与无 Key 行为一致）。
    """
    global _compat_stm
    if _compat_stm is None:
        from .short_term.compress import Compressor
        from .short_term.memory import ShortTermMemory

        # 数据目录走 config.DATA_DIR（不能用 __file__：打包后会落到解包临时目录，重启即丢）
        _compat_stm = ShortTermMemory(
            config.DATA_DIR / "memory" / "short_term" / "short_term.sqlite",
            compressor=Compressor(llm=llm or _env_llm()),
        )
    return _compat_stm


def get_checkpointer():
    """兼容旧 API：进程级单例 checkpointer（现升级为 SQLite 持久化的 SqliteSaver）。"""
    return get_short_term().saver


_memory = None  # 进程级 MemoryManager 单例（推理侧读长期记忆用）


def get_memory(llm=None, embedding_provider=None) -> MemoryManager | None:
    """进程级 MemoryManager 单例：推理侧经它读取长期记忆（facts / recall）。

    短期记忆与 get_short_term() 同源单例（同 saver/同连接，全程单连接）；
    长期嵌入按环境选择：有 OPENAI_API_KEY → OpenAI 兼容，否则 Hash 降级。
    hnswlib 不可用（HAS_LONG_TERM=False）时返回 None，调用方按无长期记忆处理。
    """
    global _memory
    if _memory is None:
        if MemoryManager is None:  # hnswlib 不可用：长期记忆降级，推理侧跳过注入
            return None
        from .long_term.rag import pick_provider

        _memory = MemoryManager(
            short_term=get_short_term(llm=llm),
            embedding_provider=embedding_provider or pick_provider(),
        )
    return _memory
