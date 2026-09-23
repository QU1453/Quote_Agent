# -*- coding: utf-8 -*-
"""Quote Agent 后端（FastAPI）。

职责：
1. 启动本地 Web 服务，同一端口托管 `ui/` 静态页面（浏览器打开即可用）；
2. 暴露 `POST /api/ask`：把网页里输入的问题交给 LangGraph Agent，
   返回 {reply, intent, data}，前端据此渲染气泡与卡片。

运行：
    python server.py          # 默认 http://127.0.0.1:8623
    uvicorn server:app --host 127.0.0.1 --port 8623

密钥：配置统一走 config.py（智能体配置槽），真实 Key 放 .env / 环境变量，
本文件不写入、不打印任何密钥。
"""
from __future__ import annotations

import hashlib
import os

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import config
from agents import Supervisor
from core.dispatch.orchestrator import Orchestrator
from core.perception.session import start_conversation
from core.telemetry import get_live_bus, get_recorder
from memory import agent_session_id, get_memory, get_short_term

UI_DIR = config.RESOURCE_DIR / "ui"   # 打包后指向解包目录，开发态即 ./ui
HOST, PORT = config.HOST, config.PORT

app = FastAPI(title="Quote Agent", version="0.1.0")

# 本地演示：允许静态预览（python -m http.server 另起端口）跨域调用后端
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 同一端口托管前端页面：/ui 前缀与根路径均可访问
app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


class AskRequest(BaseModel):
    message: str
    session_id: str = "default"
    user_id: str = "default"
    # 运行态标识（前端生成）：给了就能实时看「思考过程」，并支持「打断」；留空走旧行为
    request_id: str = ""
    # 二级归属（板块对话窗口调用时带上）：仅用于给「尚未归属」的会话做首次回填，
    # 已有归属的会话不会被改写（会话归属固定为创建时所在的板块）
    project_id: str = ""
    feature_key: str = ""


class InterruptRequest(BaseModel):
    """打断请求：request_id 精确打断某轮；只给 session_id 则打断该会话所有在跑的轮次。"""

    request_id: str = ""
    session_id: str = ""
    reason: str = "user"


class ClearRequest(BaseModel):
    session_id: str = "default"


class EndConversationRequest(BaseModel):
    session_id: str
    user_id: str = "default"


class StartConversationRequest(BaseModel):
    user_id: str = "default"
    scope: str = ""        # 区块：research=选品 / listing=上架 / global（空=未指定）
    project_id: str = ""   # 所属项目（空=未分类）
    feature_key: str = ""  # 细分功能板块 key（如 demand / competition，空=未分类）


class ProjectCreateRequest(BaseModel):
    """新建项目：name 必填（空则 400），desc 可选。"""

    user_id: str = "default"
    name: str = ""
    desc: str = ""


class ProjectPatchRequest(BaseModel):
    """项目改名 / 改描述 / 归档：字段都可选，只提交要改的（归档 ≠ 删除）。"""

    name: str | None = None
    desc: str | None = None
    status: str | None = None   # open | archived


class GuideRequest(BaseModel):
    """指导智能体（右下角悬浮窗）请求：只读导航问答，不写任何记忆。"""

    message: str
    session_id: str = ""
    scope: str = ""        # 区块：research / listing / global
    user_id: str = "default"
    project_id: str = ""   # 当前所在项目（空 = 在总控台）
    feature_key: str = ""  # 当前所在细分板块


class ConversationPatchRequest(BaseModel):
    """会话改名 / 隐藏 / 移动归属：字段都可选，只传要改的（隐藏≠删除、移动≠重建）。"""

    title: str | None = None
    hidden: bool | None = None
    # 「移动到其他板块」：三个一起给才能保证区块标签与板块 key 一致（后端不反推）
    project_id: str | None = None
    feature_key: str | None = None
    scope: str | None = None


class SettingsRequest(BaseModel):
    """运行时设置：只提交要改的字段（None/空串 = 保持不变）。"""

    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    temperature: float | None = None           # 采样温度 0~2
    permission_mode: str | None = None         # plan / ask / accept / bypass
    call_token_budget: int | None = None       # 单次请求 token 预算
    cost_budget: float | None = None           # 单会话累计金额上限
    cost_currency: str | None = None           # CNY / USD
    persist: bool = False   # True = 同时写入本机 .env（重启后仍生效；.env 不入库不进镜像）


class TestSettingsRequest(BaseModel):
    """连接测试：优先测表单里当前填的值，未填则回落到已保存配置。

    —— 这样「先填 Key 再点测试」就能立刻验证，不必先保存。
    """

    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


PERMISSION_MODES = ("plan", "ask", "accept", "bypass")
CURRENCIES = ("CNY", "USD")
PROJECT_STATUSES = ("open", "archived")   # 项目状态：归档 ≠ 删除（只改状态位）


def mask_key(key: str | None) -> str:
    """密钥脱敏展示：只留首 4 / 末 4 位，绝不回传完整密钥。"""
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}{'•' * 6}{key[-4:]}"


def config_fingerprint(api_key: str | None, base_url: str | None, model: str | None) -> str:
    """LLM 配置指纹：用于判断上一次连接测试结果是否仍适用于当前配置。"""
    raw = f"{api_key or ''}|{base_url or ''}|{model or ''}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


# 业务线（scope）→ 中文名：会话清单标题兜底与前端动作按钮展示共用
SCOPE_CN = {"research": "选品", "listing": "Listing", "global": "全局"}


def conversation_title(row: dict) -> str:
    """谈话标题：为空时用 scope/agent_name 拼一个可读默认值（不留空串给前端）。"""
    title = str(row.get("title") or "").strip()
    if title:
        return title
    scope = str(row.get("scope") or "").strip().lower()
    label = SCOPE_CN.get(scope) or scope or str(row.get("agent_name") or "").strip()
    return f"(未命名谈话·{label})" if label else "(未命名谈话)"


def persist_env(pairs: dict[str, str]) -> None:
    """把设置写回本机 .env（逐行 upsert，保留其它配置；.env 已被 git/docker 双重拦截）。

    匹配时会忽略行首的「#」，所以像 `# GLM_API=` 这样的注释占位行会被就地替换，
    不会重复追加同名的键。

    路径取 config.BASE_DIR（打包后是 exe 所在目录），确保设置写在程序旁边、
    不会随单文件解包的临时目录一起被清掉。
    """
    path = config.BASE_DIR / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        bare = line.lstrip().lstrip("#").strip()
        key = bare.split("=", 1)[0].strip() if "=" in bare else ""
        if key in pairs:
            out.append(f"{key}={pairs[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in pairs.items():
        if k not in seen:
            out.append(f"{k}={v}")
    path.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")


class AgentService:
    """懒加载单例 Supervisor（多智能体主控）+ Orchestrator（认知层编排）。

    多轮记忆由 memory/ 的 checkpointer 托管；/api/ask 走编排器流水线。
    """

    def __init__(self) -> None:
        self._supervisor: Supervisor | None = None
        self._orchestrator: Orchestrator | None = None
        # 最近一次连接测试结果（含配置指纹；指纹不匹配即视为未验证）
        self._verify: dict | None = None

    def supervisor(self) -> Supervisor:
        if self._supervisor is None:
            self._supervisor = Supervisor(
                api_key=config.API_KEY, base_url=config.BASE_URL, model=config.MODEL_ID
            )
        return self._supervisor

    def orchestrator(self) -> Orchestrator:
        if self._orchestrator is None:
            self._orchestrator = Orchestrator(self.supervisor())
        return self._orchestrator

    def reload(self) -> None:
        """丢弃已构建的 Supervisor / Orchestrator，下次请求按新配置重建（密钥热更新）。"""
        self._supervisor = None
        self._orchestrator = None

    def record_verification(self, ok: bool, error: str | None,
                            fingerprint: str) -> None:
        """记录一次连接测试结果（带配置指纹）。"""
        self._verify = {"ok": bool(ok), "error": error, "fp": fingerprint}

    def verification(self) -> tuple[bool | None, str | None]:
        """当前配置的验证状态：None=未验证 / True=通过 / False=失败。

        配置（Key / 地址 / 模型）一变，旧结果立即作废——避免"改了密钥还显示在线"。
        """
        v = self._verify
        if not v:
            return None, None
        if v["fp"] != config_fingerprint(config.API_KEY, config.BASE_URL, config.MODEL_ID):
            return None, None
        return v["ok"], v["error"]

    def status(self) -> dict:
        """当前运行状态（密钥只回传布尔与脱敏串，绝不回传明文）。

        mode/agent 只表示「是否配好了 LLM」，不代表密钥可用；
        密钥是否真的能连通，看 key_verified（需先做一次连接测试）。
        """
        sup = self.supervisor()
        verified, verify_error = self.verification()
        return {
            "ok": True,
            "agent": sup.available,
            "mode": "llm" if sup.available else "local-fallback",
            "model": sup.model if sup.available else None,
            "configured_model": config.MODEL_ID,
            "base_url": config.BASE_URL,
            "reason": sup.reason,
            "specialists": sorted(sup.specialists.keys()),
            "api_key_set": bool(config.API_KEY),
            "api_key_masked": mask_key(config.API_KEY),
            "key_verified": verified,
            "key_error": verify_error,
            "permission_mode": config.AGENT_PERMISSION_MODE,
        }

    def ask(self, message: str, session_id: str, user_id: str,
            request_id: str = "", project_id: str = "",
            feature_key: str = "") -> dict:
        """问答复用编排器：感知 → 上下文组装 → 路由 → 兜底 → 输出契约。

        request_id：前端生成的运行态标识，用于「思考过程」实时展示与打断检查点。
        project_id / feature_key：板块对话窗口带上；只对「尚未归属」的会话做首次回填
        （已有归属的会话保持不动，见 ConversationRegistry.claim_owner）。
        """
        if str(project_id or "").strip():
            # 老会话 / 未登记的会话在板块窗口里被提问时补上归属；失败不影响问答
            try:
                get_short_term().registry.claim_owner(
                    str(session_id), str(project_id).strip(),
                    str(feature_key or "").strip())
            except Exception:  # noqa: BLE001 - 归属回填属旁路，失败不阻塞问答
                pass
        result = self.orchestrator().answer(message, session_id, user_id=user_id,
                                            request_id=request_id)
        # 会话标题：首条提问当占位标题（左栏「二级会话选择」显示用）。
        # 谈话结束后会被一级总结的 topic 覆盖（见 ConversationRegistry.set_summary）。
        try:
            stm = get_short_term()
            if stm is not None and session_id:
                stm.registry.set_title_if_empty(str(session_id), message.strip()[:30])
        except Exception:  # noqa: BLE001 - 标题只影响清单展示，失败不阻塞问答
            pass
        return result

    # ===== 思考流 + 打断（运行态总线；见 core/telemetry/live.py）=====
    def think(self, request_id: str) -> dict:
        """思考过程快照：前端在等待回复期间轮询（思考小字 + 是否已被打断）。"""
        snap = get_live_bus().snapshot(request_id)
        if snap is None:
            return {"ok": False, "status": "unknown", "steps": []}
        return {"ok": True, **snap}

    def interrupt(self, request_id: str = "", session_id: str = "",
                  reason: str = "user") -> dict:
        """请求打断：只置协作式取消标记，真正停止由 agent 在下个检查点完成。"""
        return get_live_bus().request_cancel(request_id=request_id,
                                             session_id=session_id, reason=reason)

    def memory_overview(self, user_id: str = "default", rules: int = 12,
                        facts: int = 20, skills: int = 5) -> dict:
        """总控台「记忆与规则」块数据源（**只读**）：L0 全局层的死规则 + 长期事实 + 热点技能。

        不写任何记忆、不跑检索管线；记忆层不可用或查询异常时回 ok=false 的空结构
        （fail-open，与 interrupts / conversations 一致）。
        """
        empty = {"ok": False, "layer": "L0", "rules": [], "facts": [], "skills": []}
        mm = get_memory()
        if mm is None:
            return empty
        try:
            return {
                "ok": True, "layer": "L0", "user_id": user_id,
                "rules": mm.state.get_rules(user_id=user_id)[:max(0, int(rules))],
                "facts": mm.get_facts(user_id)[:max(0, int(facts))],
                "skills": mm.hot_skills(int(skills)) if int(skills) > 0 else [],
            }
        except Exception:  # noqa: BLE001 - 只读预览，失败降级为空
            return empty

    def interrupts(self, limit: int = 50, session_id: str = "",
                   status: str = "") -> dict:
        """中断日志（被打断交互的可续跑台账）——调试后台「中断记录」面板数据源。"""
        empty = {"records": [], "stats": {"open": 0, "resumed": 0, "closed": 0, "total": 0}}
        mm = get_memory()
        if mm is None:
            return empty
        try:
            return {
                "records": mm.list_interrupts(limit=limit, session_id=session_id,
                                              status=status),
                "stats": mm.interrupt.stats(),
            }
        except Exception:  # noqa: BLE001 - 面板故障不影响主链路
            return empty

    def start_conversation(self, user_id: str, scope: str = "",
                           project_id: str = "", feature_key: str = "") -> dict:
        """开始新谈话：生成 session_id 并登记（谈话注册表）。

        scope 为区块标签（research/listing）；project_id / feature_key 为二级归属
        （所属项目 + 细分功能板块，空 = 未分类）。
        """
        session_id = start_conversation(user_id, scope=scope,
                                        project_id=project_id, feature_key=feature_key)
        return {"ok": True, "session_id": session_id, "user_id": user_id,
                "scope": scope, "project_id": project_id, "feature_key": feature_key}

    def end_conversation(self, session_id: str, user_id: str) -> dict:
        """结束谈话：触发一级总结（并视游标触发二级总结）——分层总结管线入口。"""
        return self.supervisor().end_conversation(session_id, user_id=user_id)

    # ===== 会话清单 / 改名隐藏 / 只读指导智能体 =====
    def conversations(self, user_id: str = "default", scope: str = "",
                      include_hidden: bool = False, limit: int = 100,
                      project_id: str = "", feature_key: str = "") -> dict:
        """会话清单（含未完成断点聚合）：谈话注册表 + 中断台账一次读。

        断点信息用 `interrupt.open_by_session()` 一次拿全（不做 N+1）；
        project_id / feature_key（空 = 不过滤）用于筛出某项目 / 某板块下的会话；
        任何一环故障都返回 ok=False 的空清单，不抛 500（fail-open）。
        """
        empty = {"ok": False, "conversations": [],
                 "stats": {"total": 0, "open_interrupts": 0}}
        try:
            stm = get_short_term()
            rows = stm.registry.list_conversations(
                user_id=user_id, scope=scope,
                include_hidden=include_hidden, limit=limit,
                project_id=project_id, feature_key=feature_key)
        except Exception:  # noqa: BLE001 - 注册表故障：清单降级为空
            return empty

        open_map: dict = {}
        try:
            mm = get_memory()
            if mm is not None:
                open_map = mm.interrupt.open_by_session()
        except Exception:  # noqa: BLE001 - 台账故障：只丢断点信息，清单照常返回
            open_map = {}

        items: list[dict] = []
        for row in rows:
            sid = str(row.get("session_id") or "")
            info = open_map.get(sid) or {}
            # 轮次：直接读 checkpoint 消息条数（拿不到就 0，不额外引入复杂查询）
            turn_count = 0
            agent = str(row.get("agent_name") or "")
            if agent:
                try:
                    turn_count = int(stm.count_messages(agent_session_id(agent, sid)))
                except Exception:  # noqa: BLE001 - 线程不存在等场景按 0 计
                    turn_count = 0
            items.append({
                "session_id": sid,
                "scope": str(row.get("scope") or ""),
                # 二级归属：总控台的「待办汇总」要靠它把断点跳回「项目 + 板块」
                "project_id": str(row.get("project_id") or ""),
                "feature_key": str(row.get("feature_key") or ""),
                "title": conversation_title(row),
                "status": str(row.get("status") or ""),
                "agent_name": agent,
                "started_at": str(row.get("started_at") or ""),
                "ended_at": str(row.get("ended_at") or ""),
                "updated_at": str(row.get("updated_at") or ""),
                "hidden": bool(row.get("hidden")),
                "has_open_interrupt": bool(info),
                "pending_tool": str(info.get("pending_tool") or ""),
                "step_index": int(info.get("step_index") or 0),
                "interrupt_question": str(info.get("question") or ""),
                "turn_count": turn_count,
            })
        return {
            "ok": True,
            "conversations": items,
            "stats": {"total": len(items),
                      "open_interrupts": sum(1 for i in items if i["has_open_interrupt"])},
        }

    def patch_conversation(self, session_id: str, title: str | None = None,
                           hidden: bool | None = None,
                           project_id: str | None = None,
                           feature_key: str | None = None,
                           scope: str | None = None) -> dict:
        """会话改名 / 隐藏 / 移动到其他板块：只允许改 title / hidden / 归属三列。

        **隐藏 ≠ 删除**：hidden=True 只把注册表行的隐藏标记置 1，绝不删除注册表行、
        短期记忆（checkpoint）、压缩摘要或中断记录——取消隐藏后数据原样还在。
        **移动 ≠ 重建**：改项目 / 板块归属只改三列，消息与断点全部保留（见 set_owner）。
        会话不存在时返回 ok=False（端点据此回 404）。
        """
        sid = str(session_id or "").strip()
        if not sid:
            return {"ok": False, "error": "session_id 不能为空"}
        try:
            reg = get_short_term().registry
            if reg.get(sid) is None:
                return {"ok": False, "error": "session 不存在"}
            changed: list[str] = []
            if title is not None:
                reg.set_title(sid, str(title).strip())
                changed.append("title")
            if hidden is not None:
                reg.set_hidden(sid, bool(hidden))
                changed.append("hidden")
            if project_id is not None or feature_key is not None:
                reg.set_owner(sid, project_id or "", feature_key or "",
                              scope=scope if scope is not None else None)
                changed.append("owner")
        except Exception as exc:  # noqa: BLE001 - 注册表故障：回传错误文本，不抛 500
            return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"[:300]}
        return {"ok": True, "session_id": sid, "changed": changed}

    # ===== 项目（一级容器）+ 项目总览树 =====
    def projects(self, user_id: str = "default",
                 include_archived: bool = False) -> dict:
        """项目列表（含每个项目的会话数、未完成断点数与是否有未完成事项）。

        会话数与断点数用**一次** list_conversations 读全量 + **一次** open_by_session
        聚合后在内存里按 project_id 归组（不做「每个项目查一次」的 N+1）；
        任何一环故障都返回 ok=False 的空结构，不抛 500（fail-open）。
        """
        empty = {"ok": False, "projects": [],
                 "stats": {"total": 0, "archived": 0, "open_interrupts": 0}}
        try:
            stm = get_short_term()
            rows = stm.projects.list(user_id=user_id, include_archived=include_archived)
            sessions = stm.registry.list_conversations(
                user_id=user_id, include_hidden=True, limit=500)
        except Exception:  # noqa: BLE001 - 注册表故障：项目列表降级为空
            return empty

        open_map: dict = {}
        try:
            mm = get_memory()
            if mm is not None:
                open_map = mm.interrupt.open_by_session()
        except Exception:  # noqa: BLE001 - 台账故障：只丢断点信息，列表照常返回
            open_map = {}

        # 会话按 project_id 归组（一次读全，内存归组）："" 表示未分类，不属于任何项目
        by_project: dict[str, list[str]] = {}
        for row in sessions:
            pid = str(row.get("project_id") or "")
            if pid:
                by_project.setdefault(pid, []).append(str(row.get("session_id") or ""))

        items: list[dict] = []
        total_open = 0
        for p in rows:
            pid = str(p.get("project_id") or "")
            sids = by_project.get(pid, [])
            # 口径与既有 /api/conversations 一致：算「有未完成断点的会话数」，
            # 而不是断点记录条数（同一会话存在多条 open 记录时两者会不同）
            open_count = sum(1 for sid in sids if open_map.get(sid))
            total_open += open_count
            items.append({
                "project_id": pid,
                "name": str(p.get("name") or ""),
                "desc": str(p.get("desc") or ""),
                "status": str(p.get("status") or "open"),
                "created_at": str(p.get("created_at") or ""),
                "updated_at": str(p.get("updated_at") or ""),
                "session_count": len(sids),
                "open_interrupts": open_count,
                "has_unfinished": open_count > 0,
            })
        return {
            "ok": True,
            "projects": items,
            "stats": {"total": len(items),
                      "archived": sum(1 for i in items if i["status"] == "archived"),
                      "open_interrupts": total_open},
        }

    def create_project(self, user_id: str, name: str, desc: str = "") -> dict:
        """新建项目：name 为空返回 ok=False（端点据此回 400），否则返回新项目。"""
        clean = str(name or "").strip()
        if not clean:
            return {"ok": False, "error": "name 不能为空", "project": None}
        try:
            project = get_short_term().projects.create(
                user_id=user_id, name=clean, desc=str(desc or ""))
        except Exception as exc:  # noqa: BLE001 - 注册表故障：回传错误文本，不抛 500
            return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"[:300],
                    "project": None}
        return {"ok": True, "project": project}

    def patch_project(self, project_id: str, name: str | None = None,
                      desc: str | None = None, status: str | None = None) -> dict:
        """项目改名 / 改描述 / 归档：只允许改 name / desc / status，只改传入的字段。

        **归档 ≠ 删除**：status='archived' 只把项目状态位置 1，项目行与其下所有会话、
        记忆、中断记录一律保留（include_archived=True 时仍能列出），可随时改回 open。
        项目不存在时返回 ok=False + not_found=True（端点据此回 404）。
        """
        pid = str(project_id or "").strip()
        base = {"project_id": pid, "changed": []}
        if not pid:
            return {**base, "ok": False, "not_found": True, "error": "project 不存在"}
        new_name = None
        if name is not None:
            new_name = str(name).strip()
            if not new_name:
                return {**base, "ok": False, "error": "name 不能为空"}
        new_status = None
        if status is not None:
            new_status = str(status).strip().lower()
            if new_status not in PROJECT_STATUSES:
                return {**base, "ok": False,
                        "error": f"status 只能是 {'/'.join(PROJECT_STATUSES)}"}
        try:
            reg = get_short_term().projects
            if reg.get(pid) is None:
                return {**base, "ok": False, "not_found": True, "error": "project 不存在"}
            changed: list[str] = []
            if name is not None:
                changed.append("name")
            if desc is not None:
                changed.append("desc")
            if status is not None:
                changed.append("status")
            if changed:
                reg.update(pid, name=new_name, desc=desc, status=new_status)
        except Exception as exc:  # noqa: BLE001 - 注册表故障：回传错误文本，不抛 500
            return {**base, "ok": False, "error": f"{exc.__class__.__name__}: {exc}"[:300]}
        return {**base, "ok": True, "changed": changed}

    def project_tree(self, project_id: str) -> dict:
        """项目总览树：区块 → 板块 → 会话数 / 未完成断点数（一次取全，避免逐级请求）。

        **稀疏结构**：只回计数与状态，不回板块名称 —— 11 个细分板块的名称定义在前端
        `BOARDS` 里，后端不复刻这份清单（板块增删只改前端）。
        counts 的 `open` 为该板块下的未完成断点数（open_by_session 按会话归类到板块）；
        项目不存在返回 not_found=True（端点据此回 404）；任何内部异常返回 ok=False
        的空结构，不抛 500（fail-open，与 service.interrupts / conversations 一致）。
        """
        pid = str(project_id or "").strip()
        empty = {"ok": False, "project": None, "counts": {},
                 "totals": {"sessions": 0, "open_interrupts": 0}}
        try:
            stm = get_short_term()
            proj = stm.projects.get(pid) if pid else None
            if proj is None:
                return {**empty, "not_found": True, "error": "project 不存在"}
            counts = stm.registry.count_by_feature(pid)   # 一次 SQL 聚合
            rows = stm.registry.list_conversations(       # 一次 SQL：会话 → 板块映射
                project_id=pid, include_hidden=True, limit=500)
        except Exception as exc:  # noqa: BLE001 - 注册表故障：整棵树降级为空
            return {**empty, "error": f"{exc.__class__.__name__}: {exc}"[:300]}

        # 会话 → (区块, 板块)：用于把未完成断点归到对应板块
        sid_map = {str(r.get("session_id") or ""):
                   (str(r.get("scope") or ""), str(r.get("feature_key") or ""))
                   for r in rows}

        open_map: dict = {}
        try:
            mm = get_memory()
            if mm is not None:
                open_map = mm.interrupt.open_by_session()
        except Exception:  # noqa: BLE001 - 台账故障：只丢断点信息，树照常返回
            open_map = {}

        out: dict[str, dict] = {}
        for scope, features in counts.items():
            out[str(scope)] = {
                str(fk): {"sessions": int(info.get("sessions") or 0), "open": 0,
                          "last_at": str(info.get("last_at") or "")}
                for fk, info in features.items()
            }
        total_open = 0
        for sid in open_map:          # 口径 = 有未完成断点的会话数（open_map 已按会话去重）
            key = sid_map.get(sid)
            if key is None:   # 不属于本项目（或注册表查不到）的断点不计入
                continue
            scope, fk = key
            total_open += 1
            cell = out.setdefault(scope, {}).setdefault(
                fk, {"sessions": 0, "open": 0, "last_at": ""})
            cell["open"] += 1

        return {
            "ok": True,
            "project": {"project_id": proj["project_id"], "name": proj["name"],
                        "status": proj["status"]},
            "counts": out,
            "totals": {
                "sessions": sum(c["sessions"] for b in out.values() for c in b.values()),
                "open_interrupts": total_open,
            },
        }

    def guide(self, message: str, session_id: str = "", scope: str = "",
              user_id: str = "default", project_id: str = "",
              feature_key: str = "") -> dict:
        """只读指导智能体（右下角悬浮窗）：读记忆给导航建议，绝不写任何记忆。

        project_id / feature_key：告诉它用户当前在哪个项目、哪个细分板块
        （用于「你现在在哪」这一句，以及让它给出可点的项目级跳转）。
        """
        from agents.guide import guide_reply

        return guide_reply(message, session_id=session_id, scope=scope, user_id=user_id,
                           project_id=project_id, feature_key=feature_key)

    def clear_session(self, session_id: str) -> list[str]:
        """真清空：清除该会话在全部专职智能体下的 checkpoint 线程与压缩摘要。

        直接操作 checkpointer（不经过 Supervisor），无 Key 兜底模式下同样有效；
        约束层（轮次预算 / 循环熔断标记）一并复位。
        """
        stm = get_short_term()
        cleared = []
        for name in ("customer_service", "presales", "research", "listing"):
            sid = agent_session_id(name, session_id)
            stm.clear(sid)  # checkpoint 线程 + 摘要行一并清除
            cleared.append(sid)
        # 约束层复位：熔断解除、频率窗口与轮次预算清零
        from core.constraint import get_constraint_layer

        get_constraint_layer().reset(session_id)
        return cleared


service = AgentService()


@app.get("/api/status")
async def status() -> dict:
    return service.status()


# ===== 设置 API（前端「设置」面板的数据源：密钥 / 地址 / 模型 / 温度 / 权限 / 预算）=====
@app.get("/api/settings")
async def get_settings() -> dict:
    """当前设置（密钥脱敏）；更新前先读，表单只提交要改的字段。"""
    verified, verify_error = service.verification()
    return {
        "ok": True,
        "api_key_set": bool(config.API_KEY),
        "api_key_masked": mask_key(config.API_KEY),
        "api_key_from_env": bool(os.getenv("GLM_API") or os.getenv("OPENAI_API_KEY")),
        "key_verified": verified,
        "key_error": verify_error,
        "base_url": config.BASE_URL,
        "model": config.MODEL_ID,
        "temperature": config.LLM_TEMPERATURE,
        "permission_mode": config.AGENT_PERMISSION_MODE,
        "permission_modes": list(PERMISSION_MODES),
        "call_token_budget": config.CONTEXT_TOKEN_GUARD,
        "cost_budget": config.SESSION_COST_BUDGET,
        "cost_currency": config.SESSION_COST_BUDGET_CUR,
        "cost_budget_cny": config.SESSION_COST_BUDGET_CNY,
        "currencies": list(CURRENCIES),
        "usd_cny_rate": config.USD_CNY_RATE,
    }


@app.post("/api/settings")
async def update_settings(req: SettingsRequest) -> JSONResponse:
    """运行时更新配置并热重建智能体（空字段保持不变）；可选写回本机 .env。

    校验通过才落盘：任何一项不合法则整体不生效（避免"一半改了一半没改"）。
    """
    changed: list[str] = []
    errors: list[str] = []
    env_pairs: dict[str, str] = {}
    updates: list[tuple[str, object]] = []   # (设置名, setter) —— 校验通过后统一应用

    # ---- 第一步：只校验，不写 ----
    key = (req.api_key or "").strip()
    if key:
        # 常见误填：把请求地址贴进了密钥槽（会导致"显示在线但每次 401"）
        if "://" in key or key.lower().startswith(("http", "www.")):
            errors.append("API Key 看起来是一个网址，请填密钥；网址应填在「请求地址」里")
        else:
            updates.append(("api_key", key))
    url = (req.base_url or "").strip()
    if url:
        if "://" not in url:
            errors.append("请求地址需要是完整 URL（含 http:// 或 https://）")
        else:
            updates.append(("base_url", url))
    model = (req.model or "").strip()
    if model:
        updates.append(("model", model))

    temperature = None
    if req.temperature is not None:
        temperature = min(2.0, max(0.0, float(req.temperature)))
        updates.append(("temperature", temperature))

    mode = (req.permission_mode or "").strip().lower()
    if mode:
        if mode not in PERMISSION_MODES:
            errors.append(f"权限模式只能是 {'/'.join(PERMISSION_MODES)}")
        else:
            updates.append(("permission_mode", mode))

    if req.call_token_budget is not None:
        if int(req.call_token_budget) <= 0:
            errors.append("单次 token 预算需为正整数")
        else:
            updates.append(("call_token_budget", int(req.call_token_budget)))

    cost_value = currency = None
    if req.cost_budget is not None:
        currency = (req.cost_currency or config.SESSION_COST_BUDGET_CUR).strip().upper()
        if currency not in CURRENCIES:
            errors.append(f"币种只能是 {'/'.join(CURRENCIES)}")
        else:
            cost_value = max(0.0, float(req.cost_budget))

    if errors:
        return JSONResponse({"ok": False, "changed": [], "errors": errors,
                             "persisted": False, "status": service.status()})

    # ---- 第二步：应用（此时已确定无校验错误）----
    for name, value in updates:
        if name == "api_key":
            config.API_KEY = value
            env_pairs["GLM_API"] = value
            changed.append(name)
        elif name == "base_url":
            config.BASE_URL = value
            env_pairs["OPENAI_BASE_URL"] = value
            changed.append(name)
        elif name == "model":
            config.MODEL_ID = value
            env_pairs["OPENAI_MODEL"] = value
            changed.append(name)
        elif name == "temperature":
            config.LLM_TEMPERATURE = value
            env_pairs["LLM_TEMPERATURE"] = str(value)
            changed.append(name)
        elif name == "permission_mode":
            config.AGENT_PERMISSION_MODE = value
            env_pairs["AGENT_PERMISSION_MODE"] = value
            changed.append(name)
        elif name == "call_token_budget":
            config.CONTEXT_TOKEN_GUARD = value
            env_pairs["CONTEXT_TOKEN_GUARD"] = str(value)
            changed.append(name)

    if cost_value is not None:
        config.apply_cost_budget(cost_value, currency)
        env_pairs["SESSION_COST_BUDGET"] = str(config.SESSION_COST_BUDGET)
        env_pairs["SESSION_COST_BUDGET_CUR"] = config.SESSION_COST_BUDGET_CUR
        changed.append("cost_budget")

    persisted = False
    if changed and req.persist:
        try:
            persist_env(env_pairs)
            persisted = True
        except Exception:  # noqa: BLE001 - 写盘失败不影响本次运行（仅提示未持久化）
            persisted = False

    service.reload()  # 下一次请求会用新配置重建 Supervisor / Orchestrator
    return JSONResponse({
        "ok": True, "changed": changed, "errors": [], "persisted": persisted,
        "status": service.status(),
    })


@app.post("/api/settings/test")
def test_settings(req: TestSettingsRequest | None = None) -> JSONResponse:
    """做一次最小真实调用，验证 Key / 地址 / 模型是否可用。

    同步 def：内部是一次真实 LLM 调用（阻塞，最长 20s），不能占用事件循环。
    优先用请求体里表单当前填的值（可不保存先验证），未填则回落到已保存配置。
    """
    api_key = (req.api_key or "").strip() if req else ""
    base_url = (req.base_url or "").strip() if req else ""
    model = (req.model or "").strip() if req else ""
    key = api_key or config.API_KEY
    url = base_url or config.BASE_URL
    mid = model or config.MODEL_ID

    fingerprint = config_fingerprint(key, url, mid)
    if not key:
        service.record_verification(False, "未配置 API Key", fingerprint)
        return JSONResponse({"ok": False, "error": "未配置 API Key", "verified": False})
    try:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model=mid, api_key=key, base_url=url or None,
                         temperature=config.LLM_TEMPERATURE, max_tokens=16, timeout=20)
        resp = llm.invoke("ping")
        service.record_verification(True, None, fingerprint)
        return JSONResponse({
            "ok": True, "model": mid, "verified": True,
            "echo": str(getattr(resp, "content", ""))[:60],
        })
    except Exception as exc:  # noqa: BLE001 - 测试失败属预期路径，回传错误文本给前端
        error = f"{exc.__class__.__name__}: {exc}"[:300]
        service.record_verification(False, error, fingerprint)
        return JSONResponse({"ok": False, "error": error, "verified": False})


# 注意：下面是同步 def（不是 async def）——内部调用阻塞式 LLM/编排流水线，
# 写成 async def 会占死事件循环，导致回答期间 /api/think 轮询与「打断」请求全部超时。
# FastAPI 对 def 端点自动使用线程池，这正是本功能（边思考边看 + 随时打断）成立的前提。
@app.post("/api/ask")
def ask(req: AskRequest) -> JSONResponse:
    message = req.message.strip()
    if not message:
        return JSONResponse({"error": "message 不能为空"}, status_code=400)
    reply = service.ask(message, req.session_id, req.user_id.strip() or "default",
                        request_id=req.request_id.strip(),
                        project_id=req.project_id.strip(),
                        feature_key=req.feature_key.strip())
    return JSONResponse(reply)


# ===== 思考流 + 打断（前端「思考小字」与「打断」按钮的数据源）=====
@app.get("/api/think/{request_id}")
async def think(request_id: str) -> dict:
    """思考过程快照：等待回复期间轮询；返回 {steps, status, cancel, elapsed_ms}。

    status ∈ running / done / interrupted / error / unknown。
    """
    return service.think(request_id)


@app.post("/api/ask/interrupt")
async def interrupt(req: InterruptRequest) -> JSONResponse:
    """打断本轮回答（协作式）。

    只置取消标记——单次 LLM 请求发出后无法从外部掐断，真正停止发生在 agent 的
    下一个检查点（工具执行前 / 工具返回后）。停止现场会写进中断日志，供下一轮续跑。
    """
    rid, sid = req.request_id.strip(), req.session_id.strip()
    if not rid and not sid:
        return JSONResponse({"error": "request_id 与 session_id 至少给一个"}, status_code=400)
    return JSONResponse(service.interrupt(rid, sid, reason=req.reason.strip() or "user"))


@app.get("/api/interrupt/recent")
async def interrupt_recent(
    limit: int = Query(50, ge=1, le=500),
    session_id: str = Query(""),
    status: str = Query(""),
) -> dict:
    """中断日志（被打断交互的可续跑台账）——调试后台「中断记录」面板数据源。"""
    return service.interrupts(limit=limit, session_id=session_id, status=status)


@app.get("/api/memory/overview")
async def memory_overview(
    user_id: str = Query("default"),
    rules: int = Query(12, ge=1, le=100),
    facts: int = Query(20, ge=1, le=200),
    skills: int = Query(5, ge=0, le=20),
) -> dict:
    """总控台「记忆与规则」块：**只读**预览 L0 全局层（死规则 / 长期事实 / 热点技能）。

    纯读接口——不写任何记忆、不触发检索；记忆层不可用时回 ok=false 的空结构。
    """
    return service.memory_overview(user_id=user_id, rules=rules, facts=facts, skills=skills)


# ===== 项目 API（工作台一级容器：项目 → 区块 → 细分板块）=====
@app.get("/api/projects")
async def projects(
    user_id: str = Query("default"),
    include_archived: bool = Query(False),
) -> dict:
    """项目列表（含每个项目的会话数与未完成断点数）。

    include_archived=False（默认）时只回未归档项目（归档 ≠ 删除，数据仍在）；
    注册表/台账故障时返回 ok=false 的空结构（fail-open，不 500）。
    """
    return service.projects(user_id=user_id, include_archived=include_archived)


@app.post("/api/projects")
async def create_project(req: ProjectCreateRequest) -> JSONResponse:
    """新建项目：name 必填（空 → 400），desc 可选。"""
    result = service.create_project(req.user_id.strip() or "default", req.name, req.desc)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)


@app.patch("/api/projects/{project_id}")
async def patch_project(project_id: str, req: ProjectPatchRequest) -> JSONResponse:
    """项目改名 / 改描述 / 归档（name / desc / status 字段都可选，只改传入的）。

    **归档 ≠ 删除**：status='archived' 只改项目状态，项目下的会话、记忆、中断记录
    一律保留（`GET /api/projects?include_archived=true` 仍能列出，可随时改回 open）。
    项目不存在 → 404；name 为空 / status 非法 → 400。
    """
    result = service.patch_project(project_id, name=req.name, desc=req.desc,
                                   status=req.status)
    if result.get("not_found"):
        return JSONResponse(result, status_code=404)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)


@app.get("/api/projects/{project_id}/tree")
async def project_tree(project_id: str) -> JSONResponse:
    """项目总览树：一次返回「区块 → 细分板块 → 会话数 / 未完成断点数」，前端只拉一次。

    稀疏结构：只回计数与状态，不回板块名称（板块清单定义在前端 BOARDS，后端不复刻）；
    feature_key 为空的会话归到 "" 下（前端显示为「未分类」），不丢数据。
    项目不存在 → 404；内部异常 → ok=false 的空结构（fail-open，不 500）。
    """
    result = service.project_tree(project_id)
    if result.get("not_found"):
        return JSONResponse(result, status_code=404)
    return JSONResponse(result)


@app.post("/api/conversation/start")
async def conversation_start(req: StartConversationRequest) -> JSONResponse:
    """开始新谈话：返回新 session_id（前端存 localStorage，替代手工生成）。

    scope（可选）：区块标签（research / listing / global），供会话清单按区块过滤；
    project_id / feature_key（可选）：二级归属（所属项目 + 细分功能板块，空 = 未分类）。
    """
    return JSONResponse(service.start_conversation(
        req.user_id.strip() or "default", req.scope.strip(),
        req.project_id.strip(), req.feature_key.strip()))


@app.post("/api/conversation/end")
def end_conversation(req: EndConversationRequest) -> JSONResponse:
    """结束谈话：一级总结入库，达 M 个触发二级总结。

    同步 def：内部要跑总结 LLM（阻塞），不能占用事件循环。
    """
    sid = req.session_id.strip()
    if not sid:
        return JSONResponse({"error": "session_id 不能为空"}, status_code=400)
    result = service.end_conversation(sid, req.user_id.strip() or "default")
    return JSONResponse(result)


# ===== 会话清单 / 改名隐藏（前端左侧「谈话列表」数据源）=====
@app.get("/api/conversations")
async def conversations(
    user_id: str = Query("default"),
    scope: str = Query(""),
    include_hidden: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    project_id: str = Query(""),
    feature_key: str = Query(""),
) -> dict:
    """会话清单（含未完成断点聚合）：标注每段谈话的区块、状态与断点进度。

    project_id / feature_key（可选，空 = 不过滤）：筛出某项目 / 某细分板块下的会话，
    与 scope（区块）过滤语义一致。
    台账/注册表故障时返回 ok=false 的空清单（fail-open，不 500）。
    """
    return service.conversations(user_id=user_id, scope=scope,
                                 include_hidden=include_hidden, limit=limit,
                                 project_id=project_id, feature_key=feature_key)


@app.patch("/api/conversations/{session_id}")
async def patch_conversation(session_id: str,
                             req: ConversationPatchRequest) -> JSONResponse:
    """会话改名 / 隐藏 / 移动到其他板块（只允许 title / hidden / 归属三列）。

    隐藏 ≠ 删除：只置隐藏标记，注册表行、短期记忆、checkpoint、中断记录一律保留。
    移动 ≠ 重建：改 project_id / feature_key / scope 只改归属列，消息与断点全部保留。
    """
    result = service.patch_conversation(session_id, title=req.title, hidden=req.hidden,
                                        project_id=req.project_id,
                                        feature_key=req.feature_key, scope=req.scope)
    if not result.get("ok"):
        return JSONResponse(result, status_code=404)
    return JSONResponse(result)


# 同步 def（不是 async def）：内部是一次阻塞 LLM 调用（无 Key 时走真实数据兜底），
# 写成 async def 会占死事件循环。
@app.post("/api/guide")
def guide(req: GuideRequest) -> JSONResponse:
    """只读指导智能体（右下角悬浮窗）：告诉用户「现在在哪、下一步去哪儿」。

    只读记忆（会话清单 + 中断台账 + 上下文块），不写任何记忆；LLM 不可用时用真实数据兜底。
    """
    msg = req.message.strip()
    if not msg:
        return JSONResponse({"error": "message 不能为空"}, status_code=400)
    return JSONResponse(service.guide(msg, session_id=req.session_id.strip(),
                                      scope=req.scope.strip(),
                                      user_id=req.user_id.strip() or "default",
                                      project_id=req.project_id.strip(),
                                      feature_key=req.feature_key.strip()))


@app.post("/api/clear")
async def clear(req: ClearRequest) -> dict:
    """清空指定会话的后端记忆（前端「清空对话」按钮的真清空实现）。"""
    sid = req.session_id.strip()
    if not sid:
        return JSONResponse({"error": "session_id 不能为空"}, status_code=400)
    return {"ok": True, "cleared": service.clear_session(sid)}


# ===== 遥测 API（调试后台 /debug 的数据源；fail-open，库故障返回空数据）=====
@app.get("/api/telemetry/traces")
async def telemetry_traces(
    limit: int = Query(50, ge=1, le=500),
    session_id: str = Query(""),
    route: str = Query(""),
) -> dict:
    """近期 trace 列表（倒序；可按会话 / 路由过滤）。"""
    rec = get_recorder()
    return {"traces": rec.list_traces(limit=limit, session_id=session_id, route=route)}


@app.get("/api/telemetry/traces/{trace_id}")
async def telemetry_trace(trace_id: int) -> JSONResponse:
    """单条 trace + 事件明细（时间线）。"""
    trace = get_recorder().get_trace(trace_id)
    if trace is None:
        return JSONResponse({"error": "trace 不存在"}, status_code=404)
    return JSONResponse(trace)


@app.get("/api/telemetry/summary")
async def telemetry_summary() -> dict:
    """聚合统计：概览卡 + 工具排行 + 智能体分布 + 最近错误。"""
    return get_recorder().summary()


@app.get("/api/telemetry/trend")
async def telemetry_trend(session_id: str = Query(...)) -> dict:
    """某会话逐轮 token 序列（诊断上下文膨胀）。"""
    return {"trend": get_recorder().token_trend(session_id)}


@app.post("/api/telemetry/clear")
async def telemetry_clear() -> dict:
    """清空遥测库（调试期维护）。"""
    get_recorder().clear()
    return {"ok": True}


# 调试后台页面（独立静态页，不与主工作台共用路由）
@app.get("/debug")
async def debug_page() -> FileResponse:
    return FileResponse(UI_DIR / "debug.html")


# 根路径挂载静态页（必须放在所有 API 路由之后，避免吞掉 /api/*）：
# html=True 使 "/" 自动返回 index.html；style.css / app.js 等相对引用同源生效
app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="root")


if __name__ == "__main__":
    import uvicorn

    print(f"Quote Agent 已启动 → http://{HOST}:{PORT}  （{service.supervisor().reason or 'LLM 模式'}）")
    uvicorn.run(app, host=HOST, port=PORT)
