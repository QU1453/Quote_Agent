# 变更记录（CHANGELOG）

> 约定：**只维护这一个应用，不另开版本副本**。每次改动在这里追加一段，
> 并写明「回滚到哪个提交、执行什么命令」，以便随时退回上一形态。

---

## 2026-09-21 · 附九：工具层增强——8 个新工具 + 2 处工具增强（方案 A，纯追加，框架零改动）

**背景**：深入源码评审后拍板的方案 A：只加工具 / 增强工具内容，不动 `core/` 与 `agents/base.py` 框架。

**新增 6 个注册工具 + 2 个纯函数工具**：

| 文件 | 新增 | 说明 |
| --- | --- | --- |
| `tools/research.py` | `research_keywords` | 关键词市场表（搜索量 / CPC / 竞争度，按 CPC 升序） |
| | `check_compliance` | 类目合规表（required_certs / 物流提示 / 平台规则 / risk_level） |
| | `calc_freight` | 头程运费试算（sea / air / express，按重量计费，含 eta） |
| | `calc_landed_cost` | 落地成本：复用 `calc_profit` + `_TARIFF` 关税（按采购+头程计征），含 margin 与 passed |
| `tools/listing.py` | `audit_listing` | 上架前自检：标题长度 / 五点数量与长度 / 违禁词 / 图片数，输出 suggestions |
| `tools/supplier.py` | `check_supplier_risk` | 供应商工商风险（`_RISK_NOTES` 9 家：诉讼 / 处罚 / 经营异常，高/中/低分级） |
| `tools/after_sales.py` | `classify_after_sale` | 售后问题分类（`_AFTER_SALE_TYPES` 四类关键词规则：质量 / 物流 / 七天无理由 / 咨询兜底） |
| | `draft_reply` | 客服回复草拟：分类 + `lookup_order` 取订单 → 话术 + next_actions + policy |

注册形态：6 个走 P0 双层注册表（`level="L0", cost="low"`，入全局表）；`classify_after_sale` /
`draft_reply` 沿用 `after_sales.py` 纯函数直调形态（与 `handle_return` 一致，不进全局表）。

**既有工具增强（非新增）**：`tools/listing.py` 的 `check_images` 修复了被截断的函数体（恢复 7 项
检查：主图直链 / 白底 / 无水印 / 附图≥5 / 总数≤9 / 无重复 / 场景图命名）；`draft_listing` 返回体
补 `a_plus` 字段。

**挂载（agent 双动作：import + tools 列表）**：

- `tools/__init__.py`：re-export 8 个新名字，`__all__` 同步（现 28 项）；
- `agents/research_agent.py`：tools 增 `research_keywords` / `check_compliance` / `calc_freight` / `calc_landed_cost`；
- `agents/listing_agent.py`：tools 增 `audit_listing`；
- `agents/customer_service.py`：tools 增 `classify_after_sale` / `draft_reply`；
- `agents/presales.py` 不动。

**未实施**：原方案 P2 里的 `convert_skill_md`（markdown 技能文档 → 结构化工具描述）——
代码库与文档均无其定义/引用，无真实需求支撑，经评估**跳过**，待有明确场景再补。

**验证**（`.venv\Scripts\python.exe` 冒烟，4 组断言全过）：全局注册表含 6 个新注册工具；
3 个 agent 的 tools 列表完整；8 个新工具直调返回结构齐全；边界兜底正常
（未知供应商 `found=False`、无关键词售后归"咨询/其他"）。

**回滚方式**（纯追加改动，按块删除即可；**勿对 tools/ 与 agents/ 用 git checkout**——
会把附五~附八期间同文件的其他改动一起回退）：

```powershell
# 无命令可跑，按下表手工删除新增块：
```

| 文件 | 删除内容 |
| --- | --- |
| `tools/research.py` | `research_keywords` / `check_compliance` / `calc_freight` / `calc_landed_cost` 四个函数（`__all__` 同步还原） |
| `tools/listing.py` | `audit_listing` 函数（check_images 修复与 a_plus 属修复/增强，**不建议回滚**——会带回截断损伤） |
| `tools/supplier.py` | `_RISK_NOTES` 表 + `check_supplier_risk` |
| `tools/after_sales.py` | `_AFTER_SALE_TYPES` 表 + `classify_after_sale` + `draft_reply` |
| `tools/__init__.py` | 新增的 import 行与 `__all__` 同名条目 |
| `agents/research_agent.py` / `agents/listing_agent.py` / `agents/customer_service.py` | 各自新增的 import 行与 tools 列表项 |

---

## 2026-09-20 · 层级纠正（项目 = 用户创建）+ 总控台四块 + 会话归属（第 1~3 步）

**需求（用户三次纠正后的定论）**：
① **项目是用户创建的**（如「水杯项目」），不是选品 / Listing；
② **选品 / Listing 是平级的两块区块**，只做视觉分区，**不占层级、不是中间层**；
③ **只有二级** —— 项目（一级）→ 细分功能板块（二级，点进去就是对话窗口），会话在窗口内切换；
④ 中间那层「产线」去掉；项目总览**沿用最早的形态**（两块并排 + 各自的细分板块一起列出，直接点板块进入）；
⑤ 总控台**保留**且必须"有反应"（选 A）：装四块内容。

规范文档：`.trae/documents/project-hierarchy-and-memory-layering.md`（含 3.4 总控台四块、3.2 会话归属、九·已确认决策）。

**一、层级模型（权威）**

| 级别 | 名称 | 谁创建 | 例 |
| --- | --- | --- | --- |
| 一级 | **项目 Project** | **用户创建** | 水杯项目、充电宝项目 |
| 二级 | **细分功能板块 Feature** | 系统固定（现有 11 个） | 需求验证、竞争分析、利润定价 |
| 窗口内 | **会话 Conversation** | 用户新建 | 同板块下的多段对话 |

区块（`research` 选品 / `listing` Listing）**不参与记忆分层**，只承担视觉分区与 `scope` 标签。

**二、后端（第 1~2 步）**

- `memory/short_term/projects.py`（新）：`ProjectRegistry` —— `projects` 表（`project_id PK / user_id / name / desc / status / created_at / updated_at`）+ 2 个索引，
  复用 `ConversationRegistry` 同一 sqlite 连接；方法 `create / get / list / update`。**归档 ≠ 删除**：只改 `status`，从不删行。
- `memory/short_term/registry.py`：`conversation_registry` 再扩 2 列（`project_id` / `feature_key`，走既有幂等 `_migrate()`）；
  新增 `count_by_feature()`（**一条 `GROUP BY scope, feature_key` SQL**，不做 N+1）、`set_owner()`（「移动到其他板块」的唯一入口）、
  `claim_owner()`（**仅当会话尚未归属时**才写入 —— 保证"会话归属固定为创建时所在的板块"，看别的板块不会把 L2 记忆层带漂移）。
- `server.py`：新增 4 个端点 `GET/POST /api/projects`、`PATCH /api/projects/{id}`、`GET /api/projects/{id}/tree`（一次回整棵树）；
  `/api/conversations`、`/api/conversation/start` 支持 `project_id` / `feature_key` 过滤与落库；
  `/api/ask` 与 `PATCH /api/conversations/{sid}` 支持归属透传（后者即"移动到其他板块"）；
  新增只读 `GET /api/memory/overview`（总控台④：L0 死规则 / 长期事实 / 热点技能，fail-open 空结构）；
  会话清单项补 `project_id` / `feature_key`（总控台③待办跳转要反查归属）。
- `agents/guide.py`：导航状态块加入**项目维度**（当前项目 / 项目清单 / 每段会话的「项目/板块」），actions 增加 `project_id` 校验；
  兜底建议不再说"进入选品 / 进入 Listing"（那已不是可去的地方），改为给项目级跳转。

**三、前端（第 3 步）**

- 左栏：从「总控台 + 会话清单」改为**顶部总控台独立入口 + 项目列**（每行带会话数/未完成断点标记 + 重命名 / 归档）。
- **总控台 `#view-console`（四块）**：① 全局对话（`scope=global`、不挂项目 → 只注入 L0，是"换话题不污染"的落点）
  ② 项目卡片（点进项目总览）③ 待办汇总（跨项目未完成断点，点一条直接跳回「项目 + 板块 + 会话」）④ 记忆与规则（L0 只读预览）。
- **项目总览 `#view-project`**：两个**平级区块并排**，各自把细分板块网格一起列出，板块上显示「N 段对话 · M 个未完成」，
  有断点的整卡描边；**直接点板块进对话窗口**（没有中间层）。
- **板块窗口 `#view-module`**：顶部新增**会话切换条**（当前会话名 + 数量 + 新对话 + 每行「⇄ 移动到其他板块」）；
  切板块/切会话不改变归属，跨板块只能由用户显式移动一次。
- 会话本地键由「按区块」改为**「项目 + 板块」**：`quote-sid-<pid>::<fk>`（总控台为 `quote-sid-global::`）。
- 新增项目弹窗（新建 / 重命名，复用 `.modal` 语言）；`#view-home` 与「选品/Listing 当项目」的旧模型整体移除。
- 清理：`BOARDS` 去掉已无用的 `sidKey` / `msgsId` / `tag`；删掉 `renderHome/renderProject(board)/openModule/enterScope/leaveScope/focusProject/loadRail/saveBoard/restoreBoard` 等旧函数与 `.board.is-entry/.board-foot/.project-free/.project-pick` 样式。

**四、本轮修掉的两个真 bug（都在前端）**

1. **总控台全局对话不留档 / 串档**：`saveTalk()` 只按 `activeTalk` 写档，不校验"可见的对话流里渲染的到底是哪段会话"，
   于是「总控台 →（点项目）→ 回总控台」这一路会把 A 的 DOM 存进 B 的档。修法：新增 `streamSid` / `streamView` **会话↔流配对**，
   `restoreTalk()` 是唯一建立配对处，`saveTalk()` 配对不成立就跳过（`send()` 里补 `activeTalk = await ensureSession()`）。
   > 实测：修复前从项目页回总控台，全局对话只剩 0 条；修复后 3 条与用户消息原样还原。
2. **会话菜单点「⇄」后立刻弹回列表**：`document` 上的"点别处收起"监听，因 `renderTalks()` 换了 innerHTML 而判定为"点在外面"，
   把刚进入的"选择目标板块"步骤重置了。修法：⇄ 与目标板块按钮上 `stopPropagation()`。
   > 另修：行内动作原本 `opacity:0`（悬停才显形）→ 触屏没有 hover 等于**不可见也点不到**，改为常驻 `.42` 透明度、悬停/聚焦增强；并补 `aria-label`。

**改动文件**：`server.py`、`agents/guide.py`、`memory/short_term/projects.py`(新)、`memory/short_term/registry.py`、
`memory/short_term/memory.py`、`memory/short_term/__init__.py`、`core/perception/session.py`、
`ui/index.html`、`ui/style.css`、`ui/app.js`、`.trae/documents/project-hierarchy-and-memory-layering.md`

**验证**：`node --check ui/app.js` + `ast.parse` 全通过（无控制台报错）；
后端 14 项 HTTP 断言全过（建项目 / 空名 400 / tree / 归属透传 / 归档后默认不可见而 `include_archived=true` 可见 / 不存在 404），
并单独验证**断点计数三处口径一致**（造 2 条断点记录覆盖 2 个会话 → `/api/conversations`、`/api/projects`、`/tree` 三处都返回 2）；
浏览器实测：总控台四块取到真实数据、项目总览两区块+板块计数、会话切换、**移动到其他板块（含跳转跟随）**、新建项目、归档回列表、
全局对话发送与跨视图还原。另核实子代理"`get_memory()` 恒为 None"的报告不成立（实测 `HAS_LONG_TERM=True`），**无需加兜底**。

**回滚**：`git checkout 3f53663 -- server.py agents/guide.py ui/ memory/short_term/ core/perception/session.py`
（数据层是加表 + 加列，回滚不影响旧逻辑；老会话 `project_id` 为空会进「未分类」，不丢数据）。

### 附：本轮打包报错的排查与修复（Quote.spec）

重新打包后 **exe 双击无反应**：进程活着、不监听端口、`quote-agent.log` 一行都不写。排查过程与结论：

- 用 `Start-Process -RedirectStandardError` 抓到真正的异常：
  `ImportError: DLL load failed while importing _ssl: 找不到指定的模块`（`main.py` 第 18 行 `import uvicorn` 就炸）；
- 根因：本项目跑在 **conda 的 venv** 里（`sys.base_prefix = D:\Application\ToolsInComputer\anaconda`），
  `_ssl.pyd` 依赖的 `libssl-3-x64.dll` / `libcrypto-3-x64.dll` 只躺在 `<base>\Library\bin`，
  **该目录不在 PATH 上**，而 PyInstaller 是靠 PATH 找依赖 DLL 的 → 这两个 DLL 没进包；
- 旁证：坏包 25.2 MB / 解包 237 个文件，好包 28.7 MB / 245 个文件（差的正是这两个 DLL）；
- 修复（`Quote.spec`）：新增 `binaries`，按「`sys.prefix` → `base_prefix\Library\bin` → `base_prefix\DLLs` → `base_prefix`」
  顺序找这两个 DLL 并显式打进包（找不到就跳过，纯 pip 环境不受影响）。改完体积回到 28.7 MB，exe 正常起窗并监听 8623。

### 附二：用户反馈的两件事（找不到"新建项目" / 整个程序非常卡）

**一、「看不到大的项目在哪里新建」→ 实际是折叠左栏把入口一起收走了**

`.rail[data-fold="true"] .rail-l2{grid-template-rows:auto 0fr 0fr}` —— 折叠只收起列表，
但当时「新建项目」按钮就在被收起的 body 里，头部只剩「⌄ 项目 N」一行，
于是折叠状态下**哪里都点不到"新建项目"**（用户只能看到板块窗口顶部的「新对话」）。修法：

- `rail-l2head` 里加常驻的 `＋`（`#railAdd`），折叠也不消失（`margin-left:auto` 靠右）；
- 项目总览页头部加「＋ 新建项目」（`#projNew`）—— 在项目内部也能直接建下一个，不必先回总控台；
- `setLang` 增加 `[data-i18n-title]` 处理：图标按钮的文字在 `title`/`aria-label` 上，换语言要一起换。

**二、「整个程序非常的卡」→ 逐帧重算模糊 + 全屏混合层**

按"降低每帧合成成本"的思路排查，找到三个叠加原因：

1. `.atmos-sheen` 的动画驱动的是 **`left`**（触发布局 + 重绘），还挂着 `filter: blur(34px)`；
2. `.atmos-grain` 用 **`mix-blend-mode: multiply` 全屏混合**，强制整帧重合成；
3. 全文 **18 处 `backdrop-filter`**，其中 `.board` / `.deck-card` / `.console-form` / `.bubble` / `.composer` / `.stream-head`
   都是**大面积且常驻**的磨砂面。而 `backdrop-filter` 必须每帧重新采样下层并重算模糊 ——
   下层恰好就是那个一直在动的氛围层，于是每帧要做近 10 次大面积模糊 + 一次全屏混合。

改动（保持「雾白与石墨」的观感不变）：

- 氛围层**取消所有常驻动画**（极光/掠光改为静态定格），`aurora` / `sheen` 两个 `@keyframes` 删除；
- 噪点层去掉 `mix-blend-mode`（0.038 的不透明度下肉眼几乎无差）；
- 大面积内容面**去掉 `backdrop-filter`**，改用更实一点的白色渐变 —— 它们背后只有平铺的雾白画布，
  模糊它视觉上等价于提高不透明度，却要为每块大卡片每帧重算；
- `backdrop-filter` 从 18 处降到 10 处，保留的都是**小面积常驻玻璃**（顶栏 / 左栏 / 状态胶囊 / 工具按钮 / 指导智能体面板）
  与**仅在弹窗打开时生效**的遮罩（modal / 调试后台）—— 静态下层下这些只算一次。
- 剩余 `infinite` 动画只有 5 处，都是小反馈指示（状态点 / 思考点 / 等待点 / 悬浮球脉冲），不在玻璃层之后，保留。

**三、解包目录不再写 C 盘（用户确认选"相对当前目录"）**

`Quote.spec` 的 `EXE()` 加 `runtime_tmpdir="."`：单文件模式的解包目录从 `%TEMP%`（C 盘，每次约 35 MB）
改为**启动时的工作目录**；双击启动时 CWD 就是 exe 所在目录（`dist/`，在 D 盘）。
实测：启动前后 C 盘 `_MEI*` 目录数 29 → 29（不再增长），解包出现在 `dist/_MEIxxxx`。

改动文件：`ui/index.html`、`ui/style.css`、`ui/app.js`、`Quote.spec`

**验证**：`node --check` 通过、无控制台报错；exe 内 CSS 实测 `@keyframes aurora|sheen` = 0、
`backdrop-filter` = 10、`infinite` = 5；exe 正常起窗并监听 8623，
新界面标记（`view-console` / `railAdd` / `projNew`）齐全、旧的 `view-home` 已不存在。

> 另外发现：C 盘 `%TEMP%` 下累积了 **29 个 `_MEI*` 目录、约 1.1 GB**（此前每次强杀进程留下的解包残留）。
> 属于纯临时垃圾，可安全删除；本次未擅自清理 C 盘。

---

### 附三：项目"凭空消失"（左栏折叠把项目列表压成 0 高度）

**现象**：用户反馈"新建一级项目后原来的就没了，一直是当前这一个，理应有三个"。
**事实核对**：直接查库 `dist/memory/short_term/data/short_term.sqlite` —— `projects` 表里**4 行都在**
（`111` / `2222` / `666` / `666`，全部 `open`），`GET /api/projects` 也返回 4 个；
前端（默认状态）左栏 4 行、总控台项目卡 4 张 —— **后端与渲染都没丢数据**。

**根因**：左栏的「折叠」把 `.rail-l2` 的网格行压成 `auto 0fr 0fr` → `#railBody` / `#railList` 高度实测 **0**，
而项目列表正是主入口：一折就整列不可见；此时若身处项目页（`#view-project`），页面上除面包屑外没有任何项目列表，
用户就会以为旧项目被新项目"顶掉"了，于是又建一个（库里 4 行、名称重复的现象由此而来）。

**附带发现**：这个折叠**从来不能省空间** —— `.rail` 宽度固定 252px，折叠只把内容压成 0 高度，
既不回收横向空间，又制造了一个"看不到数据"的死胡同状态。

**修复**：**移除折叠**（`.rail` 去掉 `data-fold`，删掉 `#railFold` 按钮、`setFold()`、`RAIL_FOLD_KEY`、
`railFold`/`railUnfold` 文案与全部 `[data-fold="true"]` 样式）。创建入口保留在左栏头部常驻的 `＋`（`#railAdd`）。
遗留的 `localStorage["quote-rail-fold"]` 已不再被读取（实测置 1 后列表仍照常显示）。

**验证**：`node --check` 通过、无控制台报错；实测 `#railFold` 不存在、`#railBody` 高 127px、`#railList` 高 40px、
项目行可见、`#railAdd` 可见；残留引用检查（`data-fold|rail-fold|railFold|railUnfold|setFold|RAIL_FOLD_KEY`）为 0 行。

---

### 附六：启动先出界面（加载页），并修掉"新建项目"的显示歧义

**一、启动改为「先开窗 → 显示加载 → 就绪后切主界面」**

原来的顺序是「起服务 → 等服务监听 → 才开窗」，所以窗口出现之前用户面对的是**完全空白**。改动：

- `main.py` 去掉模块级的 `from server import app`，改为把 **`"server:app"` 字符串**交给 `uvicorn`
  —— 让 langchain / fastapi / 记忆库这些重量级导入发生在**服务线程**里，主线程立刻开窗；
- 主线程用内联 HTML 先开窗显示加载页（雾白底 + 品牌 mark + 细扫描条，不依赖服务）；
- 后台线程等端口真正监听后再 `window.load_url()` 切到主界面；超时（120s）则换成错误页；
- 加载页自带一个 `fetch(..., {mode:"no-cors"})` 轮询兜底（内联页的源是 opaque，普通跨源 fetch 会被 CORS 拦掉；
  拿到 opaque 响应本身就说明服务起来了），双保险；
- `_say` 增加 `[ x.xx s]` 耗时前缀，以后"到底慢在哪段"一眼可查。

> **打包必须同步改**：`Quote.spec` 的 `hiddenimports` 里补上 `"server"` —— app 变成字符串后 PyInstaller
> 静态分析看不到 server，漏了会在冻结后 `ModuleNotFoundError: server`。

实测（冻结版，从双击算起）：**T+3.6s Python 起来 → T+4.0s 窗口+加载页出现 → T+9.3s 主界面就绪**。
即"有反馈"的时间从 9.3s 提前到 4.0s。剩下那 3.6s 是 PyInstaller 单文件解包（发生在 Python 启动之前，
代码插不进去）；要根治得改 **onedir 打包**（不再解包，窗口基本秒出），代价是交付从"单个 exe"变成"一个文件夹 + exe"。

**二、「新建项目」的显示歧义**

用户反馈：项目右边的 `＋` 和下面的「新建项目」按钮是同一个功能；项目旁的「3」让人以为顶上那个项目还能新建。
核对后发现：库里有 3 个未归档项目，**其中一个的名字就叫「3」**，于是左栏头部被渲染成 `项目 3 ＋`。

- 去掉左栏头部的 `＋`（`#railAdd`）—— 它当初是为了"折叠态下也能新建"加的，而折叠功能已删，属纯重复；
- 去掉头部计数徽标（没有它就永远不会和项目名混读）—— 左栏列表就在下方，计数没有增量价值；
  折叠分组「已归档 N」「已隐藏 N」的计数保留（那里必须显示数量）；
- 弹窗说清语义：kicker 改「一级项目 / TOP LEVEL」，并在字段上方加一行说明
  「项目是最外层容器：一个项目 = 一套独立的选品 / Listing 上下文，项目之间互不串味。」
- 顺手修：`.modal-msg` 的错误态类名不匹配（CSS 是 `.is-bad`，调用处传 `"bad"`），
  导致项目/会话弹窗的报错文案一直是灰色而非警示色 → 两个 `*Msg()` 助手统一归一化类名。

**改动文件**：`main.py`、`Quote.spec`、`ui/index.html`、`ui/style.css`、`ui/app.js`

**验证**：`python -m py_compile main.py` 与 `node --check ui/app.js` 通过；
开发态实测窗口在 0.14~0.21s 打开且服务就绪后成功切换；冻结版实测上面那组时间；
`GET /` 确认左栏头部已无 `railCount`/`railAdd`、`projectNewNote` 已随包发出；无控制台报错。

---

### 附七：启动期"字重叠" + 冷启动慢（外网字体阻塞 + 每次全新缓存目录）

**一、冷启动慢的两个真因（都是外因，不在 Python 侧）**

1. **Google Fonts 是渲染阻塞资源**。`ui/index.html` 里用的是普通
   `<link rel="stylesheet" href="https://fonts.googleapis.com/...">` —— 在它下载完之前，
   主界面**一个字都不会画**。本地 `style.css` 只要 11ms，但字体表要走外网。
2. **桌面版每次启动都是全新缓存**。pywebview 默认 `private_mode=True`，用临时 profile，
   冷启动缓存为空 → 上面那次外网往返**每次启动都要重来**（日志里那些
   `Failed to delete user data folder` 就是临时 profile 的清理动作）。

改法：

- 字体表改成**非阻塞**：`media="print" onload="this.media='all'"` —— 先用本机字体把界面画出来，
  字体到了再换上去（配合原有的 `display=swap`，观感就是一次字体替换，不再白屏等网）；
- `webview.start(private_mode=False, storage_path=BASE_DIR/".webview")` —— 让 WebView2 的 HTTP
  缓存**跨启动保留**。缓存目录落在项目目录（D 盘），不写 C 盘；`.webview/` 已加进 `.gitignore`；
- `index.html` 的 `<head>` 加一行内联 `<style>html{background:#e9edee}</style>` —— 从加载页切到
  主界面的**第一帧**即使 `style.css` 还没应用，底色也是同一片雾白，不再"白闪一下"。

**二、"开头新建项目字重叠"：判定为切页残留帧，逐帧消除**

先把四处「新建项目」全部实测了一遍（`getBoundingClientRect`，含 1 栏 / 2 栏两种总控台布局）：

| 位置 | 实测几何 | 结论 |
| --- | --- | --- |
| 左栏整行按钮 `#railNew` | 内容块 72–83px（图标）+ 91–141px（文字），间距 8px；与上方「项目」标题间距 11px | 无重叠 |
| 总控台 ② 卡片 `#deckNewProject` | `deck-meta` 右边界 873 → 按钮 887（间距 14px）；2 栏窄卡下同样是 14px | 无重叠 |
| 项目页头部 `#projNew` | `project-meta` 右边界 872 → 按钮 890（间距 18px）；图标与文字间距 7px | 无重叠 |
| 新建项目弹窗头部 | kicker 底 109 → 标题顶 119（间距 10px） | 无重叠 |

四处**稳定态都没有重叠**，因此判定用户看到的是**切换瞬间**的现象：WebView2 从内联加载页
（`NavigateToString`，无 URL 的文档）切到真实 URL 时，旧文档可能还留在合成里一帧，
视觉上就是"加载页的字和主界面的字叠在一起"。

改法（两条路径都走"先淡出再跳转"）：

- 加载页 CSS 加 `body{transition:opacity .2s} body.is-out{opacity:0}`；
- 加载页自身的兜底轮询改成 `leave()`：加 `is-out` 类 → 220ms 后 `location.replace(target)`；
- `main.py` 的 `_swap()` 也先 `evaluate_js("document.body.classList.add('is-out')")`、`sleep(0.24)`
  再 `load_url(url)`（`evaluate_js` 失败就直接切，淡出只是观感，不影响功能）。

这样残留的那一帧是**全透明的加载页**，即使叠上也看不见字。

**改动文件**：`main.py`、`ui/index.html`、`.gitignore`

**验证**：
- 加载页脚本单独跑通了——用 Python 渲染出真实加载页存到 `ui/_loading_probe.html`，
  经 `http://127.0.0.1:8623/_loading_probe.html` 打开：`typeof leave === "function"`、
  `typeof target === "string"`、`body.className === ""`（未就绪不淡出），**无脚本报错**；
  再把 target 指向真实服务，3 秒后页面已自动跳到主界面（`#railNew` 存在、标题正确）→ 兜底跳转链完整。
  探针文件用完即删。
- 字体表非阻塞生效：`link[rel=stylesheet]` 的 `media` 加载后已由 `print` 变 `all`，
  `document.fonts.check('12px "Instrument Sans"')` / `("Instrument Serif")` 均为 `true`；
- `python -c "import main"` 通过，`_loading_html()` 输出已无 `__URL__` 残留占位。

**回滚方式**（按粒度从小到大）：

```powershell
# 1) 只退回"淡出切换"
git checkout HEAD -- main.py        # 但会连带丢掉 storage_path 改动，见下条

# 2) 只退回字体非阻塞 + 内联底色
git checkout HEAD -- ui/index.html

# 3) 只退回持久缓存
#    main.py 里 webview.start() 改回不带参数即可
```

> 注意：`main.py` 本轮同时含"加载页淡出"与"持久缓存"两处改动，要单独回退某一处需手工编辑，
> 不能用整文件 checkout 代替。

---

### 附八：运行期数据统一收进 `data/`（不再散落在 `memory/`、`core/` 里）

**问题**：记忆与遥测的 SQLite 原本散在**四处**，而且都嵌在源码包目录里：

```
memory/data/{access,interrupt,knowledge,skill,state}.sqlite
memory/short_term/data/short_term.sqlite
memory/long_term/data/{long_term.sqlite,memories.hnsw}
core/telemetry/data/telemetry.sqlite
```

后果是：源码树里混着运行期数据；打包后会在 exe 旁边凭空长出 `memory/`、`core/` 两个目录，
和 `Quote.exe` 堆在一起，看着像代码被吐了出来。

**改法**：运行期数据根目录从 `BASE_DIR` 换成 `BASE_DIR / "data"`，并把各模块路径里多余的
`/ "data"` 层级去掉。改完的布局：

```
data/
├── memory/
│   ├── access.sqlite          权限审计
│   ├── interrupt.sqlite       中断续跑台账
│   ├── knowledge.sqlite       知识库
│   ├── state.sqlite           状态记忆（死规则）
│   ├── skill.sqlite           技能记忆
│   ├── short_term/short_term.sqlite
│   └── long_term/{long_term.sqlite, memories.hnsw}
└── telemetry/telemetry.sqlite 调试后台 trace 库
```

**改到的 9 处**：`config.py`（根目录）、`memory/manager.py`（短期 + 长期 + 知识/状态/技能/中断的
lazy 构造）、`memory/__init__.py`（进程级单例）、`memory/access.py`、`memory/state/memory.py`、
`memory/skill/memory.py`、`memory/knowledge/indexer.py`、`memory/interrupt/memory.py`、
`core/telemetry/recorder.py`、`agents/guide.py`（单独读中断台账那条路径）。

> ⚠️ `agents/guide.py` 是最容易漏的一处：它在"长期记忆不可用"的兜底分支里**自己 new 了一个
> `InterruptLog`**，只改 `memory/` 包里的默认值会漏掉它，导致该分支读到一个空的新库
> （表现就是"断点突然全没了"）。用 `grep '"data"'` 通盘扫一遍才算改完。

**数据迁移**（关键：不能只改路径，否则等于清空用户的记忆）：

按"旧目录 → 新目录"整目录搬迁，**连同 `-wal` / `-shm` 一起搬**（WAL 模式下主库单独搬会丢最近提交）。
开发态与 `dist/`（打包版）各搬一遍：

| 旧 | 新 |
| --- | --- |
| `memory/data/` | `data/memory/` |
| `memory/short_term/data/` | `data/memory/short_term/` |
| `memory/long_term/data/` | `data/memory/long_term/` |
| `core/telemetry/data/` | `data/telemetry/` |

搬完删掉空掉的旧目录（只删 `data` 这一层，`memory/`、`core/` 是源码目录不能动）。

**其他同步改动**：

- `.gitignore` / `.dockerignore`：四处旧路径规则合并成一条 `data/`（`memory/demo_data/` 保留）；
- `docker-compose.yml`：四个 volume 合并成 `quote_memory:/app/data/memory` +
  `quote_telemetry:/app/data/telemetry` —— 不跟着改的话，容器里数据会写进匿名层、重建即丢；
- `README.md`：审计库路径示例改 `data/memory/access.sqlite`。

**顺带清掉的残留**（都不是代码，是崩溃/强杀留下的解包目录）：

- 项目根目录 `_MEI*` × 6 ≈ **233.7 MB**（之前把 exe 的 CWD 指到了项目根，解包就落在那里）；
- `dist/` 下 `_MEI*` × 11 ≈ **402.9 MB**。

**验证**：

- 迁移后**打包版**（`dist/Quote.exe`，取 `dist/data/`）实测 `GET /api/projects` = **7 个项目**、
  `GET /api/conversations` = **41 段会话** —— 与迁移前一致，数据没丢；
- 开发态（项目根 `data/`）实测 **2 个项目 / 26 段会话** —— 同样完好；
- `dist/` 顶层现在只有 `Quote.exe`、`data/`、`.webview/`、`quote-agent.log`，`memory/` 与 `core/` 不再出现；
- 4 个旧目录（`memory/data`、`memory/short_term/data`、`memory/long_term/data`、`core/telemetry/data`）
  在开发态与 `dist/` 下均已确认不存在。

**回滚方式**：

```powershell
# 1) 代码回退
git checkout HEAD -- config.py memory/ core/telemetry/recorder.py agents/guide.py `
                     .gitignore .dockerignore docker-compose.yml README.md

# 2) 数据搬回去（把上表反过来做一遍，同样要连 -wal/-shm 一起搬）
```

> 只回退代码不回退数据 = 应用会去旧路径找不到库、当空库启动（等于"记忆全没了"）。
> 两者必须一起回退。

---

### 附五：操作改为右键菜单（重命名 / 移除）+ 修掉一个"隐形点击拦截层"

**需求**：一级项目与二级会话的操作都改成「右键 → 选择」；行内小图标（✎ / ⌫ / ⇄）去掉。
用户确认：**「移除」只用可恢复语义**（项目=归档、会话=隐藏），不做不可恢复的物理删除。

**实现**：

- 新增共用的右键菜单组件（`#ctxMenu`，`role="menu"` + `menuitem`，打开即聚焦首项，键盘用菜单键 / Shift+F10 同样可用）：
  - 项目行 → 重命名 / **移除**（`归档 · 可恢复`）
  - 已归档项目行 → 恢复 / 重命名
  - 会话行 → 重命名 / 移动到其他板块 / **移除**（`隐藏 · 可恢复`）
  - 已隐藏会话行 → 恢复
  - 「移动到其他板块」做成菜单内二级步骤（11 个板块 + 取消），不再占行内图标
- 会话侧新增**「已隐藏 N」分组**（对应项目的「已归档 N」）：`loadTalks` 改为 `include_hidden=true` 并按 `hidden` 拆分；
  移除会话后**自动展开该分组**，避免"东西又不见了"的错觉；若移除的正是当前会话，自动切到另一段（没有则新建一段）。
- 会话重命名新增单字段弹窗 `#renameModal`（项目重命名继续用带描述的 `#projectModal`）。
- 删除全部行内图标相关的 DOM / CSS / 事件（`rail-acts` / `rail-act` / `talks-pick` / `talksMode` / `moveSid`）。

**顺带修掉 3 个连带 bug（都是实测才暴露的）**：

1. **菜单项点击后二级菜单立刻被关**：`renderCtx()` 会替换菜单 DOM，点击事件冒泡到 document 的「点别处收起」时判定为"点了外面"。
   → 菜单项处理器加 `stopPropagation()`（与之前 ⇄ 那次同类问题）。
2. **「滚动即收起」把菜单内部滚动也算进去了**：菜单高过视口时会出现内部滚动条，一滚菜单就关，下半部分的项永远点不到。
   → 滚动处理器忽略"事件目标在菜单内"的情况。
3. **`.guide-panel{display:flex}` 盖掉了 `[hidden]{display:none}`** —— 收起状态的指导智能体面板仍然占 **372×546** 的布局并**拦截点击**，
   位置正好压在窗口右下角：**模块视图的「发送」按钮区域可能点不动**。→ 补 `.guide-panel[hidden]{display:none}`。
   实测：修复前 `elementFromPoint(120,200)` 返回 `guide`（整块被罩住），修复后返回真实元素；`.guide` 尺寸从 261×213 回到 52×52。
   > 这个同时也是"页面某些地方点不动"的元凶。

**改动文件**：`ui/index.html`（右键菜单 + 重命名弹窗）、`ui/style.css`（`.ctx*` + `.guide-panel[hidden]`）、`ui/app.js`

**验证**：`node --check` 通过、全程无控制台报错；实测：项目/会话右键菜单内容与标题正确、首项自动聚焦、
项目重命名打开弹窗且预填、移动到其他板块二级菜单**保持展开**（11 项）、
移除会话后 DB `hidden=1` 且前端出现「已隐藏 1」分组、当前会话自动切换。
未能在浏览器里点到最后一格：调试面板视口在测试中途被缩到 313×393 直至 0×0，
「已隐藏项 → 恢复」「二级菜单里点具体板块」「会话重命名保存」这三条只走了同一套已验证的处理器与同一批
`PATCH /api/conversations/{sid}` 分支，**未做端到端点击验证**（逻辑已复核）。

---

### 附四：「归档 ≠ 删除」在界面上不存在（点了 ⌫ 项目就再也找不回来）

**追查过程**：附三修掉折叠后，又核对了用户 exe 的库 —— 4 个项目不是"丢了"，而是 `status='archived'`，
归档时刻集中在 **11:21:11–11:21:20（9 秒内 4 次）**；而 `/api/projects` 默认 `include_archived=false`，
**前端也从来没请求过已归档项目** —— 于是点了 ⌫ 之后，项目从界面上永久消失、且**没有任何入口能恢复**。

**修复**（把"归档 ≠ 删除"这条契约真正落到界面上）：

- `loadProjects()` 改为 `include_archived=true`，前端按 `status` 拆成 `projects` / `archivedProjects`；
- 左栏列表末尾新增**「已归档 N ⌄」可展开分组**（`archivedGroupHTML()`）：展开后列出已归档项目（半透明），
  每条带 `↺ 恢复`（`PATCH status=open`）；分组状态存 `showArchived`；
- **归档后自动展开该分组** —— 用户点完 ⌫ 立刻看见"它去了已归档，并且能一键恢复"，不会再误判为数据丢失；
- 总控台 ②「项目」卡在"没有进行中项目但存在已归档"时给出明确指引
  （`deckProjectsAllArchived(n)`），不再只说"还没有项目，点新建"；
- 新增文案 `railArchived` / `railRestore` / `deckProjectsAllArchived`（中/日双语）、样式 `.rail-arch*`。

**验证**（开发态，库里正好 1 进行中 + 1 已归档）：`已归档 1 ⌄` 正常渲染且默认收起 →
点开显示 `测试项目 9 小时前 ↺`（无障碍名「恢复」）→ 点恢复后该项目回到进行中列表、归档分组消失；无控制台报错。

> 用户 exe 库里那 4 个项目（`111` / `2222` / `666` / `666`）**未擅自改动**，仍为归档态 ——
> 现在打开就能在左栏「已归档」里看到并逐个恢复。

---

## 2026-09-19 · 导航改三级（总控台 → 项目 → 模块）+ 调试后台改弹窗
> 注：本轮"三级"里的"项目"实为选品 / Listing，是理解偏差；层级模型已在 2026-09-20 那条里纠正（改为「项目=用户创建，只有二级」）。

**需求**：① 调试后台不要跳浏览器标签页，要以弹窗形式呈现；② 总控台要有"选择项目"这一步 —— 点进项目之后，才是选 Listing 还是选品。

**一、导航从两级改三级**

之前 `#view-home` 把两张项目卡片和模块网格摊在同一页，点模块直接进控制台，中间没有"进入项目"这一步。现在：

| 层级 | 视图 | 内容 |
| --- | --- | --- |
| 总控台 | `#view-home` | 只放**项目选择卡片**（选品 / Listing），每张显示模块数与「进入项目 →」 |
| 项目页 | `#view-project`（新） | 该项目头部（编号 / 名称 / 描述 / **自由对话**入口）+ **该项目的模块网格** |
| 控制台 | `#view-module` | 左侧模块表单 + 右侧对话流（不变） |

- `renderHome()` 重写为「项目入口卡片」渲染（事件只首次绑定，因为语言切换会重渲染，避免重复挂监听）；
- 新增 `renderProject(board)` / `openProject(board)`；`boardDesc()` 映射 i18n 里的 `board*Desc` 词条；
- 左栏二级与视图联动：`openProject()` → `enterScope(board)` 展开该项目会话；点一级「总控台」→ `leaveScope()` 收起；
- 面包屑补上「项目」一级（可点返回项目页）：`总控台 / 01 选品 / 需求验证`；
- 控制台的「返回」按钮：原来回总览，现在**回项目页**（`back` 文案改为「返回项目」/「プロジェクトへ戻る」；`crumbHome` 改为「总控台」/「統括コンソール」）；
- `focusProject()` 收敛为 `openProject()` 的别名，保证「悬浮指导智能体点建议跳转」与「点项目卡片」走同一条状态路径；
- 项目页 `data-scope` 跟随项目，编号徽标与「自由对话」按钮改用对应信号色（选品=松青 / Listing=靛蓝）。

**二、调试后台改弹窗**

- 顶栏 `<a href="/debug" target="_blank">` → `<button id="debugBtn">`；
- 新增 `.dock` 弹窗层（`#debugDock`）：磨砂遮罩 + 近全屏面板 + 头部（kicker / 标题 / 刷新 / 在浏览器打开 / 关闭）；
- `/debug` 以 **iframe 懒加载**嵌入（首次打开才创建，避免每次启动都拉遥测数据）；「刷新」重设 `src`；
- ESC 关闭；`aria-hidden` 同步。保留了「在浏览器打开」作为逃生口（要看宽屏时用）。
- 确认不会被子框架策略挡住：server.py 只挂了 CORS 中间件，**没有** X-Frame-Options / CSP 头。

**改动文件**：`ui/index.html`、`ui/style.css`、`ui/app.js`

**验证**：`node --check ui/app.js` 通过；`server.py`/`registry.py` `py_compile` 通过；
HTML 新元素（`debugDock/debugBtn/debugBody/debugReload/view-project/projGrid/projFree/data-project/board-foot`）全部命中；
旧的 `href="/debug"` 跳转链接已移除；JS 中 `openProject/renderProject/initDebugDock/debugOpen` 与 CSS 中
`.board.is-entry/.project-head/.dock-panel/.dock-frame/#view-project` 全部就位；确认无残留的 `art.dataset.board` 绑定。

**回滚**：纯前端三文件，`git checkout 3f53663 -- ui/index.html ui/app.js ui/style.css`（会一并退回此前几轮的界面改动）。

---

## 2026-09-19 · 左栏（一级总控台 + 二级会话选择）+ 悬浮只读指导智能体

**需求**：① 一个窗口里换话题会互相污染上下文，需要"干净的会话"；② 需要一个全局入口；③ 最终定为「一级只有总控台 → 选项目进入 → 才出现该项目的会话选择」；④ 再加一个悬浮窗，里面是**只读**的指导智能体（可读全部记忆，指导用户去哪儿）。

**一、后会话成为一等公民（后端）**

- `memory/short_term/registry.py`：`conversation_registry` 表**扩 4 列** —— `scope`（research/listing/global）、`title`、`hidden`、`updated_at`；
  新增 `_migrate()`（`PRAGMA table_info` 判断缺列才 `ALTER TABLE`，幂等），并**一次性回填**老库：`scope` 由 `agent_name` 前缀推断、`updated_at` 取 `ended_at/started_at` 兜底。
  四处硬编码 SELECT 列统一为模块常量 `_COLS`，`_row_to_dict` 扩到 12 列（返回值只多不少）。
  新增 `touch / set_title / set_title_if_empty / set_hidden / list_conversations`。
- `memory/interrupt/memory.py`：新增 `open_by_session()` —— **一条 SQL** 拿到每个会话最新的 open 断点（不做 N+1）。
- `core/perception/session.py` / `memory/manager.py`：`start_conversation(..., scope="")` 贯通。
- `server.py`：
  - `POST /api/conversation/start` 增 `scope`；
  - `GET /api/conversations`（注册表 + 断点一次合并，含 `has_open_interrupt/pending_tool/step_index`）；
  - `PATCH /api/conversations/{sid}`（只允许改 `title`/`hidden`，不存在回 404）；
  - `AgentService.ask()` 收尾调 `set_title_if_empty(sid, message[:30])` —— 首条提问当占位标题；
  - `registry.set_summary()` 用一级总结的 `topic` 覆盖标题（**总结主题 > 首条提问**）。

**二、悬浮只读指导智能体（`agents/guide.py` 新建 + `POST /api/guide`）**

- **只读是双重保证，不靠提示词自觉**：`GUIDE_CALLER = MemoryCaller("guide_agent", "L0")` +
  **模块不注册任何写工具**，且只调用只读接口（`assemble_context` / `registry.list_conversations` / `interrupt.open_by_session`）；
  将来若误加写工具，`guard()` 会直接判越权。
- 单轮 `ChatOpenAI`（temperature 0.2，**不走 ReAct/LangGraph**，因为它没有工具）；JSON 输出 `{advice, actions[]}` 用正则抓第一个 `{...}`（容忍 ```json 围栏与前后解释），解析失败就整段当建议；
  `actions` ≤3 且**校验 session_id 真实存在**（模型编造的丢弃）。
- **无 Key / 失败走真实数据兜底**：有断点 → 「你上次在「选品」里有一段「XXX」没跑完，停在第 N 步（工具 X 未执行）…」+ 续跑 action；无断点 → 选品/Listing 两条 action。
- 接口是**同步 `def`**（内部阻塞调用 LLM，不能占事件循环，与 `/api/ask`、`/api/settings/test` 一致）。

**三、前端（`ui/index.html` / `style.css` / `app.js`）**

- 布局：`body` 下新增 `.shell`（横向 flex）包住左栏 `.rail` + `#app`；左栏沿用「雾白与石墨」语言（发丝线 + 玻璃 + 磨砂），
  **信号色跟随项目**（选品=松青 / Listing=靛蓝，用 `--scope` 变量切换）。
- **一级恒为「总控台」**（唯一项）；**二级只在"已进入项目"时出现**，`grid-template-rows` 折叠（箭头双向切换，状态存 `localStorage`）。
  `openModule()` → `enterScope(board)` 展开二级；`goHome()` → `leaveScope()` 收起 —— 不进项目就不展示任何项目的会话，从结构上杜绝串台。
- 会话行三态：进行中（绿）/ **有未完成断点（琥珀点 + 整行描边，且显示"第 N 步"）** / 已结束（灰）。
- 切换会话：消息按 `session_id` 留档在 `localStorage`（`quote-log-<sid>`，单段上限 240k 字符，配额异常静默跳过），切回原样还原（含思考小字与结果卡片）。
- 「+ 新对话」：先 `POST /api/conversation/end` 归档上一段（触发一级总结 → 长期记忆积累），再以 `scope` 开新会话。
- 「隐藏 ≠ 删除」写进注释与契约：隐藏只影响清单可见性，注册表行 / 短期记忆 / checkpoint / 中断记录一律保留。
- 悬浮窗：右下角常驻球（呼吸环）→ 展开面板（`position:fixed`，不遮挡主区）；建议渲染成**可点按钮**，点击直接 `focusProject(scope)` + 落到该项目自由对话模块并切到那段会话。

**改动文件**：`memory/short_term/registry.py`、`memory/interrupt/memory.py`、`core/perception/session.py`、`memory/manager.py`、`server.py`、`agents/guide.py`（新）、`ui/index.html`、`ui/style.css`、`ui/app.js`

**验证**：`node --check ui/app.js` 通过；6 个 py 文件 `py_compile` 通过；开发服务器端到端冒烟测试：
scope 贯通 ✓ / 清单与标题 ✓ / 改名 ✓ / **隐藏≠删除（默认不可见、`include_hidden=true` 可见、取消隐藏恢复）** ✓ /
**打断 → 台账 → 清单琥珀态（`has_open_interrupt=true, pending_tool=run_product_research, step=1`）** ✓ / 指导智能体 `mode=llm`、回复 219 字、1 条带真实 session 的 action ✓

**回滚**：本轮新增 `agents/guide.py` 需手动删除；其余为既有文件改动，`git checkout 3f53663 -- <file>` 可逐文件退回（注意会一并退回此前几轮的改动）。

---

## 2026-09-19 · 修复：设置面板的 API Key 栏位看起来被"清空"

**现象**：在「设置」里填入 API Key，点「保存并应用」或「测试连接」后，Key 输入框变空；
系统其实已保存（`/api/settings` 返回 `api_key_set=true`），但用户会以为"刚填的 Key 丢了"，反复重填。

**根因（纯前端展示问题，保存逻辑没问题）**
- 后端**有意不回传明文密钥**，只回 `api_key_masked` 掩码——这是安全设计，不能改；
- 前端却在**打开设置**（`openSettings`）与**保存成功**（`saveSettings`）两处都把输入框清空；
- 输入框 placeholder 又是静态的「粘贴你的密钥…」→「空栏位 + 请粘贴」在语义上等于"未配置"。

**修复**：`ui/app.js` 新增 `paintKeyField()`，把"已保存"这件事写进 **placeholder**：

| 状态 | 占位文案 |
| --- | --- |
| 已配置 | 中文 `已保存：3814••••••rAFj · 留空则不修改` / 日文 `保存済み：… · 空欄なら変更しません` |
| 未配置 | `粘贴你的密钥…` / `キーを貼り付け…` |

接入三处：打开设置、保存成功后、测试连接后（**测试后保留用户输入**，方便接着点「保存」）；
语言切换时重绘占位（用 `_lastKeyStatus` 记住上次状态，避免中日切换后占位文案不更新）。

**为什么用 placeholder 而不是回填明文**：回填意味着密钥要在响应体里明文往返，且任何一次误点"保存"
都可能把掩码当成新密钥写回。留空 = 不修改（后端 `if (k) body.api_key = k` 已保证），所以空栏位是安全的。

**改动文件**：`ui/app.js`（`paintKeyField` + `keyPhEmpty`/`keyPhSaved` 双语词条 + 三处接入）
**验证**：`node --check ui/app.js` 通过；`/api/settings` 实测返回 `api_key_set=true`、`api_key_masked=3814••••••rAFj`
**回滚**：撤销 `ui/app.js` 中 `paintKeyField` 的 1 个函数 + 2 条 i18n 词条 + 3 处调用即可（不影响其它改动）

---

## 2026-09-18 · 思考过程可视化 + 打断续跑（记忆层第六模块）

**本轮起点（回滚目标）**：`3f53663`（上一轮的界面重设计与品牌更名尚未提交，与本轮改动同属一批未提交改动）

**本轮落地提交**：待提交（推送后回填哈希）

### 一、思考过程小字（聊天框内实时可见）

新增**运行态总线** `core/telemetry/live.py`（纯内存、按 `request_id` 隔离、线程安全、fail-open、超时自动回收）。
与 `core/telemetry/recorder.py` 分工明确：**recorder 事后落库给 /debug 看，live 事中给用户看**。

`agents/base.py` 从 `graph.invoke` 改为 `graph.stream(stream_mode="updates")` 流式驱动，边跑边上报步骤。
步骤只带 `kind / name / detail` 数据，**措辞全部由前端本地化**（中日双语自动切换，后端不写文案）：

| step | 含义 |
| --- | --- |
| `stage` · `input`/`route`/`context`/`output`/`fallback`/`constraint` | 阶段推进（含路由结果、记忆装配块数与 token 估算） |
| `llm` · `think` | 第 N 步推理 |
| `tool` · `<工具名>` + `pending`/`ok`/`error`/`skipped` | 调用 / 返回 / 失败 / 未执行 |
| `note` · `resume_found`/`resume_closed`/`interrupt`/`interrupt_late` | 续跑与打断提示 |

前端等待期间小字实时滚动（450ms 轮询新接口 `GET /api/think/{request_id}`）；
**回答出来后自动折叠成一行「已思考 N 步 · 调用 M 个工具」**，点标题栏可展开看全；
被打断时保持展开，让用户直接看清停在哪一步。

### 二、打断交互 + 中断日志（记忆层第六模块）

**新模块** `memory/interrupt/`（SQLite `memory/data/interrupt.sqlite`；权限矩阵登记 write/modify = L0，
与短期记忆同档）。一条记录回答四个问题：

1. 停在哪一步 → `stage` + `step_index`
2. 干到哪了 → `completed_tools`
3. **被打断的是谁、是否被用户打断** → `pending_tool` + `tool_interrupted_by_user`
4. 已经说了什么 → `partial_reply`（便于接话）

协作式打断的三个检查点（单次 LLM 请求发出后无法从外部掐断，这是物理限制）：

| 检查点 | 时机 | 行为 |
| --- | --- | --- |
| ⓪ 入口 | 还没开跑就已请求打断 | 连模型都不调用（零成本停止） |
| ① 工具执行前 | 模型刚决定调工具 | `<code>_guard_tool</code>` 把工具**短路成占位返回** → 工具真不执行、零副作用，tools 节点照常产出 tool 消息 |
| ② 工具返回后 | 工具已跑完 | 不再推进下一步推理，如实记录「工具已执行完、非用户打断」 |

**自动续跑**：`inject_block()` 产出「上次未完成」提示块 → `core/perception/context.py` 作为新上下文块
`resume` 注入（`config.INTERRUPT_RESUME_INJECT` 可关闭）→ agent 从断点继续、不重复已完成步骤；
该会话本轮正常完成时，把 `open` 记录收尾为 `resumed`。

**前端**：跑动时输入框旁出现「打断」按钮 → `POST /api/ask/interrupt`；被如实告知打断结果话术；
**调试后台** `/debug` 新增「中断记录」面板（`GET /api/interrupt/recent`）：时间 / 会话 / 路由 / 停在哪一步 /
已完成工具 / 被打断的工具 / 打断方 / 续跑状态。

### 三、顺带修掉的四个真缺陷（不修这些，本功能不成立 / 打包后白做）

| 文件 | 问题 | 修复 |
| --- | --- | --- |
| `server.py` | `/api/ask`、`/api/conversation/end`、`/api/settings/test` 用 `async def` 包阻塞式 LLM 调用，**占死事件循环**——回答期间 `/api/think` 轮询与打断请求全部超时（实测 60s） | 改为同步 `def`（FastAPI 自动丢线程池），并在代码里注明原因 |
| `core/telemetry/live.py` | 初版用 `threading.local` 传 `request_id`，实测**工具在 `ThreadPoolExecutor-*` 线程里执行**，thread-local 跨不过去 → 工具短路判定永远为假（打断退化成「工具照跑」） | 改用 **ContextVar**（已实测可随上下文传播进工具线程） |
| `config.py` + `memory/*` + `core/telemetry/recorder.py` | 各记忆模块用 `Path(__file__)` 推导数据目录，单文件打包后 `__file__` 指向解包临时目录 `%TEMP%\_MEIxxxx` → **所有库（会话/长期记忆/知识库/状态规则/技能/中断台账/遥测/审计）退出即被清空、重启全丢**，而且往 C 盘写（违反项目铁律） | 新增 `config.DATA_DIR`（= `BASE_DIR`，打包后即可执行文件所在目录），9 处默认路径统一改走它。**开发态解析结果与改造前逐字一致，零行为变化**；打包后数据落在 exe 旁边（与 .env / 日志同一约定） |
| `agents/base.py` `_heal_dangling_tool_calls` | 打断 / 进程被强杀可能让 checkpoint 留下「有 `tool_calls` 却无任何 `tool` 结果」的 AI 消息。这种残缺历史会被模型接口整条拒绝 → **该专职智能体每轮静默失败、悄悄退化到客服智能体**（用户只看到"选品智能体好像失灵了"）。且失败轮次还会继续追加用户消息，把残缺消息挤到历史中间 | 每轮开跑前全量扫描历史，把未应答的 AI 消息连同孤儿工具结果一并清掉（`RemoveMessage`）。已用真实残缺数据验证：修复前追问 `route=customer_service`，修复后回到 `route=research` 并完整跑通四步工具链 |

实测证据（数据目录）：修复前 `%TEMP%\_MEI000084842\memory\data\interrupt.sqlite` 等 7 个库出现在解包临时目录；
修复后 exe 的库落在 `dist\memory\data\`、`dist\core\telemetry\data\`，**重启 exe 后 `/api/interrupt/recent` 仍能读回旧记录**。

> 附带发现（本轮未改动，属既有行为）：`set_current_session` / `set_current_trace` 同样用 `threading.local`，
> 因此 `_guard_tool` 的循环守卫会话键在工具线程里取不到值、实际退化为全局计数。
> 本功能不依赖它，留作后续单独评估。

### 四、实测记录（真实 GLM，非模拟）

- **检查点①**（工具未执行）：思考流 `tool run_product_research pending → skipped`；
  中断日志 `stage=tool, step=1, completed_tools=[], pending_tool=run_product_research, tool_interrupted_by_user=true`
- **检查点②**（工具已跑完）：`completed_tools=[run_product_research], pending_tool="", tool_interrupted_by_user=false`
- **续跑**：下一轮 `context` 块数 1 → 2、token 161 → 335，思考流出现 `resume_found` 与 `resume_closed`，
  日志 `open → resumed`
- **不破坏 agent 线程**：被打断后同一专职智能体下一轮仍正常作答。
  > ⚠️ 这一点在修复前是坏的：检查点①曾在 checkpoint 里留下「有 tool_calls 却无 tool 结果」的残破历史，
  > 该 agent 下一轮调模型被接口拒绝 → 静默退化到客服智能体（思考流里会莫名出现两个「第 0 步推理」）。
  > 现在改为「工具短路 + 占位工具消息」，历史始终完整。

### 五、回滚方式

```bash
# 只退回本轮的「思考流 + 打断续跑」能力（保留上一轮的界面重设计与品牌更名）
git checkout 3f53663 -- agents/base.py core/telemetry core/dispatch/orchestrator.py \
  core/perception/context.py core/output/formatter.py memory/manager.py memory/access.py \
  server.py config.py ui/

# 连上一轮的界面重设计与品牌更名一起退回
git checkout 3f53663 -- .
```

回滚后还需删除本轮**新增**的文件（`git checkout` 不会删）：

```bash
rm -f core/telemetry/live.py
rm -rf memory/interrupt
```

> ⚠️ `memory/data/interrupt.sqlite` 是**续跑台账**（记录上次被打断到哪一步、好让下轮接着做），
> 删除即丢失续跑状态，请按需保留。回滚后重新打包：先结束所有 `Quote.exe` 进程，再执行 `python build.py`。

---

## 2026-09-18 · 界面重设计（雾白与石墨）+ 桌面版单文件 exe 构建通过

**本轮起点（回滚目标）**：`3f53663` —— 浅色「白瓷与细金」界面 + 长期记忆强依赖 hnswlib

**本轮落地提交**：待提交（推送后回填哈希）

### 一、界面：浅色「白瓷与细金」→「雾白与石墨 / Frost & Graphite」

| 文件 | 改动 |
| --- | --- |
| `ui/style.css` | **整体重写**。冷调雾白画布 `#e9edee` + 石墨墨色字阶 + 松青（选品）/ 靛蓝（Listing）双信号色；层次由「磨砂玻璃层 + 发丝线 + 排版」承担；氛围层四件套（极光晕 / 经纬织纹 / 颗粒 / 掠光）；动效统一为「模糊解冻」入场（透明度 + 位移 + 模糊，长尾减速缓动），并尊重 `prefers-reduced-motion` |
| `ui/index.html` | 字体换成 Instrument Serif（衬线展示）/ Instrument Sans（正文）/ DM Mono（数据）+ Noto Sans·Serif SC·JP（中日）；新增题头索引标记与氛围层元素。**所有 id / class 契约未变** |
| `ui/app.js` | **零改动**（功能逻辑、卡片渲染、会话管理完全不动） |

功能完整保留：两个板块（选品 / Listing）、11 个细分模块、模块表单 → 结构化提问 → 8 类结果卡片、
中/日双语、双板块独立会话、状态胶囊四态、设置面板六项（含 API Key 校验与连接测试）、调试后台入口。

> 注意：`ui/debug.html` / `ui/debug.css`（调试后台）沿用原样式，本轮未改。

### 二、修复：hnswlib 缺失时整套记忆层无法导入（阻塞 exe 构建）

PyPI **没有 hnswlib 的 Windows 预编译包**（任何 Python 版本都没有，只能源码编译，需 Visual C++ Build Tools），
而项目声明的「无 hnswlib 时降级」这条路实际上是坏的——`import server` 直接崩。

| 文件 | 问题 | 修复 |
| --- | --- | --- |
| `memory/__init__.py` | 返回值注解里的 `MemoryManager \| None` 在运行时求值，`MemoryManager = None` 时抛 `TypeError`，`import memory` 失败 | 补 `from __future__ import annotations` |
| `memory/long_term/ann.py` | 模块级 `import hnswlib`，缺失即 `ModuleNotFoundError`；连 `memory.long_term.chunker` 都导不进来（约束层 `core/constraint/budget.py` 依赖它） | hnswlib 改为可选导入 + `HNSWLIB_AVAILABLE` 标记；`AnnIndex.__init__` / `load` 前置 `_require_hnswlib()`，给出可操作报错 |
| `memory/long_term/memory.py` | `_ensure_ann` 强依赖图索引，`add_document` 必崩；`recall` 直接返回空 | 无 hnswlib 时 `_ensure_ann` 返回 None；`add_document` 照常把 chunks 落库、跳过建图；`recall` 退回 `brute_force_recall` 暴力精确检索（功能不缺失、只是慢；该用户无文档时仍走零开销路径） |

修复后 `HAS_LONG_TERM=True`：长期记忆（facts / 范式条目）、知识库、状态记忆（死规则）、技能记忆
**全部可用**，仅「向量图索引」降级为暴力精确检索。

### 三、桌面版单文件 exe

```bash
python -m pip install -r requirements.txt pyinstaller
python build.py          # Windows → dist/Quote.exe
```

| 文件 | 改动 |
| --- | --- |
| `build.py` | 把 PyInstaller 的缓存 / 临时目录重定向到项目内 `.tmp\`（`PYINSTALLER_CONFIG_DIR` / `TEMP` / `TMP`）——原行为会写 `%LOCALAPPDATA%\pyinstaller`，违反项目「不读写 C 盘」铁律 |

本轮实测（Python 3.13.9 + PyInstaller 6.22.3）：产物 `dist/Quote.exe`，28.7 MB 单文件；
双击启动内嵌服务 + 原生窗口，`/api/status` 返回 llm 模式，`/api/ask` 路由命中 research 并返回真实 GLM 回复，
`/api/telemetry` 正常记账。

> 构建环境：项目内 `.venv`（基于 `D:\Application\ToolsInComputer\anaconda\python.exe` 3.13.9 创建）。
> `requirements.txt` 里的 **hnswlib 装不上**（见上），其余依赖全部安装成功；这正是第二节那条降级路径必须修好的原因。

### 四、应用内部品牌同步更名（`SellPilot` → `Quote Agent`）

执行了上一节「2026-09-18 · 项目更名为 Quote_Agent」里列出的**待办更名清单**（当时只改了仓库名与文档，
应用内部品牌仍是 SellPilot）。

| 位置 | 原名 | 新名 |
| --- | --- | --- |
| 界面 logo / 页面标题 / 窗口标题 | `SellPilot · 跨境选品与上架工作台` | `Quote Agent · 跨境选品与上架工作台`（logo 文字取短名 `Quote`） |
| 对话气泡署名 | `SELPILOT` | `QUOTE AGENT` |
| PyInstaller 配置 | `SellPilot.spec`、`name="SellPilot"` | `Quote.spec`、`name="Quote"`（旧 spec 文件已删除） |
| 打包产物 | `dist/SellPilot.exe` | `dist/Quote.exe` |
| 运行日志 | `sellpilot.log` | `quote-agent.log` |
| 前端 localStorage key | `sellpilot-lang` / `sellpilot-sid-*` | `quote-lang` / `quote-sid-*` |
| FastAPI 应用名 / 启动提示 | `SellPilot Agent` | `Quote Agent` |
| MCP 服务名 | `sellpilot-tool-server` | `quote-tool-server` |
| MCP client 名 | `sellpilot-agent` | `quote-agent` |
| Docker 镜像 / 容器名 | `sellpilot-agent` | `quote-agent` |
| Docker volume 名 | `sellpilot_*` | `quote_*` |
| 调试后台标题 | `SellPilot 控制室` | `Quote Agent 控制室` |
| 智能体系统提示词（summarizer / research / listing / presales / customer_service / skill_writer） | 「SellPilot」卖家工作台 | 「Quote Agent」卖家工作台 |
| 兜底回复文案（中日） | SellPilot 的 AI 智能客服 / 售前导购 | Quote Agent 的 AI 智能客服 / 售前导购 |
| 演示 Listing 草稿商品品牌、`tools/listing.py` 的 `brand` | `SellPilot` | `Quote` |

共 25 个文件（24 处内容修改 + `SellPilot.spec` 删除、`Quote.spec` 新增）。
**纯文案 / 文件名替换，未改任何代码逻辑**；`py_compile` 全部通过，`import server` 正常。
`grep -ri sellpilot` 在 `CHANGELOG.md`（历史记录）之外已归零。

> 副作用须知：`localStorage` 的 key 一并改名，本地已存的会话 id 与语言偏好会**重置一次**
> （重新生成会话、语言回到中文）。属预期，无需处理。

### 五、回滚方式

```bash
# 1) 只退回界面外观
git checkout 3f53663 -- ui/

# 2) 退回界面 + hnswlib 降级修复（保留 build.py 的 C 盘重定向）
git checkout 3f53663 -- ui/ memory/

# 3) 全部退回本轮起点
git checkout 3f53663 -- .
```

回滚后如需重新打包，**先结束所有残留进程**（任务管理器里所有 `Quote.exe`；PyInstaller 单文件是
「引导进程 + 应用进程」两个，只关一个会残留端口占用），再执行：

```bash
python build.py
```

---

## 2026-09-18 · 项目更名为 Quote_Agent

- GitHub 仓库更名为 **`Quote_Agent`**：https://github.com/QU1453/Quote_Agent
  （上一形态为 `-SellPilot-Agent`，开头多一个连字符；再往前是 `ECCS-Agent`）
- 本地 `origin` 已指向新地址并验证连通（`git ls-remote` 可通，旧名仍由 GitHub 重定向）
- 文档侧项目名同步为 **Quote Agent / Quote_Agent**：`README.md`、`docs/architecture.md`、`docs/memory-system-design.md`、`.trae/documents/ecss-architecture-implementation-plan.md`、`.trae/documents/安装所有依赖.md`

> **注意**：本次只改了**仓库名与文档**。应用内部的品牌标识仍是 `SellPilot`
> —— 界面标题、`SellPilot.spec`、可执行文件名 `dist/SellPilot`、日志文案等均未改动。
> 若要连应用一起更名，需另行处理（见下）。

### 如需完整更名（已执行 ✅）

> 本节列出的清单一经推出，已于同日执行完毕 —— 见本文档顶部
> 「界面重设计（雾白与石墨）+ 桌面版单文件 exe 构建通过 · 四、应用内部品牌同步更名」。
> 下表保留为当时的待办留档。

| 位置 | 现状 | 需改为 |
| --- | --- | --- |
| `ui/app.js` | `docTitle` / `brandSub` / `agent` 等文案 | Quote / Quote Agent |
| `SellPilot.spec` + `build.py` | 产物名 `SellPilot` / `SellPilot.exe` | `Quote` / `Quote.exe` |
| `server.py` | `FastAPI(title="SellPilot Agent")`、启动提示 | Quote Agent |
| `config.py` | `MCP_SERVER_NAME = "sellpilot-tool-server"` | quote-tool-server |
| `main.py` | `WINDOW_TITLE` | Quote Agent |

---

## 2026-09-18 · 桌面版 + 浅色高雅重设计

**本轮起点（回滚目标）**：`bb01e0e` —— 深色「港口仪表盘」界面 + 完整设置面板（密钥/地址/模型/温度/权限/预算）

**本轮落地提交**：`af561d4`（已推送至 origin/main）

### 一、新增：桌面应用形态

| 文件 | 作用 |
| --- | --- |
| `main.py` | 桌面入口。启动内嵌 FastAPI 服务 → 开原生窗口（pywebview）；无 GUI 后端或未装 pywebview 时自动回落默认浏览器 |
| `SellPilot.spec` | PyInstaller 打包配置，产出**单文件**可执行程序 |
| `build.py` | 一键打包脚本，构建后打印产物路径与体积 |

打包命令与产物（PyInstaller **不支持交叉编译**，需在目标平台上构建）：

```bash
python -m pip install -r requirements.txt pyinstaller
python build.py          # Windows → dist/SellPilot.exe
                         # macOS   → dist/SellPilot
                         # Linux   → dist/SellPilot
```

- 仍是**同一个应用**：`python server.py`（纯 Web）与 `python main.py`（桌面窗口）共用同一套后端与前端，没有第二份代码。
- 打包后 `config.RESOURCE_DIR` 指向解包临时目录（读 `ui/`），`config.BASE_DIR` 指向可执行文件所在目录
  —— 设置面板写入的 `.env`、运行日志 `sellpilot.log` 都落在**程序旁边**，不会随临时目录被清掉。

### 二、改动：界面改为浅色高雅风格（白瓷与细金 / Porcelain Atelier）

| 文件 | 改动 |
| --- | --- |
| `ui/style.css` | **整体重写**。暖白瓷底 `#f6f4f0` + 墨色字 + 碧玉/黄铜双信号色；发丝线与衬线标题承担层次；动效统一为「长尾减速」缓动（入场错峰、悬停位移、按钮掠光、弹窗缩放淡入），并尊重 `prefers-reduced-motion` |
| `ui/index.html` | 字体换成 Newsreader（标题衬线）/ Jost（正文）/ Noto Sans SC（中文）/ IBM Plex Mono（数据）；补 `color-scheme: light`。**所有 id / class 契约未变** |
| `ui/app.js` | 仅两处：中/日文案里的「作战台」→「工作台」；模块条目按序号错峰入场（`animation-delay`）。功能逻辑零改动 |

功能完整保留：两个板块（选品 / Listing）、11 个细分模块、模块表单 → 结构化提问 → 8 类结果卡片、
中/日双语、双板块独立会话、状态胶囊四态、设置面板六项（含 API Key 校验与连接测试）。

### 三、改动：打包相关适配

| 文件 | 改动 |
| --- | --- |
| `config.py` | 新增 `_app_dir()` / `_resource_dir()`，拆出 `BASE_DIR`（可写）与 `RESOURCE_DIR`（只读资源） |
| `server.py` | 静态资源改从 `config.RESOURCE_DIR` 挂载；`persist_env()` 写 `config.BASE_DIR/.env`（打包后写到 exe 旁） |
| `requirements.txt` | 启用 `pywebview`（桌面外壳）、新增 `pyinstaller`（打包） |

### 四、回滚方式

按粒度从大到小，任选其一：

```bash
# 1) 只退回界面外观（保留桌面版与打包能力）
git checkout bb01e0e -- ui/

# 2) 退回本轮全部已跟踪文件的改动（保留新增的 main.py / build.py / SellPilot.spec）
git checkout bb01e0e -- config.py server.py requirements.txt ui/

# 3) 连新增文件一起退回本轮起点
git checkout bb01e0e -- .
rm -f main.py build.py SellPilot.spec
```

回滚后如需重新打包，先删掉旧产物与构建缓存：

```bash
rm -rf build dist && python build.py
```

---

## 历史节点

| 提交 | 说明 |
| --- | --- |
| （仓库名） | `ECCS-Agent` → `-SellPilot-Agent` → **`Quote_Agent`**，最后一次更名见本文档顶部 |
| `af561d4` | 桌面版入口 + 单文件打包 + 界面改为浅色「白瓷与细金」 |
| `bb01e0e` | 深色「港口仪表盘」界面 + 完整运行时设置面板（上一形态，可作回滚目标） |
| `ed57e4c` | 智能体项目配置与 Git 安全 |
| `9c14866` | 品牌重塑 ECCS → SellPilot（仓库更名 SellPilot_Agent） |
