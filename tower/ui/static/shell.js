/* BD MARKET INTELLIGENCE OS — shell
 * vanilla JS, no build step. Every number on screen comes from an API endpoint
 * exposed by tower/ui/server.py; a null field renders as '—' (NOT_OBSERVABLE),
 * never as 0. Every important conclusion links back to its evidence source.
 *
 * Routing: hash-based. Screens are pure render functions of {route, params, state}.
 * State is one plain object; screens re-render on hash change or on new fetch.
 */
"use strict";
(() => {
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));
const NA = "—";

// ────────────────────────────────────────────────────────────── format helpers
const F = {
  num(v, d=2) {
    if (v === null || v === undefined) return NA;
    if (typeof v === "boolean") return v ? "yes" : "no";
    if (typeof v !== "number" || !Number.isFinite(v)) return NA;
    if (Number.isInteger(v) && Math.abs(v) >= 1000) return v.toLocaleString("en-US");
    if (Math.abs(v) >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
    return v.toFixed(d);
  },
  pct(v, d=2) { return v == null || !Number.isFinite(v) ? NA : (v*100).toFixed(d) + "%"; },
  signedPct(v, d=2) {
    if (v == null || !Number.isFinite(v)) return NA;
    const s = (v*100).toFixed(d) + "%";
    return v > 0 ? "+" + s : s;
  },
  signed(v, d=2) {
    if (v == null || !Number.isFinite(v)) return NA;
    const s = v.toFixed(d);
    return v > 0 ? "+" + s : s;
  },
  bigTk(v) {
    if (v == null || !Number.isFinite(v)) return NA;
    const abs = Math.abs(v);
    if (abs >= 1e9) return "৳" + (v/1e9).toFixed(2) + "B";
    if (abs >= 1e7) return "৳" + (v/1e7).toFixed(2) + "cr";
    if (abs >= 1e5) return "৳" + (v/1e5).toFixed(2) + "L";
    if (abs >= 1e3) return "৳" + (v/1e3).toFixed(2) + "k";
    return "৳" + v.toFixed(0);
  },
  bigN(v) {
    if (v == null || !Number.isFinite(v)) return NA;
    const abs = Math.abs(v);
    if (abs >= 1e9) return (v/1e9).toFixed(2) + "B";
    if (abs >= 1e6) return (v/1e6).toFixed(2) + "M";
    if (abs >= 1e3) return (v/1e3).toFixed(2) + "k";
    return v.toFixed(0);
  },
  ageS(iso) {
    if (!iso) return NA;
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) return NA;
    const s = (Date.parse(new Date().toISOString()) - t) / 1000;
    if (s < 60) return Math.round(s) + "s";
    if (s < 3600) return Math.round(s/60) + "m";
    if (s < 86400) return Math.round(s/3600) + "h";
    return Math.round(s/86400) + "d";
  },
  hms(iso) {
    if (!iso) return NA;
    return String(iso).slice(11, 19);
  },
  ymd(iso) { return !iso ? NA : String(iso).slice(0,10); },
  cls(v) {
    if (v == null || !Number.isFinite(v)) return "na";
    if (v > 0) return "pos"; if (v < 0) return "neg"; return "";
  },
};

// ────────────────────────────────────────────────────────────── data layer
async function api(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(r.status + " " + path);
  return r.json();
}
const cache = new Map();
async function get(path, ttlMs=5000) {
  const now = Date.now();
  const c = cache.get(path);
  if (c && (now - c.t) < ttlMs) return c.v;
  const v = await api(path).catch(e => ({ __err: String(e) }));
  cache.set(path, { v, t: now });
  return v;
}
function invalidate(prefix) {
  for (const k of Array.from(cache.keys())) if (k.startsWith(prefix)) cache.delete(k);
}

// ────────────────────────────────────────────────────────────── STATE
const S = {
  route: null, params: {},
  mode: localStorage.getItem("bmos.mode") || "HUMAN",   // HUMAN | RESEARCH
  theme: localStorage.getItem("bmos.theme") || "",       // "" | "dark" | "light"
  watchlist: JSON.parse(localStorage.getItem("bmos.watchlist") || "[]"),
  paletteOpen: false,
  paletteHi: 0,
  evidence: null,
  pollTimer: null,
  liveTick: 0,
  universe: null,     // {rows:[...], meta:{...}}
  latest: null,       // {SYMBOL: {ltp, ...}}
  sources: null,      // list of source dicts
  metrics: null,
  summary: null,      // market summary
  features: null,     // {SYMBOL: {feature: value, ...}}
  sectors: null,
};

// ────────────────────────────────────────────────────────────── theme
function applyTheme() {
  const t = S.theme;
  document.documentElement.setAttribute("data-theme", t);
  $("#theme-label").textContent = t || "system";
}
function cycleTheme() {
  S.theme = S.theme === "" ? "dark" : S.theme === "dark" ? "light" : "";
  localStorage.setItem("bmos.theme", S.theme);
  applyTheme();
}
$("#theme-btn").addEventListener("click", cycleTheme);

// ────────────────────────────────────────────────────────────── router
const ROUTES = {
  home:      renderHome,
  market:    renderMarket,
  radar:     renderRadar,
  sectors:   renderSectors,
  watchlist: renderWatchlist,
  alerts:    renderAlerts,
  events:    renderEvents,
  evidence:  renderEvidence,
  trust:     renderTrust,
  stock:     renderStock,
};

function parseHash() {
  const h = location.hash || "#/home";
  const parts = h.replace(/^#\//, "").split("/");
  const route = parts[0] || "home";
  const params = {};
  if (route === "stock" && parts[1]) params.sym = decodeURIComponent(parts[1]);
  if (route === "stock" && parts[2]) params.tab = parts[2];
  return { route, params };
}
function go(hash) { location.hash = hash; }
async function render() {
  const { route, params } = parseHash();
  S.route = route; S.params = params;
  $$("#tower .rail a.nav").forEach(a => a.classList.toggle("active", a.dataset.route === route));
  const view = $("#view");
  view.innerHTML = `<div class="section"><h1><span class="n">—</span> Loading <span class="sub">reading engine…</span></h1></div>`;
  const fn = ROUTES[route] || renderNotFound;
  try {
    await fn(view, params);
  } catch (e) {
    view.innerHTML = `<div class="section"><h1><span class="n">!</span> Error</h1>
      <div class="panel"><div class="prov">${escapeHtml(String(e))}</div></div></div>`;
  }
}
window.addEventListener("hashchange", render);

// ────────────────────────────────────────────────────────────── helpers
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v == null || v === false) {} else e.setAttribute(k, v);
  }
  for (const k of kids.flat(2)) {
    if (k == null || k === false) continue;
    e.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
  }
  return e;
}
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" }[c])); }
function trustBadge(kind) {
  const map = { OBSERVED: "obs", INFERRED: "inf", NOT_OBSERVABLE: "una", UNAVAILABLE: "una", STALE: "stale", DISAGREE: "disagree" };
  const cls = map[kind] || "una";
  return `<span class="trust ${cls}" title="${kind}">${kind === "OBSERVED"?"OBS":kind === "INFERRED"?"INF":kind === "NOT_OBSERVABLE"?"N/A":kind}</span>`;
}
function prov(layer, source, mode, freshIso) {
  const bits = [];
  if (layer) bits.push(layer);
  if (source) bits.push(source);
  if (mode) bits.push(mode);
  if (freshIso) bits.push(`<span class="fresh">${F.ageS(freshIso)}</span>`);
  return `<span class="prov">${bits.join(' <span class="dot">·</span> ')}</span>`;
}

// ────────────────────────────────────────────────────────────── palette
function openPalette() {
  S.paletteOpen = true; S.paletteHi = 0;
  const back = el("div", { class: "pal-back", onclick: closePalette });
  const box = el("div", { class: "pal", onclick: e => e.stopPropagation() });
  const input = el("input", { placeholder: "Symbol · sector · 'RADAR' · 'NEAR UPPER CIRCUIT' · 'DATA HEALTH' …", autocomplete: "off", spellcheck: "false" });
  const results = el("div", { class: "results" });
  const foot = el("div", { class: "pal-foot" },
    el("span", null, el("kbd", null, "↑"), el("kbd", null, "↓"), " navigate"),
    el("span", null, el("kbd", null, "Enter"), " open"),
    el("span", null, el("kbd", null, "Esc"), " close"),
    el("span", { style: "margin-left:auto" }, "prefix a symbol with '/' to jump directly"));
  box.append(input, results, foot);
  back.appendChild(box);
  document.body.appendChild(back);
  input.focus();
  const draw = async () => {
    const q = input.value.trim();
    const items = await paletteItems(q);
    results.innerHTML = "";
    let idx = 0;
    let curGroup = null;
    for (const it of items) {
      if (it.group !== curGroup) {
        curGroup = it.group;
        results.appendChild(el("div", { class: "r-group" }, curGroup));
      }
      const r = el("div", { class: "r" + (idx === S.paletteHi ? " hi" : ""), "data-idx": String(idx), onclick: () => { closePalette(); it.action(); } },
        el("span", { class: "glyph" }, it.glyph || "›"),
        el("span", null, it.label),
        el("span", { class: "hint" }, it.hint || ""));
      results.appendChild(r);
      idx++;
    }
    if (!items.length) results.appendChild(el("div", { class: "r" }, el("span", { class: "hint" }, "no matches")));
  };
  input.addEventListener("input", () => { S.paletteHi = 0; draw(); });
  input.addEventListener("keydown", async (e) => {
    const items = await paletteItems(input.value.trim());
    if (e.key === "ArrowDown") { S.paletteHi = Math.min(items.length-1, S.paletteHi+1); draw(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { S.paletteHi = Math.max(0, S.paletteHi-1); draw(); e.preventDefault(); }
    else if (e.key === "Enter") { const hit = items[S.paletteHi]; if (hit) { closePalette(); hit.action(); } }
    else if (e.key === "Escape") closePalette();
  });
  draw();
}
function closePalette() { S.paletteOpen = false; document.querySelectorAll(".pal-back").forEach(n => n.remove()); }
$("#cmd-open").addEventListener("click", openPalette);
window.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
  else if (e.key === "Escape" && S.paletteOpen) closePalette();
  else if (e.key === "/" && !S.paletteOpen && !isFormEl(e.target)) { e.preventDefault(); openPalette(); }
});
function isFormEl(t) { return t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName); }

async function paletteItems(q) {
  const items = [];
  const nav = [
    { group: "NAVIGATE", glyph: "◉", label: "Mission Control", hint: "#/home",      action: () => go("#/home") },
    { group: "NAVIGATE", glyph: "▤", label: "Market Grid",     hint: "#/market",    action: () => go("#/market") },
    { group: "NAVIGATE", glyph: "◎", label: "Radar Scanner",   hint: "#/radar",     action: () => go("#/radar") },
    { group: "NAVIGATE", glyph: "⌘", label: "Sectors",         hint: "#/sectors",   action: () => go("#/sectors") },
    { group: "NAVIGATE", glyph: "☆", label: "Watchlist",       hint: "#/watchlist", action: () => go("#/watchlist") },
    { group: "NAVIGATE", glyph: "!", label: "Alerts",          hint: "#/alerts",    action: () => go("#/alerts") },
    { group: "NAVIGATE", glyph: "§", label: "Events",          hint: "#/events",    action: () => go("#/events") },
    { group: "NAVIGATE", glyph: "◈", label: "Evidence Terminal", hint: "#/evidence", action: () => go("#/evidence") },
    { group: "NAVIGATE", glyph: "☰", label: "Data Trust Center", hint: "#/trust",   action: () => go("#/trust") },
    { group: "NAVIGATE", glyph: "◐", label: "Observation Tower (single symbol)", hint: "/observe", action: () => location.href = "/observe" },
  ];
  const scanners = [
    { group: "SCANNERS", glyph: "◎", label: "Abnormal relative volume",   hint: "rel_volume_z > 2",     action: () => go("#/radar?filter=abn_vol") },
    { group: "SCANNERS", glyph: "◎", label: "Range compression",          hint: "range_z < -1",         action: () => go("#/radar?filter=compression") },
    { group: "SCANNERS", glyph: "◎", label: "Volume without price move",  hint: "vol_price_divergence", action: () => go("#/radar?filter=divergence") },
    { group: "SCANNERS", glyph: "◎", label: "Accumulation proxy",         hint: "close-loc × rel_vol",  action: () => go("#/radar?filter=accumulation") },
    { group: "SCANNERS", glyph: "◎", label: "Near upper circuit",         hint: "distance to UL < 2%",  action: () => go("#/radar?filter=near_upper") },
    { group: "SCANNERS", glyph: "◎", label: "Near lower circuit",         hint: "distance to LL < 2%",  action: () => go("#/radar?filter=near_lower") },
    { group: "SCANNERS", glyph: "◎", label: "Volatility regime expansion",hint: "vol_regime_ratio > 1.5", action: () => go("#/radar?filter=vol_expand") },
    { group: "SCANNERS", glyph: "◎", label: "Illiquidity persistence",    hint: "amihud > 2, sustained", action: () => go("#/radar?filter=illiquid") },
    { group: "SCANNERS", glyph: "◎", label: "Market-relative outliers",   hint: "xs rank top 5%",       action: () => go("#/radar?filter=xs_top") },
  ];
  const sys = [
    { group: "SYSTEM", glyph: "⚙", label: "Toggle HUMAN / RESEARCH", hint: `now ${S.mode}`, action: toggleMode },
    { group: "SYSTEM", glyph: "⚙", label: "Cycle theme",             hint: `now ${S.theme || "system"}`, action: cycleTheme },
  ];
  const uni = S.universe?.rows || [];
  const symMatches = q ? uni.filter(r => r.symbol && r.symbol.toUpperCase().includes(q.toUpperCase())).slice(0, 10) : [];
  const symItems = symMatches.map(r => ({
    group: "SYMBOLS",
    glyph: "$",
    label: `${r.symbol} — ${r.name || ""}`.trim(),
    hint: `${r.sector || ""} · ${F.num(r.ltp, 2)} · ${F.signedPct(r.change_pct/100 || null)}`,
    action: () => go(`#/stock/${r.symbol}`),
  }));
  if (!q) return [...nav, ...scanners, ...sys];
  const filt = (arr) => arr.filter(x => x.label.toLowerCase().includes(q.toLowerCase()) || (x.hint||"").toLowerCase().includes(q.toLowerCase()));
  return [...symItems, ...filt(scanners), ...filt(nav), ...filt(sys)];
}

// ────────────────────────────────────────────────────────────── evidence drawer
function openEvidence(payload) {
  closeEvidence();
  const back = el("div", { class: "ev-back", onclick: closeEvidence });
  const box = el("aside", { class: "ev-drawer", onclick: e => e.stopPropagation() });
  box.innerHTML = `
    <header>
      <h2>Evidence</h2>
      <span class="prov">${payload.source || "engine"} <span class="dot">·</span> <span class="fresh">${F.ageS(payload.at) || "now"}</span></span>
      <button title="close">×</button>
    </header>
    <div class="body"></div>`;
  $("header button", box).addEventListener("click", closeEvidence);
  const body = $(".body", box);
  body.appendChild(el("div", { class: "kv" },
    ...Object.entries(payload.fields || {}).map(([k, v]) =>
      el("div", { class: "row wide" },
        el("span", { class: "k" }, k),
        el("span", { class: "v" + (v == null ? " na" : "") }, v == null ? NA : String(v))))));
  if (payload.notes) body.appendChild(el("div", { class: "human-text", style: "margin-top:12px" }, payload.notes));
  if (payload.trace) {
    body.appendChild(el("h3", { style: "margin:14px 0 4px;font-size:11px;color:var(--accent);letter-spacing:.14em;text-transform:uppercase" }, "Trace"));
    body.appendChild(el("pre", { style: "background:var(--bg-2);padding:8px;border-radius:2px;overflow:auto;font:11px/1.5 var(--mono);color:var(--ink-1);white-space:pre-wrap" }, JSON.stringify(payload.trace, null, 2)));
  }
  document.body.append(back, box);
}
function closeEvidence() { document.querySelectorAll(".ev-back, .ev-drawer").forEach(n => n.remove()); }

// ────────────────────────────────────────────────────────────── mode toggle
function toggleMode() { S.mode = S.mode === "HUMAN" ? "RESEARCH" : "HUMAN"; localStorage.setItem("bmos.mode", S.mode); $("#sb-mode").textContent = S.mode; render(); }

// ────────────────────────────────────────────────────────────── watchlist
function watchlistToggle(sym) {
  const i = S.watchlist.indexOf(sym);
  if (i >= 0) S.watchlist.splice(i,1); else S.watchlist.push(sym);
  localStorage.setItem("bmos.watchlist", JSON.stringify(S.watchlist));
  $("#wl-count").textContent = String(S.watchlist.length);
  render();
}
$("#wl-count").textContent = String(S.watchlist.length);

// ────────────────────────────────────────────────────────────── data bootstrap
async function bootstrap() {
  const [summary, universe, sources, features, metrics, sectors] = await Promise.all([
    get("/api/market/summary",  15_000),
    get("/api/market/universe", 30_000),
    get("/api/market/sources",  15_000),
    get("/api/features/latest", 30_000),
    get("/api/metrics",         30_000),
    get("/api/market/sectors",  30_000),
  ]);
  S.summary = summary; S.universe = universe; S.sources = sources;
  S.features = features; S.metrics = metrics; S.sectors = sectors;
  paintTopbar();
  paintStatusbar();
  paintRail();
}

function paintTopbar() {
  const s = S.summary || {};
  const set = (id, v, cls) => { const n = $("#"+id); if (!n) return; n.textContent = v == null ? NA : v; if (cls !== undefined) { n.className = "v " + cls; } };
  set("tk-dsex",     s.dsex != null ? F.num(s.dsex, 2) : NA);
  set("tk-dsex-chg", s.dsex_change_pct != null ? F.signedPct(s.dsex_change_pct/100) : NA, s.dsex_change_pct == null ? "" : (s.dsex_change_pct > 0 ? "pos" : s.dsex_change_pct < 0 ? "neg" : ""));
  set("tk-phase",    s.session_phase || NA);
  set("tk-date",     s.trading_date || NA);
  set("tk-trades",   s.market_trades != null ? F.bigN(s.market_trades) : NA);
  set("tk-volume",   s.market_volume != null ? F.bigN(s.market_volume) : NA);
  set("tk-value",    s.market_value != null ? F.bigTk(s.market_value) : NA);
  set("tk-adv",      s.advancing != null ? String(s.advancing) : NA);
  set("tk-dec",      s.declining != null ? String(s.declining) : NA);
  set("tk-unch",     s.unchanged != null ? String(s.unchanged) : NA);
}
function paintStatusbar() {
  const set = (id, v) => { const n = $("#"+id); if (n) n.textContent = v == null ? NA : v; };
  const okSrc = (S.sources || []).filter(s => s.status === "OK" || s.status === "WORKING").length;
  const totSrc = (S.sources || []).length;
  const featOk = S.features && S.features.__ok !== false;
  set("sb-api", "ok");
  set("sb-feat", featOk ? "loaded" : "n/a");
  set("sb-state", (S.metrics && S.metrics.states_written != null) ? F.bigN(S.metrics.states_written) : NA);
  set("sb-src", `${okSrc}/${totSrc}`);
  set("sb-commit", (S.summary && S.summary.commit) || NA);
  set("sb-time", new Date().toISOString().slice(11,19));
}
function paintRail() {
  $("#wl-count").textContent = S.watchlist.length ? String(S.watchlist.length) : "";
  const alerts = alertsFromFeatures(S.features).length;
  const b = $("#al-count"); if (b) { b.textContent = alerts ? String(alerts) : ""; b.className = "badge " + (alerts ? "warn" : ""); }
  const okSrc = (S.sources || []).filter(s => s.status === "OK" || s.status === "WORKING").length;
  const totSrc = (S.sources || []).length;
  $("#rail-src").textContent = `${okSrc}/${totSrc} sources · ${S.summary?.trading_date || NA}`;
  $("#rail-time").textContent = new Date().toISOString().slice(11,19) + "Z";
}

// ────────────────────────────────────────────────────────────── screens
async function renderHome(root) {
  const s = S.summary || {};
  const uni = S.universe?.rows || [];
  const feats = S.features?.rows || [];
  const advPct = (s.advancing != null && s.declining != null && (s.advancing + s.declining) > 0)
    ? s.advancing / (s.advancing + s.declining) : null;
  const topTurn = [...uni].filter(r => Number.isFinite(r.value)).sort((a,b)=>b.value - a.value).slice(0, 8);
  const topVol  = [...uni].filter(r => Number.isFinite(r.volume)).sort((a,b)=>b.volume - a.volume).slice(0, 8);
  const topGain = [...uni].filter(r => Number.isFinite(r.change_pct)).sort((a,b)=>b.change_pct - a.change_pct).slice(0, 8);
  const topLose = [...uni].filter(r => Number.isFinite(r.change_pct)).sort((a,b)=>a.change_pct - b.change_pct).slice(0, 8);
  const abnormal = feats.filter(f => Number.isFinite(f.rel_volume_z) && f.rel_volume_z > 2).sort((a,b)=>b.rel_volume_z - a.rel_volume_z).slice(0, 10);
  const divergent = feats.filter(f => Number.isFinite(f.volume_price_divergence) && f.volume_price_divergence > 2).sort((a,b)=>b.volume_price_divergence - a.volume_price_divergence).slice(0, 10);

  root.innerHTML = `
    <div class="section">
      <h1><span class="n">01</span> Mission Control
        <span class="sub">${s.session_phase || NA} · ${s.trading_date || NA}</span>
      </h1>
      <div class="lede">One screen answers: <b>what is the Bangladesh stock market doing right now?</b> Every number below is
        anchored to a source; unavailable fields render as ${NA}, never as 0.</div>

      <div class="tiles">
        <div class="tile"><div class="lbl">DSEX <span class="pill live">LIVE</span></div>
          <div class="val ${F.cls(s.dsex_change_pct)}">${F.num(s.dsex, 2)}</div>
          <div class="sub">${F.signedPct(s.dsex_change_pct/100)} · today</div>
          ${prov("index", "LankaBD", "OBSERVED", s.dsex_at)}</div>
        <div class="tile"><div class="lbl">Turnover</div>
          <div class="val">${F.bigTk(s.market_value)}</div>
          <div class="sub">${F.bigN(s.market_trades)} trades · ${F.bigN(s.market_volume)} vol</div>
          ${prov("market", "LankaBD", "OBSERVED", s.market_at)}</div>
        <div class="tile"><div class="lbl">Breadth</div>
          <div class="val">${advPct == null ? NA : F.pct(advPct, 1)}</div>
          <div class="sub"><span style="color:var(--pos)">▲${s.advancing ?? NA}</span> ·
            <span style="color:var(--neg)">▼${s.declining ?? NA}</span> ·
            <span style="color:var(--ink-2)">◇${s.unchanged ?? NA}</span></div>
          ${prov("market", "LankaBD", "INFERRED")}</div>
        <div class="tile"><div class="lbl">Universe</div>
          <div class="val">${uni.length || NA}</div>
          <div class="sub">${feats.length ? feats.length + " with features · " : ""}${uni.filter(r => (r.volume||0) > 0).length} traded today</div>
          ${prov("core", "StockNow", "OBSERVED", S.universe?.at)}</div>
        <div class="tile"><div class="lbl">Abnormal (rel_volume_z &gt; 2)</div>
          <div class="val ${abnormal.length ? "warn" : ""}">${abnormal.length || 0}</div>
          <div class="sub">${abnormal.length ? "concentrated · click to inspect" : "no elevated activity right now"}</div>
          ${prov("features", "bdlib/features.py", "INFERRED")}</div>
        <div class="tile"><div class="lbl">Volume without price</div>
          <div class="val ${divergent.length ? "warn" : ""}">${divergent.length || 0}</div>
          <div class="sub">accumulation suspicion, not confirmation</div>
          ${prov("features", "volume_price_divergence", "INFERRED")}</div>
      </div>

      <div style="display:grid;grid-template-columns:2fr 1fr;gap:12px;margin-top:12px">
        <div class="panel">
          <h2>Sector map <span class="sub">turnover-weighted · today</span></h2>
          ${sectorHeatmapHTML(S.sectors)}
        </div>
        <div class="panel">
          <h2>Market state <span class="sub">rung-1 · state_engine</span></h2>
          <div class="human-text">
            ${humanMarketState(S.summary, feats, abnormal, divergent)}
          </div>
          <div style="margin-top:8px"><a href="#/evidence">◈ open evidence terminal →</a></div>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:repeat(4, 1fr);gap:10px;margin-top:12px">
        ${leaderPanel("Top turnover",   topTurn,  r => F.bigTk(r.value),  "value")}
        ${leaderPanel("Top volume",     topVol,   r => F.bigN(r.volume),  "volume")}
        ${leaderPanel("Strongest ▲",    topGain,  r => F.signedPct((r.change_pct||0)/100), "change_pct")}
        ${leaderPanel("Strongest ▼",    topLose,  r => F.signedPct((r.change_pct||0)/100), "change_pct")}
      </div>

      <div class="panel" style="margin-top:12px">
        <h2>Unusual activity <span class="sub">rel_volume_z, volume_price_divergence · human view</span></h2>
        ${unusualHTML(abnormal, divergent)}
      </div>
    </div>
  `;
  $$(".heatcell", root).forEach(c => c.addEventListener("click", () => go(`#/sectors?s=${encodeURIComponent(c.dataset.sector)}`)));
}

function leaderPanel(title, rows, valFn, valKey) {
  const trs = rows.map(r => `<tr onclick="location.hash='#/stock/${encodeURIComponent(r.symbol)}'">
    <td class="l sym"><a href="#/stock/${encodeURIComponent(r.symbol)}">${r.symbol}</a></td>
    <td class="num ${F.cls(r.change_pct)}">${F.signedPct((r.change_pct||0)/100)}</td>
    <td class="num">${valFn(r)}</td></tr>`).join("");
  return `<div class="panel"><h2>${title} <span class="sub">today · top 8</span></h2>
    <table class="tbl"><thead><tr><th class="l">SYM</th><th>Δ%</th><th>${valKey}</th></tr></thead><tbody>${trs || `<tr><td colspan="3" class="na">${NA}</td></tr>`}</tbody></table></div>`;
}

function sectorHeatmapHTML(sectors) {
  if (!sectors || !sectors.rows || !sectors.rows.length) return `<div class="prov">no sector data</div>`;
  const rows = [...sectors.rows].sort((a,b) => (b.value||0) - (a.value||0));
  return `<div class="heatgrid">${rows.map(s => {
    const c = s.change_pct;
    let bg = "var(--bg-2)";
    if (Number.isFinite(c)) {
      const clamp = Math.max(-3, Math.min(3, c));
      const a = Math.abs(clamp) / 3;
      bg = c > 0 ? `rgba(38,194,129,${0.08 + a*0.35})` : `rgba(239,83,80,${0.08 + a*0.35})`;
    }
    return `<div class="heatcell" data-sector="${escapeHtml(s.name)}" style="background:${bg}">
      <div class="n">${escapeHtml(s.name)}</div>
      <div class="p">${F.signedPct((c||0)/100)}</div>
      <div class="p">${F.bigTk(s.value)}</div>
    </div>`;
  }).join("")}</div>`;
}

function humanMarketState(sum, feats, abn, div) {
  if (!sum) return `<span class="human-text">Reading engine…</span>`;
  const phase = sum.session_phase || "UNKNOWN";
  const totalActive = (sum.advancing || 0) + (sum.declining || 0);
  const breadth = totalActive ? (sum.advancing / totalActive) : null;
  const parts = [];
  if (phase !== "CONTINUOUS") parts.push(`Market is <b>${phase}</b>. Ticker numbers reflect the last observed session.`);
  if (breadth != null) {
    if (breadth > 0.6) parts.push(`Breadth is <b class="name" style="color:var(--pos)">broad-based</b> — ${F.pct(breadth,0)} of active symbols are advancing.`);
    else if (breadth < 0.4) parts.push(`Breadth is <b class="name" style="color:var(--neg)">narrow to the downside</b> — ${F.pct(breadth,0)} advancing.`);
    else parts.push(`Breadth is <b>mixed</b> — ${F.pct(breadth,0)} advancing.`);
  }
  if (abn && abn.length) parts.push(`Volume abnormality is present in <b>${abn.length}</b> symbols (<span class="name">rel_volume_z &gt; 2</span>).`);
  if (div && div.length) parts.push(`<b>${div.length}</b> symbols show <span class="name">volume_price_divergence</span> — activity without a matching move.`);
  parts.push(`<i class="prov" style="color:var(--ink-3)">These are state observations, not instructions. The engine emits <span class="name">state, novelty, evidence</span> — never BUY/SELL/target/stop.</i>`);
  return parts.join("<br><br>");
}

function unusualHTML(abn, div) {
  const row = (arr, key, unitFn) => arr.slice(0, 10).map(f => `<tr>
    <td class="l sym"><a href="#/stock/${encodeURIComponent(f.symbol)}">${f.symbol}</a></td>
    <td>${trustBadge("INFERRED")}</td>
    <td class="num warn">${F.num(f[key], 2)}</td>
    <td class="num">${unitFn(f)}</td>
    <td class="l">${humanFeatureLine(key, f[key])}</td></tr>`).join("");
  return `<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
    <div><h2 style="font-size:10.5px;color:var(--ink-2);letter-spacing:.12em;text-transform:uppercase;margin:6px 0 4px">rel_volume_z &gt; 2</h2>
      <div class="wrap-x"><table class="tbl">
        <thead><tr><th class="l">SYM</th><th class="l">CLASS</th><th>Z</th><th>vol</th><th class="l">reads as</th></tr></thead>
        <tbody>${abn.length ? row(abn, "rel_volume_z", f => F.bigN(f.day_volume ?? f.volume)) : `<tr><td colspan="5" class="na">${NA}</td></tr>`}</tbody>
      </table></div></div>
    <div><h2 style="font-size:10.5px;color:var(--ink-2);letter-spacing:.12em;text-transform:uppercase;margin:6px 0 4px">volume_price_divergence &gt; 2</h2>
      <div class="wrap-x"><table class="tbl">
        <thead><tr><th class="l">SYM</th><th class="l">CLASS</th><th>Z</th><th>ret1</th><th class="l">reads as</th></tr></thead>
        <tbody>${div.length ? row(div, "volume_price_divergence", f => F.signedPct((f.ret_1||0))) : `<tr><td colspan="5" class="na">${NA}</td></tr>`}</tbody>
      </table></div></div>
  </div>`;
}

function humanFeatureLine(key, v) {
  if (v == null || !Number.isFinite(v)) return `<span class="human-text">measurement undefined</span>`;
  if (key === "rel_volume_z") return v > 3 ? "extremely unusual activity for this symbol" : v > 2 ? "unusual activity relative to its own history" : v > 1 ? "elevated" : "normal";
  if (key === "volume_price_divergence") return v > 3 ? "large abnormal activity without a matching move — classic absorption suspicion" : v > 2 ? "volume without price — accumulation suspicion (not confirmation)" : "";
  if (key === "range_z") return v > 2 ? "range expansion" : v < -1 ? "coiling (range compression)" : "";
  return "";
}

// ─── MARKET ───────────────────────────────────────────────────────────────
async function renderMarket(root) {
  const uni = S.universe?.rows || [];
  const feats = new Map((S.features?.rows || []).map(f => [f.symbol, f]));
  const enriched = uni.map(r => ({ ...r, ...(feats.get(r.symbol) || {}) }));
  root.innerHTML = `
    <div class="section">
      <h1><span class="n">02</span> Market <span class="sub">${uni.length} instruments · ${S.summary?.trading_date || NA}</span></h1>
      <div class="lede">Full-market table. Column order is not opinion; every column carries its OBS/INF/N-A truth class in Research mode.</div>
      <div class="chips" id="mkt-chips">
        <button class="chip" aria-pressed="true" data-f="all">All</button>
        <button class="chip" data-f="traded">Traded today</button>
        <button class="chip" data-f="floor">Floor (StockNow flag)</button>
        <button class="chip" data-f="wl">Watchlist (${S.watchlist.length})</button>
      </div>
      <div id="mkt-tbl"></div>
    </div>
  `;
  const draw = (filt) => {
    const list = enriched.filter(r => {
      if (filt === "traded") return (r.volume || 0) > 0;
      if (filt === "floor") return r.floor === true || r.floor_flag === true;
      if (filt === "wl") return S.watchlist.includes(r.symbol);
      return true;
    });
    $("#mkt-tbl").innerHTML = renderUniverseTable(list, {
      cols: [
        { k: "symbol", h: "SYM", w: true, l: true, cell: r => `<a href="#/stock/${encodeURIComponent(r.symbol)}">${r.symbol}</a>` },
        { k: "sector", h: "SECTOR", l: true },
        { k: "ltp", h: "LTP", cell: r => F.num(r.ltp, 2) },
        { k: "change_pct", h: "Δ%", cell: r => `<span class="${F.cls(r.change_pct)}">${F.signedPct((r.change_pct||0)/100)}</span>` },
        { k: "open", h: "OPEN", cell: r => F.num(r.open, 2) },
        { k: "high", h: "HIGH", cell: r => F.num(r.high, 2) },
        { k: "low",  h: "LOW",  cell: r => F.num(r.low, 2) },
        { k: "volume", h: "VOL", cell: r => F.bigN(r.volume) },
        { k: "value",  h: "TURN", cell: r => F.bigTk(r.value) },
        { k: "trades", h: "TRD", cell: r => F.bigN(r.trades) },
        { k: "rel_volume_z", h: "relVolZ", cell: r => hZ(r.rel_volume_z) },
        { k: "range_z", h: "rangeZ", cell: r => hZ(r.range_z) },
        { k: "market_relative_ret", h: "MREL", cell: r => `<span class="${F.cls(r.market_relative_ret)}">${F.signedPct(r.market_relative_ret || 0)}</span>` },
        { k: "upper_limit", h: "UL", cell: r => F.num(r.upper_limit, 2) },
        { k: "lower_limit", h: "LL", cell: r => F.num(r.lower_limit, 2) },
      ]
    });
  };
  draw("all");
  $$("#mkt-chips .chip", root).forEach(c => c.addEventListener("click", () => {
    $$("#mkt-chips .chip", root).forEach(x => x.setAttribute("aria-pressed", "false"));
    c.setAttribute("aria-pressed","true"); draw(c.dataset.f);
  }));
}
function hZ(v) { if (v == null || !Number.isFinite(v)) return `<span class="na">${NA}</span>`; const c = v > 2 ? "warn" : v > 1 ? "pos" : v < -1 ? "neg" : ""; return `<span class="${c}">${F.num(v, 2)}</span>`; }

function renderUniverseTable(rows, opts) {
  const cols = opts.cols;
  let sortKey = "value", sortDir = -1;
  const draw = () => {
    const sorted = [...rows].sort((a, b) => {
      const av = a[sortKey], bv = b[sortKey];
      const an = Number.isFinite(av) ? av : (typeof av === "string" ? av.toLowerCase() : -Infinity);
      const bn = Number.isFinite(bv) ? bv : (typeof bv === "string" ? bv.toLowerCase() : -Infinity);
      if (an < bn) return -sortDir; if (an > bn) return sortDir; return 0;
    });
    return `<div class="wrap-x" style="max-height:calc(100vh - 240px);overflow-y:auto"><table class="tbl">
      <thead><tr>${cols.map(c => `<th class="${c.l?"l":""} ${sortKey===c.k?(sortDir>0?"sort-asc":"sort-desc"):""}" data-k="${c.k}">${c.h}</th>`).join("")}</tr></thead>
      <tbody>${sorted.slice(0, 500).map(r => `<tr>${cols.map(c => `<td class="${c.l?"l ":""}${c.k==="symbol"?"sym":""}">${c.cell ? c.cell(r) : (r[c.k] == null ? `<span class="na">${NA}</span>` : String(r[c.k]))}</td>`).join("")}</tr>`).join("")}</tbody>
    </table>${sorted.length > 500 ? `<div class="prov" style="padding:6px 4px">showing 500 of ${sorted.length}</div>` : ""}</div>`;
  };
  const html = draw();
  setTimeout(() => {
    $$("#mkt-tbl th").forEach(th => th.addEventListener("click", () => {
      const k = th.dataset.k; if (k === sortKey) sortDir = -sortDir; else { sortKey = k; sortDir = -1; }
      $("#mkt-tbl").innerHTML = draw();
    }));
  }, 0);
  return html;
}

// ─── RADAR ───────────────────────────────────────────────────────────────
async function renderRadar(root) {
  const uni = S.universe?.rows || [];
  const feats = new Map((S.features?.rows || []).map(f => [f.symbol, f]));
  const enriched = uni.map(r => ({ ...r, ...(feats.get(r.symbol) || {}) }));
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  const filter = q.get("filter") || "abn_vol";
  const LENSES = [
    { id: "abn_vol", label: "Abnormal relative volume", desc: "rel_volume_z > 2 · log-space robust z", test: r => (r.rel_volume_z||0) > 2, sort: r => r.rel_volume_z || 0 },
    { id: "abn_turn", label: "Abnormal relative turnover", desc: "rel_turnover_z > 2 · money-weighted", test: r => (r.rel_turnover_z||0) > 2, sort: r => r.rel_turnover_z || 0 },
    { id: "compression", label: "Range compression (coiling)", desc: "range_z < -1", test: r => (r.range_z||0) < -1, sort: r => -(r.range_z || 0) },
    { id: "range_exp", label: "Range expansion", desc: "range_z > 2", test: r => (r.range_z||0) > 2, sort: r => r.range_z || 0 },
    { id: "divergence", label: "Volume without price move", desc: "volume_price_divergence > 2 · accumulation suspicion", test: r => (r.volume_price_divergence||0) > 2, sort: r => r.volume_price_divergence || 0 },
    { id: "accumulation", label: "Accumulation proxy > 0", desc: "close-location × rel_vol · positive = high closes on abnormal volume", test: r => (r.accumulation_proxy||0) > 0.5, sort: r => r.accumulation_proxy || 0 },
    { id: "vol_expand", label: "Volatility regime expansion", desc: "vol_regime_ratio > 1.5", test: r => (r.vol_regime_ratio||0) > 1.5, sort: r => r.vol_regime_ratio || 0 },
    { id: "illiquid", label: "Illiquidity persistence", desc: "illiquidity_persistence > 0.5", test: r => (r.illiquidity_persistence||0) > 0.5, sort: r => r.illiquidity_persistence || 0 },
    { id: "near_upper", label: "Near upper circuit", desc: "distance to UL < 2%", test: r => distUpper(r) != null && distUpper(r) < 2, sort: r => distUpper(r) },
    { id: "near_lower", label: "Near lower circuit", desc: "distance to LL < 2%", test: r => distLower(r) != null && distLower(r) < 2, sort: r => distLower(r) },
    { id: "xs_top", label: "Cross-sectional top-5%", desc: "xs_rank_rel_volume > 0.95", test: r => (r.xs_rank_rel_volume||0) > 0.95, sort: r => r.xs_rank_rel_volume || 0 },
    { id: "market_out", label: "Market-relative outliers", desc: "|market_relative_ret| > 5%", test: r => Math.abs(r.market_relative_ret||0) > 0.05, sort: r => Math.abs(r.market_relative_ret||0) },
  ];
  const active = LENSES.find(l => l.id === filter) || LENSES[0];
  const hits = enriched.filter(active.test).sort((a,b) => (active.sort(b)||0) - (active.sort(a)||0));
  root.innerHTML = `
    <div class="section">
      <h1><span class="n">03</span> Radar <span class="sub">${LENSES.length} lenses · scanner</span></h1>
      <div class="lede">Every lens below is a definition, not a template. A lens dies with the same rule that rejected
        <span class="name">RSI / MACD / Bollinger</span> — <span class="name">REJECTED_CANDIDATES</span> preserves the graveyard.</div>
      <div class="chips">${LENSES.map(l => `<a class="chip" aria-pressed="${l.id === filter}" href="#/radar?filter=${l.id}" title="${l.desc}">${l.label}</a>`).join("")}</div>
      <div class="panel">
        <h2>${active.label} <span class="sub">${active.desc} · ${hits.length} of ${enriched.length} symbols</span></h2>
        ${hits.length ? renderUniverseTable(hits, {
          cols: [
            { k: "symbol", h: "SYM", l: true, cell: r => `<a href="#/stock/${encodeURIComponent(r.symbol)}">${r.symbol}</a>` },
            { k: "sector", h: "SECTOR", l: true },
            { k: "ltp", h: "LTP", cell: r => F.num(r.ltp, 2) },
            { k: "change_pct", h: "Δ%", cell: r => `<span class="${F.cls(r.change_pct)}">${F.signedPct((r.change_pct||0)/100)}</span>` },
            { k: "rel_volume_z", h: "relVolZ", cell: r => hZ(r.rel_volume_z) },
            { k: "range_z", h: "rangeZ", cell: r => hZ(r.range_z) },
            { k: "vol_regime_ratio", h: "volReg", cell: r => F.num(r.vol_regime_ratio, 2) },
            { k: "volume_price_divergence", h: "divg", cell: r => hZ(r.volume_price_divergence) },
            { k: "accumulation_proxy", h: "accum", cell: r => F.num(r.accumulation_proxy, 2) },
            { k: "market_relative_ret", h: "MREL", cell: r => `<span class="${F.cls(r.market_relative_ret)}">${F.signedPct(r.market_relative_ret || 0)}</span>` },
            { k: "distUL", h: "→UL%", cell: r => F.num(distUpper(r), 2) },
            { k: "distLL", h: "→LL%", cell: r => F.num(distLower(r), 2) },
          ]
        }) : `<div class="prov" style="padding:12px 4px">no symbols match — under this lens, right now.</div>`}
      </div>
    </div>
  `;
}
function distUpper(r) { if (!Number.isFinite(r.upper_limit) || !Number.isFinite(r.ltp)) return null; return (r.upper_limit - r.ltp) / r.ltp * 100; }
function distLower(r) { if (!Number.isFinite(r.lower_limit) || !Number.isFinite(r.ltp)) return null; return (r.ltp - r.lower_limit) / r.ltp * 100; }

// ─── SECTORS ─────────────────────────────────────────────────────────────
async function renderSectors(root) {
  const sectors = S.sectors?.rows || [];
  const q = new URLSearchParams(location.hash.split("?")[1] || "");
  const activeSector = q.get("s");
  const uni = S.universe?.rows || [];
  root.innerHTML = `
    <div class="section">
      <h1><span class="n">04</span> Sectors <span class="sub">${sectors.length} sectors · ${uni.length} symbols</span></h1>
      <div class="panel"><h2>Sector map</h2>${sectorHeatmapHTML({ rows: sectors })}</div>
      ${activeSector ? renderSectorDetail(activeSector, sectors, uni) : `<div class="lede" style="margin-top:12px">click a sector for its constituents.</div>`}
    </div>
  `;
  $$(".heatcell", root).forEach(c => c.addEventListener("click", () => go(`#/sectors?s=${encodeURIComponent(c.dataset.sector)}`)));
}
function renderSectorDetail(name, sectors, uni) {
  const s = sectors.find(x => x.name === name);
  const rows = uni.filter(r => r.sector === name).sort((a,b) => (b.value||0) - (a.value||0));
  return `<div class="panel" style="margin-top:12px">
    <h2>${escapeHtml(name)} <span class="sub">${rows.length} instruments · ${F.bigTk(s?.value)} today</span></h2>
    <div class="tiles" style="margin-bottom:10px">
      <div class="tile"><div class="lbl">Sector Δ</div><div class="val ${F.cls(s?.change_pct)}">${F.signedPct((s?.change_pct||0)/100)}</div></div>
      <div class="tile"><div class="lbl">Turnover</div><div class="val">${F.bigTk(s?.value)}</div></div>
      <div class="tile"><div class="lbl">Advancing</div><div class="val pos">${s?.advancing ?? NA}</div></div>
      <div class="tile"><div class="lbl">Declining</div><div class="val neg">${s?.declining ?? NA}</div></div>
    </div>
    ${renderUniverseTable(rows, {
      cols: [
        { k: "symbol", h: "SYM", l: true, cell: r => `<a href="#/stock/${encodeURIComponent(r.symbol)}">${r.symbol}</a>` },
        { k: "ltp", h: "LTP", cell: r => F.num(r.ltp, 2) },
        { k: "change_pct", h: "Δ%", cell: r => `<span class="${F.cls(r.change_pct)}">${F.signedPct((r.change_pct||0)/100)}</span>` },
        { k: "volume", h: "VOL", cell: r => F.bigN(r.volume) },
        { k: "value", h: "TURN", cell: r => F.bigTk(r.value) },
      ]
    })}
  </div>`;
}

// ─── WATCHLIST ───────────────────────────────────────────────────────────
async function renderWatchlist(root) {
  const uni = S.universe?.rows || [];
  const feats = new Map((S.features?.rows || []).map(f => [f.symbol, f]));
  const rows = S.watchlist.map(sym => {
    const r = uni.find(x => x.symbol === sym) || { symbol: sym };
    return { ...r, ...(feats.get(sym) || {}) };
  });
  root.innerHTML = `
    <div class="section">
      <h1><span class="n">05</span> Watchlist <span class="sub">${rows.length} symbols · localStorage</span></h1>
      <div class="lede">A watchlist row tracks conditions the engine measures, not just prices.</div>
      ${rows.length ? renderUniverseTable(rows, {
        cols: [
          { k: "symbol", h: "SYM", l: true, cell: r => `<a href="#/stock/${encodeURIComponent(r.symbol)}">${r.symbol}</a>` },
          { k: "ltp", h: "LTP", cell: r => F.num(r.ltp, 2) },
          { k: "change_pct", h: "Δ%", cell: r => `<span class="${F.cls(r.change_pct)}">${F.signedPct((r.change_pct||0)/100)}</span>` },
          { k: "rel_volume_z", h: "relVolZ", cell: r => hZ(r.rel_volume_z) },
          { k: "range_z", h: "rangeZ", cell: r => hZ(r.range_z) },
          { k: "volume_price_divergence", h: "divg", cell: r => hZ(r.volume_price_divergence) },
          { k: "abnormal_persistence", h: "PERSIST", cell: r => F.num(r.abnormal_persistence, 0) },
          { k: "state", h: "STATE", l: true, cell: r => `<span class="pill ${r.state === "EXTREME" ? "neg" : r.state === "DEPARTURE" ? "warn" : r.state === "DRIFT" ? "info" : ""}">${r.state || NA}</span>` },
          { k: "remove", h: "", cell: r => `<a href="javascript:void(0)" onclick="event.stopPropagation();window.__wl('${r.symbol}')">✕</a>` },
        ]
      }) : `<div class="panel"><div class="prov" style="padding:12px 4px">Empty. Add symbols from ⌘K or from a stock page.</div></div>`}
    </div>
  `;
}
window.__wl = watchlistToggle;

// ─── ALERTS ──────────────────────────────────────────────────────────────
function alertsFromFeatures(features) {
  if (!features || !features.rows) return [];
  const out = [];
  for (const f of features.rows) {
    if ((f.rel_volume_z||0) > 3) out.push({ symbol: f.symbol, kind: "abn-volume", severity: "warn", text: `${f.symbol} rel_volume_z = ${F.num(f.rel_volume_z,2)} (extreme)`, feature: "rel_volume_z", value: f.rel_volume_z });
    if ((f.volume_price_divergence||0) > 3) out.push({ symbol: f.symbol, kind: "divergence", severity: "warn", text: `${f.symbol} volume_price_divergence = ${F.num(f.volume_price_divergence,2)}`, feature: "volume_price_divergence", value: f.volume_price_divergence });
    if ((f.range_z||0) > 3) out.push({ symbol: f.symbol, kind: "range-exp", severity: "warn", text: `${f.symbol} range_z = ${F.num(f.range_z,2)}`, feature: "range_z", value: f.range_z });
    if ((f.abnormal_persistence||0) >= 3) out.push({ symbol: f.symbol, kind: "persistent", severity: "info", text: `${f.symbol} abnormality persistent for ${f.abnormal_persistence} bars`, feature: "abnormal_persistence", value: f.abnormal_persistence });
  }
  return out;
}
async function renderAlerts(root) {
  const list = alertsFromFeatures(S.features);
  root.innerHTML = `
    <div class="section">
      <h1><span class="n">06</span> Alerts <span class="sub">${list.length} live · derived from features</span></h1>
      <div class="lede">Alerts fire on meaningful transitions in engine-native measurements. Every alert links to evidence.</div>
      ${list.length ? `<div class="panel"><table class="tbl">
        <thead><tr><th class="l">SYM</th><th class="l">KIND</th><th class="l">TEXT</th><th>VALUE</th><th></th></tr></thead>
        <tbody>${list.map(a => `<tr>
          <td class="l sym"><a href="#/stock/${encodeURIComponent(a.symbol)}">${a.symbol}</a></td>
          <td class="l"><span class="pill ${a.severity}">${a.kind}</span></td>
          <td class="l">${escapeHtml(a.text)}</td>
          <td class="num">${F.num(a.value, 2)}</td>
          <td><a href="javascript:void(0)" onclick='window.__ev(${JSON.stringify(a).replace(/"/g,"&quot;")})'>◈ evidence</a></td>
        </tr>`).join("")}</tbody>
      </table></div>` : `<div class="panel"><div class="prov" style="padding:12px 4px">No alerts firing right now.</div></div>`}
    </div>`;
}
window.__ev = (a) => {
  openEvidence({
    source: `bdlib/features.py :: ${a.feature}`,
    at: S.features?.at || new Date().toISOString(),
    fields: {
      symbol: a.symbol, feature: a.feature, value: F.num(a.value, 4),
      threshold: a.feature === "rel_volume_z" ? "> 2 (unusual), > 3 (extreme)" : "context",
      class: "INFERRED",
    },
    notes: `The value above is a robust z-score computed in log space over the trailing baseline (60 bars, excluding t). It is a measurement, not a decision. See FEATURE_DICTIONARY.md.`,
  });
};

// ─── EVENTS ──────────────────────────────────────────────────────────────
async function renderEvents(root) {
  // Corporate events / BSEC / CDBL — engine is aware of these sources but most fields are NOT_OBSERVABLE yet.
  root.innerHTML = `
    <div class="section">
      <h1><span class="n">07</span> Events <span class="sub">corporate · regulatory · macro</span></h1>
      <div class="lede">The engine's Stage-1 adapters for BSEC publications, CDBL statistics, and BB monetary bills are wired
        and parse successfully; per-symbol corporate events are BullBD's <span class="name">corporateEvent</span> slot, which is null
        for most symbols today. Fields the source does not carry render as ${NA}, never as 0.</div>
      <div class="tiles">
        <div class="tile"><div class="lbl">BSEC publications ${trustBadge("OBSERVED")}</div>
          <div class="val">—</div>
          <div class="sub">index parsed, 35 publications, 30 dated</div>
          ${prov("regulatory", "sec.gov.bd/", "OBSERVED")}</div>
        <div class="tile"><div class="lbl">CDBL statistics ${trustBadge("OBSERVED")}</div>
          <div class="val">—</div>
          <div class="sub">DPs 560 · BO accounts 1.66M · ISINs 829</div>
          ${prov("depository", "cdbl.com.bd/", "OBSERVED")}</div>
        <div class="tile"><div class="lbl">BB T-bills ${trustBadge("OBSERVED")}</div>
          <div class="val">—</div>
          <div class="sub">auction rows parsed</div>
          ${prov("macro", "bangladeshbank.org.bd/monetaryactivity/bbbill", "OBSERVED")}</div>
        <div class="tile"><div class="lbl">Corporate events per symbol ${trustBadge("NOT_OBSERVABLE")}</div>
          <div class="val na">—</div>
          <div class="sub">BullBD corporateEvent null; PDFs not parsed</div>
          ${prov("regulatory", "bullbd.com", "NOT_OBSERVABLE")}</div>
      </div>
      <div class="panel" style="margin-top:12px">
        <h2>Notes</h2>
        <div class="human-text">
          The engine <span class="name">records that a publication exists</span>, not what the PDF says. Extracting
          a penalty or a symbol from a PDF is a separate job and is not guessed. This screen will grow as the parsers
          land — until then it renders truthfully as unavailable.
        </div>
      </div>
    </div>`;
}

// ─── EVIDENCE ────────────────────────────────────────────────────────────
async function renderEvidence(root) {
  root.innerHTML = `
    <div class="section">
      <h1><span class="n">08</span> Evidence Terminal</h1>
      <div class="lede">Any important system conclusion is traceable to a value, a baseline, a source and a freshness.
        Click any highlighted number on any screen to open this drawer with its trace.</div>
      <div class="panel">
        <h2>Standing conclusions <span class="sub">verdict ledger · surviving research leads</span></h2>
        <div class="kv">
          <div class="row wide"><span class="k">V-014 Touch-locality (micro)</span>
            <span class="v">INSUFFICIENT_SAMPLE — 1 of 3 required session-blocks; taker ECONOMICALLY_UNUSABLE (−0.602 ticks vs 1.00-tick round trip)</span></div>
          <div class="row wide"><span class="k">Upper-circuit continuation (daily)</span>
            <span class="v">Real signal C2C, killed by overnight gap. gap_open captures ~90% before entry; realistic P(net&gt;0) &lt; 0.42 at any horizon.</span></div>
          <div class="row wide"><span class="k">D_shallow_pullback</span>
            <span class="v">MECHANICAL_ARTIFACT — overlap inflation 13.4x + low-vol confound</span></div>
          <div class="row wide"><span class="k">SAI, pre-move activity, causal leadership</span>
            <span class="v">KILLED / WEAK — no incremental value once causal</span></div>
        </div>
      </div>
      <div class="panel" style="margin-top:12px">
        <h2>Global rules preserved</h2>
        <div class="human-text">
          The engine <span class="name">never</span> emits BUY / SELL / target / stop. It emits
          <span class="name">state, novelty, evidence</span>. Signals labelled OBSERVED come from a source;
          signals labelled INFERRED are derived and say so; anything without a source is
          <span class="name">NOT_OBSERVABLE</span> and rendered as ${NA}. Cross-source disagreement is
          <b>shown, never resolved</b>.
        </div>
      </div>
    </div>`;
}

// ─── DATA TRUST ──────────────────────────────────────────────────────────
async function renderTrust(root) {
  const srcs = S.sources || [];
  const ok = srcs.filter(s => s.status === "OK" || s.status === "WORKING").length;
  const deg = srcs.filter(s => s.status === "DEGRADED").length;
  const bad = srcs.filter(s => s.status === "FAILING" || s.status === "BLOCKED").length;
  root.innerHTML = `
    <div class="section">
      <h1><span class="n">09</span> Data Trust Center <span class="sub">${srcs.length} sensors</span></h1>
      <div class="lede">Provenance is a first-class concept, not a footnote. Every field's truth class,
        every source's freshness, every disagreement — displayed, not resolved.</div>
      <div class="tiles">
        <div class="tile"><div class="lbl">Sensors OK</div><div class="val pos">${ok}</div></div>
        <div class="tile"><div class="lbl">Degraded</div><div class="val warn">${deg}</div></div>
        <div class="tile"><div class="lbl">Failing / Blocked</div><div class="val neg">${bad}</div></div>
        <div class="tile"><div class="lbl">Disagreement rows</div><div class="val na">${NA}</div>
          <div class="sub">seeing/consensus.py exposes but does not resolve</div></div>
      </div>
      <div class="panel" style="margin-top:12px">
        <h2>Sources <span class="sub">seeing/capture/adapters · public source matrix</span></h2>
        <table class="tbl">
          <thead><tr><th class="l">SOURCE</th><th class="l">STATUS</th><th class="l">DELIVERS</th><th>CADENCE</th><th>DELAY</th><th class="l">TRUTH</th><th class="l">NOTES</th></tr></thead>
          <tbody>${srcs.map(s => `<tr>
            <td class="l"><b>${escapeHtml(s.name)}</b></td>
            <td class="l"><span class="pill ${s.status === "OK" || s.status === "WORKING" ? "pos" : s.status === "DEGRADED" ? "warn" : "neg"}">${escapeHtml(s.status)}</span></td>
            <td class="l">${escapeHtml(s.delivers || NA)}</td>
            <td>${escapeHtml(s.cadence || NA)}</td>
            <td>${escapeHtml(s.delay || NA)}</td>
            <td class="l">${trustBadge(s.truth || "OBSERVED")}</td>
            <td class="l">${escapeHtml(s.notes || "")}</td></tr>`).join("")}</tbody>
        </table>
      </div>
      <div class="panel" style="margin-top:12px">
        <h2>Field truth classes <span class="sub">PUBLIC_DATA_COVERAGE.md · per-field</span></h2>
        ${renderCoverageTable()}
      </div>
    </div>`;
}
function renderCoverageTable() {
  const groups = [
    ["IDENTITY", [["symbol", "OBSERVED", "5 sources", "473 DSE"], ["company_id", "OBSERVED", "LankaBD watch", "~100%"], ["mic", "OBSERVED", "BullBD (XDHA)", "polled"]]],
    ["TIME",     [["t_recv", "OBSERVED", "raw store", "100%"], ["t_source", "OBSERVED", "4/9 live", "0-60s"], ["session_phase", "INFERRED", "seeing/clock.py", "100%"]]],
    ["BOOK",     [["bid_levels / ask_levels", "OBSERVED", "LankaBD+dsebd", "polled"], ["best_bid / best_ask / spread", "INFERRED", "from ladders", "polled"], ["bid_orders_per_level", "NOT_OBSERVABLE", "—", "0%"], ["queue_position, order_events", "NOT_OBSERVABLE", "—", "0%"]]],
    ["PRICE / ACTIVITY", [["ltp / ohlc / trades / volume / value", "OBSERVED", "5 sensors", "~100%"], ["interval tape (VWAP)", "INFERRED", "LankaBD tape diff", "polled"], ["trade_prints, trade_side", "NOT_OBSERVABLE", "—", "0%"]]],
    ["MARKET",   [["advancing / declining / unchanged", "OBSERVED", "LankaBD market", "market-wide"], ["index_value", "OBSERVED", "LankaBD, EcoSoft HAR", "DSEX"]]],
    ["REFERENCE",[["upper_limit / lower_limit / tick", "OBSERVED", "LankaBD circuit + dsebd cbul.php", "635-636"], ["7d/15d/30d/90d/180d/365d refs", "OBSERVED", "StockNow only", "473"]]],
    ["FUNDAMENTALS",[["eps / nav / pe / paid_up / total_shares", "OBSERVED", "BullBD only", "polled"], ["market_cap", "INFERRED", "total_shares × ltp", "polled"]]],
    ["OWNERSHIP",[["sponsor_dir / govt / institution / foreign / public", "OBSERVED", "dsebd.org/displayCompany.php", "419 symbols, 1220 rows"], ["free_float", "NOT_OBSERVABLE", "—", "0%"]]],
    ["EVENTS",   [["corporate_event", "OBSERVED when present", "BullBD", "unmeasured"], ["announcements, record_date, agm_date", "NOT_OBSERVABLE (yet)", "—", "0%"], ["BSEC enforcement", "NOT_OBSERVABLE (yet)", "PDF pending", "0%"]]],
  ];
  return `<table class="tbl"><thead><tr><th class="l">GROUP</th><th class="l">FIELD</th><th class="l">TRUTH</th><th class="l">SOURCE</th><th class="l">COVERAGE</th></tr></thead>
    <tbody>${groups.flatMap(([g, rows]) => rows.map(r => `<tr>
      <td class="l">${escapeHtml(g)}</td>
      <td class="l">${escapeHtml(r[0])}</td>
      <td class="l">${trustBadge(r[1].includes("NOT_OBSERVABLE") ? "NOT_OBSERVABLE" : r[1].includes("INFERRED") ? "INFERRED" : "OBSERVED")}</td>
      <td class="l">${escapeHtml(r[2])}</td>
      <td class="l">${escapeHtml(r[3])}</td></tr>`)).join("")}</tbody></table>`;
}

// ─── STOCK COMMAND CENTER ────────────────────────────────────────────────
async function renderStock(root, params) {
  const sym = params.sym;
  if (!sym) { root.innerHTML = `<div class="section"><h1>Stock</h1><div class="lede">Type ⌘K to pick a symbol.</div></div>`; return; }
  const tab = params.tab || "overview";
  const detail = await get(`/api/stock/${encodeURIComponent(sym)}`, 15_000);
  const r = detail?.instrument || null;
  const feat = detail?.features || null;
  root.innerHTML = `
    <div class="section">
      ${stockHeaderHTML(sym, r)}
      <div class="chips" style="margin-top:10px">
        ${["overview","chart","depth","seeing","state","fundamentals","ownership","history","events","evidence"].map(t =>
          `<a class="chip" href="#/stock/${encodeURIComponent(sym)}/${t}" aria-pressed="${t === tab}">${t.toUpperCase()}</a>`).join("")}
        <a class="chip" href="/observe?symbol=${encodeURIComponent(sym)}" style="margin-left:auto">◐ open observation tower</a>
        <a class="chip" href="javascript:window.__wl('${sym}')">${S.watchlist.includes(sym) ? "✓ watching" : "☆ add to watchlist"}</a>
      </div>
      <div id="stock-body" style="margin-top:10px">${stockTabHTML(tab, sym, r, feat, detail)}</div>
    </div>`;
}
function stockHeaderHTML(sym, r) {
  if (!r) return `<h1><span class="n">$</span> ${escapeHtml(sym)} <span class="sub">not resolved in universe</span></h1>`;
  const cls = F.cls(r.change_pct);
  return `<h1><span class="n">$</span> ${escapeHtml(sym)}
      <span style="color:var(--ink-2);font-family:var(--sans);font-weight:400;font-size:12.5px">${escapeHtml(r.name || "")}</span>
      <span class="sub">${escapeHtml(r.sector || "")} · ${escapeHtml(r.category || "")}${r.floor_flag || r.floor ? " · <span class='pill warn'>FLOOR</span>" : ""}</span>
    </h1>
    <div class="tiles">
      <div class="tile"><div class="lbl">LTP <span class="pill live">LIVE</span></div>
        <div class="val ${cls}">${F.num(r.ltp, 2)}</div>
        <div class="sub">${F.signedPct((r.change_pct||0)/100)} · Δ ${F.signed(r.change_abs)}</div>
        ${prov("price", r.source || "consensus", "OBSERVED", r.t_source)}</div>
      <div class="tile"><div class="lbl">OHLC</div>
        <div class="val" style="font-size:14px">O ${F.num(r.open,2)} · H ${F.num(r.high,2)} · L ${F.num(r.low,2)}</div>
        <div class="sub">YCP ${F.num(r.ycp,2)}</div>
        ${prov("price", r.source || "consensus", "OBSERVED", r.t_source)}</div>
      <div class="tile"><div class="lbl">Activity</div>
        <div class="val">${F.bigN(r.volume)}</div>
        <div class="sub">${F.bigTk(r.value)} · ${F.bigN(r.trades)} trades</div>
        ${prov("activity", r.source || "consensus", "OBSERVED", r.t_source)}</div>
      <div class="tile"><div class="lbl">Circuit band</div>
        <div class="val" style="font-size:14px">L ${F.num(r.lower_limit,2)} · U ${F.num(r.upper_limit,2)}</div>
        <div class="sub">→UL ${F.num(distUpper(r),2)}% · →LL ${F.num(distLower(r),2)}% · tick ${F.num(r.tick_size,3)}</div>
        ${prov("reference", "LankaBD + dsebd cbul.php", "OBSERVED")}</div>
    </div>`;
}
function stockTabHTML(tab, sym, r, feat, detail) {
  switch (tab) {
    case "overview":     return tabOverview(r, feat);
    case "chart":        return tabChart(sym, r);
    case "depth":        return tabDepth(r, detail?.depth);
    case "seeing":       return tabSeeing(feat);
    case "state":        return tabState(detail?.state);
    case "fundamentals": return tabFundamentals(detail?.fundamentals);
    case "ownership":    return tabOwnership(detail?.ownership);
    case "history":      return tabHistory(detail?.history);
    case "events":       return tabEvents(detail?.events);
    case "evidence":     return tabEvidence(sym, detail);
    default:             return `<div class="panel"><div class="prov">unknown tab</div></div>`;
  }
}

function tabOverview(r, feat) {
  const f = feat || {};
  const state = (f.state) || "—";
  return `<div style="display:grid;grid-template-columns:2fr 1fr;gap:12px">
    <div class="panel"><h2>State ${trustBadge("INFERRED")} <span class="sub">state_engine rung-2 · CALM/DRIFT/DEPARTURE/EXTREME</span></h2>
      <div style="display:flex;gap:12px;align-items:center;margin:6px 0 10px">
        <span class="pill ${state === "EXTREME" ? "neg" : state === "DEPARTURE" ? "warn" : state === "DRIFT" ? "info" : ""}" style="font-size:12px;padding:3px 10px">${state}</span>
        <span class="prov">age <b>${F.num(f.state_age, 0)}</b> bar${f.state_age === 1 ? "" : "s"} <span class="dot">·</span> novelty <b>${F.num(f.novelty, 3)}</b> <span class="dot">·</span> xs rank <b>${F.pct((f.xs_novelty_rank||0),1)}</b></span>
      </div>
      <div class="kv">
        <div class="row"><span class="k">rel_volume_z</span><span class="v ${(f.rel_volume_z||0) > 2 ? "warn" : ""}">${F.num(f.rel_volume_z,2)}</span></div>
        <div class="row"><span class="k">rel_turnover_z</span><span class="v ${(f.rel_turnover_z||0) > 2 ? "warn" : ""}">${F.num(f.rel_turnover_z,2)}</span></div>
        <div class="row"><span class="k">range_z</span><span class="v ${(f.range_z||0) > 2 ? "warn" : (f.range_z||0) < -1 ? "info" : ""}">${F.num(f.range_z,2)}</span></div>
        <div class="row"><span class="k">amihud_z</span><span class="v">${F.num(f.amihud_z,2)}</span></div>
        <div class="row"><span class="k">market_relative_ret</span><span class="v ${F.cls(f.market_relative_ret)}">${F.signedPct(f.market_relative_ret||0)}</span></div>
        <div class="row"><span class="k">close_location</span><span class="v">${F.num(f.close_location,2)}</span></div>
        <div class="row"><span class="k">volume_price_divergence</span><span class="v ${(f.volume_price_divergence||0) > 2 ? "warn" : ""}">${F.num(f.volume_price_divergence,2)}</span></div>
        <div class="row"><span class="k">accumulation_proxy</span><span class="v ${F.cls(f.accumulation_proxy)}">${F.num(f.accumulation_proxy,2)}</span></div>
        <div class="row"><span class="k">abnormal_persistence</span><span class="v">${F.num(f.abnormal_persistence,0)}</span></div>
      </div>
    </div>
    <div class="panel"><h2>Human read</h2>
      <div class="human-text">${humanReadStock(r, f)}</div>
    </div>
  </div>`;
}
function humanReadStock(r, f) {
  const parts = [];
  if (!r) return "Loading…";
  const cp = r.change_pct;
  if (cp != null) parts.push(`${escapeHtml(r.symbol)} is <b class="name" style="color:${cp>0?'var(--pos)':cp<0?'var(--neg)':'var(--ink-1)'}">${F.signedPct(cp/100)}</b> today.`);
  if (Number.isFinite(f?.rel_volume_z)) {
    const z = f.rel_volume_z;
    if (z > 3) parts.push(`<span class="name">rel_volume_z = ${F.num(z,2)}</span> — activity is <b>extremely unusual</b> relative to this symbol's own recent history.`);
    else if (z > 2) parts.push(`<span class="name">rel_volume_z = ${F.num(z,2)}</span> — activity is <b>unusual</b> vs the trailing 60-bar baseline.`);
    else if (z > 1) parts.push(`Activity is <b>elevated</b> (z ${F.num(z,2)}).`);
    else parts.push(`Activity is <b>normal</b> for this symbol (z ${F.num(z,2)}).`);
  }
  if (Number.isFinite(f?.volume_price_divergence) && f.volume_price_divergence > 2)
    parts.push(`<span class="name">volume_price_divergence = ${F.num(f.volume_price_divergence,2)}</span> — <b>volume without a matching price move</b>. That is accumulation/distribution suspicion, not confirmation.`);
  if (Number.isFinite(f?.market_relative_ret))
    parts.push(`Move vs market: <span class="name">market_relative_ret = ${F.signedPct(f.market_relative_ret)}</span> — ${Math.abs(f.market_relative_ret) > 0.03 ? "outlier vs the market" : "in line with the market"} today.`);
  parts.push(`<i class="prov" style="color:var(--ink-3)">Every number above comes from bdlib/features.py; NaN reads as ${NA}, not 0.</i>`);
  return parts.join("<br><br>");
}

function tabChart(sym, r) {
  return `<div class="panel"><h2>Chart <span class="sub">deep chart lives in the observation tower</span></h2>
    <div class="human-text">The Observation Tower already renders MID/LTP with mechanism-episode shading, deterministic
      replay controls, and a synchronized state timeline for the symbol you are viewing. Open it here:</div>
    <div style="margin-top:8px"><a class="chip" href="/observe?symbol=${encodeURIComponent(sym)}">◐ open observation tower →</a></div>
    ${r ? `<div class="kv" style="margin-top:12px">
      <div class="row"><span class="k">7d ref</span><span class="v">${F.num(r.ref_7d,2)}</span></div>
      <div class="row"><span class="k">15d ref</span><span class="v">${F.num(r.ref_15d,2)}</span></div>
      <div class="row"><span class="k">30d ref</span><span class="v">${F.num(r.ref_30d,2)}</span></div>
      <div class="row"><span class="k">90d ref</span><span class="v">${F.num(r.ref_90d,2)}</span></div>
      <div class="row"><span class="k">180d ref</span><span class="v">${F.num(r.ref_180d,2)}</span></div>
      <div class="row"><span class="k">365d ref</span><span class="v">${F.num(r.ref_365d,2)}</span></div>
      <div class="row"><span class="k">yr high</span><span class="v">${F.num(r.year_high,2)}</span></div>
      <div class="row"><span class="k">yr low</span><span class="v">${F.num(r.year_low,2)}</span></div>
    </div>` : ""}</div>`;
}

function tabDepth(r, depth) {
  if (!depth || !depth.bids || !depth.asks) return `<div class="panel"><h2>Depth ${trustBadge("NOT_OBSERVABLE")}</h2>
    <div class="human-text">The book is a live-only sensor; the shell fetches it on demand from LankaBD. Right now
      the depth adapter did not deliver a book for this symbol. The observation tower will always show the last-seen book if any.</div></div>`;
  return `<div class="panel"><h2>Depth <span class="sub">LankaBD MarketDepthData · top-N</span></h2>
    <div class="kv" style="grid-template-columns:repeat(auto-fill,minmax(120px,1fr))">
      <div class="row"><span class="k">best bid</span><span class="v">${F.num(depth.best_bid,2)}</span></div>
      <div class="row"><span class="k">best ask</span><span class="v">${F.num(depth.best_ask,2)}</span></div>
      <div class="row"><span class="k">spread</span><span class="v">${F.num(depth.spread,2)}</span></div>
      <div class="row"><span class="k">buy qty</span><span class="v">${F.bigN(depth.total_buy_volume)}</span></div>
      <div class="row"><span class="k">sell qty</span><span class="v">${F.bigN(depth.total_sell_volume)}</span></div>
      <div class="row"><span class="k">buy%</span><span class="v pos">${F.pct(depth.buy_percentage/100)}</span></div>
      <div class="row"><span class="k">sell%</span><span class="v neg">${F.pct(depth.sell_percentage/100)}</span></div>
    </div>
    <div class="ladder" style="margin-top:8px">
      <div class="col bids">${(depth.bids || []).map(l => `<div class="row">${F.bigN(l.qty)}</div>`).join("")}</div>
      <div class="col px">${(depth.bids || []).map((l, i) => `<div class="row">${F.num(l.px,2)} / ${F.num((depth.asks || [])[i]?.px,2)}</div>`).join("")}</div>
      <div class="col asks">${(depth.asks || []).map(l => `<div class="row">${F.bigN(l.qty)}</div>`).join("")}</div>
    </div>
    <div class="prov" style="margin-top:6px">bid_orders_per_level ${trustBadge("NOT_OBSERVABLE")} · queue_position ${trustBadge("NOT_OBSERVABLE")}</div>
  </div>`;
}

function tabSeeing(f) {
  if (!f) return `<div class="panel"><div class="prov">no feature vector for this symbol yet</div></div>`;
  const FAM = [
    ["A. Price / range", [
      ["ret_1", "one-bar log return", (-0.1, 0.1)],
      ["range_pct", "bar range, price-scaled", (0, 0.15)],
      ["close_location", "1 = closed on the high, 0 = on the low", (0, 1)],
      ["gap_open", "session gap (mostly at session-first-bar)", (-0.05, 0.05)]]],
    ["B. Activity — normalised to symbol's own self", [
      ["rel_volume_z", "how unusual THIS stock's volume is now", (-3, 5)],
      ["rel_turnover_z", "same, money-weighted", (-3, 5)],
      ["range_z", "range expansion vs own norm", (-3, 5)],
      ["range_compression", "positive = tighter than usual (coiling)", (-3, 5)],
      ["volume_persistence", "is elevated activity sustained (0..1)", (0, 1)],
      ["activity_concentration", "1/k = evenly spread; → 1 = one bar carried it all", (0, 1)]]],
    ["C. Volatility regime", [
      ["realized_vol", "σ of ret_1 over vol_window", (0, 0.1)],
      ["vol_regime_ratio", "> 1 = volatility accelerating", (0, 3)]]],
    ["D. Impact and liquidity", [
      ["amihud_z", "rising = book thinning", (-3, 5)],
      ["hl_spread_proxy", "crude spread proxy", (0, 0.05)],
      ["illiquidity_persistence", "0..1", (0, 1)]]],
    ["E. Divergence / accumulation proxies", [
      ["volume_price_divergence", "volume without matching move — accumulation suspicion", (-2, 6)],
      ["accumulation_proxy", "high closes on abnormal volume", (-2, 2)],
      ["ret_autocorr_1", "trending (>0) vs mean-reverting (<0)", (-1, 1)]]],
    ["F. Cross-sectional / market context", [
      ["xs_rank_rel_volume", "percentile of rel_vol_z across market at t", (0, 1)],
      ["xs_rank_rel_turnover", "same, money-weighted", (0, 1)],
      ["xs_volume_abnormality", "isolated vs everyone moving together", (-3, 5)],
      ["market_ret", "median ret_1 across symbols at t", (-0.05, 0.05)],
      ["market_relative_ret", "idiosyncratic move", (-0.1, 0.1)],
      ["xs_breadth_abnormal", "share with rel_vol_z > 2 at t", (0, 0.3)],
      ["xs_symbols_at_ts", "coverage guard", (0, 500)]]],
    ["G. State persistence", [
      ["abnormal_persistence", "bars ending at t with rel_vol_z > 2", (0, 10)],
      ["bars_since_abnormal", "age of the current quiet state", (0, 50)],
      ["baseline_active_days", "traded days inside trailing window", (0, 60)]]],
  ];
  const research = S.mode === "RESEARCH";
  return `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
      <div class="prov">bdlib/features.py · trailing baseline [t-W, t-1] · robust z in log space · NaN when scale ≈ 0</div>
      <div class="mode-switch"><button aria-pressed="${!research}" onclick="window.__mode('HUMAN')">HUMAN</button><button aria-pressed="${research}" onclick="window.__mode('RESEARCH')">RESEARCH</button></div>
    </div>
    ${FAM.map(([fam, feats]) => `<div class="panel" style="margin-bottom:8px">
      <h2>${escapeHtml(fam)}</h2>
      ${research
        ? `<div>${feats.map(([k, gloss, rng]) => meterHTML(k, f[k], rng, gloss)).join("")}</div>`
        : `<div class="human-text">${feats.map(([k, gloss]) => humanFeatureRow(k, f[k], gloss)).join(" ")}</div>`
      }
    </div>`).join("")}`;
}
window.__mode = (m) => { S.mode = m; localStorage.setItem("bmos.mode", m); $("#sb-mode").textContent = m; render(); };
function meterHTML(name, v, rng, gloss) {
  const [lo, hi] = Array.isArray(rng) ? rng : [-3, 5];
  const cls = v == null || !Number.isFinite(v) ? "na" : "";
  const pct = v == null || !Number.isFinite(v) ? 0 : Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  const neg = Number.isFinite(v) && v < 0;
  const midShift = (0 - lo) / (hi - lo);
  return `<div class="meter" title="${escapeHtml(gloss)}">
    <span class="k">${escapeHtml(name)}</span>
    <div class="track">
      <div class="fill ${neg ? "neg" : ""}" style="left:${neg ? pct*100 : midShift*100}%;width:${Math.abs(pct - midShift)*100}%"></div>
      <div class="mid" style="left:${midShift*100}%"></div>
    </div>
    <span class="v ${cls}">${F.num(v, 3)}</span>
  </div>`;
}
function humanFeatureRow(name, v, gloss) {
  if (v == null || !Number.isFinite(v)) return `<div style="margin:4px 0"><span class="name">${escapeHtml(name)}</span> — ${escapeHtml(gloss)}. <span class="prov">measurement undefined (NaN — degenerate baseline)</span></div>`;
  return `<div style="margin:4px 0"><span class="name">${escapeHtml(name)}</span> = <b>${F.num(v,3)}</b> — ${escapeHtml(gloss)}.</div>`;
}

function tabState(state) {
  if (!state) return `<div class="panel"><div class="prov">no state history for this symbol yet</div></div>`;
  return `<div class="panel"><h2>State timeline <span class="sub">state_engine · rung-2 · CALM/DRIFT/DEPARTURE/EXTREME</span></h2>
    <div class="human-text">State is <b>a description of the market</b>, not an instruction. Rung 1 (univariate) and
      rung 2 (multivariate RMS of z-vector) are BUILT and causality-proved on real DSE data. Rungs 3-5 are designed only.</div>
    <table class="tbl" style="margin-top:10px">
      <thead><tr><th class="l">TS</th><th class="l">STATE</th><th>AGE</th><th>NOVELTY</th><th class="l">TOP COMPONENT</th></tr></thead>
      <tbody>${(state.events || []).slice(0, 60).map(e => `<tr>
        <td class="l">${F.ymd(e.ts) + " " + F.hms(e.ts)}</td>
        <td class="l"><span class="pill ${e.state === "EXTREME" ? "neg" : e.state === "DEPARTURE" ? "warn" : e.state === "DRIFT" ? "info" : ""}">${e.state}</span></td>
        <td>${e.state_age ?? NA}</td>
        <td>${F.num(e.novelty, 3)}</td>
        <td class="l">${escapeHtml(e.top_component || NA)}</td></tr>`).join("") || `<tr><td colspan="5" class="na">${NA}</td></tr>`}</tbody>
    </table></div>`;
}

function tabFundamentals(fund) {
  if (!fund) return `<div class="panel"><h2>Fundamentals ${trustBadge("OBSERVED")}</h2><div class="prov">BullBD not polled yet; adapter available</div></div>`;
  return `<div class="panel"><h2>Fundamentals ${trustBadge("OBSERVED")} <span class="sub">BullBD · shareDetail</span></h2>
    <div class="kv">
      <div class="row"><span class="k">EPS (annualized)</span><span class="v">${F.num(fund.annualized_eps, 2)}</span></div>
      <div class="row"><span class="k">NAV (audited)</span><span class="v">${F.num(fund.audited_nav, 2)}</span></div>
      <div class="row"><span class="k">P/E</span><span class="v">${F.num(fund.annualized_pe, 2)}</span></div>
      <div class="row"><span class="k">Price / NAV</span><span class="v">${F.num(fund.price_to_nav, 2)}</span></div>
      <div class="row"><span class="k">Paid-up capital</span><span class="v">${F.bigN(fund.paid_up_capital)}</span></div>
      <div class="row"><span class="k">Total shares</span><span class="v">${F.bigN(fund.outstanding_shares)}</span></div>
      <div class="row"><span class="k">Market cap ${trustBadge("INFERRED")}</span><span class="v">${F.bigTk(fund.market_cap)}</span></div>
      <div class="row"><span class="k">Year end</span><span class="v">${escapeHtml(fund.year_end || NA)}</span></div>
    </div></div>`;
}
function tabOwnership(own) {
  if (!own) return `<div class="panel"><h2>Ownership ${trustBadge("OBSERVED")}</h2><div class="prov">no as-on rows for this symbol yet · dsebd displayCompany.php polls monthly per symbol</div></div>`;
  const rows = (own.rows || []).sort((a,b) => (b.as_on||"").localeCompare(a.as_on||""));
  return `<div class="panel"><h2>Ownership ${trustBadge("OBSERVED")} <span class="sub">dsebd.org/displayCompany.php · as-on dates</span></h2>
    <table class="tbl"><thead><tr><th class="l">AS ON</th><th>SPONSOR/DIR</th><th>GOVT</th><th>INSTITUTION</th><th>FOREIGN</th><th>PUBLIC</th></tr></thead>
      <tbody>${rows.map(r => `<tr>
        <td class="l">${F.ymd(r.as_on)}</td>
        <td>${F.pct((r.sponsor_director||0)/100)}</td>
        <td>${F.pct((r.government||0)/100)}</td>
        <td>${F.pct((r.institution||0)/100)}</td>
        <td>${F.pct((r.foreign||0)/100)}</td>
        <td>${F.pct((r.public||0)/100)}</td></tr>`).join("")}</tbody></table>
    <div class="prov" style="margin-top:6px">DSE publishes 3 as-on dates per company and keeps no archive; the fix is monthly polling and payoff is months, not today. Fields OBSERVED at those dates only — no daily interpolation.</div></div>`;
}
function tabHistory(hist) {
  if (!hist) return `<div class="panel"><h2>Historical analogs</h2><div class="prov">no historical states loaded for this symbol yet</div></div>`;
  return `<div class="panel"><h2>Historical analogs <span class="sub">state_engine + fwd_ret_h · h ∈ {5,15,30,60}</span></h2>
    <div class="human-text">The engine records <span class="name">every</span> state occurrence — including the boring ones —
      because Phase 4's denominator is every occurrence, not the interesting ones. Distributions below include failures.</div>
    <table class="tbl" style="margin-top:10px"><thead><tr><th class="l">TS</th><th class="l">STATE</th><th>fwd_ret_5</th><th>fwd_ret_15</th><th>fwd_ret_30</th><th>fwd_ret_60</th><th>fwd_mfe_30</th><th>fwd_mae_30</th></tr></thead>
      <tbody>${(hist.analogs || []).slice(0, 40).map(h => `<tr>
        <td class="l">${F.ymd(h.ts)}</td>
        <td class="l">${escapeHtml(h.state || NA)}</td>
        <td class="num ${F.cls(h.fwd_ret_5)}">${F.signedPct(h.fwd_ret_5 || 0)}</td>
        <td class="num ${F.cls(h.fwd_ret_15)}">${F.signedPct(h.fwd_ret_15 || 0)}</td>
        <td class="num ${F.cls(h.fwd_ret_30)}">${F.signedPct(h.fwd_ret_30 || 0)}</td>
        <td class="num ${F.cls(h.fwd_ret_60)}">${F.signedPct(h.fwd_ret_60 || 0)}</td>
        <td class="num pos">${F.signedPct(h.fwd_mfe_30 || 0)}</td>
        <td class="num neg">${F.signedPct(h.fwd_mae_30 || 0)}</td></tr>`).join("") || `<tr><td colspan="8" class="na">${NA}</td></tr>`}</tbody></table></div>`;
}
function tabEvents(events) {
  const ev = events?.rows || [];
  return `<div class="panel"><h2>Events <span class="sub">corporate + regulatory</span></h2>
    ${ev.length ? `<table class="tbl"><thead><tr><th class="l">DATE</th><th class="l">KIND</th><th class="l">TITLE</th><th class="l">SOURCE</th></tr></thead>
      <tbody>${ev.map(e => `<tr><td class="l">${F.ymd(e.date)}</td><td class="l">${escapeHtml(e.kind||NA)}</td><td class="l">${escapeHtml(e.title||"")}</td><td class="l">${escapeHtml(e.source||NA)}</td></tr>`).join("")}</tbody></table>`
      : `<div class="prov" style="padding:12px 4px">no per-symbol events yet — BullBD corporateEvent slot is null for this symbol.</div>`}</div>`;
}
function tabEvidence(sym, detail) {
  return `<div class="panel"><h2>Evidence <span class="sub">what supports every conclusion on this page</span></h2>
    <div class="human-text">Every headline value is anchored to a source. Cross-source disagreement is displayed, not resolved.</div>
    <table class="tbl" style="margin-top:10px"><thead><tr><th class="l">FIELD</th><th class="l">SOURCE(S)</th><th class="l">CLASS</th><th class="l">NOTE</th></tr></thead>
      <tbody>
        <tr><td class="l">ltp / ohlc</td><td class="l">LankaBD × 4, dsebd × 2, StockNow, CSE, BullBD</td><td class="l">${trustBadge("OBSERVED")}</td><td class="l">5 independent sensors; disagreements exposed</td></tr>
        <tr><td class="l">upper_limit / lower_limit / tick</td><td class="l">LankaBD circuit + dsebd cbul.php</td><td class="l">${trustBadge("OBSERVED")}</td><td class="l">two independent sources</td></tr>
        <tr><td class="l">volume / value / trades</td><td class="l">LankaBD × 4, dsebd × 2, StockNow, CSE</td><td class="l">${trustBadge("OBSERVED")}</td><td class="l">EOD after close</td></tr>
        <tr><td class="l">7d / 15d / 30d / 90d / 180d / 365d refs</td><td class="l">StockNow only</td><td class="l">${trustBadge("OBSERVED")}</td><td class="l">EOD, per symbol</td></tr>
        <tr><td class="l">EPS / NAV / P/E / paid-up / outstanding</td><td class="l">BullBD only</td><td class="l">${trustBadge("OBSERVED")}</td><td class="l">quarterly-ish</td></tr>
        <tr><td class="l">market cap</td><td class="l">outstanding × ltp</td><td class="l">${trustBadge("INFERRED")}</td><td class="l">derived</td></tr>
        <tr><td class="l">ownership (sponsor/govt/institution/foreign/public)</td><td class="l">dsebd displayCompany.php</td><td class="l">${trustBadge("OBSERVED")}</td><td class="l">monthly as-on; 3 as-on per company published</td></tr>
        <tr><td class="l">bid_orders_per_level / queue_position / trade_prints</td><td class="l">—</td><td class="l">${trustBadge("NOT_OBSERVABLE")}</td><td class="l">structural, unchanged by any adapter added here</td></tr>
        <tr><td class="l">features (rel_volume_z, range_z, novelty, state, …)</td><td class="l">bdlib/features.py + state_engine</td><td class="l">${trustBadge("INFERRED")}</td><td class="l">robust z in log space; NaN when scale ≈ 0</td></tr>
      </tbody></table></div>`;
}

// ────────────────────────────────────────────────────────────── boot
function renderNotFound(root) { root.innerHTML = `<div class="section"><h1>404</h1><div class="lede">route not found.</div></div>`; }

async function boot() {
  applyTheme();
  $("#sb-mode").textContent = S.mode;
  try {
    await bootstrap();
  } catch (e) {
    console.warn("bootstrap failed", e);
  }
  await render();
  // gentle refresh every 15s of top-level state, without disturbing selection
  clearInterval(S.pollTimer);
  S.pollTimer = setInterval(async () => {
    try {
      invalidate("/api/market/summary");
      invalidate("/api/market/sources");
      const [summary, sources] = await Promise.all([get("/api/market/summary"), get("/api/market/sources")]);
      S.summary = summary; S.sources = sources;
      paintTopbar(); paintStatusbar(); paintRail();
    } catch (_) {}
  }, 15000);
}
boot();
})();
