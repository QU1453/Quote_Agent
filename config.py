# -*- coding: utf-8 -*-
"""智能体配置槽：API Key / 请求地址 / 模型 ID 统一在此配置，所有智能体共用。

填写方式（二选一，推荐 .env）：
1. 复制 .env.example 为 .env，填写三个槽位（.env 已被 .gitignore 拦截，绝不入库）；
2. 直接设置同名环境变量（Docker / 云环境常用）。

各智能体（agents/）不要自行读环境变量，一律从本模块取值，
保证换模型 / 换服务商时只改这一处。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def _app_dir() -> Path:
    """可写数据目录：打包后取可执行文件所在目录（.env 随程序走），开发态取源码目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resource_dir() -> Path:
    """只读资源目录：打包后是 PyInstaller 解包目录，开发态同源码目录。"""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


BASE_DIR = _app_dir()             # 可写：.env、运行期数据
RESOURCE_DIR = _resource_dir()    # 只读：ui/ 等前端资源

# 运行期数据根目录：所有记忆 / 遥测库统一收进这一个文件夹，不再散落到源码目录里
# （以前是 memory/data、memory/short_term/data、memory/long_term/data、core/telemetry/data 四处）。
#     data/memory/      短期 · 长期 · 知识库 · 状态 · 技能 · 中断续跑 · 审计
#     data/telemetry/   调试后台的 trace 库
# 【必须走 BASE_DIR，不能用 __file__ 推导】单文件打包后 __file__ 指向解包临时目录
# （%TEMP%\_MEIxxxx），数据会随进程退出被清空——重启即丢全部记忆（会话、长期记忆、
# 知识库、状态规则、技能、中断续跑台账），而且会往 C 盘写。
DATA_DIR = BASE_DIR / "data"

load_dotenv(BASE_DIR / ".env")  # 读取本地密钥配置（已被 .gitignore 拦截）

# ===== LLM 配置槽（所有智能体共用，OpenAI 兼容协议）=====
# API Key：优先读 GLM_API（智谱密钥环境变量名），兼容旧槽位 OPENAI_API_KEY；
# 真实密钥只放 .env / 环境变量，代码与仓库中不出现
API_KEY: str | None = (
    os.getenv("GLM_API", "").strip()
    or os.getenv("OPENAI_API_KEY", "").strip()
    or None
)

# 请求地址：默认智谱 GLM 开放平台（与默认模型 glm-5.3-flash 配套，OpenAI 兼容协议）；
# 改用 OpenAI 官方时显式设为 https://api.openai.com/v1
_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
BASE_URL: str = os.getenv("OPENAI_BASE_URL", "").strip() or _DEFAULT_BASE_URL

# 模型 ID：本项目默认 GLM-5.3-Flash（智谱开放平台），兼容 OpenAI 协议
MODEL_ID: str = os.getenv("OPENAI_MODEL", "glm-5.3-flash").strip() or "glm-5.3-flash"

# 采样温度：0 最确定（选品/合规这类判定建议低值），越高越发散
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))

# ===== 服务配置 =====
HOST: str = os.getenv("SERVER_HOST", "127.0.0.1")
PORT: int = int(os.getenv("SERVER_PORT", "8623"))

# ===== 记忆系统配置槽（见 docs/memory-system-design.md §8）=====
# 短期窗口内的谈话数 K（可调高 8/10）
SHORT_TERM_CONVERSATIONS: int = int(os.getenv("SHORT_TERM_CONVERSATIONS", "5"))

# 每 M 个谈话触发一次二级总结（滚动合并进长期记忆）
L2_SUMMARY_INTERVAL: int = int(os.getenv("L2_SUMMARY_INTERVAL", "5"))

# 单次请求 token 预算（= 窗口组装上限）：超限按最新优先丢最旧谈话原文（绝不丢一级总结）；
# 同时作为上下文五段分配的基数（见下方 BUDGET_PCT_*），可在网页「设置」里调
CONTEXT_TOKEN_GUARD: int = int(os.getenv("CONTEXT_TOKEN_GUARD", "24000"))

# 状态记忆（死规则）注入开关：False 时不再向 LLM 上下文注入铁律块
STATE_MEMORY_INJECT: bool = os.getenv("STATE_MEMORY_INJECT", "1") == "1"

# 热技能（评分最高）预注入条数
SKILL_HOT_INJECT_TOP: int = int(os.getenv("SKILL_HOT_INJECT_TOP", "3"))

# 总结智能体模型（默认跟随主模型 glm-5.3-flash）
SUMMARIZER_MODEL: str = os.getenv("SUMMARIZER_MODEL", "").strip() or MODEL_ID

# ===== 中断续跑 + 思考流配置槽（memory/interrupt/ + core/telemetry/live.py）=====
# 中断续跑提示注入开关：True 时把「上次被打断到哪一步 / 哪个工具被用户打断」注入下一轮
# 上下文，实现自动续跑；关闭后中断日志仍照常落库，只是不再自动注入
INTERRUPT_RESUME_INJECT: bool = os.getenv("INTERRUPT_RESUME_INJECT", "1") == "1"

# 聊天框「思考过程」单轮最多保留的步骤条数（超出丢最旧；纯展示用，防界面过长）
LIVE_THINK_MAX_STEPS: int = int(os.getenv("LIVE_THINK_MAX_STEPS", "40"))

# 运行态（含打断标记）在内存中的保留时长（秒），超时自动回收
LIVE_THINK_TTL_SECONDS: int = int(os.getenv("LIVE_THINK_TTL_SECONDS", "1800"))

# ===== 约束层配置槽（core/constraint/，见 README 约束层章节）=====
# 单轮输入长度上限（字符数；超长否决，防 token 灌注）
CONSTRAINT_MAX_INPUT_CHARS: int = int(os.getenv("CONSTRAINT_MAX_INPUT_CHARS", "4000"))

# 单会话轮次预算（超出后建议结束谈话开新会话）
CONSTRAINT_MAX_SESSION_TURNS: int = int(os.getenv("CONSTRAINT_MAX_SESSION_TURNS", "200"))

# 循环守卫：连续重复提问阈值（第 N 次相同问题时打断引导）
CONSTRAINT_LOOP_REPEAT_Q: int = int(os.getenv("CONSTRAINT_LOOP_REPEAT_Q", "3"))

# 循环守卫：连续雷同回复阈值
CONSTRAINT_LOOP_REPEAT_A: int = int(os.getenv("CONSTRAINT_LOOP_REPEAT_A", "3"))

# 循环守卫：连续兜底次数达到该值触发 LLM 熔断（该会话跳过 LLM 调用止损）
CONSTRAINT_LOOP_FALLBACK_TRIP: int = int(os.getenv("CONSTRAINT_LOOP_FALLBACK_TRIP", "4"))

# ===== 验证层配置槽（core/constraint/validator.py：工具调用前置四道检查）=====
# 权限模式四档：plan（只读）/ ask（写操作需人工确认）/ accept（低风险写自动放行）/ bypass（全放开）
# 项目精度优先：默认只开 plan，待逐步信任机制就绪后逐档放开
AGENT_PERMISSION_MODE: str = os.getenv("AGENT_PERMISSION_MODE", "plan").strip().lower()

# 写/执行类工具名单（这些工具会改变状态，plan/ask 模式下被拦截）；
# 新增有副作用的工具必须在此登记，或命名命中 TOOL_WRITE_NAME_RE
TOOL_WRITE_TOOLS: set[str] = {
    t.strip() for t in os.getenv(
        "TOOL_WRITE_TOOLS", "register_return,handle_return").split(",") if t.strip()
}

# 路径检查：允许访问的根目录（绝对路径必须在根内；相对路径禁止 .. 穿越）
TOOL_PATH_ROOTS: list[str] = [
    p.strip() for p in os.getenv("TOOL_PATH_ROOTS", str(BASE_DIR)).split(";") if p.strip()
]

# 网络访问策略：deny（全禁）/ allowlist（仅白名单域名）/ allow（放开，仍禁云元数据端点）
TOOL_NET_POLICY: str = os.getenv("TOOL_NET_POLICY", "allowlist").strip().lower()
TOOL_NET_ALLOW_HOSTS: list[str] = [
    h.strip().lower() for h in os.getenv("TOOL_NET_ALLOW_HOSTS", "").split(",") if h.strip()
]

# ===== 循环守卫配置槽（core/constraint/tool_loop.py：agent 循环防打转）=====
# 单会话工具调用总量上限（超出即熔断，后续工具调用全部拒绝）
TOOL_LOOP_MAX_CALLS: int = int(os.getenv("TOOL_LOOP_MAX_CALLS", "40"))

# 连续失败次数上限（工具连续抛错达阈值即熔断）
TOOL_LOOP_MAX_CONSEC_FAILS: int = int(os.getenv("TOOL_LOOP_MAX_CONSEC_FAILS", "5"))

# 完全相同调用检测的记录窗口（保留最近 N 条调用；签名 = MD5(工具名+参数)前12位 + 结果前20字符）
TOOL_LOOP_IDENTICAL_WINDOW: int = int(os.getenv("TOOL_LOOP_IDENTICAL_WINDOW", "10"))

# ===== 预算熔断配置槽（core/constraint/budget.py）=====
# 单会话 token 总预算（输入+输出累计，超出后 LLM 熔断转离线答复）
SESSION_TOKEN_BUDGET: int = int(os.getenv("SESSION_TOKEN_BUDGET", "100000"))

# 单会话累计金额上限：值 + 币种（CNY / USD）；0 = 不启用金额熔断
# 账单单价（PRICE_*_CNY_PER_M）是人民币口径，故 USD 预算按 USD_CNY_RATE 折算后比较
SESSION_COST_BUDGET: float = float(os.getenv("SESSION_COST_BUDGET", "0"))
SESSION_COST_BUDGET_CUR: str = (os.getenv("SESSION_COST_BUDGET_CUR", "CNY").strip().upper() or "CNY")

# 美元 → 人民币汇率（只用于把 USD 口径预算折算成 CNY 与账单对齐，可按需修改）
USD_CNY_RATE: float = float(os.getenv("USD_CNY_RATE", "7.2"))


def to_cny(value: float, currency: str = "CNY") -> float:
    """把金额按币种归一到 CNY 口径。"""
    return float(value) * USD_CNY_RATE if str(currency).upper() == "USD" else float(value)


def apply_cost_budget(value: float, currency: str = "CNY") -> float:
    """设定累计金额上限（值 + 币种），同步归一后的 CNY 生效值（预算守卫读它）。"""
    global SESSION_COST_BUDGET, SESSION_COST_BUDGET_CUR, SESSION_COST_BUDGET_CNY
    SESSION_COST_BUDGET = max(0.0, float(value))
    SESSION_COST_BUDGET_CUR = "USD" if str(currency).upper() == "USD" else "CNY"
    SESSION_COST_BUDGET_CNY = to_cny(SESSION_COST_BUDGET, SESSION_COST_BUDGET_CUR)
    return SESSION_COST_BUDGET_CNY


# 生效金额预算（CNY 口径，预算守卫直接读；由上面两个槽位归一而来）
SESSION_COST_BUDGET_CNY: float = to_cny(SESSION_COST_BUDGET, SESSION_COST_BUDGET_CUR)

# 计费单价（元 / 百万 token）：按智谱账单实际价格填写，默认 0 = 只按 token 熔断
PRICE_INPUT_CNY_PER_M: float = float(os.getenv("PRICE_INPUT_CNY_PER_M", "0"))
PRICE_OUTPUT_CNY_PER_M: float = float(os.getenv("PRICE_OUTPUT_CNY_PER_M", "0"))

# 上下文五段预算分配（百分比，合计 100）：
# 输出预留 15% / 系统提示词 10% / 长期记忆 5% / 当前任务 20% / 历史会话（短期+知识库）50%
BUDGET_PCT_OUTPUT: int = int(os.getenv("BUDGET_PCT_OUTPUT", "15"))
BUDGET_PCT_SYSTEM: int = int(os.getenv("BUDGET_PCT_SYSTEM", "10"))
BUDGET_PCT_LONG_TERM: int = int(os.getenv("BUDGET_PCT_LONG_TERM", "5"))
BUDGET_PCT_TASK: int = int(os.getenv("BUDGET_PCT_TASK", "20"))
BUDGET_PCT_HISTORY: int = int(os.getenv("BUDGET_PCT_HISTORY", "50"))

# ===== 工具层配置槽（core/toolkit/：MCP 格式工具注册 / 参数校验 / 调用）=====
# 配置黑名单：开发者可禁用某些工具（逗号分隔；命中者永不暴露给模型）
TOOL_DISABLED: set[str] = {
    t.strip() for t in os.getenv("TOOL_DISABLED", "").split(",") if t.strip()
}

# MCP client 单次调用超时（秒）与重试上限（含首次，最多 5 次仍失败即退出）
TOOL_CALL_TIMEOUT_S: float = float(os.getenv("TOOL_CALL_TIMEOUT_S", "10"))
TOOL_CALL_MAX_RETRIES: int = int(os.getenv("TOOL_CALL_MAX_RETRIES", "5"))

# 规划阶段基础轮数（阶段一：前 N 轮只暴露 planning 工具；实际轮数与任务结构复杂度正相关）
TOOL_PLANNING_ROUNDS_BASE: int = int(os.getenv("TOOL_PLANNING_ROUNDS_BASE", "2"))

# MCP 协议：版本号 / 传输方式（inprocess=同进程直连；stdio=独立 server 进程）
MCP_PROTOCOL_VERSION: str = os.getenv("MCP_PROTOCOL_VERSION", "2024-11-05")
MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "inprocess").strip().lower()
MCP_SERVER_NAME: str = os.getenv("MCP_SERVER_NAME", "quote-tool-server")
MCP_SERVER_VERSION: str = "1.0.0"

