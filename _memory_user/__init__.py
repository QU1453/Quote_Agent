# -*- coding: utf-8 -*-
"""Quote Agent 记忆模块（Memory）：短期 + 长期 + ANN-RAG + 记忆压缩。

独立模块，可整体搬移。快速上手：

    from Memory import MemoryManager
    mm = MemoryManager()                                  # 数据落 ./short_term/data 与 ./long_term/data
    mm.add_message("s1", "user", "你好")                   # LLM 输入接口（对话消息）
    mm.save_fact("u1", "last_order_no", "2026081200012")   # 后端数据输入接口（结构化事实）
    mm.add_document("u1", "退货政策：……")                   # 后端数据输入接口（长文本→分块→ANN）
    hits = mm.recall("u1", "退货流程")                      # ANN 速度优先 + 精确重排
    ctx = mm.build_context("s1", "u1", query="退货流程")     # summary/history/facts/recalled 四段组装

接入 LangGraph Agent（不改本模块）：

    graph = create_react_agent(..., checkpointer=mm.saver)
    graph.invoke({"messages": [...]}, mm.chat_config(session_id))
    mm.maybe_compress(session_id)                          # 超阈值触发滚动摘要压缩
"""
from .long_term.ann import AnnIndex
from .long_term.chunker import chunk_text
from .long_term.rag import (
    EmbeddingProvider,
    HashEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from .manager import MemoryManager
from .short_term.compress import Compressor

__all__ = [
    "MemoryManager",
    "Compressor",
    "AnnIndex",
    "chunk_text",
    "EmbeddingProvider",
    "HashEmbeddingProvider",
    "OpenAIEmbeddingProvider",
]
