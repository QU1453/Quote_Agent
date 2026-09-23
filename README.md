# Quote Agent

**跨境卖家「选品 → 上架」智能体（日本市场）** — 对应《电商流程与进销存管理表》的 **第三部分（选品）+ 第四部分（Listing 创建与上架）**：自动寻找热销品与供应商、生成并上架 Listing。

> 大学生创新创业大赛项目 · Quote_Agent（跨境卖家自动选品与上架智能体）
> 仓库：https://github.com/QU1453/Quote_Agent

---

## 项目简介

跨境电商场景下，卖家在素人阶段最高频的两件事正是 **选品（找供应商）** 与 **写 Listing（上架）**，也就是本项目锚定的流程表第三、四部分。本项目构建一套 **多智能体工作台**：

- **选品（本职 · 流程表第三部分）**：需求 / 竞争 / 利润 / 供应链 四步验证 + 1688 等供应商搜索对比；
- **上架（本职 · 流程表第四部分）**：Listing 起草（标题 / 五点 / Search Terms）、图片合规自检、定价与 FBA/FBM 履约建议；
- **买家客服（附带能力，非本职）**：订单物流查询、退换货办理、商品推荐，中日双语一键切换；对应流程表第六部分，正式归属「物流管理智能体」；
- **五模块记忆系统**：短期（谈话粒度 K 窗口）/ 长期（二级总结范式条目）/ 知识库（索引先行两阶段检索）/ 状态记忆（死规则注入）/ 技能记忆（事/的/痛/解 + 反馈闭环），全程带权限体系与审计；
- **认知层（core/）**：感知（输入/上下文/会话）→ 思考（ReAct）→ 调度（编排器 + 双层工具注册表）→ 反思（技能提炼）→ 输出（清洗/格式化），五大模块。

## 功能特性

- **双工作台 UI**：选品工作台 / Listing 工作台两个独立聊天流，「结束谈话」触发一级总结归档（`ui/`）
- **多智能体协作**：supervisor 主控调度 4 个专职智能体（customer_service / presales / research / listing），路由优先级 listing > research > 客服/售前
- **卡片式智能回复**：选品结论卡、供应商对比表、Listing 草稿卡、定价/履约卡、物流轨迹卡、商品推荐卡
- **双层工具注册表**：模块局部注册表（高频直连）+ 全局注册表（跨模块调度/鉴权/审计），LLM 工具描述统一范式（何时使用 + JSON 调用格式 + 参数类型说明）
- **双保险演示**：配置 `OPENAI_API_KEY` 走真实 LLM（GLM-5.3-Flash）；未配置时每个智能体都有本地规则兜底，全链路可演示
- **权限体系**：所有记忆写操作走权限等级（L0~L9），越权拒绝并留审计（`data/memory/access.sqlite`）

## 技术架构

| 层 | 方案 |
| --- | --- |
| 桌面容器 | Python + pywebview（Windows WebView2，开发中） |
| 后端服务 | FastAPI + uvicorn（`server.py`，同一端口托管前端） |
| 认知层 | `core/`：perception（感知）/ cognition（思考-ReAct 引擎）/ dispatch（调度-编排器 + 工具注册表）/ reflection（反思-技能提炼）/ output（输出-清洗格式化） |
| 智能体 | `agents/`：LangGraph ReAct；supervisor 主控 + 客服 / 售前 / 选品 / Listing 四个专职智能体 + 总结智能体 |
| 工具 | `tools/`：客服（订单/物流/售后/推荐）+ 卖家（选品/供应商/Listing），全部经 P0 注册表登记 |
| 记忆 | `memory/`：短期（谈話窗口 + SqliteSaver）+ 长期（facts + ANN-RAG + 范式条目）+ 知识库 + 状态记忆 + 技能记忆（五模块，见下） |
| 界面 | 原生 HTML / CSS / JS（`ui/`，双工作台 + 中日切换） |
| 兜底引擎 | 无 Key 时每个专职智能体自带 `classic_*_reply` 规则路由，编排器统一回落 |
| 容器化 | Docker + docker-compose（`Dockerfile` / `docker-compose.yml` / `.dockerignore`） |

数据链路：`网页输入 → POST /api/ask → Orchestrator（感知 → 上下文组装 → supervisor 路由 → 专职智能体 ReAct / 兜底 → 输出清洗）→ {reply, intent, data, route} → 前端渲染`。

### 记忆系统（五模块）

| 模块 | 目录 | 做什么 | 写权限 |
| --- | --- | --- | --- |
| 短期记忆 | `memory/short_term/` | 谈话粒度注册表 + K 窗口上下文（摘要 + 原文，token 守卫裁剪）；消息条数压缩兜底 | L0（本人会话） |
| 长期记忆 | `memory/long_term/` | facts 精确 KV + 文档分块 ANN-RAG + `long_term_entries` 范式条目（画像/事实/待办/偏好，来源可回溯） | L1 |
| 知识库 | `memory/knowledge/` | 文档入库分块 → 索引简述先行 + 按 chunk_id 取正文（两阶段，省 token） | L1 |
| 状态记忆 | `memory/state/` | 死规则表（scope：全员/指定智能体/指定用户），每次上下文组装自动注入「铁律」块 | L3 |
| 技能记忆 | `memory/skill/` | 事/的/痛/解四要素 + 搜索 + 反馈闭环（连续失败自动归档，成功复出） | L2 |

**分层总结管线**：谈话结束 → 一级总结（保留细节，十个字段范式）→ 每 M 个一级总结滚动合并 → 二级总结 → 长期记忆条目（`L2_SUMMARY_INTERVAL` 控制间隔）。权限体系 `memory/access.py`：L0~L9 等级 + 默认拒绝显式授权 + 全程审计。

## 目录结构

```
大创一/
├── .gitignore / .dockerignore / .env.example  # 密钥防护（真实 .env 绝不入库）
├── Dockerfile / docker-compose.yml            # 容器化（Key 走环境变量注入）
├── server.py            # FastAPI 后端：托管 ui + 全部 API
├── config.py            # 智能体配置槽 + 记忆参数槽（统一入口）
├── core/                # 认知层五大模块
│   ├── perception/      # 感知：input（清洗/语言/意图打分）+ context（上下文组装）+ session（谈话生命周期）
│   ├── cognition/       # 思考：react.py（LangGraph ReAct 引擎提炼，兼容多版本）
│   ├── dispatch/        # 调度：registry.py（双层工具注册表）+ orchestrator.py（问答编排流水线）
│   ├── reflection/      # 反思：reflector.py（L2 技能提炼）+ skill_writer.py（复盘文本→四要素）
│   └── output/          # 输出：validator.py（清洗工具 JSON 泄露）+ formatter.py（{reply,intent,data,route}）
├── agents/              # 智能体目录（多智能体协作，一人一文件）
│   ├── supervisor.py    # 主控：路由调度 + 谈话结束编排（一二级总结触发）
│   ├── customer_service.py / presales.py   # 买家客服 / 售前导购（保留，行为不变）
│   ├── research_agent.py  # 选品智能体（四步验证 + 供应商）
│   ├── listing_agent.py   # Listing 智能体（起草/图片/定价/履约）
│   └── summarizer.py    # 总结智能体（L1：一级/二级总结，无 Key 规则降级）
├── tools/               # 工具目录（P0 注册表 + LLM 描述范式）
│   ├── catalog.py / order.py / after_sales.py / recommend.py   # 客服工具
│   ├── research.py      # 选品：check_demand/check_competition/calc_profit/run_product_research
│   ├── supplier.py      # 供应商：search_supplier/compare_supplier（1688 接入槽位 SUPPLIER_SOURCE）
│   └── listing.py       # 上架：draft_listing/check_images/price_strategy/recommend_fulfillment
├── memory/              # 记忆目录（五模块 + 权限 + 审计 + demo）
│   ├── access.py        # 权限等级 L0~L9 / guard 鉴权 / AuditLogger
│   ├── manager.py       # MemoryManager 统一入口（聚合五模块）
│   ├── short_term/ long_term/ knowledge/ state/ skill/   # 五大记忆模块
│   └── demo.py          # python -m memory.demo 一键自检（9 个场景断言）
├── requirements.txt
└── ui/                  # 双工作台界面（选品 / Listing 标签 + 中日切换 + 结束谈话）
    ├── index.html / style.css / app.js
```

## 智能体配置槽（API / 请求地址 / 模型 ID）

所有智能体的 LLM 配置集中在 `config.py`，三个字段：

| 槽位 | 变量 | 说明 | 默认值 |
| --- | --- | --- | --- |
| API Key | `OPENAI_API_KEY` | 真实密钥，只放 `.env` / 环境变量，绝不入库 | 空（走本地兜底） |
| 请求地址 | `OPENAI_BASE_URL` | OpenAI 兼容 base_url | 智谱开放平台 |
| 模型 ID | `OPENAI_MODEL` | 本项目默认 GLM-5.3-Flash | `glm-5.3-flash` |

记忆参数槽位（均已设默认值，可按需用环境变量覆盖）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SHORT_TERM_CONVERSATIONS` | 5 | 短期窗口 K（最近的已结束谈话数） |
| `L2_SUMMARY_INTERVAL` | 5 | 二级总结间隔 M（M 个一级总结合并一次） |
| `CONTEXT_TOKEN_GUARD` | 24000 | 窗口 token 上限（超限丢最旧原文、保留摘要） |
| `STATE_MEMORY_INJECT` | 1 | 死规则注入总开关（0=关闭） |
| `SKILL_HOT_INJECT_TOP` | 3 | 热技能预注入条数 |
| `SUMMARIZER_MODEL` | 同 MODEL_ID | 总结智能体专用模型 |

填写方式（二选一）：

```bash
# 方式 A：本地 .env（推荐；.env 已被 .gitignore 拦截）
cp .env.example .env
# 编辑 .env，取消注释并填三个槽位

# 方式 B：环境变量（Docker / 云环境）
export OPENAI_API_KEY=sk-xxx
export OPENAI_BASE_URL=https://api.deepseek.com/v1
export OPENAI_MODEL=deepseek-chat
```

约定：智能体一律从 `config.py` 取值，不自行读环境变量；三个槽位全留空时自动走本地规则兜底，演示不断线。

## API 一览

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/api/ask` | POST | 问答（走编排器）：`{message, session_id, user_id}` → `{reply, intent, data, route}` |
| `/api/conversation/start` | POST | 开始新谈话，返回新 `session_id`（前端存 localStorage） |
| `/api/conversation/end` | POST | 结束谈话：一级总结入库；达 M 个自动二级总结 → 长期记忆 |
| `/api/clear` | POST | 清空指定会话在全部智能体下的 checkpoint 线程与摘要 |
| `/api/status` | GET | 运行模式 / 模型 / 已注册 specialists |

## 本地运行

方式一 · 启动 Agent 后端（推荐，浏览器即全功能）：

```bash
pip install -r requirements.txt
cp .env.example .env      # 填入 OPENAI_API_KEY（不填也能跑，走本地兜底）
python server.py
# 浏览器打开 http://127.0.0.1:8623
```

方式二 · 仅预览界面（无需依赖，走前端内置演示）：

```bash
python -m http.server 8623 --directory ui
# 浏览器打开 http://localhost:8623/index.html
```

方式三 · Docker 一键运行（环境与依赖全部封装在容器内）：

```bash
cp .env.example .env      # 可选：填入 OPENAI_API_KEY（不填走本地兜底）
docker compose up -d --build
# 浏览器打开 http://127.0.0.1:8623
```

一键自检（不依赖任何密钥）：

```bash
python -m memory.demo     # 9 个场景断言：记忆压缩/会话隔离/RAG/持久化/权限/窗口裁剪/知识库两阶段/死规则/技能闭环
```

## 安全说明

> 本项目仓库内**禁止出现任何 API Key / 密钥**。

- 真实密钥只保存在本地 `.env`（被 `.gitignore` 拦截），代码仅从环境变量读取
- `.env.example` 只含占位符，可安全入库；**绝不把真实 `.env` 提交**
- 每次 `git push` 前需核对暂存文件清单，确认无密钥类文件后再上传
- 机器人铁律（状态记忆种子规则，可扩展）：不得编造订单/数据；回复跟随用户语言；不暴露工具内部 JSON；不读写本机 C 盘；密钥不入库

## 协作约定（重要，团队须遵守）

1. **不要随意创建新版本 / 复制新版本文档**：代码、文档、设计稿一律在原文件上迭代，禁止动不动另存为"xxx_v2 / 最终版 / 新版本"之类的新文件或新分支，避免仓库里出现一堆重复版本。
2. **不要读写本机 C 盘文件**：本项目的所有源码与产物只在项目目录（仓库工作区）内读写，任何操作都不允许涉及 C 盘路径（如 `C:\...`），防止误改系统文件、泄漏本地数据。
3. **每次上传仓库前必须检查密钥文件**：`git push` / 提交前，先 `git status` + `git diff --cached --name-only` 核对暂存清单，确认没有 `.env`、密钥、token、证书等 API Key 相关文件被纳入上传；发现即拦截修正后再推。

## Roadmap

- [x] Python 多智能体后端：supervisor 调度 + LangGraph ReAct + 工具调用
- [x] 买家客服链路：订单 / 物流 / 售后 / 推荐 + 中日双语
- [x] 卖家工作台：选品四步验证 + 供应商对比 + Listing 起草/图片/定价/履约
- [x] 认知层五模块 + 编排器（感知/思考/调度/反思/输出）
- [x] 五模块记忆系统：短期窗口 / 长期条目 / 知识库两阶段 / 死规则 / 技能闭环 + 权限审计
- [x] 分层总结管线：谈话结束一级总结 → M 个滚动合并二级总结 → 长期记忆
- [ ] 真实数据源接入：1688/阿里供应商 API、选品数据 API（接入槽位已预留）
- [ ] 知识库 / 技能检索升级向量检索（ANN 接入点已留 TODO）
- [ ] pywebview 封装 + PyInstaller 打包单 EXE
- [ ] LLM 意图路由替换规则路由（接口不变）

## 演示数据声明

界面内商品图、订单号、物流轨迹、供应商、市场数据均为演示用模拟数据，与真实系统无关；供应商 / 选品数据 API 接入槽位已预留（`tools/supplier.py` 的 `SUPPLIER_SOURCE` 等）。