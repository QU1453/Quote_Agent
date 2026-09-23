# -*- coding: utf-8 -*-
"""工具层-MCP client：协议封装 / 连接管理 / 重试（最多 5 次）/ 超时。

职责（用户规范）：
- 协议封装：把「工具 + 参数」封装成 JSON-RPC 2.0 请求（initialize → tools/list → tools/call）；
- 连接管理：stdio 传输下负责拉起独立 server 进程并复用连接，进程死掉自动判为传输故障；
- 重试与超时：传输类故障（超时 / 断连 / 协议帧异常）最多重试 max_retries 次（默认 5）后退出；
  业务级错误（JSON-RPC error、isError=true）不重试，直接作为结果返回给模型。

两种传输：
- InProcessTransport：同进程直连 MCPServer（默认；业务工具依赖进程内演示数据）；
- StdioTransport：拉起 `python -m core.toolkit.server` 独立进程，按行 JSON-RPC over stdio。
"""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod

import config

from .types import AgentConfig, Status, ToolResult

__all__ = ["TransportError", "InProcessTransport", "StdioTransport", "MCPClient"]


class TransportError(Exception):
    """传输层故障（可重试）；kind 用于区分超时与一般断连。"""

    def __init__(self, message: str, kind: str = "transport"):
        super().__init__(message)
        self.kind = kind  # timeout / transport


class Transport(ABC):
    """传输抽象：request 发出 JSON-RPC 请求并取回响应；notify 发通知（无响应）。"""

    @abstractmethod
    def request(self, method: str, params: dict, timeout_s: float, req_id: int) -> dict:
        raise NotImplementedError

    def notify(self, method: str, params: dict) -> None:  # pragma: no cover - 默认无操作
        return None

    def close(self) -> None:  # pragma: no cover - 默认无操作
        return None


class InProcessTransport(Transport):
    """同进程直连：请求交给 MCPServer.handle，零进程/零序列化开销。"""

    def __init__(self, server):
        self._server = server

    def request(self, method: str, params: dict, timeout_s: float, req_id: int) -> dict:
        req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        resp = self._server.handle(req)
        if resp is None:
            raise TransportError(f"server 未返回响应：{method}")
        return resp

    def notify(self, method: str, params: dict) -> None:
        try:
            self._server.handle({"jsonrpc": "2.0", "method": method, "params": params})
        except Exception:  # noqa: BLE001 - 通知失败不影响主链路
            pass


class StdioTransport(Transport):
    """独立 server 进程：JSON-RPC 2.0 over stdio（每行一个 JSON 对象）。"""

    def __init__(self, cmd: list[str] | None = None, cwd=None, env: dict | None = None):
        self._cmd = cmd or [sys.executable, "-m", "core.toolkit.server"]
        self._cwd = str(cwd or config.BASE_DIR)
        self._env = env
        self._proc: subprocess.Popen | None = None
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()

    def start(self) -> None:
        """拉起 server 进程（幂等）并启动 stdout 读取线程。"""
        if self._proc is not None and self._proc.poll() is None:
            return
        try:
            self._proc = subprocess.Popen(
                self._cmd, cwd=self._cwd, env=self._env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
        except Exception as exc:  # noqa: BLE001
            raise TransportError(f"无法启动 MCP server 进程：{exc}") from exc
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        try:
            for line in self._proc.stdout:  # type: ignore[union-attr]
                self._queue.put(line)
        except Exception:  # noqa: BLE001 - 进程退出时读取结束
            pass

    def request(self, method: str, params: dict, timeout_s: float, req_id: int) -> dict:
        with self._lock:  # 单请求串行，简化 id 配对
            self.start()
            payload = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method,
                                  "params": params}, ensure_ascii=False)
            try:
                self._proc.stdin.write(payload + "\n")  # type: ignore[union-attr]
                self._proc.stdin.flush()  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001
                raise TransportError(f"写入 server 失败：{exc}") from exc

            deadline = time.monotonic() + max(float(timeout_s), 0.1)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"MCP server 响应超时（{timeout_s}s）")
                try:
                    line = self._queue.get(timeout=remaining)
                except queue.Empty:
                    raise TimeoutError(f"MCP server 响应超时（{timeout_s}s）") from None
                line = (line or "").strip()
                if not line:
                    continue
                if self._proc.poll() is not None:
                    raise TransportError("MCP server 进程已退出")
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 非 JSON 输出（日志等）跳过
                if resp.get("id") != req_id:
                    continue  # 通知/串扰行跳过
                return resp

    def notify(self, method: str, params: dict) -> None:
        if self._proc is None or self._proc.poll() is not None:
            return
        try:
            payload = json.dumps({"jsonrpc": "2.0", "method": method, "params": params},
                                 ensure_ascii=False)
            self._proc.stdin.write(payload + "\n")  # type: ignore[union-attr]
            self._proc.stdin.flush()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


class MCPClient:
    """MCP 客户端：握手 + 工具发现 + 带重试/超时的工具调用。"""

    def __init__(self, transport: Transport, *, max_retries: int | None = None,
                 protocol_version: str | None = None,
                 client_name: str = "quote-agent", client_version: str = "1.0.0"):
        self._transport = transport
        self.max_attempts = max(1, int(max_retries if max_retries is not None
                                      else config.TOOL_CALL_MAX_RETRIES))
        self._protocol_version = protocol_version or config.MCP_PROTOCOL_VERSION
        self._client_name = client_name
        self._client_version = client_version
        self._id = 0
        self._id_lock = threading.Lock()
        self._initialized = False
        self.server_info: dict = {}

    # ---- JSON-RPC 基础：id 分配 + 带重试的请求 ----------------------------------------
    def _next_id(self) -> int:
        with self._id_lock:
            self._id += 1
            return self._id

    def _request(self, method: str, params: dict, timeout_s: float) -> dict:
        """传输类故障重试到上限（含首次共 max_attempts 次）后退出；协议错误响应不重试。"""
        last: Exception | None = None
        kind = "transport"
        for _ in range(self.max_attempts):
            try:
                return self._transport.request(method, params, timeout_s, self._next_id())
            except TimeoutError as exc:
                last, kind = exc, "timeout"
            except (TransportError, OSError) as exc:
                last, kind = exc, getattr(exc, "kind", "transport")
        raise TransportError(
            f"重试 {self.max_attempts} 次仍失败：{last}", kind=kind
        )

    # ---- initialize：协议版本与能力协商 ------------------------------------------------
    def initialize(self, timeout_s: float | None = None) -> dict:
        resp = self._request("initialize", {
            "protocolVersion": self._protocol_version,
            "capabilities": {"tools": {}},
            "clientInfo": {"name": self._client_name, "version": self._client_version},
        }, timeout_s or config.TOOL_CALL_TIMEOUT_S)
        result = resp.get("result") or {}
        self.server_info = result.get("serverInfo") or {}
        self._initialized = True
        self._transport.notify("notifications/initialized", {})
        return result

    # ---- tools/list：运行时动态发现工具 -------------------------------------------------
    def list_tools(self, timeout_s: float | None = None) -> list[dict]:
        if not self._initialized:
            self.initialize(timeout_s)
        resp = self._request("tools/list", {}, timeout_s or config.TOOL_CALL_TIMEOUT_S)
        return (resp.get("result") or {}).get("tools") or []

    # ---- tools/call：调用工具，回收 结果数据 + 状态码 + 元信息 ---------------------------
    def call_tool(self, name: str, args: dict, cfg: AgentConfig | None = None) -> ToolResult:
        cfg = cfg or AgentConfig()
        try:
            if not self._initialized:
                self.initialize(cfg.timeout_s)
            resp = self._request("tools/call", {
                "name": name,
                "arguments": args or {},
                "_meta": {"agentConfig": {
                    "role": cfg.role,
                    "round_index": cfg.round_index,
                    "planning_rounds": cfg.planning_rounds,
                    "read_only_mode": cfg.read_only_mode,
                    "session_id": cfg.session_id,
                    "timeout_s": cfg.timeout_s,
                    "max_retries": cfg.max_retries,
                }},
            }, cfg.timeout_s)
        except TransportError as exc:
            status = Status.TIMEOUT if exc.kind == "timeout" else Status.TRANSPORT_ERROR
            return ToolResult(False, f"MCP 调用失败（已重试至上限 {self.max_attempts} 次退出）：{exc}",
                              status, {"tool": name, "attempts": self.max_attempts,
                                       "kind": exc.kind})
        except Exception as exc:  # noqa: BLE001 - 传输层之外的意外也转给模型
            return ToolResult(False, f"MCP 调用异常（{exc.__class__.__name__}）：{exc}",
                              Status.TRANSPORT_ERROR, {"tool": name})

        if "error" in resp:
            err = resp.get("error") or {}
            return ToolResult(False, f"协议错误[{err.get('code')}]：{err.get('message', '')}",
                              Status.PROTOCOL_ERROR, {"tool": name})
        result = resp.get("result") or {}
        content = result.get("content") or []
        text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict)
                         and c.get("type") == "text")
        is_error = bool(result.get("isError"))
        meta = result.get("_meta") or {}
        status = meta.get("status") or (Status.OK if not is_error else Status.INTERNAL_ERROR)
        return ToolResult(not is_error, text, status, dict(meta))

    def close(self) -> None:
        self._transport.close()
