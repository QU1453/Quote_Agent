# -*- coding: utf-8 -*-
"""遥测模块（core/telemetry）：智能体运行观测，调试后台（/debug）的数据层。

字段口径对齐 OpenInference 语义约定 + 本项目特有维度（route / llm_mode / 约束拦截）。
旁路容错（fail-open）：写入查询均吞异常，遥测故障绝不影响问答主链路。

两个数据层，职责不同：
- recorder：**事后**落库（SQLite），给调试后台做历史统计与排查；
- live：**事中**内存总线，给聊天框展示「思考过程小字」并承载「打断」信号。

埋点方：
- core/dispatch/orchestrator.py：trace 骨架（各阶段 stage 事件 + 约束拦截 + token）
- agents/base.py：AIMessage usage_metadata / tool_calls 提取（LLM 模式真实 token）
- core/dispatch/registry.py：GLOBAL_REGISTRY.call 工具事件（跨模块/约束层工具）
"""
from .live import (
    STATUS_DONE,
    STATUS_ERROR,
    STATUS_INTERRUPTED,
    STATUS_RUNNING,
    LiveBus,
    current_cancelled,
    get_current_request,
    get_live_bus,
    set_current_request,
)
from .recorder import (
    TelemetryRecorder,
    get_current_trace,
    get_recorder,
    set_current_trace,
)

__all__ = [
    "TelemetryRecorder", "get_recorder", "get_current_trace", "set_current_trace",
    "LiveBus", "get_live_bus",
    "STATUS_RUNNING", "STATUS_DONE", "STATUS_INTERRUPTED", "STATUS_ERROR",
    "set_current_request", "get_current_request", "current_cancelled",
]
