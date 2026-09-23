/* =====================================================================
   Quote Agent · 跨境选品与上架作战台
   结构：总览（01 选品 / 02 Listing）→ 模块控制台（左表单 + 右对话流）
   - 每个板块一条独立会话（session_id 存 localStorage）
   - 表单 → 结构化提问 → POST /api/ask → 卡片渲染（后端不可达时本地演示兜底）
   - 设置面板：API Key / 请求地址 / 模型 ID（POST /api/settings，可测试连接）
   ===================================================================== */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const now = () => { const d = new Date(); return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
const wait = ms => new Promise(r => setTimeout(r, ms));
const DELAY = () => 700 + Math.random() * 500;

function escapeHTML(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* ---------- 多语言文案 ---------- */
const I18N = {
  zh: {
    docTitle: "Quote Agent · 跨境选品与上架工作台",
    brandSub: "跨境选品 · 上架工作台",
    crumbHome: "总控台", back: "返回项目",
    settings: "设置", debug: "调试后台",
    heroKicker: "OPERATIONS · SELECT → LIST",
    heroTitle: "两条产线，一次跑通",
    heroLead: "选品解决「卖什么」，Listing 解决「怎么卖」。先选一个项目进入，再挑模块 —— 不同项目的上下文互相隔离。",
    boardResearch: "选品", boardResearchDesc: "需求 · 竞争 · 利润 · 货源，四步筛出能赚钱的品。",
    boardListing: "Listing", boardListingDesc: "文案 · 图片 · 定价 · 履约，把品推上货架。",
    placeholder: "直接说需求…（Enter 发送 / Shift+Enter 换行）",
    hint: "AI 多智能体 · 选品 + 上架",
    thinking: "智能体思考中…", replied: "已回复",
    /* —— 思考过程小字 + 打断（后端 core/telemetry/live.py、memory/interrupt/）—— */
    thinkTitle: "思考过程",
    thinkStep: n => `第 ${n} 步推理`,
    thinkSummary: (steps, tools) => `已思考 ${steps} 步 · 调用 ${tools} 个工具`,
    thinkNoTool: steps => `已思考 ${steps} 步 · 未调用工具`,
    stop: "打断",
    stopHint: "已请求打断，正在收尾…",
    interruptNotice: d => {
      const where = [d.step_index ? `第 ${d.step_index} 步` : "", (I18N.zh.stages[d.stage] || "")].filter(Boolean).join(" · ");
      const tool = d.pending_tool ? `，工具 ${d.pending_tool} 未执行` : "";
      return `已打断${where ? `（${where}${tool}）` : ""}。断点已记入记忆，下一次对话可以接着做。`;
    },
    stages: {
      input: "解析输入", route: "意图路由", context: "装配记忆", output: "整理输出",
      fallback: "本地兜底", constraint: "约束拦截",
      tool: "工具调用", thinking: "思考推理", perception: "输入解析"
    },
    routes: { research: "选品", listing: "Listing", customer_service: "客服", presales: "售前" },
    toolCall: (n, d) => `调用工具 ${n}${d ? `（${d}）` : ""}`,
    toolDone: (n, d) => `工具 ${n} 已返回${d ? `（${d}）` : ""}`,
    toolFail: (n, d) => `工具 ${n} 失败${d ? `（${d}）` : ""}`,
    toolSkipped: n => `工具 ${n} 未执行（已被打断）`,
    notes: {
      resume_found: "检测到上次未完成的任务，尝试接着做",
      resume_closed: "上次未完成的任务已被接手",
      interrupt: "已按你的要求打断",
      interrupt_late: "打断信号到达时本轮已完成"
    },
    runModule: "开始分析", required: "请先填写：",
    freeTip: "这个模块不预设表单 —— 直接在右下角输入框里描述你的需求即可。",
    you: "你", agent: "QUOTE AGENT",
    endTalk: "结束谈话", clear: "清空",
    endDone: "本次谈话已结束并完成 <span class='em'>总结归档</span>（多轮记忆与长期画像已更新）。已开启新一轮谈话～",
    endFail: "结束谈话未成功（后端不可达），请稍后重试。",
    cleared: "已清空当前板块的对话。",
    switched: "已切换为中文。",
    /* 左栏：顶部「总控台」独立入口 + 项目列（用户创建的项目） */
    railHome: "总控台",
    railProjects: "项目",
    railHint: "选一个项目进入；不同项目的上下文互相隔离。",
    railNewProject: "新建项目", railSearchPh: "搜索项目…",
    railNew: "新对话",
    railEmpty: "还没有项目，点「新建项目」开始。",
    railNoMatch: "没有匹配的项目。",
    railUntitled: "未命名谈话",
    railPending: "未完成断点",
    railOpen: "进行中", railClosed: "已结束",
    railSwitched: "已切换到「{title}」。",
    railNewDone: "已开启新对话（上一段已归档总结）。",
    railStartFail: "开启新对话失败，请检查后端是否在运行。",
    railHid: "已从列表隐藏（记忆与断点都保留了）。",
    /* 总控台四块：全局对话 / 项目卡片 / 待办汇总 / 记忆与规则 */
    deckTalk: "全局对话",
    deckTalkDesc: "不挂任何项目的通用问答，只带全局记忆，换话题最干净。",
    deckTalkPh: "说点什么…（Enter 发送 / Shift+Enter 换行）",
    deckProjects: "项目", deckProjectsDesc: "每个项目一套独立的选品 / Listing 上下文。",
    deckProjectsAllArchived: n => `当前没有进行中的项目；有 ${n} 个已归档，可在左栏「已归档」里一键恢复。`,
    deckTodos: "待办汇总", deckTodosDesc: "被你打断的任务，点一条接着做。",
    deckTodoEmpty: "没有未完成的断点 —— 干净。",
    deckMemory: "记忆与规则", deckMemoryDesc: "L0 全局层只读预览；项目 / 板块两层随项目隔离。",
    deckMemRules: "死规则（铁律）", deckMemFacts: "长期事实", deckMemSkills: "热点技能",
    deckMemEmpty: "暂无条目。", deckMemFail: "记忆层暂不可读。",
    /* 项目总览 + 板块窗口 */
    projectNoDesc: "还没有描述。",
    projectSessions: n => `${n} 段对话`,
    projectNew: "新建项目",
    projectNewName: "项目名称", projectNewPh: "例如：水杯项目",
    projectNewDesc: "描述（可选）", projectNewDescPh: "例如：日本站保温杯，主打轻量",
    projectCreate: "创建", projectCancel: "取消",
    projectNewFail: "创建失败：",
    projectRename: "重命名", projectArchive: "归档", projectUnarchive: "取消归档",
    projectArchived: "已归档（会话、记忆、断点都保留着）。",
    projectUnarchived: "已恢复。",
    projectEditFail: "操作失败：",
    projectKicker: "一级项目 / TOP LEVEL",
    projectNewNote: "项目是最外层容器：一个项目 = 一套独立的选品 / Listing 上下文，项目之间互不串味。",
    railArchived: "已归档", railRestore: "恢复",
    railHidden: "已隐藏",
    /* 右键菜单（行内不再放小图标，操作统一收进菜单） */
    ctxRemove: "移除",
    ctxHintArchive: "归档 · 可恢复",
    ctxHintHide: "隐藏 · 可恢复",
    talkRename: "重命名会话",
    renameLabel: "会话名称",
    renameSave: "保存",
    renameFail: "重命名失败：",
    talksSwitch: "切换本板块的会话", talksEmpty: "本板块还没有对话。",
    talksMove: "移动到其他板块", talksMoved: n => `已移到「${n}」。`,
    talksMoveFail: "移动失败：",
    deckGreet: "这里是总控台。这条是<b>全局对话</b>：不挂任何项目，也就不带项目级 / 板块级记忆 —— 想换话题又怕污染上下文，就在这儿问。要开始正经活，点「项目」里的一张卡片进去。",
    /* 调试后台改为弹窗（不再跳浏览器标签页） */
    debugTitle: "调试后台",
    debugKicker: "TELEMETRY / TRACE",
    debugRefresh: "刷新",
    debugBrowser: "在浏览器打开",
    debugClose: "关闭",
    /* 悬浮指导智能体（只读） */
    guideTitle: "指导智能体", guideRO: "只读",
    guidePh: "问：我该去哪儿？",
    guideGreet: "我是只读的指导智能体：能看到你的全部记忆与未完成进度，但不会改动任何东西。试试问我「我上次做到哪了」。",
    guideThinking: "正在查看记忆…",
    guideFail: "暂时连不上指导服务，稍后再试。",
    guideYou: "你",
    greet: "这里是<b>{name}</b>模块。左侧填好参数点「开始分析」，或直接在下方输入框里说需求。",
    settingsTitle: "运行时配置",
    setKey: "API Key", setUrl: "请求地址（Base URL）", setModel: "模型 ID",
    setTemp: "温度 temperature", setPerm: "权限模式",
    permPlan: "plan · 只读", permAsk: "ask · 写操作需确认",
    permAccept: "accept · 低风险自动放行", permBypass: "bypass · 完全放开",
    setCallBudget: "单次 token 预算", setCost: "累计金额上限（单会话）",
    setCostNote: r => `0 = 不启用金额熔断。USD 按 1 USD ≈ ${r} CNY 折算成人民币与账单对齐。`,
    setPersist: "写入本机 .env（重启后仍生效）", setTest: "测试连接", setSave: "保存并应用",
    setKeyNone: "当前未配置密钥（本地演示模式）",
    setKeyMasked: k => `当前已配置：${k}（留空则保持不变）`,
    keyOk: " · 连接测试已通过", keyBad: " · 上次连接测试失败", keyUnverified: " · 尚未验证",
    /* 密钥输入框的占位提示：明文永不回填，靠占位文案表达"已保存" */
    keyPhEmpty: "粘贴你的密钥…",
    keyPhSaved: k => `已保存：${k} · 留空则不修改`,
    saveNone: "没有需要更新的内容。",
    saveOk: (n, p) => `已更新 ${n} 项${p ? "，并写入本机 .env" : "（仅本次运行有效）"}。`,
    saveFail: "保存失败：",
    testing: "正在测试连接…",
    testOk: m => `连接成功 · 模型 ${m}`,
    testFail: "连接失败：",
    statusLive: m => `GLM 在线 · ${m}`,
    statusLocal: "本地演示模式", statusDown: "后端未连接",
    statusUnverified: "GLM 已配置 · 待验证", statusKeyBad: "密钥校验失败",
    card: {
      verdict: "选品结论", score: "四步得分", passed: "通过", failed: "未通过",
      supplier: "供应商对比", recommend: "综合推荐", moq: "起订量", unitPrice: "单价", lead: "交期",
      draft: "Listing 草稿", bullets: "五点描述", terms: "Search Terms", tips: "合规提示",
      images: "图片合规自检", price: "定价建议", suggested: "建议售价", floor: "保本底价",
      fulfill: "履约建议", mode: "建议方式", batch: "首批备货",
      order: "订单", carrier: "顺丰速运", reco: "智能推荐 · 商品库命中",
      steps: ["已付款", "运输中", "派送中", "已签收"], paidAt: "昨天 15:02 付款"
    }
  },
  ja: {
    docTitle: "Quote Agent · 越境セラー選品・出品コンソール",
    brandSub: "選品・出品コンソール",
    crumbHome: "統括コンソール", back: "プロジェクトへ戻る",
    settings: "設定", debug: "デバッグ",
    heroKicker: "OPERATIONS · SELECT → LIST",
    heroTitle: "2本のラインを、一度に",
    heroLead: "選品は「何を売るか」、Listing は「どう売るか」。まずプロジェクトを選び、それからモジュールへ —— プロジェクト間の文脈は分離されます。",
    boardResearch: "選品", boardResearchDesc: "需要・競合・利益・仕入れ先。4ステップで利益の出る商品を絞り込み。",
    boardListing: "Listing", boardListingDesc: "コピー・画像・価格・フルフィルメント。商品を棚に上げる。",
    placeholder: "ご要望を入力…（Enterで送信 / Shift+Enterで改行）",
    hint: "AI マルチエージェント · 選品 + 出品",
    thinking: "エージェントが考え中…", replied: "返信しました",
    /* —— 思考プロセス（小文字）+ 中断 —— */
    thinkTitle: "思考プロセス",
    thinkStep: n => `推論 第 ${n} ステップ`,
    thinkSummary: (steps, tools) => `${steps} ステップ思考 · ツール ${tools} 件`,
    thinkNoTool: steps => `${steps} ステップ思考 · ツール未使用`,
    stop: "中断",
    stopHint: "中断を要求しました。終了処理中…",
    interruptNotice: d => {
      const where = [d.step_index ? `第 ${d.step_index} ステップ` : "", (I18N.ja.stages[d.stage] || "")].filter(Boolean).join(" · ");
      const tool = d.pending_tool ? `、ツール ${d.pending_tool} は未実行` : "";
      return `中断しました${where ? `（${where}${tool}）` : ""}。中断地点は記憶に記録済みで、次の会話から続けられます。`;
    },
    stages: {
      input: "入力を解析", route: "意図ルーティング", context: "記憶を組み立て", output: "出力を整理",
      fallback: "ローカル代替", constraint: "制約により遮断",
      tool: "ツール呼び出し", thinking: "推論", perception: "入力解析"
    },
    routes: { research: "選品", listing: "Listing", customer_service: "サポート", presales: "ご案内" },
    toolCall: (n, d) => `ツール ${n} を呼び出し${d ? `（${d}）` : ""}`,
    toolDone: (n, d) => `ツール ${n} が返却${d ? `（${d}）` : ""}`,
    toolFail: (n, d) => `ツール ${n} が失敗${d ? `（${d}）` : ""}`,
    toolSkipped: n => `ツール ${n} は未実行（中断されました）`,
    notes: {
      resume_found: "前回の未完了タスクを検出、続行を試みます",
      resume_closed: "前回の未完了タスクを引き継ぎました",
      interrupt: "ご指示どおり中断しました",
      interrupt_late: "中断信号の到着時点で既に完了していました"
    },
    runModule: "分析を開始", required: "未入力です：",
    freeTip: "このモジュールに固定フォームはありません。右下の入力欄から直接ご要望をどうぞ。",
    you: "あなた", agent: "QUOTE AGENT",
    endTalk: "会話を終了", clear: "消去",
    endDone: "今回の会話は終了し、<span class='em'>要約を保存</span>しました（多輪記憶と長期プロファイルを更新済み）。新しい会話を開始しました〜",
    endFail: "会話の終了に失敗しました（バックエンド未接続）。後ほどお試しください。",
    cleared: "このワークベンチの会話を消去しました。",
    switched: "日本語に切り替えました。",
    railHome: "統括コンソール",
    railProjects: "プロジェクト",
    railHint: "プロジェクトを選んでください。プロジェクト間の文脈は分離されます。",
    railNewProject: "新規プロジェクト", railSearchPh: "プロジェクトを検索…",
    railNew: "新しい会話",
    railEmpty: "プロジェクトがありません。「新規プロジェクト」からどうぞ。",
    railNoMatch: "一致するプロジェクトがありません。",
    railUntitled: "無題の会話",
    railPending: "未完了の中断",
    railOpen: "進行中", railClosed: "終了",
    railSwitched: "「{title}」に切り替えました。",
    railNewDone: "新しい会話を開始しました（前の会話は要約済み）。",
    railStartFail: "新しい会話を開始できませんでした。",
    railHid: "リストから非表示にしました（記憶と中断記録は保持）。",
    deckTalk: "グローバル会話",
    deckTalkDesc: "プロジェクトに属さない汎用の対話。グローバル記憶のみで、話題の切り替えが最もクリーン。",
    deckTalkPh: "何か入力…（Enter で送信 / Shift+Enter で改行）",
    deckProjects: "プロジェクト", deckProjectsDesc: "プロジェクトごとに独立した選品 / Listing の文脈。",
    deckProjectsAllArchived: n => `進行中のプロジェクトはありません（アーカイブ済みが ${n} 件。左の「アーカイブ済み」から復元できます）。`,
    deckTodos: "未完了タスク", deckTodosDesc: "中断されたタスク。クリックで続きから。",
    deckTodoEmpty: "未完了の中断はありません。",
    deckMemory: "記憶とルール", deckMemoryDesc: "L0 グローバル層の読み取り専用プレビュー。プロジェクト / モジュール層は分離されます。",
    deckMemRules: "鉄則ルール", deckMemFacts: "長期ファクト", deckMemSkills: "頻出スキル",
    deckMemEmpty: "項目はありません。", deckMemFail: "記憶層を読み取れません。",
    projectNoDesc: "説明はまだありません。",
    projectSessions: n => `${n} 件の会話`,
    projectNew: "新規プロジェクト",
    projectNewName: "プロジェクト名", projectNewPh: "例：タンブラー計画",
    projectNewDesc: "説明（任意）", projectNewDescPh: "例：日本向け軽量タンブラー",
    projectCreate: "作成", projectCancel: "キャンセル",
    projectNewDone: n => `「${n}」を作成しました。モジュールを選んで始めましょう。`,
    projectNewFail: "作成に失敗：",
    projectRename: "名前を変更", projectArchive: "アーカイブ", projectUnarchive: "アーカイブ解除",
    projectArchived: "アーカイブしました（会話・記憶・中断記録は保持）。",
    projectUnarchived: "復元しました。",
    projectEditFail: "操作に失敗：",
    projectKicker: "第一級プロジェクト / TOP LEVEL",
    projectNewNote: "プロジェクトは最上位の入れ物です。1 プロジェクト = 独立した選品 / Listing の文脈で、相互に混ざりません。",
    railArchived: "アーカイブ済み", railRestore: "復元",
    railHidden: "非表示",
    ctxRemove: "外す",
    ctxHintArchive: "アーカイブ · 復元可",
    ctxHintHide: "非表示 · 復元可",
    talkRename: "会話の名前を変更",
    renameLabel: "会話名",
    renameSave: "保存",
    renameFail: "名前の変更に失敗：",
    talksSwitch: "このモジュールの会話を切り替え", talksEmpty: "このモジュールにはまだ会話がありません。",
    talksMove: "他のモジュールへ移動", talksMoved: n => `「${n}」へ移動しました。`,
    talksMoveFail: "移動に失敗：",
    deckGreet: "ここは統括コンソールです。この欄は<b>グローバル会話</b>：どのプロジェクトにも属さず、プロジェクト / モジュール層の記憶も持ちません。話題を変えたいが文脈を汚したくないときはここで。本題は「プロジェクト」のカードから入ってください。",
    debugTitle: "デバッグコンソール",
    debugKicker: "TELEMETRY / TRACE",
    debugRefresh: "更新",
    debugBrowser: "ブラウザで開く",
    debugClose: "閉じる",
    guideTitle: "ガイドエージェント", guideRO: "読み取り専用",
    guidePh: "例：次はどこへ？",
    guideGreet: "読み取り専用のガイドです。記憶と未完了の進捗は見えますが、何も変更しません。「前回どこまでやった？」と聞いてみてください。",
    guideThinking: "記憶を確認しています…",
    guideFail: "ガイドサービスに接続できませんでした。",
    guideYou: "あなた",
    greet: "こちらは<b>{name}</b>モジュールです。左のフォームで実行するか、下の入力欄から直接どうぞ。",
    settingsTitle: "ランタイム設定",
    setKey: "API Key", setUrl: "リクエスト先（Base URL）", setModel: "モデル ID",
    setTemp: "温度 temperature", setPerm: "権限モード",
    permPlan: "plan · 読み取り専用", permAsk: "ask · 書き込みは要確認",
    permAccept: "accept · 低リスクは自動許可", permBypass: "bypass · 全開放",
    setCallBudget: "1回あたり token 予算", setCost: "累計金額上限（1セッション）",
    setCostNote: r => `0 = 金額ブレーキ無効。USD は 1 USD ≈ ${r} CNY で人民元換算し、請求と突き合わせます。`,
    setPersist: "ローカルの .env に保存（再起動後も有効）", setTest: "接続テスト", setSave: "保存して適用",
    setKeyNone: "キー未設定（ローカルデモモード）",
    setKeyMasked: k => `設定済み：${k}（空欄なら変更しません）`,
    keyOk: " · 接続テスト済み", keyBad: " · 前回の接続テストに失敗", keyUnverified: " · 未検証",
    keyPhEmpty: "キーを貼り付け…",
    keyPhSaved: k => `保存済み：${k} · 空欄なら変更しません`,
    saveNone: "更新する項目がありません。",
    saveOk: (n, p) => `${n} 件を更新しました${p ? "（.env に保存）" : "（今回の実行のみ有効）"}。`,
    saveFail: "保存に失敗：",
    testing: "接続をテスト中…",
    testOk: m => `接続成功 · モデル ${m}`,
    testFail: "接続失敗：",
    statusLive: m => `GLM オンライン · ${m}`,
    statusLocal: "ローカルデモモード", statusDown: "バックエンド未接続",
    statusUnverified: "GLM 設定済み · 未検証", statusKeyBad: "キー検証に失敗",
    card: {
      verdict: "選品結論", score: "4段階スコア", passed: "合格", failed: "不合格",
      supplier: "仕入れ先比較", recommend: "総合おすすめ", moq: "最小ロット", unitPrice: "単価", lead: "納期",
      draft: "Listing 下書き", bullets: "箇条書き", terms: "Search Terms", tips: "規約ヒント",
      images: "画像規約チェック", price: "価格提案", suggested: "推奨価格", floor: "損益分岐価格",
      fulfill: "フルフィルメント提案", mode: "推奨方式", batch: "初回仕入れ",
      order: "ご注文", carrier: "順豊エクスプレス", reco: "おすすめ · 商品ライブラリから",
      steps: ["支払い済み", "輸送中", "配達中", "受取済み"], paidAt: "昨日 15:02 支払い済み"
    }
  }
};

let lang = localStorage.getItem("quote-lang") || "zh";
const L = () => I18N[lang];
const tr = obj => (obj && (obj[lang] ?? obj.zh)) || "";

/* ---------- 站点下拉（中/日共用） ---------- */
const SITES = [["US", "Amazon US"], ["JP", "Amazon JP"], ["DE", "Amazon DE"], ["GB", "Amazon UK"]];
const SITE_FIELD = { name: "site", label: { zh: "目标站点", ja: "対象サイト" }, type: "select", options: SITES };

/* ---------- 板块与模块定义 ---------- */
const BOARDS = {
  research: {
    no: "01",
    title: () => L().boardResearch,
    modules: [
      {
        no: "1.1", key: "demand",
        title: { zh: "需求验证", ja: "需要検証" },
        desc: { zh: "搜索量 / 价格带 / 重量门槛，先确认这个品有没有人买。", ja: "検索量・価格帯・重量。まず需要の有無を確認。" },
        fields: [
          { name: "category", label: { zh: "品类 / 关键词", ja: "カテゴリ / キーワード" }, ph: { zh: "例如：便携榨汁杯", ja: "例：ポータブルジューサー" }, required: true },
          SITE_FIELD,
          { name: "price", label: { zh: "目标售价（$）", ja: "目標価格（$）" }, type: "number", ph: { zh: "29.9", ja: "29.9" } }
        ],
        prompt: (v, zh) => zh
          ? `对「${v.category}」做需求验证：目标站点 ${v.site}，目标售价约 $${v.price || "未定"}。请检查搜索量是否达标、售价是否落在 $20~$70 区间、单品重量是否低于 2lb，并给出明确的需求结论。`
          : `「${v.category}」の需要検証をお願いします。対象 ${v.site}、目標価格 約$${v.price || "未定"}。検索ボリューム・価格帯（$20〜$70）・重量（2lb未満）を確認し、明確な結論を出してください。`
      },
      {
        no: "1.2", key: "competition",
        title: { zh: "竞争分析", ja: "競合分析" },
        desc: { zh: "首页竞品评分与评论数，判断进入难度。", ja: "上位の評価・レビュー数から参入難易度を判断。" },
        fields: [
          { name: "category", label: { zh: "品类 / 关键词", ja: "カテゴリ / キーワード" }, ph: { zh: "例如：蓝牙耳机", ja: "例：ワイヤレスイヤホン" }, required: true },
          SITE_FIELD
        ],
        prompt: (v, zh) => zh
          ? `分析「${v.category}」在 ${v.site} 的竞争强度：请给出首页竞品的平均评分、平均评论数、是否存在垄断卖家，并判断是否值得进入。`
          : `「${v.category}」(${v.site}) の競合強度を分析してください。上位の平均評価・平均レビュー数・独占セラーの有無を示し、参入すべきか判断してください。`
      },
      {
        no: "1.3", key: "profit",
        title: { zh: "利润测算", ja: "利益試算" },
        desc: { zh: "折算头程、佣金、FBA 与广告，算清算净利。", ja: "輸送・手数料・FBA・広告を織り込み純利益を算出。" },
        fields: [
          { name: "category", label: { zh: "品类 / 关键词", ja: "カテゴリ / キーワード" }, ph: { zh: "例如：云感耳机", ja: "例：雲感イヤホン" }, required: true },
          { name: "cost", label: { zh: "采购成本（¥）", ja: "仕入原価（¥）" }, type: "number", ph: { zh: "45", ja: "45" } },
          { name: "price", label: { zh: "目标售价（$）", ja: "目標価格（$）" }, type: "number", ph: { zh: "29.9", ja: "29.9" } }
        ],
        prompt: (v, zh) => zh
          ? `测算「${v.category}」的利润：采购成本约 ¥${v.cost || "未知"}，目标售价 $${v.price || "未知"}。请折算头程、平台佣金、FBA 配送费与广告费，给出单件利润与净利率，并判断是否达到 30% 净利率红线。`
          : `「${v.category}」の利益を試算してください。仕入原価 約¥${v.cost || "不明"}、目標価格 $${v.price || "不明"}。輸送費・手数料・FBA配送料・広告費を織り込み、1個あたり利益と純利益率を示し、純利益率30%のラインを満たすか判断してください。`
      },
      {
        no: "1.4", key: "supplier",
        title: { zh: "供应商对比", ja: "仕入れ先比較" },
        desc: { zh: "单价 / 起订量 / 交期 / 评分横向比。", ja: "単価・最小ロット・納期・評価を横断比較。" },
        fields: [
          { name: "keyword", label: { zh: "货源关键词", ja: "仕入れキーワード" }, ph: { zh: "例如：蓝牙耳机", ja: "例：ワイヤレスイヤホン" }, required: true },
          { name: "moq", label: { zh: "可接受起订量（件）", ja: "許容できる最小ロット（個）" }, type: "number", ph: { zh: "100", ja: "100" } }
        ],
        prompt: (v, zh) => zh
          ? `对比「${v.keyword}」的供应商货源，起订量希望控制在 ${v.moq || "不限"} 件以内。请给出候选货源的单价、起订量、交期与评分对比，并给出综合推荐与理由。`
          : `「${v.keyword}」の仕入れ先を比較してください。最小ロットは ${v.moq || "問わない"} 個以内が希望です。候補ごとに単価・最小ロット・納期・評価を比較し、総合おすすめと理由を示してください。`
      },
      {
        no: "1.5", key: "report",
        title: { zh: "一键选品报告", ja: "選品レポート一括" },
        desc: { zh: "需求 + 竞争 + 利润一次跑完，出推进/放弃结论。", ja: "需要・競合・利益を一括で検証し、結論を出す。" },
        fields: [
          { name: "category", label: { zh: "品类 / 关键词", ja: "カテゴリ / キーワード" }, ph: { zh: "例如：便携榨汁杯", ja: "例：ポータブルジューサー" }, required: true },
          SITE_FIELD
        ],
        prompt: (v, zh) => zh
          ? `对「${v.category}」（站点 ${v.site}）出具一份完整选品报告：依次完成需求验证、竞争分析、利润测算三步，给出总分以及「推进 / 放弃」的明确结论。`
          : `「${v.category}」(${v.site}) の選品レポートを作成してください。需要検証・競合分析・利益試算を順に実施し、総合スコアと「推進 / 見送り」の明確な結論を出してください。`
      },
      {
        no: "1.6", key: "free", free: true,
        title: { zh: "自由对话", ja: "フリートーク" },
        desc: { zh: "没有合适模块？直接描述你的选品问题。", ja: "該当モジュールがない場合は自由に相談。" },
        fields: []
      }
    ]
  },

  listing: {
    no: "02",
    title: () => L().boardListing,
    modules: [
      {
        no: "2.1", key: "draft",
        title: { zh: "Listing 起草", ja: "Listing 作成" },
        desc: { zh: "标题 / 五点 / Search Terms 一次成稿。", ja: "タイトル・箇条書き・Search Terms を一括生成。" },
        fields: [
          { name: "product", label: { zh: "产品名", ja: "商品名" }, ph: { zh: "例如：云感无线蓝牙耳机 Pro", ja: "例：雲感ワイヤレスイヤホン Pro" }, required: true },
          { name: "points", label: { zh: "核心卖点", ja: "主要セールスポイント" }, type: "textarea", ph: { zh: "主动降噪 / 36小时续航 / 单耳 3.8g …", ja: "ANC / 36時間再生 / 片耳3.8g …" } },
          SITE_FIELD
        ],
        prompt: (v, zh) => zh
          ? `为「${v.product}」起草亚马逊 Listing（站点 ${v.site}）。核心卖点：${v.points || "请按品类通用卖点提炼"}。请输出标题（≤75 字符）、五点描述、Search Terms 与合规提示。`
          : `「${v.product}」の Amazon Listing を作成してください（対象 ${v.site}）。主要セールスポイント：${v.points || "カテゴリ一般の訴求点で構成"}。タイトル（75文字以内）・箇条書き5点・Search Terms・規約上の注意を出力してください。`
      },
      {
        no: "2.2", key: "image",
        title: { zh: "图片合规自检", ja: "画像規約チェック" },
        desc: { zh: "白底 / 商品占比 / 文字水印逐图核查。", ja: "白背景・占有率・文字ウォーターマークを確認。" },
        fields: [
          { name: "product", label: { zh: "产品名", ja: "商品名" }, ph: { zh: "例如：云感无线蓝牙耳机 Pro", ja: "例：雲感ワイヤレスイヤホン Pro" }, required: true },
          { name: "count", label: { zh: "图片张数", ja: "画像枚数" }, type: "number", ph: { zh: "7", ja: "7" } }
        ],
        prompt: (v, zh) => zh
          ? `检查「${v.product}」的主图合规性，共 ${v.count || 7} 张。请逐图判断是否为纯白底、商品占比是否达标、是否出现文字或水印，并给出合规结论。`
          : `「${v.product}」のメイン画像の規約チェックをお願いします（全 ${v.count || 7} 枚）。各画像について純白背景か・商品占有率・文字やウォーターマークの有無を判定し、結論を出してください。`
      },
      {
        no: "2.3", key: "price",
        title: { zh: "定价策略", ja: "価格戦略" },
        desc: { zh: "建议售价 + 保本底价，守住净利率红线。", ja: "推奨価格と損益分岐価格を提示。" },
        fields: [
          { name: "product", label: { zh: "产品名", ja: "商品名" }, ph: { zh: "例如：云感无线蓝牙耳机 Pro", ja: "例：雲感ワイヤレスイヤホン Pro" }, required: true },
          { name: "competitor", label: { zh: "竞品参考价（¥）", ja: "競合参考価格（¥）" }, type: "number", ph: { zh: "299", ja: "299" } }
        ],
        prompt: (v, zh) => zh
          ? `为「${v.product}」制定定价策略，竞品参考价约 ¥${v.competitor || "未知"}。请给出建议售价、保本底价与定价理由，并说明是否适合首发低价冲量。`
          : `「${v.product}」の価格戦略を策定してください。競合参考価格 約¥${v.competitor || "不明"}。推奨価格・損益分岐価格・根拠を示し、初回の低価格戦略が適切かも述べてください。`
      },
      {
        no: "2.4", key: "fulfill",
        title: { zh: "履约建议", ja: "フルフィルメント提案" },
        desc: { zh: "FBA 还是自发货，首批备多少。", ja: "FBA か自己発送か、初回仕入れ数は。" },
        fields: [
          { name: "product", label: { zh: "产品名", ja: "商品名" }, ph: { zh: "例如：云感无线蓝牙耳机 Pro", ja: "例：雲感ワイヤレスイヤホン Pro" }, required: true },
          { name: "monthly", label: { zh: "预计月销（件）", ja: "想定月販（個）" }, type: "number", ph: { zh: "300", ja: "300" } }
        ],
        prompt: (v, zh) => zh
          ? `为「${v.product}」给出履约建议，预计月销 ${v.monthly || "未知"} 件。请建议采用 FBA 还是自发货，给出首批备货量，并说明理由与风险。`
          : `「${v.product}」のフルフィルメント提案をお願いします。想定月販 ${v.monthly || "不明"} 個。FBA か自己発送かを提案し、初回仕入れ数・根拠・リスクを述べてください。`
      },
      {
        no: "2.5", key: "free", free: true,
        title: { zh: "自由对话", ja: "フリートーク" },
        desc: { zh: "没有合适模块？直接描述你的上架问题。", ja: "該当モジュールがない場合は自由に相談。" },
        fields: []
      }
    ]
  }
};

/* ---------- 会话归属 ----------
   层级：项目（一级）→ 细分板块（二级）→ 窗口内会话。
   所以会话的本地键 = 「项目 + 板块」；总控台的全局对话用 project_id="" 的 "global" 段。
   —— 会话归属只在创建时确定，之后只有用户显式「移动到其他板块」才会变（见 moveTalk）。
   ------------------------------------------------------------------ */
let projects = [];          // 左栏项目列表（GET /api/projects，只含未归档）
let archivedProjects = [];  // 已归档项目（归档 ≠ 删除：必须能看见、能恢复）
let showArchived = false;   // 已归档分组是否展开
let activeProject = null;   // 当前项目 {project_id,name,desc,status}；null = 在总控台
let activeBoard = "research"; // 区块（选品 / Listing）：只用于视觉分区与 scope 标签
let activeModule = null;    // 当前细分板块（module 对象，含 key）
let activeTalk = "";        // 当前会话 session_id
let streamSid = "";         // 当前可见对话流里渲染的是哪段会话（留档配对用）
let streamView = "";        // 该对话流所在的视图
let railFilter = "";        // 左栏项目搜索词
let boardStats = {};        // 当前项目的板块计数（来自 /api/projects/{id}/tree）
let talks = [];             // 当前板块下的会话清单（顶部会话切换条）
let hiddenTalks = [];       // 已隐藏的会话（隐藏 ≠ 删除，得能看见、能恢复）
let showHidden = false;     // 已隐藏分组是否展开
let talkIndex = new Map();  // session_id → 会话行（待办汇总跳转要靠它反查归属）
let lastTodos = [];         // 最近一次拉到的未完成断点

const TALK_KEY = (pid, fk) => `quote-sid-${pid || "global"}::${fk || ""}`;
const sidOf = (pid, fk) => localStorage.getItem(TALK_KEY(pid, fk)) || "";
const storeSid = (pid, fk, sid) => localStorage.setItem(TALK_KEY(pid, fk), sid);

const projId = () => (activeProject ? activeProject.project_id : "");
const featKey = () => (activeModule ? activeModule.key : "");
const scopeOf = () => (activeProject ? activeBoard : "global");
const activeSid = () => sidOf(projId(), featKey());

async function ensureSession(force = false) {
  const pid = projId(), fk = featKey();
  if (!force && sidOf(pid, fk)) return sidOf(pid, fk);
  try {
    const res = await fetch("/api/conversation/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: "default", scope: scopeOf(),
        project_id: pid, feature_key: fk   // 二级归属：会话创建时就挂在「项目 + 板块」之下
      })
    });
    if (res.ok) {
      const j = await res.json();
      if (j && j.session_id) { storeSid(pid, fk, j.session_id); return j.session_id; }
    }
  } catch (e) { /* 后端不可达 → 本地生成 */ }
  const sid = (crypto.randomUUID ? crypto.randomUUID() : `s-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  storeSid(pid, fk, sid);
  return sid;
}

async function askBackend(q, requestId) {
  const sid = await ensureSession();
  const res = await fetch("/api/ask", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message: q, session_id: sid, user_id: "default", lang,
                           request_id: requestId || "",
                           project_id: projId(), feature_key: featKey() })
  });
  if (!res.ok) return null;
  const j = await res.json();
  if (!j) return null;
  // 被打断时可能没有正文（partial 为空），此时也算有效结果，不能落到本地演示兜底
  const interrupted = !!(j.data && j.data.interrupted);
  return (interrupted || j.reply) ? j : null;
}

/* ---------- 气泡 ----------
   总控台的「全局对话」和板块窗口的对话流是两个容器，同一时刻只有一个可见；
   streamEl() 取当前视图下可见的那个 —— 发送 / 留档 / 还原全走它，调用方不用区分。
   ------------------------------------------------------------------ */
let activeView = "view-console";
const streamEl = () => $(activeView === "view-console" ? "#msgs-console" : "#msgs-main");
const scrollBottom = () => { const el = streamEl(); if (el) el.scrollTop = el.scrollHeight; };

function addMsg(who, html, cardHTML) {
  const el = streamEl();
  const div = document.createElement("div");
  div.className = `msg ${who}`;
  div.innerHTML = `
    <div class="msg-head"><span>${escapeHTML(who === "user" ? L().you : L().agent)}</span><span>${now()}</span></div>
    <div class="bubble">${html}${cardHTML ? `<div class="card">${cardHTML}</div>` : ""}</div>`;
  el.appendChild(div);
  scrollBottom();
  saveTalk();   // 消息留档：切换会话后可原样还原
  return div;
}

/* ---------- 思考过程面板（等待期间滚动小字；完成/打断后折叠，可点开看全） ---------- */
const newRequestId = () => (crypto.randomUUID
  ? crypto.randomUUID()
  : `r-${Date.now()}-${Math.random().toString(36).slice(2)}`);

function showThinking() {
  const el = streamEl();
  const div = document.createElement("div");
  div.className = "msg ai msg-think";
  div.innerHTML = `
    <div class="think is-open" data-state="running">
      <button class="think-head" type="button" aria-expanded="true">
        <span class="think-dot"></span>
        <span class="think-title">${escapeHTML(L().thinkTitle)}</span>
        <span class="think-meta"></span>
        <span class="think-caret">⌄</span>
      </button>
      <ol class="think-steps"><li class="think-line is-wait"><i></i><i></i><i></i></li></ol>
    </div>`;
  el.appendChild(div);
  scrollBottom();
  const box = $(".think", div);
  const head = $(".think-head", div);
  head.addEventListener("click", () => {
    box.classList.toggle("is-open");
    head.setAttribute("aria-expanded", String(box.classList.contains("is-open")));
  });
  return { el: div, box, head, steps: $(".think-steps", div), meta: $(".think-meta", div),
           rendered: 0, done: false, lastSteps: [] };
}

/** 单条步骤 → 小字文案：后端只给 kind/name/detail 数据，措辞全部在前端本地化。 */
function thinkStepText(s) {
  const t = L();
  if (s.kind === "stage") {
    const label = t.stages[s.name] || s.name;
    if (s.name === "route" && s.detail) return `${label} · ${t.routes[s.detail] || s.detail}`;
    return s.detail ? `${label} · ${s.detail}` : label;
  }
  if (s.kind === "llm") return (s.detail && s.detail !== "0") ? t.thinkStep(s.detail) : t.thinking;
  if (s.kind === "tool") {
    if (s.status === "pending") return t.toolCall(s.name, s.detail);
    if (s.status === "skipped") return t.toolSkipped(s.name);
    if (s.status === "error" || s.status === "deny") return t.toolFail(s.name, s.detail);
    return t.toolDone(s.name, s.detail);
  }
  if (s.kind === "note") {
    const v = t.notes[s.name];
    return typeof v === "function" ? v(s.detail) : (v || s.name);
  }
  return `${s.name || ""} ${s.detail || ""}`.trim();
}

function renderThink(panel, snap) {
  const steps = snap.steps || [];
  if (steps.length && panel.rendered === 0) panel.steps.innerHTML = "";  // 去掉占位点
  for (let i = panel.rendered; i < steps.length; i++) {
    const s = steps[i];
    const li = document.createElement("li");
    li.className = `think-line is-${s.kind}`
      + ((s.status === "error" || s.status === "deny") ? " is-bad" : "")
      + (s.status === "pending" ? " is-pending" : "");
    li.textContent = thinkStepText(s);
    panel.steps.appendChild(li);
    panel.rendered = i + 1;
  }
  panel.lastSteps = steps;
  panel.box.dataset.state = snap.status || "running";
  if (snap.status && snap.status !== "running") {
    if (!panel.done) { panel.done = true; finishThink(panel, steps, snap.status); }
  } else {
    panel.meta.textContent = L().thinking;
  }
  scrollBottom();
}

/** 收尾：折叠成一行摘要（被打断时保持展开，让用户看清停在哪一步）。 */
function finishThink(panel, steps, status) {
  const tools = new Set(steps.filter(s => s && s.kind === "tool").map(s => s.name)).size;
  panel.meta.textContent = tools ? L().thinkSummary(steps.length, tools) : L().thinkNoTool(steps.length);
  panel.meta.classList.add("is-final");
  panel.box.classList.toggle("is-open", status === "interrupted");
  panel.head.setAttribute("aria-expanded", String(panel.box.classList.contains("is-open")));
}

/** 轮询运行态快照；返回停止函数（回复到达即停，避免多余请求）。 */
function pollThink(panel, requestId) {
  let alive = true;
  const tick = async () => {
    if (!alive) return;
    try {
      const res = await fetch(`/api/think/${encodeURIComponent(requestId)}`);
      if (res.ok) renderThink(panel, await res.json());
    } catch (e) { /* 轮询失败静默：思考展示是旁路，绝不打扰主链路 */ }
  };
  tick();
  const timer = setInterval(tick, 450);
  return () => { alive = false; clearInterval(timer); };
}

/* ---------- 卡片渲染 ---------- */
const checkIcon = ok => ok ? `<span class="ck ok">✓</span>` : `<span class="ck bad">✗</span>`;

const orderCard = (o) => `
  <div class="card-title"><svg viewBox="0 0 24 24"><path d="M3 7h11v8H3zM14 10h4l3 3v2h-7z"/><circle cx="7" cy="17.4" r="1.6"/><circle cx="17.4" cy="17.4" r="1.6"/></svg>${L().card.order} ${escapeHTML(o.order_no)} · ${escapeHTML(o.carrier)}</div>
  <div class="order-row">
    <img src="${o.product.img}" alt="">
    <div><b>${escapeHTML(o.product.name)}</b><div class="sub">¥${o.product.price} × ${o.qty} · ${escapeHTML(o.paid_at)}</div></div>
  </div>
  <div class="track">${(o.steps || []).map(s => `<span class="tp ${s.state}">${escapeHTML(s.label)}</span>`).join("")}</div>`;

const recoCard = (items) => `
  <div class="card-title"><svg viewBox="0 0 24 24"><path d="m12 3 2.6 5.3L20 9l-4 3.8.9 5.6L12 15.9 7.1 18.4 8 12.8 4 9l5.4-.7z"/></svg>${L().card.reco}</div>
  <div class="link-row">${items.map(p => `
    <div class="prod-card"><img src="${p.img}" alt=""><b>${escapeHTML(p.name)}</b><div class="price"><small>¥</small>${p.price}</div></div>`).join("")}</div>`;

const reportCard = (d) => `
  <div class="card-title">${L().card.verdict} · ${escapeHTML(d.category || "")} <span class="score-tag">${escapeHTML(d.score || "")}</span><span class="verdict ${d.verdict === "建议推进" ? "go" : "warn"}">${escapeHTML(d.verdict || "")}</span></div>
  <div class="check-list">${Object.entries(d.steps || {}).map(([k, v]) => `
    <div class="check-row">${checkIcon(v.passed)}<span class="ck-name">${escapeHTML(k)}</span>
      <span class="ck-detail">${Object.entries(v).filter(([x]) => x !== "passed").map(([x, b]) => `${escapeHTML(x)}:${typeof b === "boolean" ? (b ? "✓" : "✗") : escapeHTML(String(b))}`).join(" · ")}</span>
    </div>`).join("")}</div>`;

const supplierCard = (d) => `
  <div class="card-title">${L().card.supplier} · ${escapeHTML(d.keyword || "")}</div>
  <table class="sup-table">
    <tr><th>供应商</th><th>${L().card.moq}</th><th>${L().card.unitPrice}</th><th>${L().card.lead}</th><th>评分</th></tr>
    ${(d.rows || []).map(r => `
      <tr class="${r.name === d.recommend ? "best" : ""}"><td>${r.name === d.recommend ? "★ " : ""}${escapeHTML(r.name)}<small>${escapeHTML(r.city || "")}</small></td>
      <td>${r.moq} 件</td><td>¥${r.unit_price}</td><td>${r.lead_days} 天</td><td>${r.rating}</td></tr>`).join("")}
  </table>
  <div class="recommend-line">${L().card.recommend}：<b>${escapeHTML(d.recommend || "")}</b>${d.reason ? ` —— ${escapeHTML(d.reason)}` : ""}</div>`;

const listingCard = (d) => `
  <div class="card-title">${L().card.draft} · ${escapeHTML(d.product || "")} <span class="score-tag">标题 ${d.title_len || 0} 字符</span></div>
  <div class="draft-title">${escapeHTML(d.title || "")}</div>
  <div class="sub-head">${L().card.bullets}</div>
  <ol class="bullets">${(d.bullets || []).map(b => `<li>${escapeHTML(b)}</li>`).join("")}</ol>
  <div class="sub-head">${L().card.terms}</div>
  <div class="terms">${escapeHTML(d.search_terms || "")}</div>
  <div class="tips">${(d.tips || []).map(t => `<span>${escapeHTML(t)}</span>`).join("")}</div>`;

const imageCard = (d) => `
  <div class="card-title">${L().card.images} <span class="score-tag">${d.passed_count}/${d.total}</span></div>
  <div class="check-list">${(d.detail || []).map(x => `
    <div class="check-row">${checkIcon(x.passed)}<span class="ck-name">图 ${x.image_no}</span>
      <span class="ck-detail">${x.white_bg ? "白底✓" : "白底✗"} · 占比${x.ratio_ok ? "✓" : "✗"} · ${x.no_text_watermark ? "无文字✓" : "有文字✗"}</span>
    </div>`).join("")}</div>`;

const priceCard = (d) => `
  <div class="card-title">${L().card.price} · ${escapeHTML(d.product || "")}</div>
  <div class="price-line"><span class="price-big">¥${d.suggested}</span><span class="price-sub">${L().card.suggested}</span></div>
  <div class="price-meta">竞品参考 ¥${d.competitor_ref} · ${L().card.floor} <b>¥${d.min_break_even}</b></div>
  <div class="tips">${d.hint ? `<span>${escapeHTML(d.hint)}</span>` : ""}</div>`;

const fulfillCard = (d) => `
  <div class="card-title">${L().card.fulfill} · ${escapeHTML(d.product || "")}</div>
  <div class="price-line"><span class="price-big">${escapeHTML(d.suggested_mode || "")}</span><span class="price-sub">${L().card.mode}</span></div>
  <div class="price-meta">${L().card.batch}：<b>${d.first_batch} 件</b></div>
  <div class="tips">${d.hint ? `<span>${escapeHTML(d.hint)}</span>` : ""}</div>`;

function cardFrom(a) {
  if (!a || !a.data) return "";
  if (a.intent === "order") return orderCard(a.data);
  if (a.intent === "recommend" && Array.isArray(a.data.items)) return recoCard(a.data.items);
  const t = a.data.type;
  if (t === "research_report") return reportCard(a.data);
  if (t === "supplier_compare") return supplierCard(a.data);
  if (t === "listing_draft") return listingCard(a.data);
  if (t === "image_check") return imageCard(a.data);
  if (t === "price_strategy") return priceCard(a.data);
  if (t === "fulfillment") return fulfillCard(a.data);
  return "";
}

/* ---------- 本地演示兜底（后端不可达时） ---------- */
function localAnswer(q) {
  const s = q.toLowerCase(), zh = lang === "zh";
  if (/(供应商|1688|货源|进货|采购|仕入れ)/.test(s)) {
    return { reply: zh ? "已为您找到候选货源（演示数据），详见对比卡片：" : "仕入れ先の候補です（デモデータ）。比較カードをご覧ください：",
      intent: "research", data: { type: "supplier_compare", keyword: zh ? "蓝牙耳机" : "ワイヤレスイヤホン",
        recommend: "深圳声学智造", reason: zh ? "评分 4.9 且综合分最高" : "評価4.9かつ総合スコア最高",
        rows: [
          { name: "深圳声学智造", city: "深圳", moq: 60, unit_price: 45.0, lead_days: 9, rating: 4.9 },
          { name: "东莞音频电子", city: "东莞", moq: 100, unit_price: 39.0, lead_days: 12, rating: 4.5 },
          { name: "义乌数码港·鑫声", city: "义乌", moq: 40, unit_price: 48.0, lead_days: 6, rating: 4.4 }] } };
  }
  if (/(选品|选个|热销|爆款|利润|竞争|需求|报告|選品|利益|需要)/.test(s)) {
    return { reply: zh ? "「云感耳机」选品四步结论：<b>建议推进</b>（3/3）。" : "「雲感イヤホン」の選品結論：<b>推進を推奨</b>（3/3）。",
      intent: "research", data: { type: "research_report", category: zh ? "云感耳机" : "雲感イヤホン", score: "3/3",
        verdict: zh ? "建议推进" : "推進を推奨",
        steps: {
          [zh ? "需求" : "需要"]: { passed: true, [zh ? "搜索量≥3000" : "検索量≥3000"]: true, [zh ? "售价$20~$70" : "価格$20〜$70"]: true, [zh ? "重量<2lb" : "重量<2lb"]: true },
          [zh ? "竞争" : "競合"]: { passed: true, [zh ? "首页评分≤4.3" : "上位評価≤4.3"]: true, [zh ? "平均评论≤500" : "平均レビュー≤500"]: true },
          [zh ? "利润" : "利益"]: { passed: true, margin: 0.34, profit_per_unit: 17.6 } } } };
  }
  if (/(图片|主图|合规|画像|規約)/.test(s)) {
    return { reply: zh ? "主图合规自检完成（演示数据）：" : "メイン画像の規約チェック完了（デモデータ）：",
      intent: "listing", data: { type: "image_check", passed_count: 5, total: 7,
        detail: [1, 2, 3, 4, 5].map(n => ({ image_no: n, passed: true, white_bg: true, ratio_ok: true, no_text_watermark: true }))
          .concat([{ image_no: 6, passed: false, white_bg: false, ratio_ok: true, no_text_watermark: true },
                   { image_no: 7, passed: false, white_bg: true, ratio_ok: false, no_text_watermark: false }]) } };
  }
  if (/(定价|售价|价格|価格)/.test(s)) {
    return { reply: zh ? "「云感耳机」定价建议：¥284（略低于竞品做首发）。" : "「雲感イヤホン」の価格提案：¥284（競合よりやや低めで初回投入）。",
      intent: "listing", data: { type: "price_strategy", product: zh ? "云感无线蓝牙耳机 Pro · 半入耳" : "雲感ワイヤレスイヤホン Pro",
        competitor_ref: 299, suggested: 284, min_break_even: 233,
        hint: zh ? "低于 233 元将跌破 30% 净利率红线。" : "233元を下回ると純利益率30%を割ります。" } };
  }
  if (/(履约|备货|fba|发货|フルフィルメント|発送)/.test(s)) {
    return { reply: zh ? "履约建议（演示数据）：" : "フルフィルメント提案（デモデータ）：",
      intent: "listing", data: { type: "fulfillment", product: zh ? "云感无线蓝牙耳机 Pro" : "雲感ワイヤレスイヤホン Pro",
        suggested_mode: "FBA", first_batch: 300,
        hint: zh ? "首批 300 件试销，销速稳定后转海运补货。" : "初回300個でテスト、回転が安定したら船便で補充。" } };
  }
  if (/(listing|上架|标题|五点|出品|タイトル)/.test(s)) {
    return { reply: zh ? "「云感耳机」Listing 草稿已生成（见卡片）：" : "「雲感イヤホン」の Listing 下書きを生成しました：",
      intent: "listing", data: { type: "listing_draft", product: zh ? "云感耳机" : "雲感イヤホン",
        title: "Quote Wireless Earbuds with ANC HiFi Stereo / 36H Playtime / IPX5 for Sports & Commuting",
        title_len: 80,
        bullets: zh
          ? ["主动降噪，通勤地铁一戴安静：双馈 ANC 降噪深度 -35dB，专注不被打扰。",
             "36 小时长续航：单次 8h + 充电盒再续 28h，出差一周不用带线。",
             "云感半入耳，久戴不痛：单耳仅 3.8g，人体工学贴合，跑步也不掉。",
             "HiFi 双单元：10mm 动圈 + 高解析解码，低音有量、人声清晰。",
             "IPX5 防水：运动流汗、小雨天都可以放心用。"]
          : ["ANC で通勤が静か：-35dB のハイブリッド ANC。",
             "36時間再生：8h + ケース28h。",
             "片耳3.8gのセミインナーで長時間でも痛くない。",
             "10mmドライバー＋高解像度デコード。",
             "IPX5 防水で運動や小雨も安心。"],
        search_terms: "bluetooth earphones anc wireless earbuds sport headset true wireless ipx5",
        tips: zh ? ["标题 ≤75 字符（亚马逊 2025 新规）", "五点每点首词大写、先答核心问题"]
                  : ["タイトルは75文字以内（Amazon 2025 の新規約）", "箇条書きは先頭を大文字に"] } };
  }
  return { reply: L().greet.replace("{name}", L().boardResearch), intent: "none", data: null };
}

/* ---------- 发送流程 ---------- */
const inputEl = () => $(activeView === "view-console" ? "#inputCon" : "#input");
const stopEl = () => $(activeView === "view-console" ? "#stopBtnCon" : "#stopBtn");
const hintEl = () => $(activeView === "view-console" ? "#hintCon" : "#hint");
let busy = false;
let activeRequestId = "";   // 当前在跑这一轮的运行态标识（打断按钮要用）
const greeted = new Set();   // 每个上下文只打一次招呼（语言切换会重渲染，不应重复问候）

/** 当前上下文的标识（打招呼去重 / 会话标题取用都按它区分）。 */
const contextKey = () => `${projId()}::${featKey()}`;

/** 忙碌态 UI：只在跑动时露出「打断」按钮。 */
function setBusyUI(on) {
  const stop = stopEl();
  if (stop) { stop.hidden = !on; stop.disabled = false; }
}

/** 请求打断本轮：只发信号，真正停止由后端在下个检查点完成（协作式）。 */
async function interruptCurrent() {
  if (!busy || !activeRequestId) return;
  const btn = stopEl();
  if (btn) btn.disabled = true;
  hintEl().textContent = L().stopHint;
  try {
    await fetch("/api/ask/interrupt", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request_id: activeRequestId, session_id: activeSid() })
    });
  } catch (e) { /* 打断失败：本轮会自然跑完，不影响结果 */ }
}

async function send(text) {
  const box = inputEl();
  const q = (text ?? box.value).trim();
  if (!q || busy) return;
  busy = true;
  setBusyUI(true);
  if (text === undefined) { box.value = ""; autoGrow(box); }
  // 先确保本上下文有会话 id：留档（saveTalk）按 activeTalk 存，拿到 id 才能存得下来
  activeTalk = await ensureSession();

  addMsg("user", escapeHTML(q));
  const panel = showThinking();
  hintEl().textContent = L().thinking;

  const requestId = newRequestId();
  activeRequestId = requestId;
  const stopPolling = pollThink(panel, requestId);

  let a = null, usedLocal = false;
  try { a = await askBackend(q, requestId); } catch (e) { a = null; }
  stopPolling();
  activeRequestId = "";

  const interrupted = !!(a && a.data && a.data.interrupted);
  if (!a || (!interrupted && !a.reply)) { usedLocal = true; a = localAnswer(q); }
  if (usedLocal) await wait(DELAY());

  // 思考面板收尾：后端已结束但轮询没赶上时，本地也要落到终态，绝不留"进行中"
  if (!panel.done) {
    panel.done = true;
    finishThink(panel, panel.lastSteps || [], interrupted ? "interrupted" : "done");
  }
  setBusyUI(false);

  const replyDiv = interrupted
    ? addMsg("ai", [
        a.reply ? escapeHTML(a.reply) : "",
        `<span class="em">${escapeHTML(L().interruptNotice(a.data || {}))}</span>`
      ].filter(Boolean).join("<br>"))
    : addMsg("ai", a.reply, cardFrom(a));

  // 把思考面板挪进这条回复里（头部之下、气泡之上），随对话一起留档
  const bubble = $(".bubble", replyDiv);
  if (bubble) replyDiv.insertBefore(panel.box, bubble);
  panel.el.remove();
  scrollBottom();

  hintEl().textContent = L().replied;
  saveTalk();   // 收尾再存一次：把思考面板挪进气泡后的最终 DOM 一起留档
  refreshTalks();   // 首轮提问会写标题，会话切换条要跟着更新
  busy = false;
}

/* ---------- 结束谈话 ---------- */
async function endConversation() {
  const sid = activeSid();
  let ok = !!sid;
  if (sid) {
    try {
      const res = await fetch("/api/conversation/end", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid, user_id: "default" })
      });
      ok = res.ok;
    } catch (e) { ok = false; }
  }
  await ensureSession(true);
  addMsg("ai", L()[ok ? "endDone" : "endFail"]);
}

/* ---------- 视图切换 ---------- */
function showView(id) {
  activeView = id;
  $$(".view").forEach(v => v.classList.toggle("is-active", v.id === id));
}

/** 面包屑：总控台 / 项目 / 细分板块（前两级可点回退）。 */
function renderCrumb(mod) {
  const parts = [`<span class="crumb-node" data-home>${escapeHTML(L().crumbHome)}</span>`];
  if (activeProject) {
    const cls = mod ? "crumb-node" : "crumb-node is-current";
    parts.push(`<span class="crumb-sep">/</span><span class="${cls}" data-project-node>`
      + `${escapeHTML(activeProject.name)}</span>`);
  }
  if (mod) {
    parts.push(`<span class="crumb-sep">/</span><span class="crumb-node is-current">${escapeHTML(tr(mod.title))}</span>`);
  }
  $("#crumb").innerHTML = parts.join("");
  const home = $("[data-home]", $("#crumb"));
  if (home) home.addEventListener("click", goConsole);
  const proj = $("[data-project-node]", $("#crumb"));
  if (proj) proj.addEventListener("click", () => openProject(activeProject));
}

/** 回总控台：退出项目 —— 不挂项目就不注入项目级 / 板块级记忆，上下文最干净。 */
async function goConsole() {
  saveTalk();
  activeModule = null;
  activeProject = null;
  activeBoard = "research";
  renderCrumb(null);
  showView("view-console");
  activeTalk = await ensureSession();   // 总控台用的是全局会话
  restoreTalk();
  renderRail();
  loadConsoleBlocks();                  // 项目卡片 / 待办 / 记忆三块重新取数
}

/** 进入板块对话窗口 —— 这里就是「二级」：会话条在窗口顶部，切板块不改变会话归属。 */
async function openFeature(board, key) {
  if (!activeProject || !BOARDS[board]) return;
  const mod = BOARDS[board].modules.find(m => m.key === key);
  if (!mod) return;
  saveTalk();
  activeBoard = board;
  activeModule = mod;

  // 左侧表单
  $("#modBadge").textContent = mod.no;
  $("#modTitle").textContent = tr(mod.title);
  $("#modDesc").textContent = tr(mod.desc);
  renderForm(mod);

  // 对话流头
  $("#streamLabel").textContent = `${BOARDS[board].no} ${BOARDS[board].title()} · ${tr(mod.title)}`;
  hintEl().textContent = L().hint;
  renderCrumb(mod);
  showView("view-module");

  activeTalk = await ensureSession();   // 会话挂在「项目 + 板块」之下
  restoreTalk();
  await loadTalks();

  const ck = contextKey();
  if (!greeted.has(ck)) {
    greeted.add(ck);
    addMsg("ai", L().greet.replace("{name}", tr(mod.title)));
  }
  if (mod.free) inputEl().focus();
}

function renderForm(mod) {
  const form = $("#modForm");
  if (mod.free || !mod.fields.length) {
    form.innerHTML = `<p class="form-tip">${escapeHTML(L().freeTip)}</p>`;
    return;
  }
  form.innerHTML = mod.fields.map(fieldHTML).join("") +
    `<button class="form-submit" type="submit">${escapeHTML(L().runModule)}</button>`;
}

function fieldHTML(f) {
  const lab = escapeHTML(tr(f.label));
  const ph = escapeHTML(f.ph ? tr(f.ph) : "");
  const req = f.required ? "required" : "";
  if (f.type === "select") {
    const opts = f.options.map(([v, t]) => `<option value="${escapeHTML(v)}">${escapeHTML(t)}</option>`).join("");
    return `<div class="field"><label>${lab}</label><select name="${f.name}">${opts}</select></div>`;
  }
  if (f.type === "textarea") {
    return `<div class="field"><label>${lab}</label><textarea name="${f.name}" placeholder="${ph}" ${req}></textarea></div>`;
  }
  // number 必须给 step="any"：默认 step=1 会让 29.9 这类小数校验失败，原生校验会静默拦住整个表单
  const num = f.type === "number" ? ' step="any" min="0" inputmode="decimal"' : "";
  return `<div class="field"><label>${lab}</label><input type="${f.type || "text"}" name="${f.name}" placeholder="${ph}"${num} ${req}></div>`;
}

/* ---------- 设置面板 ---------- */
const modal = $("#settingsModal");
const setMsg = $("#setMsg");

function openModal() { modal.classList.add("is-open"); modal.setAttribute("aria-hidden", "false"); }
function closeModal() { modal.classList.remove("is-open"); modal.setAttribute("aria-hidden", "true"); }
function msg(text, kind = "") { setMsg.className = `modal-msg ${kind}`; setMsg.textContent = text; }

/* 密钥状态说明：已配置 + 连接测试结论（未验证 / 通过 / 失败） */
function keyNoteText(j) {
  if (!j.api_key_set) return L().setKeyNone;
  const base = L().setKeyMasked(j.api_key_masked);
  if (j.key_verified === true) return base + L().keyOk;
  if (j.key_verified === false) return base + L().keyBad;
  return base + L().keyUnverified;
}

/**
 * 画密钥输入框。明文永不回填（后端只回掩码，这是安全设计），
 * 所以"已保存"这件事只能靠 placeholder 表达 —— 否则空栏位会被误读成"没保存"。
 * clear=true：清空输入（打开弹窗、保存成功后）；false：只刷新占位（测试连接后保留用户输入）。
 * 传 j 时记住状态，供语言切换后重绘占位（不换语言就用上次的状态）。
 */
let _lastKeyStatus = null;

function paintKeyField(j, clear = true) {
  if (j) _lastKeyStatus = j;
  const el = $("#setKey");
  if (!el) return;
  if (clear) el.value = "";
  const st = j || _lastKeyStatus;
  el.placeholder = (st && st.api_key_set)
    ? L().keyPhSaved(st.api_key_masked || "")
    : L().keyPhEmpty;
}

async function openSettings() {
  msg("");
  paintKeyField(null);
  try {
    const res = await fetch("/api/settings");
    const j = await res.json();
    $("#setUrl").value = j.base_url || "";
    $("#setModel").value = j.model || "";
    $("#setTemp").value = j.temperature ?? "";
    $("#setPerm").value = j.permission_mode || "plan";
    $("#setCallBudget").value = j.call_token_budget ?? "";
    $("#setCost").value = j.cost_budget ?? 0;
    $("#setCostCur").value = j.cost_currency || "CNY";
    $("#setCostNote").textContent = L().setCostNote(j.usd_cny_rate);
    $("#setKeyNow").textContent = keyNoteText(j);
    paintKeyField(j);   // 已保存 → 占位显示掩码，避免空栏位被误读成"没保存"
  } catch (e) {
    $("#setKeyNow").textContent = L().statusDown;
  }
  openModal();
  $("#setKey").focus();
}

async function saveSettings() {
  const body = { persist: $("#setPersist").checked };
  const k = $("#setKey").value.trim();
  const u = $("#setUrl").value.trim();
  const m = $("#setModel").value.trim();
  const t = $("#setTemp").value.trim();
  const cb = $("#setCallBudget").value.trim();
  const cost = $("#setCost").value.trim();
  if (k) body.api_key = k;
  if (u) body.base_url = u;
  if (m) body.model = m;
  if (t !== "") body.temperature = Number(t);
  if (cb !== "") body.call_token_budget = Number(cb);
  if (cost !== "") { body.cost_budget = Number(cost); body.cost_currency = $("#setCostCur").value; }
  body.permission_mode = $("#setPerm").value;
  msg("…");
  try {
    const res = await fetch("/api/settings", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    });
    const j = await res.json();
    if (j.errors && j.errors.length) { msg(j.errors.join("；"), "is-bad"); return; }
    if (!j.changed || !j.changed.length) { msg(L().saveNone); return; }
    msg(L().saveOk(j.changed.length, j.persisted), "is-ok");
    $("#setKeyNow").textContent = keyNoteText({
      api_key_set: j.status.api_key_set,
      api_key_masked: j.status.api_key_masked,
      key_verified: j.status.key_verified,
    });
    // 保存成功后清空明文输入，但把掩码写进占位提示 —— 否则用户会以为"刚保存的 Key 丢了"
    paintKeyField({
      api_key_set: j.status.api_key_set,
      api_key_masked: j.status.api_key_masked,
    });
    applyStatus(j.status);
  } catch (e) {
    msg(L().saveFail + e.message, "is-bad");
  }
}

async function testSettings() {
  const btn = $("#setTest");
  // 带上表单当前填的值：可以「先填 Key 再点测试」，不必先保存
  const body = {};
  const k = $("#setKey").value.trim();
  const u = $("#setUrl").value.trim();
  const m = $("#setModel").value.trim();
  if (k) body.api_key = k;
  if (u) body.base_url = u;
  if (m) body.model = m;
  btn.disabled = true; msg(L().testing);
  try {
    const res = await fetch("/api/settings/test", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    });
    const j = await res.json();
    if (j.ok) msg(L().testOk(j.model) + (j.echo ? ` · echo: ${j.echo}` : ""), "is-ok");
    else msg(L().testFail + (j.error || ""), "is-bad");
    await refreshStatus();
    const g = await (await fetch("/api/settings")).json();
    $("#setKeyNow").textContent = keyNoteText(g);
    paintKeyField(g, false);   // 保留用户刚填的明文（可能接着点保存），只刷新占位
  } catch (e) {
    msg(L().testFail + e.message, "is-bad");
  } finally { btn.disabled = false; }
}

/* ---------- 运行状态 ---------- */
function applyStatus(st) {
  const pill = $("#statusPill"), txt = $("#statusText");
  if (!st) { pill.className = "status-pill"; pill.title = ""; txt.textContent = L().statusDown; return; }
  if (!st.api_key_set) {                     // 没配密钥 → 本地兜底
    pill.className = "status-pill is-local";
    txt.textContent = L().statusLocal;
    pill.title = st.reason || "";
    return;
  }
  if (st.key_verified === true) {            // 配了密钥且连接测试通过
    pill.className = "status-pill is-live";
    txt.textContent = L().statusLive(st.model || st.configured_model || "LLM");
    pill.title = "";
    return;
  }
  if (st.key_verified === false) {           // 配了密钥但测试失败
    pill.className = "status-pill is-bad";
    txt.textContent = L().statusKeyBad;
    pill.title = st.key_error || "";
    return;
  }
  pill.className = "status-pill is-warn";    // 配了密钥但还没验证过
  txt.textContent = L().statusUnverified;
  pill.title = st.reason || "";
}

async function refreshStatus() {
  try {
    const res = await fetch("/api/status");
    applyStatus(await res.json());
  } catch (e) { applyStatus(null); }
}

/* ---------- 语言 ---------- */
function setLang(l, announce = true) {
  lang = l;
  localStorage.setItem("quote-lang", l);
  document.documentElement.lang = (l === "ja") ? "ja" : "zh-CN";
  document.title = L().docTitle;
  $("#langZH").classList.toggle("is-active", l === "zh");
  $("#langJA").classList.toggle("is-active", l === "ja");
  $("#langZH").setAttribute("aria-pressed", String(l === "zh"));
  $("#langJA").setAttribute("aria-pressed", String(l === "ja"));

  $$("[data-i18n]").forEach(el => { const v = L()[el.dataset.i18n]; if (typeof v === "string") el.textContent = v; });
  $$("[data-i18n-ph]").forEach(el => { const v = L()[el.dataset.i18nPh]; if (typeof v === "string") el.placeholder = v; });
  paintKeyField(null, false);   // 密钥栏位带状态（已保存/未配置），换语言要重绘占位文案

  renderRail();                 // 左栏项目列的状态词 / 相对时间随语言切换
  renderDeckProjects();
  renderDeckTodos();
  renderDeckMemory();           // 记忆块的小标题与空态文案
  renderTalks();                // 会话切换条的三态词随语言切换
  if (activeProject) renderProject();
  if (activeModule) {           // 板块窗口内的静态文案就地重绘（不重新取数）
    $("#modBadge").textContent = activeModule.no;
    $("#modTitle").textContent = tr(activeModule.title);
    $("#modDesc").textContent = tr(activeModule.desc);
    $("#streamLabel").textContent =
      `${BOARDS[activeBoard].no} ${BOARDS[activeBoard].title()} · ${tr(activeModule.title)}`;
    renderForm(activeModule);
    renderCrumb(activeModule);
  } else {
    renderCrumb(null);
  }
  refreshStatus();
  if (announce) addMsg("ai", L().switched);
}

/* ---------- 项目总览渲染 ---------- */
/** 板块 key → 可读名称（会话清单 / 待办汇总 / 移动菜单共用）。 */
function featureTitle(fk) {
  if (!fk) return lang === "zh" ? "未分类" : "未分類";
  for (const b of Object.values(BOARDS)) {
    const m = b.modules.find(x => x.key === fk);
    if (m) return tr(m.title);
  }
  return fk;
}

/** 全部细分板块（含所属区块），「移动到其他板块」的候选来源。 */
function allFeatures() {
  const out = [];
  Object.entries(BOARDS).forEach(([board, cfg]) =>
    cfg.modules.forEach(m => out.push({ board, key: m.key, label: `${m.no} ${tr(m.title)}` })));
  return out;
}

/**
 * 项目总览：选品 / Listing **两块平级区块并排**，每块把自己的细分板块一起列出来，
 * **直接点板块就进对话窗口**（没有中间层）。板块上顺带显示会话数与未完成断点数。
 */
function renderProject() {
  if (!activeProject) return;
  $("#projNo").textContent = (activeProject.name || "PR").slice(0, 2).toUpperCase();
  $("#projTitle").textContent = activeProject.name || L().railUntitled;
  $("#projDesc").textContent = activeProject.desc || L().projectNoDesc;

  ["research", "listing"].forEach(board => {
    const cfg = BOARDS[board];
    const grid = $(`#grid-${board}`);
    const counts = boardStats[board] || {};
    grid.innerHTML = cfg.modules.map((m, i) => {
      const c = counts[m.key] || {};
      const n = Number(c.sessions || 0);
      const open = Number(c.open || 0);
      const meta = [
        n ? L().projectSessions(n) : "",
        open ? (lang === "zh" ? `${open} 个未完成` : `未完了 ${open}`) : ""
      ].filter(Boolean).join(" · ");
      return `<button class="mod-card" type="button" data-board="${board}" data-mod="${m.key}"
              data-open="${open ? "1" : "0"}" style="animation-delay:${i * 55}ms">
        <span class="mod-no">${m.no}</span>
        <span class="mod-body"><b>${escapeHTML(tr(m.title))}</b><i>${escapeHTML(tr(m.desc))}</i></span>
        ${meta ? `<span class="mod-meta">${escapeHTML(meta)}</span>` : ""}
        <span class="mod-go">→</span>
      </button>`;
    }).join("");
    $$(".mod-card", grid).forEach(btn =>
      btn.addEventListener("click", () => openFeature(btn.dataset.board, btn.dataset.mod)));
  });
}

/** 板块计数：一次拿全（含未分类），避免逐板块请求。 */
async function loadStats() {
  boardStats = {};
  if (!activeProject) return;
  try {
    const res = await fetch(`/api/projects/${encodeURIComponent(activeProject.project_id)}/tree`);
    const j = await res.json();
    boardStats = (j && j.counts) || {};
  } catch (e) { boardStats = {}; }
}

/** 打开项目：进入项目总览（两个平级区块 + 板块网格）。 */
async function openProject(p) {
  const proj = (typeof p === "string" ? projects.find(x => x.project_id === p) : p);
  if (!proj) return;
  saveTalk();
  activeProject = proj;
  activeModule = null;
  activeBoard = "research";
  activeTalk = "";      // 项目总览本身没有会话（配对失效，避免串档）
  renderCrumb(null);
  showView("view-project");
  renderRail();
  await loadStats();
  renderProject();
}

/* ---------- 总控台四块 ---------- */
async function loadProjects() {
  try {
    // include_archived=true：归档过的也要拿到 —— 归档 ≠ 删除，界面上得看得见、能恢复
    const res = await fetch("/api/projects?user_id=default&include_archived=true");
    const j = await res.json();
    const all = (j && j.projects) || [];
    projects = all.filter(p => p.status !== "archived");
    archivedProjects = all.filter(p => p.status === "archived");
  } catch (e) {
    projects = [];
    archivedProjects = [];
  }
  if (activeProject) {
    const fresh = projects.find(p => p.project_id === activeProject.project_id);
    if (fresh) activeProject = fresh;
  }
}

/** ② 项目卡片：与左栏项目列同源，点一张进项目总览。 */
function renderDeckProjects() {
  const list = $("#deckProjects");
  if (!list) return;
  if (!projects.length) {
    const text = archivedProjects.length
      ? L().deckProjectsAllArchived(archivedProjects.length)
      : L().railEmpty;
    list.innerHTML = `<li class="deck-empty">${escapeHTML(text)}</li>`;
    return;
  }
  list.innerHTML = projects.map(p => {
    const open = Number(p.open_interrupts || 0);
    const meta = [
      L().projectSessions(Number(p.session_count || 0)),
      open ? (lang === "zh" ? `${open} 个未完成` : `未完了 ${open}`) : ""
    ].filter(Boolean).join(" · ");
    return `<li><button class="deck-item" type="button" data-pid="${escapeHTML(p.project_id)}">
      <span class="deck-item-main">
        <b>${escapeHTML(p.name || L().railUntitled)}</b>
        <i>${escapeHTML(p.desc || L().projectNoDesc)}</i>
      </span>
      <span class="deck-item-meta">${escapeHTML(meta)}</span>
      <span class="mod-go">→</span>
    </button></li>`;
  }).join("");
  $$(".deck-item", list).forEach(btn =>
    btn.addEventListener("click", () => openProject(btn.dataset.pid)));
}

/** ③ 待办汇总：跨项目的未完成断点（台账只存 session_id，归属去会话清单里反查）。 */
async function loadTodos() {
  try {
    const [ir, cv] = await Promise.all([
      fetch("/api/interrupt/recent?status=open&limit=50").then(r => r.json()),
      fetch("/api/conversations?user_id=default&limit=500").then(r => r.json())
    ]);
    talkIndex = new Map(((cv && cv.conversations) || []).map(c => [c.session_id, c]));
    lastTodos = ((ir && ir.records) || []).filter(r => (r.status || "open") === "open");
  } catch (e) { talkIndex = new Map(); lastTodos = []; }
  renderDeckTodos();
}

function renderDeckTodos() {
  const list = $("#deckTodos");
  if (!list) return;
  $("#deckTodoCount").textContent = String(lastTodos.length);
  if (!lastTodos.length) {
    list.innerHTML = `<li class="deck-empty">${escapeHTML(L().deckTodoEmpty)}</li>`;
    return;
  }
  list.innerHTML = lastTodos.map((r, i) => {
    const where = talkIndex.get(r.session_id) || {};
    const proj = projects.find(p => p.project_id === where.project_id);
    const loc = [proj ? proj.name : (lang === "zh" ? "未分类" : "未分類"),
                 featureTitle(where.feature_key)].filter(Boolean).join(" · ");
    const step = r.step_index ? (lang === "zh" ? `第 ${r.step_index} 步` : `${r.step_index} 歩目`) : "";
    const tool = r.pending_tool
      ? (lang === "zh" ? `工具 ${r.pending_tool} 未执行` : `ツール ${r.pending_tool} 未実行`) : "";
    const stage = r.stage ? (L().stages[r.stage] || r.stage) : "";
    return `<li><button class="deck-item" type="button" data-todo="${i}">
      <span class="deck-item-main">
        <b>${escapeHTML(r.question || r.reason || L().railPending)}</b>
        <i>${escapeHTML([loc, stage].filter(Boolean).join(" · "))}</i>
      </span>
      <span class="deck-item-meta">${escapeHTML([step, tool].filter(Boolean).join(" · "))}</span>
      <span class="mod-go">→</span>
    </button></li>`;
  }).join("");
  $$(".deck-item", list).forEach(btn => btn.addEventListener("click", () => {
    const r = lastTodos[Number(btn.dataset.todo)];
    if (r) jumpToTalk(r.session_id);
  }));
}

/** ④ 记忆与规则：L0 全局层只读预览（死规则 / 长期事实 / 热点技能）。 */
async function renderDeckMemory() {
  const box = $("#deckMemory");
  if (!box) return;
  let j = null;
  try { j = await (await fetch("/api/memory/overview?user_id=default")).json(); } catch (e) { j = null; }
  if (!j || !j.ok) {
    box.innerHTML = `<p class="deck-empty">${escapeHTML(L().deckMemFail)}</p>`;
    return;
  }
  const group = (title, rows) => `
    <div class="mem-group">
      <h3>${escapeHTML(title)}</h3>
      ${rows.length
        ? `<ul>${rows.map(r => `<li>${escapeHTML(r)}</li>`).join("")}</ul>`
        : `<p class="deck-empty">${escapeHTML(L().deckMemEmpty)}</p>`}
    </div>`;
  const facts = (j.facts || []).map(f => {
    const v = typeof f.value === "string" ? f.value : JSON.stringify(f.value);
    return `${f.key}：${v}`;
  });
  box.innerHTML = group(L().deckMemRules, (j.rules || []).map(r => r.rule_text || "").filter(Boolean))
    + group(L().deckMemFacts, facts)
    + group(L().deckMemSkills, (j.skills || []).map(s => s.situation || s.goal || "").filter(Boolean));
}

/** 总控台三块异步取数（① 全局对话是本地会话，不需要取）。 */
async function loadConsoleBlocks() {
  await Promise.all([loadProjects(), loadTodos()]);
  renderDeckProjects();
  renderDeckMemory();
}

/** 跳到某段会话：项目 → 板块 → 会话（总控台「待办汇总」的点击落点）。 */
async function jumpToTalk(sid) {
  const where = talkIndex.get(sid) || {};
  const proj = projects.find(p => p.project_id === where.project_id);
  if (!proj) return;   // 不属于任何项目（未分类）→ 没有可跳的落点
  const inListing = BOARDS.listing.modules.some(m => m.key === where.feature_key);
  const board = BOARDS[where.scope] ? where.scope : (inListing ? "listing" : "research");
  const key = where.feature_key || freeChatKey(board);
  await openProject(proj);
  await openFeature(board, key);
  await switchTalk(sid);
}

/* ---------- 项目弹窗（新建 / 重命名） ---------- */
let projEditing = null;   // null = 新建；否则为被改名的项目

function openProjectModal(proj) {
  projEditing = proj || null;
  $("#projModalTitle").textContent = proj ? L().projectRename : L().projectNew;
  $("#projectModalSave").textContent = proj ? L().projectRename : L().projectCreate;
  $("#projName").value = proj ? (proj.name || "") : "";
  $("#projDescInput").value = proj ? (proj.desc || "") : "";
  projMsg("");
  const m = $("#projectModal");
  m.classList.add("is-open");
  m.setAttribute("aria-hidden", "false");
  setTimeout(() => $("#projName").focus(), 120);
}

function closeProjectModal() {
  const m = $("#projectModal");
  m.classList.remove("is-open");
  m.setAttribute("aria-hidden", "true");
}

function projMsg(text, kind = "") {
  const el = $("#projMsg");
  // 统一成 CSS 里的 .is-ok / .is-bad：调用处传的是 "bad"/"ok"，直接拼会得到无样式类名
  el.className = `modal-msg ${kind ? (kind.startsWith("is-") ? kind : "is-" + kind) : ""}`;
  el.textContent = text;
}

async function saveProject() {
  const name = ($("#projName").value || "").trim();
  const desc = ($("#projDescInput").value || "").trim();
  if (!name) { projMsg(L().required, "bad"); return; }
  projMsg("…");
  try {
    if (projEditing) {
      const pid = projEditing.project_id;
      const res = await fetch(`/api/projects/${encodeURIComponent(pid)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, desc })
      });
      const j = await res.json();
      if (!res.ok || !j.ok) { projMsg(L().projectEditFail + (j.error || res.status), "bad"); return; }
      closeProjectModal();
      await loadProjects();
      renderRail();
      renderDeckProjects();
      if (activeProject && activeProject.project_id === pid) { renderProject(); renderCrumb(null); }
      return;
    }
    const res = await fetch("/api/projects", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: "default", name, desc })
    });
    const j = await res.json();
    if (!res.ok || !j.ok) { projMsg(L().projectNewFail + (j.error || res.status), "bad"); return; }
    closeProjectModal();
    await loadProjects();
    renderRail();
    renderDeckProjects();
    await openProject(j.project);   // 直接进新项目：少一次点击，也明确"创建成功"
  } catch (e) {
    projMsg(L().projectNewFail + e.message, "bad");
  }
}

/** 归档 / 取消归档（归档 ≠ 删除：会话、记忆、断点全部保留）。 */
async function archiveProject(proj, archived) {
  if (!proj) return;
  try {
    const res = await fetch(`/api/projects/${encodeURIComponent(proj.project_id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: archived ? "archived" : "open" })
    });
    const j = await res.json();
    if (!res.ok || !j.ok) return;
  } catch (e) { return; }
  const wasActive = activeProject && activeProject.project_id === proj.project_id;
  if (archived) showArchived = true;   // 归档后自动展开「已归档」分组：让用户看见它去了哪、能一键恢复
  await loadProjects();
  renderRail();
  renderDeckProjects();
  if (archived && wasActive) goConsole();   // 正在看的项目被归档 → 回总控台
}

/* ---------- 板块窗口顶部的会话切换条 ---------- */

function talksOpen(on) {
  const menu = $("#talksMenu"), btn = $("#talksCur");
  if (!menu || !btn) return;
  menu.hidden = !on;
  btn.setAttribute("aria-expanded", String(on));
}

async function loadTalks() {
  talks = [];
  hiddenTalks = [];
  if (!activeProject || !activeModule) return;
  try {
    // include_hidden=true：被隐藏的会话也要拿到 —— 隐藏 ≠ 删除，得能看见、能恢复
    const q = new URLSearchParams({ user_id: "default", project_id: projId(),
                                   feature_key: featKey(), limit: "60",
                                   include_hidden: "true" });
    const res = await fetch(`/api/conversations?${q}`);
    const j = await res.json();
    const all = (j && j.conversations) || [];
    talks = all.filter(t => !t.hidden);
    hiddenTalks = all.filter(t => t.hidden);
  } catch (e) { talks = []; hiddenTalks = []; }
  renderTalks();
}

const refreshTalks = () => { if (activeModule) loadTalks(); };

/** 已隐藏会话分组：隐藏 ≠ 删除，恢复入口放在这里（右键已隐藏项）。 */
function hiddenTalksGroupHTML() {
  if (!hiddenTalks.length) return "";
  const rows = hiddenTalks.map(t => `
    <li><div class="rail-row" data-state="done" data-hidden-sid="${escapeHTML(t.session_id)}">
      <span class="rail-item is-archived" role="button" tabindex="0">
        <span class="t">${escapeHTML(talkTitle(t))}</span>
        <span class="m"><span class="rail-when">${escapeHTML(relTime(t.updated_at || t.started_at))}</span></span>
      </span>
    </div></li>`).join("");
  return `<li class="rail-arch">
    <button class="rail-arch-head" id="talksHiddenToggle" type="button" aria-expanded="${showHidden}">
      <span class="rail-arch-title">${escapeHTML(L().railHidden)}</span>
      <span class="rail-count">${hiddenTalks.length}</span>
      <span class="rail-arch-caret" aria-hidden="true">⌄</span>
    </button>
    <ul class="rail-arch-list"${showHidden ? "" : " hidden"}>${rows}</ul>
  </li>`;
}

function renderTalks() {
  const name = $("#talksName"), count = $("#talksCount"), list = $("#talksList");
  if (!name || !list) return;

  const cur = talks.find(t => t.session_id === activeTalk) || null;
  name.textContent = cur ? talkTitle(cur) : L().railUntitled;
  count.textContent = String(talks.length);

  const rows = talks.map(it => {
    const st = it.has_open_interrupt ? "pending" : (it.status === "open" ? "open" : "done");
    const tag = it.has_open_interrupt ? `<span class="rail-tag">${escapeHTML(L().railPending)}</span>` : "";
    const when = (it.has_open_interrupt && it.step_index)
      ? (lang === "zh" ? `第 ${it.step_index} 步` : `${it.step_index} 歩目`)
      : relTime(it.updated_at || it.started_at);
    return `<li><div class="rail-row" data-state="${st}" data-sid="${escapeHTML(it.session_id)}"
        aria-current="${it.session_id === activeTalk ? "true" : "false"}">
      <button class="rail-item" type="button" data-sid="${escapeHTML(it.session_id)}">
        <span class="t">${escapeHTML(talkTitle(it))}</span>
        <span class="m"><i class="rail-dot"></i>${tag}<span class="rail-when">${escapeHTML(when)}</span></span>
      </button>
    </div></li>`;
  }).join("");
  const empty = `<li class="rail-empty">${escapeHTML(L().talksEmpty)}</li>`;
  list.innerHTML = (rows || empty) + hiddenTalksGroupHTML();

  $$(".rail-item[data-sid]", list).forEach(btn =>
    btn.addEventListener("click", () => switchTalk(btn.dataset.sid)));
  // 右键 = 重命名 / 移动到其他板块 / 移除（隐藏）；移除都可恢复
  $$(".rail-row[data-sid]", list).forEach(row => row.addEventListener("contextmenu", ev => {
    const t = talks.find(x => x.session_id === row.dataset.sid);
    if (t) talkCtx(ev, t);
  }));
  $$(".rail-row[data-hidden-sid]", list).forEach(row =>
    row.addEventListener("contextmenu", ev => {
      const t = hiddenTalks.find(x => x.session_id === row.dataset.hiddenSid);
      if (t) hiddenTalkCtx(ev, t);
    }));
  const toggle = $("#talksHiddenToggle", list);
  if (toggle) toggle.addEventListener("click", () => {
    showHidden = !showHidden;
    renderTalks();
  });
}

/** 切换会话：原会话留档 → 换 sid → 还原目标会话的消息（不改变所在板块）。 */
async function switchTalk(sid) {
  if (!sid || sid === activeTalk) { talksOpen(false); return; }
  saveTalk();
  activeTalk = sid;
  storeSid(projId(), featKey(), sid);
  restoreTalk();
  greeted.add(contextKey());   // 切回来的会话不再重复打招呼
  if (!streamEl().children.length && activeModule) {
    addMsg("ai", L().greet.replace("{name}", tr(activeModule.title)));
  }
  talksOpen(false);
  renderTalks();
  scrollBottom();
}

/** 新对话：先把上一段正常结束（触发总结 → 记忆积累），再开一段新的。 */
async function newTalk() {
  if (!activeProject || !activeModule) return;
  const old = activeTalk;
  saveTalk();
  if (old) {
    try {
      await fetch("/api/conversation/end", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: old, user_id: "default" })
      });
    } catch (e) { /* 归档失败也继续开新会话，不阻塞用户 */ }
  }
  activeTalk = await ensureSession(true);
  restoreTalk();          // 新会话的档是空的 → 等于清空，同时重建配对
  greeted.add(contextKey());
  addMsg("ai", L().railNewDone);
  await loadTalks();
  hintEl().textContent = L().hint;
}

/** 移动到其他板块：只改归属三列，消息与断点都保留（移动 ≠ 重建）。 */
async function applyMove(sid, board, key) {
  const pid = projId();
  const from = featKey();
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(sid)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project_id: pid, feature_key: key, scope: board })
    });
    const j = await res.json();
    if (!res.ok || !j.ok) {
      hintEl().textContent = L().talksMoveFail + (j.error || res.status);
      talksOpen(false);
      return;
    }
  } catch (e) {
    hintEl().textContent = L().talksMoveFail + e.message;
    talksOpen(false);
    return;
  }
  try {   // 本地会话映射跟着搬：旧板块不再指向它，新板块打开就是它
    localStorage.removeItem(TALK_KEY(pid, from));
    storeSid(pid, key, sid);
  } catch (e) { /* 存储不可用：仅影响本地默认选中 */ }
  talksOpen(false);
  hintEl().textContent = L().talksMoved(featureTitle(key));
  await openFeature(board, key);   // 跟着搬过去，避免"会话凭空消失"的错觉
  await loadTalks();
}

/* ---------- 工具 ---------- */
const autoGrow = t => { t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 132) + "px"; };

/* =====================================================================
   右键菜单（项目行 / 会话行共用）
   —— 行内不再放小图标：重命名 / 移除 / 移动到其他板块 统一收进右键菜单
      （键盘用「菜单键 / Shift+F10」，同样触发 contextmenu）。
   —— 硬契约：「移除」一律是可恢复语义 —— 项目=归档、会话=隐藏，界面上都留有恢复入口，
      不做不可恢复的物理删除（避免误点即丢数据）。
   ===================================================================== */
let ctxItems = [];

function openCtx(ev, items, title = "") {
  ev.preventDefault();
  ev.stopPropagation();
  ctxItems = items;
  const titleEl = $("#ctxTitle");
  titleEl.textContent = title;
  titleEl.hidden = !title;
  renderCtx();
  const box = $("#ctxMenu");
  box.hidden = false;
  box.setAttribute("aria-hidden", "false");
  placeCtx(ev.clientX, ev.clientY);
  const first = $(".ctx-item", box);
  if (first) first.focus();   // 打开即可用键盘上下选择
}

/** 把菜单收进视口（右下角右键时不会溢出）。 */
function placeCtx(x, y) {
  const box = $("#ctxMenu");
  const r = box.getBoundingClientRect();
  box.style.left = Math.max(8, Math.min(x, window.innerWidth - r.width - 8)) + "px";
  box.style.top = Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) + "px";
}

function renderCtx() {
  const list = $("#ctxList");
  list.innerHTML = ctxItems.map((it, i) => it.sep
    ? `<li class="ctx-sep" role="separator"></li>`
    : `<li role="none"><button class="ctx-item" type="button" role="menuitem" data-i="${i}">
         <span class="ctx-ico" aria-hidden="true">${it.icon || ""}</span>
         <span class="ctx-label">${escapeHTML(it.label)}</span>
         ${it.hint ? `<span class="ctx-hint">${escapeHTML(it.hint)}</span>` : ""}
       </button></li>`).join("");
  $$(".ctx-item", list).forEach(btn => btn.addEventListener("click", e => {
    // stopPropagation：renderCtx() 会换掉 DOM，冒泡到 document 的「点别处收起」
    // 会误判成点了外面，把刚展开的二级菜单（移动到其他板块）立刻关掉
    e.stopPropagation();
    const it = ctxItems[Number(btn.dataset.i)];
    if (!it) return;
    if (it.keepOpen) { if (it.fn) it.fn(); return; }   // 二级菜单：不关，换成下一步内容
    closeCtx();
    if (it.fn) it.fn();
  }));
}

function closeCtx() {
  const box = $("#ctxMenu");
  if (!box || box.hidden) return;
  box.hidden = true;
  box.setAttribute("aria-hidden", "true");
  ctxItems = [];
}

/** 项目行右键：重命名 / 移除（归档，可恢复）。 */
function projectCtx(ev, p) {
  openCtx(ev, [
    { label: L().projectRename, icon: "✎", fn: () => openProjectModal(p) },
    { label: L().ctxRemove, icon: "⌫", hint: L().ctxHintArchive,
      fn: () => archiveProject(p, true) }
  ], p.name || L().railUntitled);
}

/** 已归档项目行右键：恢复 / 重命名。 */
function archivedProjectCtx(ev, p) {
  openCtx(ev, [
    { label: L().railRestore, icon: "↺", fn: () => archiveProject(p, false) },
    { label: L().projectRename, icon: "✎", fn: () => openProjectModal(p) }
  ], p.name || L().railUntitled);
}

/** 会话行右键：重命名 / 移动到其他板块 / 移除（隐藏，可恢复）。 */
function talkCtx(ev, t) {
  openCtx(ev, [
    { label: L().talkRename, icon: "✎", fn: () => openRenameTalk(t.session_id) },
    { label: L().talksMove, icon: "⇄", keepOpen: true, fn: () => ctxMoveSub(t.session_id) },
    { label: L().ctxRemove, icon: "⌫", hint: L().ctxHintHide,
      fn: () => hideTalk(t.session_id) }
  ], talkTitle(t));
}

/** 已隐藏会话行右键：恢复。 */
function hiddenTalkCtx(ev, t) {
  openCtx(ev, [{ label: L().railRestore, icon: "↺", fn: () => unhideTalk(t.session_id) }],
          talkTitle(t));
}

/** 「移动到其他板块」：就地换成板块清单（子菜单的轻量做法，可返回）。 */
function ctxMoveSub(sid) {
  const from = featKey();
  ctxItems = allFeatures().filter(f => f.key !== from)
    .map(f => ({ label: f.label, icon: "→", fn: () => applyMove(sid, f.board, f.key) }));
  ctxItems.push({ sep: true }, { label: L().projectCancel, icon: "↩", fn: () => {} });
  const titleEl = $("#ctxTitle");
  titleEl.textContent = L().talksMove;
  titleEl.hidden = false;
  renderCtx();
  const box = $("#ctxMenu");
  placeCtx(parseFloat(box.style.left) || 8, parseFloat(box.style.top) || 8);
}

/* ---------- 会话重命名 / 移除（隐藏） ---------- */
let renamingSid = "";

function renameMsg(text, kind = "") {
  const el = $("#renameMsg");
  el.className = `modal-msg ${kind ? (kind.startsWith("is-") ? kind : "is-" + kind) : ""}`;
  el.textContent = text;
}

function openRenameTalk(sid) {
  const t = talks.find(x => x.session_id === sid) || hiddenTalks.find(x => x.session_id === sid);
  if (!t) return;
  renamingSid = sid;
  $("#renameInput").value = t.title || "";
  renameMsg("");
  const m = $("#renameModal");
  m.classList.add("is-open");
  m.setAttribute("aria-hidden", "false");
  setTimeout(() => { const i = $("#renameInput"); i.focus(); i.select(); }, 120);
}

function closeRenameModal() {
  const m = $("#renameModal");
  m.classList.remove("is-open");
  m.setAttribute("aria-hidden", "true");
}

async function saveRenameTalk() {
  const title = ($("#renameInput").value || "").trim();
  if (!title) { renameMsg(L().required, "bad"); return; }
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(renamingSid)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title })
    });
    const j = await res.json();
    if (!res.ok || !j.ok) { renameMsg(L().renameFail + (j.error || res.status), "bad"); return; }
  } catch (e) { renameMsg(L().renameFail + e.message, "bad"); return; }
  closeRenameModal();
  await loadTalks();
}

/** 移除会话 = 隐藏：消息、断点、总结一律保留，可在「已隐藏」里恢复。 */
async function hideTalk(sid) {
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(sid)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hidden: true })
    });
    const j = await res.json();
    if (!res.ok || !j.ok) {
      hintEl().textContent = L().projectEditFail + (j.error || res.status);
      return;
    }
  } catch (e) { hintEl().textContent = L().projectEditFail + e.message; return; }
  if (sid === activeTalk) {          // 隐藏的正是当前会话 → 切到另一段，别让"当前会话"悬空
    const next = talks.find(t => t.session_id !== sid);
    if (next) await switchTalk(next.session_id);
    else await newTalk();
  }
  showHidden = true;                 // 让它"去了哪"可见，避免又一次"东西没了"的错觉
  await loadTalks();
}

async function unhideTalk(sid) {
  try {
    const res = await fetch(`/api/conversations/${encodeURIComponent(sid)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hidden: false })
    });
    const j = await res.json();
    if (!res.ok || !j.ok) {
      hintEl().textContent = L().projectEditFail + (j.error || res.status);
      return;
    }
  } catch (e) { hintEl().textContent = L().projectEditFail + e.message; return; }
  await loadTalks();
}

/* ---------- 事件 ---------- */
$("#backBtn").addEventListener("click", () => openProject(activeProject));  // 板块窗口 → 回项目总览
$("#brandBtn").addEventListener("click", goConsole);                       // 品牌标记 → 回总控台

// 两个输入区（总控台全局对话 / 板块对话）共用同一套发送流程
$("#sendBtn").addEventListener("click", () => send());
$("#sendBtnCon").addEventListener("click", () => send());
[$("#input"), $("#inputCon")].forEach(el => {
  el.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } });
  el.addEventListener("input", () => autoGrow(el));
});
$("#stopBtn").addEventListener("click", interruptCurrent);
$("#stopBtnCon").addEventListener("click", interruptCurrent);

$("#modForm").addEventListener("submit", e => {
  e.preventDefault();
  const mod = activeModule;
  if (!mod || mod.free) return;
  const vals = {};
  new FormData(e.target).forEach((v, k) => { vals[k] = String(v).trim(); });
  const missing = (mod.fields || []).filter(f => f.required && !vals[f.name]).map(f => tr(f.label));
  if (missing.length) {
    hintEl().textContent = L().required + missing.join(" / ");
    const first = $(`[name="${mod.fields.find(f => f.required && !vals[f.name]).name}"]`, e.target);
    if (first) first.focus();
    return;
  }
  send(mod.prompt(vals, lang === "zh"));
});

$("#endBtn").addEventListener("click", endConversation);
$("#clearBtn").addEventListener("click", () => {
  const sid = activeTalk;
  if (sid) {
    fetch("/api/clear", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sid })
    }).catch(() => {});
  }
  streamEl().innerHTML = "";
  addMsg("ai", L().cleared);
});

// 会话切换条
$("#talksCur").addEventListener("click", () => {
  const menu = $("#talksMenu");
  talksOpen(menu.hidden);
});
$("#talksNew").addEventListener("click", newTalk);
document.addEventListener("click", e => {   // 点别处收起会话菜单 / 右键菜单
  const wrap = $("#talks");
  if (wrap && !wrap.contains(e.target)) talksOpen(false);
  const ctx = $("#ctxMenu");
  if (ctx && !ctx.hidden && !ctx.contains(e.target)) closeCtx();
});
document.addEventListener("keydown", e => {
  if (e.key === "Escape") { talksOpen(false); closeProjectModal(); closeRenameModal(); closeCtx(); }
});
window.addEventListener("resize", closeCtx);
// 页面滚动后菜单位置就错了 → 收起。但**菜单内部的滚动不算**：
// 菜单高过视口时会出现内部滚动条，若一并收起，点到下半部分的项会落空。
window.addEventListener("scroll", e => {
  const box = $("#ctxMenu");
  if (box && !box.hidden && e.target instanceof Node && box.contains(e.target)) return;
  closeCtx();
}, true);

// 项目弹窗
$("#railNew").addEventListener("click", () => openProjectModal(null));
$("#deckNewProject").addEventListener("click", () => openProjectModal(null));
$("#projNew").addEventListener("click", () => openProjectModal(null));
$("#projectModalSave").addEventListener("click", saveProject);
$("#projectModalCancel").addEventListener("click", closeProjectModal);
$("#projectModalClose").addEventListener("click", closeProjectModal);
$$("[data-pclose]").forEach(el => el.addEventListener("click", closeProjectModal));
$("#projName").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); saveProject(); } });

// 会话重命名弹窗
$("#renameSave").addEventListener("click", saveRenameTalk);
$("#renameCancel").addEventListener("click", closeRenameModal);
$("#renameClose").addEventListener("click", closeRenameModal);
$$("[data-rclose]").forEach(el => el.addEventListener("click", closeRenameModal));
$("#renameInput").addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); saveRenameTalk(); }
});

$("#settingsBtn").addEventListener("click", openSettings);
$("#settingsClose").addEventListener("click", closeModal);
$$("[data-close]").forEach(el => el.addEventListener("click", closeModal));
$("#setSave").addEventListener("click", saveSettings);
$("#setTest").addEventListener("click", testSettings);
document.addEventListener("keydown", e => { if (e.key === "Escape") closeModal(); });

$("#langZH").addEventListener("click", () => setLang("zh"));
$("#langJA").addEventListener("click", () => setLang("ja"));

/* =====================================================================
   左栏：顶部「总控台」独立入口 + 项目列表（用户创建的项目）
   —— 项目是**一级**；细分板块（二级）与其下的会话都在主区，不占左栏。
   —— 硬契约（照抄 deepseek-harness 的 workspace）：项目只能「归档」，绝不删除 ——
      归档后会话 / 记忆 / checkpoint / 中断记录全部保留，随时可恢复。
   ===================================================================== */
const LOG_PREFIX = "quote-log-";     // 每段会话的消息留档（键 = session_id）
const LOG_MAX = 240000;              // 单段留档上限（字符），防 localStorage 配额爆掉

const talkTitle = it => ((it && it.title && String(it.title).trim()) || L().railUntitled);

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / 昨天 / 日期 */
function relTime(s) {
  if (!s) return "";
  const t = Date.parse(String(s).replace(/-/g, "/"));
  if (!t) return "";
  const d = Math.floor((Date.now() - t) / 1000);
  if (d < 60) return lang === "zh" ? "刚刚" : "たった今";
  if (d < 3600) return lang === "zh" ? `${Math.floor(d / 60)} 分钟前` : `${Math.floor(d / 60)} 分前`;
  if (d < 86400) return lang === "zh" ? `${Math.floor(d / 3600)} 小时前` : `${Math.floor(d / 3600)} 時間前`;
  if (d < 172800) return lang === "zh" ? "昨天" : "昨日";
  return String(s).slice(5, 10);
}

/** 消息留档：切走会话不丢内容，切回来原样还原（含思考小字与结果卡片）。
 *
 *  留档必须「会话 ↔ 对话流」严格配对：可见的流里渲染的必须是 streamSid 那段会话，
 *  否则会出现「把 A 的 DOM 存到 B 的档里」这种串档（总控台 → 项目 → 回总控台时最容易踩）。
 *  restoreTalk() 是唯一建立配对的地方，所以写档前先校验配对是否仍然成立。
 */
function saveTalk() {
  if (!streamSid || streamSid !== activeTalk || streamView !== activeView) return;
  try {
    const html = streamEl().innerHTML;
    if (!html) { localStorage.removeItem(LOG_PREFIX + streamSid); return; }
    localStorage.setItem(LOG_PREFIX + streamSid,
                         html.length > LOG_MAX ? html.slice(-LOG_MAX) : html);
  } catch (e) { /* 配额满：本次不存，不影响使用 */ }
}

/** 把 activeTalk 的消息渲染进当前视图的对话流，并建立「会话 ↔ 流」配对。 */
function restoreTalk() {
  streamEl().innerHTML = activeTalk ? (localStorage.getItem(LOG_PREFIX + activeTalk) || "") : "";
  streamSid = activeTalk;
  streamView = activeView;
}

/** 某区块的自由对话模块（跳转落点：要看见某段会话，得先落在它的对话流里）。 */
const freeChatKey = board => (BOARDS[board].modules.find(m => m.free) || {}).key || "";

/** 已归档分组：附在项目列表末尾的一段可展开区 —— 归档 ≠ 删除，必须能看见、能右键恢复。 */
function archivedGroupHTML() {
  if (!archivedProjects.length) return "";
  const rows = archivedProjects.map(p => `
    <li><div class="rail-row" data-state="done" data-archived-pid="${escapeHTML(p.project_id)}">
      <span class="rail-item is-archived" role="button" tabindex="0">
        <span class="t">${escapeHTML(p.name || L().railUntitled)}</span>
        <span class="m"><span class="rail-when">${escapeHTML(relTime(p.updated_at || p.created_at))}</span></span>
      </span>
    </div></li>`).join("");
  return `<li class="rail-arch">
    <button class="rail-arch-head" id="railArchToggle" type="button" aria-expanded="${showArchived}">
      <span class="rail-arch-title">${escapeHTML(L().railArchived)}</span>
      <span class="rail-count">${archivedProjects.length}</span>
      <span class="rail-arch-caret" aria-hidden="true">⌄</span>
    </button>
    <ul class="rail-arch-list"${showArchived ? "" : " hidden"}>${rows}</ul>
  </li>`;
}

/** 左栏项目列：当前项目高亮 + 未完成断点标记；操作（重命名 / 移除）走右键菜单。 */
function renderRail() {
  const rail = $("#rail"), list = $("#railList");
  if (!rail || !list) return;
  const home = $("#railHome");
  if (home) home.setAttribute("aria-current", activeProject ? "false" : "page");
  rail.setAttribute("data-scope", activeProject ? activeBoard : "");

  const items = railFilter
    ? projects.filter(p => (p.name || "").toLowerCase().includes(railFilter))
    : projects;
  let html = items.length
    ? items.map(p => {
      const open = Number(p.open_interrupts || 0);
      const cur = activeProject && activeProject.project_id === p.project_id;
      const tag = open ? `<span class="rail-tag">${escapeHTML(L().railPending)}</span>` : "";
      const when = relTime(p.updated_at || p.created_at);
      return `<li><div class="rail-row" data-state="${open ? "pending" : "open"}"
        data-pid="${escapeHTML(p.project_id)}" aria-current="${cur ? "true" : "false"}">
      <button class="rail-item" type="button" data-pid="${escapeHTML(p.project_id)}">
        <span class="t">${escapeHTML(p.name || L().railUntitled)}</span>
        <span class="m"><i class="rail-dot"></i>${tag}<span class="rail-when">${escapeHTML(when)}</span></span>
      </button>
    </div></li>`;
    }).join("")
    : `<li class="rail-empty">${escapeHTML(railFilter ? L().railNoMatch : L().railEmpty)}</li>`;
  list.innerHTML = html + archivedGroupHTML();

  $$(".rail-item[data-pid]", list).forEach(btn => btn.addEventListener("click", () =>
    openProject(projects.find(x => x.project_id === btn.dataset.pid))));
  // 右键 = 重命名 / 移除（菜单键、Shift+F10 也会触发 contextmenu，键盘同样可用）
  $$(".rail-row[data-pid]", list).forEach(row => row.addEventListener("contextmenu", ev => {
    const p = projects.find(x => x.project_id === row.dataset.pid);
    if (p) projectCtx(ev, p);
  }));
  $$(".rail-row[data-archived-pid]", list).forEach(row =>
    row.addEventListener("contextmenu", ev => {
      const p = archivedProjects.find(x => x.project_id === row.dataset.archivedPid);
      if (p) archivedProjectCtx(ev, p);
    }));
  const toggle = $("#railArchToggle", list);
  if (toggle) toggle.addEventListener("click", () => {
    showArchived = !showArchived;
    renderRail();
  });
}

function initRail() {
  $("#railHome").addEventListener("click", goConsole);
  $("#railSearch").addEventListener("input", e => {
    railFilter = (e.target.value || "").trim().toLowerCase();
    renderRail();
  });
  renderRail();
}

/* =====================================================================
   悬浮指导智能体（只读）
   —— 它读得到全部记忆（含未完成断点与会话清单），但不改任何东西；
      后端以 MemoryCaller("guide_agent", "L0") + 零写工具双重保证。
   —— 核心价值不是陪聊，而是「给出可点的跳转」：直接切到那个项目 / 那段谈话。
   ===================================================================== */
let guideBusy = false;

function guideOpen(on) {
  const wrap = $("#guide"), panel = $("#guidePanel"), ball = $("#guideBall");
  if (!wrap || !panel) return;
  wrap.setAttribute("data-open", on ? "true" : "false");
  ball.setAttribute("aria-expanded", String(on));
  panel.hidden = !on;
  if (on) {
    if (!panel.dataset.seeded) {
      panel.dataset.seeded = "1";
      guideMsg("ai", escapeHTML(L().guideGreet));
    }
    setTimeout(() => $("#guideInput").focus(), 140);
  }
}

function guideMsg(who, html, actions) {
  const box = $("#guideMsgs");
  const div = document.createElement("div");
  div.className = `gmsg ${who === "me" ? "me" : "ai"}`;
  const acts = (actions || []).map((a, i) =>
    `<button class="gact" type="button" data-i="${i}">${escapeHTML(a.label || L().railHome)}</button>`).join("");
  div.innerHTML = `<span class="who">${escapeHTML(who === "me" ? L().guideYou : L().guideTitle)}</span>`
    + `<span class="bub">${html}</span>`
    + (acts ? `<span class="gacts">${acts}</span>` : "");
  box.appendChild(div);
  if (actions && actions.length) {
    $$(".gact", div).forEach(btn => btn.addEventListener("click", () => {
      guideJump(actions[Number(btn.dataset.i)] || {});
    }));
  }
  box.scrollTop = box.scrollHeight;
  return div;
}

/** 点建议 → 真的跳过去：有 session_id 就落到那段会话，否则进项目（或回总控台）。 */
async function guideJump(a) {
  guideOpen(false);
  const sid = (a && a.session_id) || "";
  if (sid) { await jumpToTalk(sid); return; }
  const pid = (a && a.project_id) || "";
  const proj = pid ? projects.find(p => p.project_id === pid) : null;
  if (proj) { await openProject(proj); return; }
  if (projects.length === 1) { await openProject(projects[0]); return; }
  goConsole();   // 没有更好的落点：回总控台（项目卡片就在那儿）
}

async function guideSend() {
  const box = $("#guideInput");
  const q = (box.value || "").trim();
  if (!q || guideBusy) return;
  box.value = "";
  guideMsg("me", escapeHTML(q));
  guideBusy = true;
  const wait = guideMsg("ai",
    `${escapeHTML(L().guideThinking)}<span class="guide-dots"><i></i><i></i><i></i></span>`);
  try {
    const res = await fetch("/api/guide", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: q, session_id: activeTalk,
        scope: scopeOf(), user_id: "default",
        project_id: projId(), feature_key: featKey()
      })
    });
    const j = await res.json();
    wait.remove();
    if (j && j.ok && j.reply) {
      guideMsg("ai", escapeHTML(j.reply).replace(/\n/g, "<br>"), j.actions || []);
    } else {
      guideMsg("ai", escapeHTML(L().guideFail));
    }
  } catch (e) {
    wait.remove();
    guideMsg("ai", escapeHTML(L().guideFail));
  }
  guideBusy = false;
}

function initGuide() {
  const wrap = $("#guide");
  if (!wrap) return;
  $("#guideBall").addEventListener("click", () =>
    guideOpen(wrap.getAttribute("data-open") !== "true"));
  $("#guideClose").addEventListener("click", () => guideOpen(false));
  $("#guideSend").addEventListener("click", guideSend);
  $("#guideInput").addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); guideSend(); }
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && wrap.getAttribute("data-open") === "true") guideOpen(false);
  });
}

/* =====================================================================
   调试后台弹窗：把 /debug 以 iframe 嵌进主窗口，不再跳浏览器标签页
   —— 懒加载：首次打开才创建 iframe（避免每次启动都拉遥测数据）
   ===================================================================== */
let debugLoaded = false;

function debugOpen(on) {
  const dock = $("#debugDock");
  if (!dock) return;
  if (on && !debugLoaded) {
    const f = document.createElement("iframe");
    f.className = "dock-frame";
    f.setAttribute("title", L().debugTitle);
    f.src = "/debug";
    $("#debugBody").appendChild(f);
    debugLoaded = true;
  }
  dock.classList.toggle("is-open", on);
  dock.setAttribute("aria-hidden", String(!on));
  document.body.classList.toggle("is-docked", on);   // 弹窗打开时抑制背景滚动
}

function initDebugDock() {
  const dock = $("#debugDock");
  if (!dock) return;
  $("#debugBtn").addEventListener("click", () => debugOpen(true));
  $("#debugClose").addEventListener("click", () => debugOpen(false));
  $$("[data-debug-close]", dock).forEach(el =>
    el.addEventListener("click", () => debugOpen(false)));
  $("#debugReload").addEventListener("click", () => {
    const f = $(".dock-frame", dock);
    if (f) f.src = "/debug";     // 重设 src 即重新拉取遥测数据
  });
  $("#debugBrowser").addEventListener("click", () => window.open("/debug", "_blank"));
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && dock.classList.contains("is-open")) debugOpen(false);
  });
}

/* ---------- 启动 ---------- */
/** 首屏：总控台 + 全局对话（不挂项目 = 不注入项目级 / 板块级记忆，上下文最干净）。 */
async function boot() {
  await loadProjects();
  initRail();
  activeTalk = await ensureSession();   // 全局会话也要有 id，否则总控台的对话留存不下来
  restoreTalk();
  if (!streamEl().children.length) {
    greeted.add(contextKey());
    addMsg("ai", L().deckGreet);
  }
  loadConsoleBlocks();
}

initGuide();
initDebugDock();
boot();
setLang(lang, false);
refreshStatus();
