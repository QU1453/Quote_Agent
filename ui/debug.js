/* =====================================================================
   Quote Agent 控制室 · 调试后台前端逻辑
   数据源：/api/telemetry/*（summary / traces / traces/{id} / trend / clear）
   纯原生 JS，无依赖；轮询默认关闭
   ===================================================================== */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  pollTimer: null,
  sessions: new Set(),      // 已知会话（过滤下拉用）
  activeTrace: null,        // 当前展开的 trace id
  trendSession: "",         // 趋势图当前会话
};

/* ---- 工具函数 ---- */
const fmtTokens = (n) => (n >= 10000 ? (n / 1000).toFixed(1) + "k" : String(n || 0));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTs = (ts) => String(ts || "").slice(5, 19);  // MM-DD HH:MM:SS

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

/* ---- 概览仪表 ---- */
function renderSummary(s) {
  $("gTraces").textContent = s.total_traces ?? 0;
  $("gTokens").textContent = fmtTokens(s.total_tokens);
  $("gTokensIn").textContent = fmtTokens((s.total_tokens ?? 0) - 0 || s.total_tokens);
  $("gFallback").textContent = Math.round((s.fallback_rate ?? 0) * 100) + "%";
  $("gConstraint").textContent = s.constraint_count ?? 0;
  $("gLatency").textContent = s.avg_latency_ms ?? 0;
  renderToolRank(s.tools || []);
  renderRoutes(s.routes || []);
  renderErrors(s.recent_errors || []);
}

/* 概览里 入/出 token 拆分：summary 只有总量，单独累加（走 traces 列表时同步） */
function renderTokenSplit(traces) {
  let tin = 0, tout = 0;
  for (const t of traces) {
    if (t.tokens_source === "api") { tin += t.input_tokens || 0; tout += t.output_tokens || 0; }
  }
  $("gTokensIn").textContent = fmtTokens(tin);
  $("gTokensOut").textContent = fmtTokens(tout);
}

/* ---- 工具排行 ---- */
function renderToolRank(tools) {
  const box = $("toolRank");
  if (!tools.length) { box.innerHTML = '<p class="empty">暂无工具调用记录</p>'; return; }
  const max = Math.max(...tools.map((t) => t.calls), 1);
  box.innerHTML = tools.map((t) => `
    <div class="bar-row">
      <span class="t-name">${esc(t.name)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(t.calls / max) * 100}%"></div></div>
      <span class="t-calls">×${t.calls}</span>
      <span class="t-ms">${Math.round(t.avg_ms || 0)}ms</span>
    </div>`).join("");
}

/* ---- 智能体分布（route × llm_mode 堆叠） ---- */
function renderRoutes(routes) {
  const box = $("routeBars");
  if (!routes.length) { box.innerHTML = '<p class="empty">暂无路由记录</p>'; return; }
  // 按 route 聚合三种模式
  const byRoute = {};
  for (const r of routes) {
    byRoute[r.route || "—"] = byRoute[r.route || "—"] || { llm: 0, fallback: 0, constraint: 0 };
    byRoute[r.route || "—"][r.llm_mode === "fallback" ? "fallback"
      : r.llm_mode === "constraint" ? "constraint" : "llm"] += r.calls;
  }
  const max = Math.max(...Object.values(byRoute).map((v) => v.llm + v.fallback + v.constraint), 1);
  box.innerHTML = Object.entries(byRoute).map(([name, v]) => {
    const total = v.llm + v.fallback + v.constraint;
    const pct = (x) => (x / max) * 100;
    return `
    <div class="route-row">
      <span class="route-name">${esc(name)}</span>
      <div class="route-track">
        <div class="seg-llm" style="width:${pct(v.llm)}%"></div>
        <div class="seg-fallback" style="width:${pct(v.fallback)}%"></div>
        <div class="seg-constraint" style="width:${pct(v.constraint)}%"></div>
      </div>
      <span class="route-meta">LLM ${v.llm} · 兜底 ${v.fallback} · 拦截 ${v.constraint}（共 ${total}）</span>
    </div>`;
  }).join("");
}

/* ---- 最近错误 ---- */
function renderErrors(errs) {
  $("panelErrors").hidden = !errs.length;
  $("errList").innerHTML = errs.map((e) => `
    <li><b>#${e.id}</b> ${esc(fmtTs(e.ts))} · ${esc(e.route || "-")} · 会话 ${esc(e.session_id || "-")}
      <span class="err-msg">${esc(e.error || "")}</span></li>`).join("");
}

/* ---- trace 表格 ---- */
function renderTraces(traces) {
  const body = $("traceBody");
  $("traceCount").textContent = (traces.length || 0) + " 条";
  renderTokenSplit(traces);
  if (!traces.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">暂无记录，先去工作台聊几句</td></tr>';
    return;
  }
  // 收集会话（过滤下拉）
  for (const t of traces) if (t.session_id) state.sessions.add(t.session_id);
  syncSessionOptions();
  body.innerHTML = traces.map((t) => {
    const tok = t.tokens_source === "api"
      ? `<span class="tok-cell">${fmtTokens(t.total_tokens)}</span>`
      : `<span class="tok-cell none">—</span>`;
    const mode = t.llm_mode || "—";
    const modeBadge = `<span class="badge mode-${esc(mode)}">${esc(mode)}</span>`;
    const st = t.status === "error"
      ? '<span class="badge st-error">ERROR</span>'
      : '<span class="badge st-ok">OK</span>';
    return `
    <tr class="data-row${state.activeTrace === t.id ? " active" : ""}" data-id="${t.id}">
      <td>${esc(fmtTs(t.ts))}</td>
      <td title="${esc(t.session_id)}">${esc(String(t.session_id || "").slice(-12))}</td>
      <td><span class="badge route">${esc(t.route || "—")}</span></td>
      <td>${modeBadge}</td>
      <td>×${t.tool_count ?? 0}</td>
      <td>${tok}${t.context_tokens ? ` <span style="color:var(--ink-3)" title="上下文注入估算">+${fmtTokens(t.context_tokens)}e</span>` : ""}</td>
      <td>${t.latency_ms ?? 0}ms</td>
      <td>${st}</td>
    </tr>`;
  }).join("");
  // 行点击 → 展开/收起详情
  body.querySelectorAll("tr.data-row").forEach((row) => {
    row.addEventListener("click", () => toggleDetail(Number(row.dataset.id)));
  });
}

/* 会话下拉与全局过滤状态保持一致 */
function syncSessionOptions() {
  const sel = $("filterSession");
  const cur = sel.value;
  const opts = ['<option value="">全部会话</option>']
    .concat([...state.sessions]
      .map((s) => `<option value="${esc(s)}">${esc(s.slice(-16))}</option>`));
  sel.innerHTML = opts.join("");
  if (cur && state.sessions.has(cur)) sel.value = cur;
}

/* ---- trace 详情（事件时间线） ---- */
async function toggleDetail(traceId) {
  if (state.activeTrace === traceId) { closeDetail(); return; }
  try {
    const t = await api(`/api/telemetry/traces/${traceId}`);
    state.activeTrace = traceId;
    const box = $("traceDetail");
    box.hidden = false;
    $("detailTitle").textContent = `TRACE #${t.id} · ${t.route || "—"} · ${t.llm_mode || "—"}`;
    $("detailQuestion").textContent = `Q: ${t.question || ""}`;
    $("detailTimeline").innerHTML = (t.events || []).map((e) => {
      const params = e.params_json
        ? esc(e.params_json.length > 160 ? e.params_json.slice(0, 160) + "…" : e.params_json)
        : esc(e.detail || "");
      const st = e.status && e.status !== "ok"
        ? ` <span class="ev-status ${esc(e.status)}">${esc(e.status)}</span>` : "";
      return `
      <li>
        <span class="ev-kind ${esc(e.kind)}">${esc(e.kind)}${st}</span>
        <span class="ev-name">${esc(e.name)}</span>
        <span class="ev-params">${params}</span>
        <span class="ev-ms">${e.duration_ms != null ? e.duration_ms + "ms" : ""}</span>
      </li>`;
    }).join("") || '<li><span class="ev-params">无事件</span></li>';
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    // 表格行高亮
    document.querySelectorAll("tr.data-row").forEach((r) =>
      r.classList.toggle("active", Number(r.dataset.id) === traceId));
  } catch { /* fail-open：详情加载失败仅忽略 */ }
}

function closeDetail() {
  state.activeTrace = null;
  $("traceDetail").hidden = true;
  document.querySelectorAll("tr.data-row.active").forEach((r) => r.classList.remove("active"));
}

/* ---- token 趋势折线（选中会话后显示） ---- */
async function loadTrend(sessionId) {
  state.trendSession = sessionId;
  const { trend } = await api(`/api/telemetry/trend?session_id=${encodeURIComponent(sessionId)}`);
  const svg = $("trendSvg"), empty = $("trendEmpty");
  if (!trend.length) { empty.style.display = "grid"; return; }
  empty.style.display = "none";
  const W = 600, H = 160, PL = 40, PB = 20, PT = 12, PR = 10;
  const xs = trend.map((_, i) =>
    PL + (i / Math.max(trend.length - 1, 1)) * (W - PL - PR));
  const maxY = Math.max(...trend.map((t) => Math.max(t.input_tokens || 0, t.output_tokens || 0)), 10);
  const ys = (v) => H - PB - ((v || 0) / maxY) * (H - PT - PB);
  const line = (key) => trend.map((t, i) =>
    `${i ? "L" : "M"}${xs[i].toFixed(1)},${ys(t[key] || 0).toFixed(1)}`).join(" ");
  const dots = trend.map((t, i) =>
    `<circle class="dot" cx="${xs[i].toFixed(1)}" cy="${ys(t.input_tokens || 0).toFixed(1)}" r="2.5"/>`).join("");
  svg.innerHTML = `
    <line class="axis" x1="${PL}" y1="${H - PB}" x2="${W - PR}" y2="${H - PB}"/>
    <line class="axis" x1="${PL}" y1="${PT}" x2="${PL}" y2="${H - PB}"/>
    <text x="${PL - 6}" y="${ys(maxY) + 3}" text-anchor="end">${fmtTokens(maxY)}</text>
    <text x="${PL - 6}" y="${H - PB}" text-anchor="end">0</text>
    <text x="${W / 2}" y="${H - 6}" text-anchor="middle">${esc(sessionId.slice(-10))} · ${trend.length} 轮</text>
    <path class="line-in" d="${line("input_tokens")}"/>
    <path class="line-out" d="${line("output_tokens")}"/>
    ${dots}`;
}

/* ---- 中断记录（被打断交互的可续跑台账，数据源 /api/interrupt/recent） ---- */
const INT_STAGE = { perception: "输入解析", context: "记忆装配", thinking: "思考推理",
                    tool: "工具调用", output: "输出整理" };
const INT_STATUS = { open: ["待续跑", "st-open"], resumed: ["已接手", "st-ok"],
                     closed: ["已关闭", "st-closed"] };

function renderInterrupts(data) {
  const recs = (data && data.records) || [];
  const st = (data && data.stats) || {};
  $("intStats").textContent =
    `待续跑 ${st.open || 0} · 已接手 ${st.resumed || 0} · 已关闭 ${st.closed || 0}`;
  const body = $("intBody");
  if (!recs.length) {
    body.innerHTML = '<tr><td colspan="8" class="empty">暂无中断记录（去工作台点一次「打断」试试）</td></tr>';
    return;
  }
  body.innerHTML = recs.map((r) => {
    const [label, cls] = INT_STATUS[r.status] || [r.status || "—", ""];
    const who = r.tool_interrupted_by_user
      ? '<span class="badge st-open">用户打断</span>'
      : `<span class="badge route">${esc(r.interrupted_by || "system")}</span>`;
    const tools = (r.completed_tools || []).length
      ? esc(r.completed_tools.join("、")) : "—";
    return `
    <tr class="data-row" title="Q: ${esc(r.question || "")}">
      <td>${esc(fmtTs(r.created_at))}</td>
      <td title="${esc(r.session_id)}">${esc(String(r.session_id || "").slice(-12))}</td>
      <td><span class="badge route">${esc(r.route || "—")}</span></td>
      <td>第 ${r.step_index || 0} 步 · ${esc(INT_STAGE[r.stage] || r.stage || "—")}</td>
      <td>${tools}</td>
      <td>${r.pending_tool ? esc(r.pending_tool) : "—"}</td>
      <td>${who}</td>
      <td><span class="badge ${esc(cls)}">${esc(label)}</span></td>
    </tr>`;
  }).join("");
}

/* ---- 刷新入口 ---- */
async function refresh() {
  try {
    const sid = $("filterSession").value, route = $("filterRoute").value;
    const [summary, list, interrupts] = await Promise.all([
      api("/api/telemetry/summary"),
      api(`/api/telemetry/traces?limit=100${sid ? `&session_id=${encodeURIComponent(sid)}` : ""}${route ? `&route=${route}` : ""}`),
      api(`/api/interrupt/recent?limit=50${sid ? `&session_id=${encodeURIComponent(sid)}` : ""}`),
    ]);
    renderSummary(summary);
    renderTraces(list.traces || []);
    renderInterrupts(interrupts);
    if (state.trendSession) loadTrend(state.trendSession);
    setLive("正常 · " + fmtTs(new Date().toISOString().slice(0, 19).replace("T", " ")), false);
  } catch {
    setLive("连接失败", true);
  }
}

function setLive(text, err) {
  $("liveText").textContent = text;
  $("liveDot").classList.toggle("on", !err);
}

/* ---- 交互绑定 ---- */
$("refreshBtn").addEventListener("click", refresh);
$("detailClose").addEventListener("click", closeDetail);
$("filterSession").addEventListener("change", (e) => {
  if (e.target.value) loadTrend(e.target.value); refresh();
});
$("filterRoute").addEventListener("change", refresh);

$("pollBtn").addEventListener("click", (e) => {
  const btn = e.currentTarget;
  const on = btn.getAttribute("aria-pressed") === "true";
  btn.setAttribute("aria-pressed", String(!on));
  btn.textContent = on ? "自动轮询 · 关" : "自动轮询 · 开";
  clearInterval(state.pollTimer);
  state.pollTimer = on ? null : setInterval(refresh, 5000);
});

$("clearBtn").addEventListener("click", async () => {
  if (!confirm("确定清空全部遥测数据？此操作不可恢复。")) return;
  try {
    await api("/api/telemetry/clear", { method: "POST" });
    state.trendSession = "";
    $("trendSvg").innerHTML = "";
    $("trendEmpty").style.display = "grid";
    closeDetail();
    refresh();
  } catch { setLive("清空失败", true); }
});

/* ---- 启动 ---- */
refresh();
