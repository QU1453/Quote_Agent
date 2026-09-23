# -*- coding: utf-8 -*-
"""对话运行态总线（LiveBus）：一轮问答的实时思考流 + 打断信号。

一条总线同时服务两个前端特性：

1. **思考过程小字**：agent 每推进一步（意图 / 上下文 / 决策 / 工具调用 / 工具返回）都往这里
   追加一条 step；前端在等待回复期间轮询 `GET /api/think/{request_id}` 增量展示。
   与 core/telemetry 的区别：遥测是**事后**落库给调试后台看，总线是**事中**给用户看。

2. **打断交互**：前端点「打断」→ `POST /api/ask/interrupt` 置 cancel 标记；agent 在每个检查点
   （下一次模型调用前 / 工具执行前 / 工具返回后）查这个标记，命中即停止后续步骤，
   并把「停在哪一步、哪个工具是被用户打断的」写进中断日志（memory/interrupt/）。

关键设计：
- 纯内存、按 `request_id` 隔离、线程安全（/api/ask 在工作线程跑，打断请求在另一个线程到达）；
- **旁路容错（fail-open）**：任何异常都不影响问答主链路；request_id 为空时全部方法空转（零开销）；
- 有界：单请求最多保留 `max_steps` 条 step，超过 TTL 的请求自动回收，防内存膨胀。
"""
from __future__ import annotations

import contextvars
import threading
import time
from typing import Any

__all__ = ["LiveBus", "get_live_bus", "STATUS_RUNNING", "STATUS_DONE",
           "STATUS_INTERRUPTED", "STATUS_ERROR",
           "set_current_request", "get_current_request", "current_cancelled"]

# 运行态
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_INTERRUPTED = "interrupted"
STATUS_ERROR = "error"

# 单请求保留的最大 step 数（超出丢最旧，保证头部阶段信息可见）
_MAX_STEPS = 40
# 单条 detail 截断（前端是一行小字，过长无意义）
_MAX_DETAIL = 160
# 运行态保留时长（秒）：超时自动回收，防内存膨胀
_TTL_SECONDS = 1800


def _cut(text: Any, limit: int = _MAX_DETAIL) -> str:
    """任意值 → 单行截断字符串（前端小字展示口径）。"""
    s = "" if text is None else str(text).replace("\n", " ").strip()
    return s[:limit]


class LiveBus:
    """运行态与打断信号的进程内总线；所有方法 fail-open（异常不外抛）。"""

    def __init__(self, max_steps: int = _MAX_STEPS, ttl_seconds: int = _TTL_SECONDS):
        self._lock = threading.Lock()
        self._runs: dict[str, dict] = {}
        self._max_steps = max(5, int(max_steps))
        self._ttl = max(60, int(ttl_seconds))

    # ---------------- 生命周期 ----------------
    def begin(self, request_id: str, session_id: str = "", route: str = "") -> None:
        """开一次运行态（编排器进入 answer() 时调用）。"""
        if not request_id:
            return
        try:
            now = time.time()
            with self._lock:
                self._runs[str(request_id)] = {
                    "request_id": str(request_id),
                    "session_id": str(session_id or ""),
                    "route": str(route or ""),
                    "status": STATUS_RUNNING,
                    "detail": "",
                    "started_at": now,
                    "started_ts": time.strftime("%H:%M:%S", time.localtime(now)),
                    "ended_at": None,
                    "elapsed_ms": 0,
                    "cancel": False,
                    "cancel_reason": "",
                    "seq": 0,
                    "steps": [],
                }
        except Exception:  # noqa: BLE001
            pass

    def add(self, request_id: str, kind: str, name: str, detail: str = "",
            status: str = "ok", duration_ms: int | None = None) -> None:
        """追加一条思考步骤。无 request_id / 未知请求时静默空转（零开销）。"""
        if not request_id:
            return
        try:
            with self._lock:
                run = self._runs.get(str(request_id))
                if run is None:
                    return
                run["seq"] += 1
                ts = time.strftime("%H:%M:%S")
                run["steps"].append({
                    "seq": run["seq"], "ts": ts,
                    "kind": str(kind or "stage"), "name": str(name or ""),
                    "detail": _cut(detail), "status": str(status or "ok"),
                    "duration_ms": duration_ms,
                })
                if len(run["steps"]) > self._max_steps:
                    run["steps"] = run["steps"][-self._max_steps:]
        except Exception:  # noqa: BLE001
            pass

    def finish(self, request_id: str, status: str = STATUS_DONE, detail: str = "") -> None:
        """结束运行态（编排器返回前调用）；此后 snapshot 仍可读，便于前端拿最终状态。"""
        if not request_id:
            return
        try:
            now = time.time()
            with self._lock:
                run = self._runs.get(str(request_id))
                if run is None:
                    return
                run["status"] = str(status or STATUS_DONE)
                if detail:
                    run["detail"] = _cut(detail)
                run["ended_at"] = now
                run["elapsed_ms"] = int((now - float(run.get("started_at") or now)) * 1000)
        except Exception:  # noqa: BLE001
            pass

    def finish_all_running(self, session_id: str, status: str = STATUS_DONE,
                           detail: str = "") -> int:
        """兜底收尾：把该会话所有仍在 running 的运行态标记结束（防前端永远转圈）。"""
        if not session_id:
            return 0
        try:
            now = time.time()
            n = 0
            with self._lock:
                for run in self._runs.values():
                    if run.get("status") != STATUS_RUNNING:
                        continue
                    if str(run.get("session_id") or "") != str(session_id):
                        continue
                    run["status"] = status
                    if detail:
                        run["detail"] = _cut(detail)
                    run["ended_at"] = now
                    run["elapsed_ms"] = int((now - float(run.get("started_at") or now)) * 1000)
                    n += 1
            return n
        except Exception:  # noqa: BLE001
            return 0

    # ---------------- 打断信号（协作式取消） ----------------
    def request_cancel(self, request_id: str = "", session_id: str = "",
                       reason: str = "user") -> dict:
        """请求打断：置 cancel 标记。给 session_id 时命中该会话所有在跑的运行态。

        只是**置标记**——真正的停止由 agent 在下一次检查点完成
        （单次 LLM 请求发出后无法从外部掐断，这是物理限制）。
        """
        hit: list[str] = []
        try:
            with self._lock:
                for rid, run in self._runs.items():
                    if request_id and rid != str(request_id):
                        continue
                    if session_id and str(run.get("session_id") or "") != str(session_id):
                        continue
                    if request_id or session_id:
                        if run.get("status") != STATUS_RUNNING:
                            continue
                        run["cancel"] = True
                        run["cancel_reason"] = str(reason or "user")
                        hit.append(rid)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "cancelled": hit, "count": len(hit)}

    def is_cancelled(self, request_id: str) -> bool:
        """检查点询问：本轮是否已被请求打断。"""
        if not request_id:
            return False
        try:
            with self._lock:
                run = self._runs.get(str(request_id))
                return bool(run and run.get("cancel"))
        except Exception:  # noqa: BLE001
            return False

    def cancel_reason(self, request_id: str) -> str:
        """打断来源（user / system）；未打断返回空串。"""
        if not request_id:
            return ""
        try:
            with self._lock:
                run = self._runs.get(str(request_id))
                return str(run.get("cancel_reason") or "") if run else ""
        except Exception:  # noqa: BLE001
            return ""

    # ---------------- 查询 ----------------
    def snapshot(self, request_id: str) -> dict | None:
        """取某次运行态快照（前端轮询用）；不存在返回 None。"""
        if not request_id:
            return None
        try:
            with self._lock:
                run = self._runs.get(str(request_id))
                if run is None:
                    return None
                out = dict(run)
                out["steps"] = [dict(s) for s in run.get("steps", [])]
                out["cancel"] = bool(run.get("cancel"))
                return out
        except Exception:  # noqa: BLE001
            return None

    def prune(self) -> int:
        """回收超时运行态；返回回收条数。"""
        try:
            deadline = time.time() - self._ttl
            with self._lock:
                stale = [rid for rid, run in self._runs.items()
                         if float(run.get("ended_at") or run.get("started_at") or 0) < deadline]
                for rid in stale:
                    self._runs.pop(rid, None)
                return len(stale)
        except Exception:  # noqa: BLE001
            return 0

    def clear(self) -> None:
        """清空全部运行态（调试期维护）。"""
        try:
            with self._lock:
                self._runs.clear()
        except Exception:  # noqa: BLE001
            pass

    def stats(self) -> dict:
        """概览：在跑 / 已结束 / 打断计数（调试用）。"""
        try:
            with self._lock:
                runs = list(self._runs.values())
            return {
                "total": len(runs),
                "running": sum(1 for r in runs if r.get("status") == STATUS_RUNNING),
                "interrupted": sum(1 for r in runs if r.get("status") == STATUS_INTERRUPTED),
                "cancel_requested": sum(1 for r in runs if r.get("cancel")),
            }
        except Exception:  # noqa: BLE001
            return {"total": 0, "running": 0, "interrupted": 0, "cancel_requested": 0}


# 进程级单例（与 get_recorder / get_constraint_layer 同模式）
_BUS: LiveBus | None = None


def get_live_bus() -> LiveBus:
    """运行态总线单例入口（orchestrator / agent / server 统一从这里取）。"""
    global _BUS
    if _BUS is None:
        try:
            import config

            _BUS = LiveBus(max_steps=config.LIVE_THINK_MAX_STEPS,
                           ttl_seconds=config.LIVE_THINK_TTL_SECONDS)
        except Exception:  # noqa: BLE001 - 配置不可用时退回默认值
            _BUS = LiveBus()
    return _BUS


# ---- 当前上下文活跃的运行态（工具短路判定用）----------------------------------------
# 工具包装器（agents/base._guard_tool）拿不到 request_id，靠这个上下文变量判断
# 「本轮是否已被请求打断」；orchestrator 在 answer() 开头 set、结尾 clear。
#
# 【必须用 ContextVar，不能用 threading.local】LangGraph 会把工具丢到线程池里执行
# （实测线程名形如 ThreadPoolExecutor-2_0），thread-local 跨不过去 → 短路判定永远为假；
# ContextVar 随运行上下文传播，工具线程里能读到正确的 request_id（已实测验证）。
_current_request: contextvars.ContextVar[str] = contextvars.ContextVar(
    "live_current_request", default="")


def set_current_request(request_id: str) -> None:
    """登记当前上下文活跃的 request_id（orchestrator 一轮问答开始时调用）。"""
    _current_request.set(str(request_id or ""))


def get_current_request() -> str:
    """取当前上下文活跃的 request_id（无则空串）。"""
    return _current_request.get() or ""


def current_cancelled() -> bool:
    """当前这一轮是否已被请求打断（工具据此短路，不产生任何副作用）。"""
    rid = get_current_request()
    return bool(rid) and get_live_bus().is_cancelled(rid)
