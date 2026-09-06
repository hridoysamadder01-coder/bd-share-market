/* DSE Observation Tower — front end (vanilla JS, no build step, no CDN).
 *
 * Every number on screen comes from the API; a null field is rendered as '—'
 * (NOT_OBSERVABLE), never as 0. All panels render from one state dict, so the
 * live view (newest state) and a replay position (state at or before a time)
 * use exactly the same code path: showAt(t) → /api/state/{sym}?at=t → render(). */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const NA = "—";
  const SLIDER_MAX = 10000;
  const POLL_MS = 2000;
  const PLAY_TICK_MS = 200;

  const S = {
    symbol: null, range: null, cur: null, cross: null, history: null, timeline: null, metrics: null,
    live: true, playing: false, virt: null, busy: false, pollTimer: null, playTimer: null,
    expanded: new Set(), showAllMech: false, tlSegments: [],
  };

  // ------------------------------------------------------------------ helpers
  async function api(path) {
    const r = await fetch(path, { cache: "no-store" });
    if (!r.ok) {
      let msg = r.status + " " + r.statusText;
      try { const j = await r.json(); if (j && j.error) msg = j.error; } catch (_) { /* keep */ }
      throw new Error(msg);
    }
    return r.json();
  }
  function ms(iso) {                       // ISO (any fraction length) → epoch ms
    if (iso === null || iso === undefined) return null;
    const m = String(iso).replace(/(\.\d{3})\d+/, "$1");
    const v = Date.parse(m);
    return Number.isFinite(v) ? v : null;
  }
  function iso(t) { return new Date(t).toISOString(); }
  function hms(isoOrMs) {
    const t = typeof isoOrMs === "number" ? isoOrMs : ms(isoOrMs);
    if (t === null) return NA;
    return new Date(t).toISOString().slice(11, 23);
  }
  function isNum(v) { return typeof v === "number" && Number.isFinite(v); }
  function f(v, d) {
    if (d === undefined) d = 2;
    if (v === null || v === undefined) return NA;
    if (typeof v === "boolean") return v ? "yes" : "no";
    if (typeof v === "number") {
      if (!Number.isFinite(v)) return NA;
      if (Number.isInteger(v) && Math.abs(v) >= 1000) return v.toLocaleString("en-US");
      if (Math.abs(v) >= 1000) return v.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
      return v.toFixed(d);
    }
    if (Array.isArray(v)) return v.length ? v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ") : NA;
    if (typeof v === "object") return JSON.stringify(v);
    return String(v);
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
  function cls(v, kind) {
    if (v === null || v === undefined) return "na";
    if (kind === "sign" && isNum(v)) return v > 0 ? "pos" : v < 0 ? "neg" : "";
    if (kind === "flag") return v ? "bad" : "";
    if (kind === "good") return v ? "ok" : "";
    return "";
  }
  /* rows: [label, value, {d, kind, text}] */
  function kv(el, rows) {
    el.innerHTML = rows.map(([k, v, o]) => {
      o = o || {};
      const txt = o.text !== undefined ? o.text : f(v, o.d);
      return `<div class="row"><span class="k">${esc(k)}</span><span class="v ${cls(v, o.kind)}">${esc(txt)}</span></div>`;
    }).join("");
  }
  /* gauge: -1..1 centred (bipolar) or 0..1 */
  function gauge(label, v, o) {
    o = o || {};
    const bipolar = o.bipolar !== false;
    let bar = "";
    if (isNum(v)) {
      const x = Math.max(bipolar ? -1 : 0, Math.min(1, v));
      const color = o.color || (x >= 0 ? "var(--bid)" : "var(--ask)");
      if (bipolar) {
        const w = Math.abs(x) * 50;
        bar = x >= 0 ? `<i style="left:50%;width:${w}%;background:${color}"></i>` : `<i style="left:${50 - w}%;width:${w}%;background:${color}"></i>`;
      } else bar = `<i style="left:0;width:${x * 100}%;background:${o.color || "var(--accent)"}"></i>`;
    }
    return `<div class="g"><span class="k">${esc(label)}</span><div class="bar">${bipolar ? '<span class="mid"></span>' : ""}${bar}</div><span class="v ${cls(v)}">${f(v, o.d === undefined ? 3 : o.d)}</span></div>`;
  }
  function setStatus(msg, err) { const el = $("#status"); el.textContent = msg; el.className = "status" + (err ? " err" : ""); }
  function fitCanvas(c) {
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth || 300, h = parseInt(c.getAttribute("height"), 10) || 100;
    c.width = Math.round(w * dpr); c.height = Math.round(h * dpr);
    const ctx = c.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }
  const STATE_COLORS = {
    balanced: "#3b4b5c", pressure_building: "#ffb74d", expansion: "#26c281", rejection: "#ef5350", reversal: "#ab47bc",
    normal: "#3b4b5c", depletion: "#ff8a65", recovery: "#4dd0e1", continuation: "#26c281", no_recovery: "#ef5350", vacuum: "#d50000",
    free: "#3b4b5c", approach: "#ffb74d", hit: "#ff7043", lock: "#d50000", unlock: "#4dd0e1", relock: "#b71c1c",
    none: "#3b4b5c", accumulation_like: "#ffd54f", breakout: "#26c281", failed_pressure: "#ef5350",
    streak: "#ffb74d", weakening: "#ab47bc", break: "#ef5350",
    inactive: "rgba(0,0,0,0)", building: "#ffb74d", active: "#26c281", confirmed: "#00e676", failed: "#ef5350", resolved: "#4fc3f7",
  };
  function stateColor(s) {
    if (STATE_COLORS[s]) return STATE_COLORS[s];
    let h = 0; for (const ch of String(s)) h = (h * 31 + ch.charCodeAt(0)) % 360;
    return `hsl(${h},60%,55%)`;
  }

  // ------------------------------------------------------------------ data loading
  async function loadSymbols() {
    const d = await api("/api/symbols");
    const sel = $("#symbol");
    const prev = sel.value;
    sel.innerHTML = d.symbols.map((s) => {
      const act = s.active_mechanisms.length ? ` · ${s.active_mechanisms.length} active` : "";
      return `<option value="${esc(s.symbol)}">${esc(s.symbol)} · ${hms(s.last_t)} · ${esc(s.session_phase || NA)}${act}</option>`;
    }).join("");
    if (prev && d.symbols.some((s) => s.symbol === prev)) sel.value = prev;
    return d.symbols;
  }
  async function loadRange() {
    const d = await api("/api/replay");
    const r = d.symbols[S.symbol];
    S.range = r ? { first: ms(r.first_t), last: ms(r.last_t), count: r.count } : null;
    $("#range-first").textContent = r ? hms(r.first_t) : NA;
    $("#range-last").textContent = r ? hms(r.last_t) : NA;
  }
  async function loadSeries() {
    const fields = "mid,ltp,best_bid,best_ask,signed_flow_window,price_only_response,price_impact,pressure_strength";
    const [h, tl] = await Promise.all([
      api(`/api/history/${encodeURIComponent(S.symbol)}?fields=${fields}`),
      api(`/api/timeline/${encodeURIComponent(S.symbol)}`),
    ]);
    S.history = h; S.timeline = tl;
  }
  async function loadMetrics() { try { S.metrics = await api("/api/metrics"); } catch (e) { S.metrics = null; } }

  async function showAt(t) {                     // t: epoch ms or null (latest)
    if (!S.symbol) return;
    S.busy = true;
    try {
      const q = t === null ? "latest" : encodeURIComponent(iso(t));
      const sym = encodeURIComponent(S.symbol);
      const [st, cr] = await Promise.all([api(`/api/state/${sym}?at=${q}`), api(`/api/cross/${sym}?at=${q}`).catch(() => null)]);
      S.cur = st; S.cross = cr;
      if (t !== null) { try { fetch(`/api/replay/seek?symbol=${sym}&at=${q}`, { method: "POST" }); } catch (_) { /* cursor is advisory */ } }
      render();
      setStatus(`ok · ${S.symbol} · ${st.index + 1}/${st.count}`);
    } catch (e) {
      setStatus("error: " + e.message, true);
    } finally { S.busy = false; }
  }

  // ------------------------------------------------------------------ rendering
  function render() {
    const st = S.cur ? S.cur.state : null;
    renderHeader(st);
    renderReplay();
    renderBook(st); renderFlow(st); renderPressure(st); renderLiquidity(st); renderResponse(st);
    renderMech(st); renderCircuit(st); renderCross(); renderSources(st);
    renderChart(); renderTimeline();
  }
  function renderHeader(st) {
    $("#state-time").textContent = st ? st.t : NA;
    $("#state-seq").textContent = st ? f(st.seq, 0) : NA;
    $("#state-phase").textContent = st ? (st.session_phase || NA) : NA;
    $("#state-book-source").textContent = st ? (st.book_source || NA) : NA;
    $("#mode").textContent = S.live ? "LIVE (newest state)" : "REPLAY";
  }
  function renderReplay() {
    const c = S.cur;
    $("#replay-pos").textContent = c ? `${c.index + 1}/${c.count} @ ${hms(c.t)}` : NA;
    $("#btn-live").classList.toggle("on", S.live);
    $("#btn-play").classList.toggle("on", S.playing);
    $("#btn-play").innerHTML = S.playing ? "&#10074;&#10074;" : "&#9654;";
    if (c && S.range && S.range.last > S.range.first) {
      const frac = (ms(c.t) - S.range.first) / (S.range.last - S.range.first);
      $("#replay-slider").value = Math.round(Math.max(0, Math.min(1, frac)) * SLIDER_MAX);
    } else if (c) $("#replay-slider").value = SLIDER_MAX;
  }

  function renderBook(st) {
    const tb = $("#ladder tbody");
    if (!st) { tb.innerHTML = ""; $("#book-l1").innerHTML = ""; $("#book-geometry").innerHTML = ""; return; }
    $("#book-sub").textContent = `${st.book_source || "no book source"} · age ${f(st.book_age_s, 1)}s` + (st.empty_book ? " · EMPTY" : st.one_sided ? " · ONE-SIDED" : "") + (st.crossed ? " · CROSSED" : "") + (st.locked ? " · LOCKED" : "");
    $("#book-l1").innerHTML =
      `<span>BID <b class="bidpx">${f(st.best_bid)}</b> × ${f(st.bid_qty1, 0)}</span>` +
      `<span>SPREAD <b>${f(st.spread)}</b> (${f(st.spread_ticks, 1)} t)</span>` +
      `<span>ASK <b class="askpx">${f(st.best_ask)}</b> × ${f(st.ask_qty1, 0)}</span>` +
      `<span>MID <b>${f(st.mid, 3)}</b></span><span>µP <b>${f(st.microprice, 3)}</b></span><span>LTP <b>${f(st.ltp)}</b></span><span>TICK <b>${f(st.tick_size)}</b></span>`;
    const bids = st.bids || [], asks = st.asks || [];
    const maxQ = Math.max(1, ...bids.map((l) => l[1] || 0), ...asks.map((l) => l[1] || 0));
    const wb = st.wall_bid && st.wall_bid.price, wa = st.wall_ask && st.wall_ask.price;
    const rows = [];
    const askRows = asks.map((l, i) => [l, i]).reverse();
    for (const [l, i] of askRows) {
      const n = st.ask_orders ? st.ask_orders[i] : null;
      rows.push(`<tr class="ladder-row ask${i === 0 ? " best" : ""}${wa !== null && wa !== undefined && l[0] === wa ? " wall" : ""}"><td class="qty"></td><td class="px">${f(l[0])}</td><td class="qty"><div class="fill" style="width:${(100 * (l[1] || 0)) / maxQ}%"></div><span>${f(l[1], 0)}</span></td><td class="n">${f(n, 0)}</td></tr>`);
    }
    rows.push(`<tr class="spread"><td colspan="4">${st.empty_book ? "empty book — NOT_OBSERVABLE" : `spread ${f(st.spread)} · ${f(st.spread_ticks, 1)} ticks · ${asks.length} ask lvl / ${bids.length} bid lvl`}</td></tr>`);
    bids.forEach((l, i) => {
      const n = st.bid_orders ? st.bid_orders[i] : null;
      rows.push(`<tr class="ladder-row bid${i === 0 ? " best" : ""}${wb !== null && wb !== undefined && l[0] === wb ? " wall" : ""}"><td class="qty"><div class="fill" style="width:${(100 * (l[1] || 0)) / maxQ}%"></div><span>${f(l[1], 0)}</span></td><td class="px">${f(l[0])}</td><td class="qty"></td><td class="n">${f(n, 0)}</td></tr>`);
    });
    tb.innerHTML = rows.join("");
    const wallTxt = (w) => w ? `${f(w.price)} × ${f(w.qty, 0)} (${f(w.share * 100, 0)}% · ${f(w.persistence_s, 0)}s · ${f(w.dist_ticks, 0)}t)` : NA;
    kv($("#book-geometry"), [
      ["imb L1", st.imb_l1, { kind: "sign", d: 3 }], ["imb top5", st.imb_topk, { kind: "sign", d: 3 }], ["imb weighted", st.imb_weighted, { kind: "sign", d: 3 }],
      ["vis bid liq", st.visible_bid_liq, { d: 0 }], ["vis ask liq", st.visible_ask_liq, { d: 0 }], ["depth ratio", st.depth_ratio, { d: 3 }],
      ["HHI bid", st.depth_concentration_bid, { d: 3 }], ["HHI ask", st.depth_concentration_ask, { d: 3 }],
      ["slope bid", st.depth_slope_bid], ["slope ask", st.depth_slope_ask], ["curv bid", st.depth_curvature_bid], ["curv ask", st.depth_curvature_ask],
      ["hollow bid", st.hollow_bid, { d: 0 }], ["hollow ask", st.hollow_ask, { d: 0 }],
      ["wall bid", st.wall_bid, { text: wallTxt(st.wall_bid) }], ["wall ask", st.wall_ask, { text: wallTxt(st.wall_ask) }],
      ["migration bid", st.depth_migration_bid], ["migration ask", st.depth_migration_ask], ["side asym", st.side_asymmetry, { kind: "sign", d: 3 }],
      ["OFI", st.ofi, { kind: "sign", d: 0 }], ["OFI window", st.ofi_window, { kind: "sign", d: 0 }],
      ["chg velocity", st.book_change_velocity], ["chg accel", st.book_change_acceleration],
      ["+bid / −bid", null, { text: `${f(st.depth_added_bid, 0)} / ${f(st.depth_removed_bid, 0)}` }], ["+ask / −ask", null, { text: `${f(st.depth_added_ask, 0)} / ${f(st.depth_removed_ask, 0)}` }],
    ]);
  }

  function renderFlow(st) {
    if (!st) { $("#flow-print").innerHTML = ""; $("#flow-gauges").innerHTML = ""; $("#flow-kv").innerHTML = ""; return; }
    const p = st.last_print;
    const pe = $("#flow-print");
    if (p) {
      const dir = isNum(p.direction) ? (p.direction > 0 ? "buy" : p.direction < 0 ? "sell" : "") : "";
      pe.className = "print " + dir;
      pe.innerHTML = `LAST PRINT ${esc(hms(p.t))} · ${f(p.price)} × ${f(p.qty, 0)} · dir ${f(p.direction, 0)} · aggr ${esc(p.aggressor || NA)} · ${esc(p.direction_rule || NA)}${p.inferred_from_delta ? " · from cumulative Δ" : ""}${p.trade_id ? " · #" + esc(p.trade_id) : ""}`;
    } else { pe.className = "print"; pe.textContent = "last print — (no individual prints in the sources)"; }
    $("#flow-gauges").innerHTML = gauge("direction", st.trade_flow_direction) + gauge("trade pressure", st.trade_pressure);
    kv($("#flow-kv"), [
      ["intensity /min", st.trade_intensity], ["acceleration", st.trade_acceleration, { kind: "sign" }],
      ["interval trades", st.interval_trades, { d: 0 }], ["interval volume", st.interval_volume, { d: 0 }], ["interval vwap", st.interval_vwap, { d: 3 }],
      ["signed flow win", st.signed_flow_window, { kind: "sign", d: 0 }],
      ["day trades", st.trade_count, { d: 0 }], ["day volume", st.trade_volume, { d: 0 }], ["day value", st.trade_value, { d: 0 }],
      ["tape source", st.tape_source], ["tape age s", st.tape_age_s, { d: 1 }],
    ]);
  }

  function renderPressure(st) {
    if (!st) { $("#pressure-gauges").innerHTML = ""; $("#pressure-kv").innerHTML = ""; return; }
    $("#pressure-gauges").innerHTML = gauge("book", st.book_pressure) + gauge("trade", st.trade_pressure) + gauge("combined", st.combined_pressure) +
      gauge("strength", st.pressure_strength, { bipolar: false }) + gauge("divergence", st.pressure_divergence, { color: "var(--warn)" });
    kv($("#pressure-kv"), [
      ["direction", st.pressure_direction, { kind: "sign", text: st.pressure_direction === null || st.pressure_direction === undefined ? NA : st.pressure_direction > 0 ? "+1 bid" : st.pressure_direction < 0 ? "−1 ask" : "0 balanced" }],
      ["persistence s", st.pressure_persistence_s, { d: 1 }], ["reversal", st.pressure_reversal, { kind: "flag" }],
      ["layer", (st.layer_states || {}).pressure], ["since", null, { text: hms((st.layer_since || {}).pressure) }],
    ]);
  }

  function renderLiquidity(st) {
    if (!st) { $("#liq-kv").innerHTML = ""; return; }
    kv($("#liq-kv"), [
      ["depletion", st.liquidity_depletion, { d: 3 }], ["replenishment", st.liquidity_replenishment, { d: 3 }], ["response", st.liquidity_response],
      ["retreat", st.liquidity_retreat, { kind: "flag" }], ["vacuum", st.liquidity_vacuum, { kind: "flag" }],
      ["resilience", st.resilience_state], ["recovery speed", st.recovery_speed], ["recovery asym", st.recovery_asymmetry, { kind: "sign" }],
      ["layer", (st.layer_states || {}).liquidity], ["since", null, { text: hms((st.layer_since || {}).liquidity) }],
    ]);
    const { ctx, w, h } = fitCanvas($("#recovery"));
    ctx.clearRect(0, 0, w, h);
    const curve = st.recovery_curve;
    ctx.font = "10px monospace"; ctx.fillStyle = "#7f8ea0";
    if (!curve || !curve.length) { ctx.fillText("recovery curve — (no shock in window)", 4, 12); return; }
    const xs = curve.map((p) => p[0]), ys = curve.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs, x0 + 1e-9), y0 = Math.min(0, ...ys), y1 = Math.max(1, ...ys);
    const X = (x) => 4 + ((x - x0) / (x1 - x0)) * (w - 8), Y = (y) => h - 4 - ((y - y0) / (y1 - y0)) * (h - 16);
    ctx.strokeStyle = "#22303f"; ctx.beginPath(); ctx.moveTo(4, Y(1)); ctx.lineTo(w - 4, Y(1)); ctx.stroke();
    ctx.strokeStyle = "#4dd0e1"; ctx.lineWidth = 1.5; ctx.beginPath();
    curve.forEach((p, i) => (i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]))));
    ctx.stroke(); ctx.lineWidth = 1;
    ctx.fillText(`${curve.length} pts · ${f(x1, 0)}s · last ${f(ys[ys.length - 1], 2)}`, 4, 10);
  }

  function renderResponse(st) {
    if (!st) { $("#response-kv").innerHTML = ""; return; }
    kv($("#response-kv"), [
      ["velocity t/min", st.price_velocity, { kind: "sign" }], ["acceleration", st.price_acceleration, { kind: "sign" }],
      ["impact t/flow", st.price_impact, { d: 5 }], ["price-only resp", st.price_only_response, { kind: "sign", d: 1 }],
      ["volume-only resp", st.volume_only_response, { d: 0 }], ["failed response", st.failed_response, { kind: "flag" }],
      ["signed flow win", st.signed_flow_window, { kind: "sign", d: 0 }],
    ]);
    // flow → impact relation from causal history (points at or before the cursor)
    const { ctx, w, h } = fitCanvas($("#flow-impact"));
    ctx.clearRect(0, 0, w, h);
    ctx.font = "10px monospace"; ctx.fillStyle = "#7f8ea0";
    const tcur = S.cur ? ms(S.cur.t) : null;
    const pts = ((S.history && S.history.points) || []).filter((p) => isNum(p.signed_flow_window) && isNum(p.price_only_response) && ms(p.t) <= tcur);
    if (!pts.length) { ctx.fillText("flow-impact — (signed flow or price response not observable)", 4, 12); return; }
    const xs = pts.map((p) => p.signed_flow_window), ys = pts.map((p) => p.price_only_response);
    const ax = Math.max(1e-9, ...xs.map(Math.abs)), ay = Math.max(1e-9, ...ys.map(Math.abs));
    const X = (x) => w / 2 + (x / ax) * (w / 2 - 6), Y = (y) => h / 2 - (y / ay) * (h / 2 - 6);
    ctx.strokeStyle = "#22303f"; ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.moveTo(w / 2, 0); ctx.lineTo(w / 2, h); ctx.stroke();
    pts.forEach((p, i) => { ctx.fillStyle = i === pts.length - 1 ? "#ffb74d" : "rgba(79,195,247,0.6)"; ctx.fillRect(X(p.signed_flow_window) - 1.5, Y(p.price_only_response) - 1.5, 3, 3); });
    ctx.fillStyle = "#7f8ea0"; ctx.fillText(`${pts.length} pts · |flow|≤${f(ax, 0)} · |resp|≤${f(ay, 1)}t`, 4, 10);
  }

  const MECH_RANK = { confirmed: 0, active: 1, building: 2, failed: 3, resolved: 4, inactive: 5 };
  function renderMech(st) {
    const tb = $("#mech-table tbody");
    if (!st) { tb.innerHTML = ""; return; }
    const all = Object.values(st.mechanisms || {});
    const nonInactive = all.filter((m) => m.state !== "inactive" || m.score > 0);
    const shown = (S.showAllMech || !nonInactive.length ? all : nonInactive)
      .sort((a, b) => (MECH_RANK[a.state] ?? 9) - (MECH_RANK[b.state] ?? 9) || b.score - a.score || a.name.localeCompare(b.name));
    $("#mech-sub").textContent = `${(st.active_mechanisms || []).length} active/confirmed · ${nonInactive.length} non-inactive · ${all.length} total` + (!nonInactive.length && all.length ? " (all inactive — showing all)" : "");
    const rows = [];
    for (const m of shown) {
      const ev = m.evidence || {};
      const brief = Object.entries(ev).filter(([k, v]) => k !== "peak_score" && (v === null || typeof v !== "object")).slice(0, 4)
        .map(([k, v]) => `${k}=${typeof v === "number" ? f(v, 3) : f(v)}`).join(" ");
      const missing = Array.isArray(ev.missing) && ev.missing.length ? ` <span class="pill warn">missing: ${esc(ev.missing.join(","))}</span>` : "";
      const open = S.expanded.has(m.name);
      rows.push(`<tr class="mech-row ${esc(m.state)}" data-name="${esc(m.name)}"><td>${esc(m.name)}</td><td>${esc(m.family)}</td>` +
        `<td><span class="scorebar"><i style="width:${Math.round(100 * Math.max(0, Math.min(1, m.score)))}%"></i></span>${f(m.score, 3)}</td>` +
        `<td class="state">${esc(m.state)}</td><td>${esc(hms(m.start_time))}</td><td>${f(m.duration_s, 0)}s</td>` +
        `<td class="ev" title="click to expand">${open ? "▾" : "▸"} ${esc(brief) || "(no scalar evidence)"}${missing}</td></tr>`);
      if (open) rows.push(`<tr class="mech-ev"><td colspan="7">evidence: ${esc(JSON.stringify(ev, null, 1))}\nbaseline: ${esc(JSON.stringify(m.baseline || {}, null, 1))}</td></tr>`);
    }
    tb.innerHTML = rows.join("");
  }

  function renderCircuit(st) {
    if (!st) { $("#circuit-band").innerHTML = ""; $("#circuit-kv").innerHTML = ""; return; }
    const c = st.circuit || {};
    const lockTxt = c.locked_up ? '<span class="lock">LOCKED UP</span>' : c.locked_down ? '<span class="lock">LOCKED DOWN</span>' : c.hit_up ? "HIT UP" : c.hit_down ? "HIT DOWN" : (c.locked_up === null || c.locked_up === undefined) ? NA : "free";
    $("#circuit-band").innerHTML = `<span>LOWER <b>${f(c.lower_limit)}</b> ◄ ${f(c.dist_down_ticks, 0)}t / ${f(c.dist_down_pct, 2)}%</span><span>${esc(c.price_basis || "price")} <b>${f(c.price)}</b> · ${lockTxt}</span><span>${f(c.dist_up_ticks, 0)}t / ${f(c.dist_up_pct, 2)}% ► UPPER <b>${f(c.upper_limit)}</b></span>`;
    kv($("#circuit-kv"), [
      ["rule source", c.rule_source, { text: `${c.rule_source || NA}${c.unverified ? " (unverified)" : ""}` }], ["band", c.band, { d: 4 }], ["yclose", c.yclose],
      ["nearer", c.nearer_limit], ["approach vel", c.approach_velocity, { kind: "sign" }], ["approach accel", c.approach_acceleration, { kind: "sign" }],
      ["first hit", c.first_hit_time, { text: `${hms(c.first_hit_time)} ${c.first_hit_side || ""}` }], ["first lock", null, { text: hms(c.first_lock_time) }],
      ["time locked s", c.time_locked_s, { d: 0 }], ["up / down s", null, { text: `${f(c.time_locked_up_s, 0)} / ${f(c.time_locked_down_s, 0)}` }],
      ["unlocks / relocks", null, { text: `${f(c.unlock_count, 0)} / ${f(c.relock_count, 0)}` }], ["unlock→relock s", c.time_between_unlock_relock_s, { d: 0 }],
      ["queue @upper", c.queue_at_upper, { d: 0 }], ["queue @lower", c.queue_at_lower, { d: 0 }], ["queue side", c.queue_side], ["queue Δ60s", c.queue_delta_60s, { kind: "sign", d: 0 }],
      ["queue growth", c.queue_growth], ["queue decay", c.queue_decay], ["queue persist s", c.queue_persistence_s, { d: 0 }],
      ["vol approaching", c.volume_approaching, { d: 0 }], ["vol while locked", c.volume_while_locked, { d: 0 }],
      ["shares to door", c.shares_to_door, { d: 0 }], ["door visible", c.door_visible], ["shares to floor", c.shares_to_floor, { d: 0 }], ["floor visible", c.floor_visible],
      ["streak up / down", null, { text: `${f(c.consecutive_upper_streak, 0)} / ${f(c.consecutive_lower_streak, 0)}` }],
      ["continuation str", c.streak_continuation_strength, { d: 3 }], ["weakening", c.streak_weakening, { kind: "flag" }], ["break day", c.break_day, { kind: "flag" }],
      ["break behaviour", c.break_behaviour], ["next session", c.next_session, { text: c.next_session ? JSON.stringify(c.next_session) : NA }],
      ["exception", c.exception, { kind: "flag" }], ["pressure before hit", c.pressure_before_hit, { d: 3 }],
      ["layer", (st.layer_states || {}).circuit], ["streak layer", (st.layer_states || {}).streak],
      ["auction phase", (st.auction || {}).phase], ["indicative", (st.auction || {}).indicative_price], ["auction pressure", (st.auction || {}).auction_pressure, { kind: "sign", d: 3 }],
    ]);
  }

  function renderCross() {
    const cr = S.cross;
    const tb = $("#cross-table tbody");
    if (!cr) { $("#cross-kv").innerHTML = ""; tb.innerHTML = ""; return; }
    const x = cr.cross || {}, sec = cr.sector || {};
    kv($("#cross-kv"), [
      ["breadth up/down/n", null, { text: `${f(x.breadth_up, 0)} / ${f(x.breadth_down, 0)} / ${f(x.breadth_n, 0)}` }], ["breadth net", x.breadth_net, { kind: "sign", d: 3 }], ["breadth age s", x.breadth_age_s, { d: 0 }],
      ["symbol ret 60s", x.symbol_return_60s, { kind: "sign", d: 5 }], ["market ret 60s", x.market_return_60s, { kind: "sign", d: 5 }], ["vs market", x.symbol_vs_market_60s, { kind: "sign", d: 5 }],
      ["syms with ret", x.n_symbols_with_return, { d: 0 }], ["lead/lag pairs", x.lead_lag_pairs_evaluated, { d: 0 }], ["basket sync", x.basket_sync, { d: 3 }],
      ["circuit cluster", x.circuit_cluster, { text: x.circuit_cluster ? JSON.stringify(x.circuit_cluster) : NA }],
      ["simult. liq change", x.simultaneous_liquidity_change, { text: x.simultaneous_liquidity_change === null || x.simultaneous_liquidity_change === undefined ? NA : typeof x.simultaneous_liquidity_change === "object" ? JSON.stringify(x.simultaneous_liquidity_change) : f(x.simultaneous_liquidity_change, 3) }],
      ["sync expansion", x.synchronized_expansion, { text: x.synchronized_expansion === null || x.synchronized_expansion === undefined ? NA : typeof x.synchronized_expansion === "object" ? JSON.stringify(x.synchronized_expansion) : f(x.synchronized_expansion, 3) }],
      ["sector", sec.sector, { text: `${sec.sector || NA}${sec.sector_source ? " (" + sec.sector_source + ")" : ""}` }], ["sector n", sec.n, { d: 0 }],
      ["sector ret 60s", sec.sector_return_60s, { kind: "sign", d: 5 }], ["peer ret 60s", sec.peer_return_60s, { kind: "sign", d: 5 }], ["vs sector", sec.symbol_vs_sector_60s, { kind: "sign", d: 5 }],
      ["sector pressure", sec.sector_pressure, { kind: "sign", d: 3 }], ["sector breadth", sec.sector_breadth, { kind: "sign", d: 3 }],
    ]);
    const rel = cr.related || [];
    tb.innerHTML = rel.length ? rel.map((r) => {
      const lock = r.locked_up ? "UP" : r.locked_down ? "DOWN" : (r.locked_up === null || r.locked_up === undefined) ? NA : "free";
      const pr = isNum(r.pressure_strength) ? `${r.pressure_direction > 0 ? "+" : r.pressure_direction < 0 ? "−" : "·"}${f(r.pressure_strength, 2)}` : NA;
      return `<tr class="cross-row"><td>${esc(r.symbol)}${r.present ? "" : " <span class=\"pill\">not in store</span>"}</td><td>${esc(r.roles.join(","))}</td><td>${f(r.lag_s, 0)}</td><td>${f(r.corr, 3)}</td><td>${f(r.mid, 3)}</td><td>${f(r.ltp)}</td><td>${esc(pr)}</td><td>${esc(lock)}</td></tr>`;
    }).join("") : `<tr><td colspan="8" class="na">no leaders / laggers / sector peers observable at this time</td></tr>`;
  }

  function renderSources(st) {
    const tb = $("#sources-table tbody");
    if (!st) { tb.innerHTML = ""; return; }
    const srcs = Object.values(st.sources || {}).sort((a, b) => a.source.localeCompare(b.source));
    tb.innerHTML = srcs.map((s) => {
      const agr = Object.entries(s.agreement || {}).map(([k, v]) => `<span class="pill ${v ? "ok" : "bad"}">${esc(k)}</span>`).join("") || NA;
      const dis = Object.entries(s.disagreement || {}).map(([k, v]) => `${esc(k)}: ${esc(f(v.this))} vs ${esc(f(v.other))} (${esc(v.other_source || "?")})`).join("; ") || NA;
      return `<tr class="src-row${s.stale ? " stale" : ""}" data-source="${esc(s.source)}"><td>${esc(s.source)}</td><td>${esc(hms(s.last_update))}</td><td class="${s.stale ? "bad" : ""}">${f(s.freshness_s, 1)}</td><td>${f(s.cadence_s, 1)}</td>` +
        `<td>${s.stale ? '<span class="pill bad">STALE</span>' : "no"}</td><td>${s.duplicate ? '<span class="pill warn">dup</span>' : "no"}</td><td>${f(s.updates, 0)}</td><td>${f(s.duplicates, 0)}</td><td>${f(s.gaps, 0)}</td>` +
        `<td>${agr}</td><td>${dis}</td><td title="${esc((s.field_coverage || []).join(", "))}">${(s.field_coverage || []).length}</td></tr>`;
    }).join("") || `<tr><td colspan="12" class="na">no sources observed</td></tr>`;
    const prov = Object.entries(st.provenance || {}).map(([k, v]) => `${k}←${v}`).join(" ");
    const agreeState = Object.entries(st.source_agreement || {}).map(([k, v]) => `${k}:${v ? "✓" : "✗"}`).join(" ");
    $("#sources-sub").textContent = `${srcs.length} sources · agreement ${agreeState || NA} · provenance ${prov || NA}`;
    const m = S.metrics && S.metrics.metrics;
    kv($("#metrics-kv"), m ? [
      ["events in", m.events_in, { d: 0 }], ["states out", m.states_out, { d: 0 }], ["ingest ev/s", m.ingest_rate_eps, { d: 1 }], ["proc st/s", m.processing_rate_sps, { d: 1 }],
      ["backlog", m.backlog, { d: 0 }], ["event lag s", m.last_event_lag_s, { d: 2 }], ["max lag s", m.max_event_lag_s, { d: 2 }],
      ["parse failures", m.parse_failures, { d: 0, kind: "flag" }], ["seq gaps", m.sequence_gaps, { d: 0 }], ["dup rate", m.duplicate_rate, { d: 4 }],
      ["stale sources", m.stale_sources, { text: (m.stale_sources || []).join(", ") || "none" }], ["recon failures", m.reconstruction_failures, { d: 0, kind: "flag" }],
      ["symbols", m.symbols, { d: 0 }], ["transitions", S.metrics.transitions, { d: 0 }],
    ] : [["engine metrics", null, { text: "metrics.json not available" }]]);
  }

  // ------------------------------------------------------------------ chart: mid / ltp with episode shading
  const CHART_LINES = [["mid", "#4fc3f7"], ["ltp", "#e0e6ee"], ["best_bid", "#26c281"], ["best_ask", "#ef5350"]];
  function renderChart() {
    const { ctx, w, h } = fitCanvas($("#chart"));
    ctx.clearRect(0, 0, w, h);
    ctx.font = "10px monospace";
    const H = S.history, R = S.range, tcur = S.cur ? ms(S.cur.t) : null;
    if (!H || !R || tcur === null) { ctx.fillStyle = "#7f8ea0"; ctx.fillText("no history", 6, 12); return; }
    const L = 48, Rm = 8, T = 6, B = 16;
    const t0 = R.first, t1 = Math.max(R.last, R.first + 1000);
    const X = (t) => L + ((t - t0) / (t1 - t0)) * (w - L - Rm);
    const pts = H.points.filter((p) => ms(p.t) <= tcur);
    const vals = [];
    for (const p of pts) for (const [k] of CHART_LINES) if (isNum(p[k])) vals.push(p[k]);
    // episode shading (causal: clipped at the cursor)
    for (const ep of H.episodes || []) {
      const a = ms(ep.start); if (a === null || a > tcur) continue;
      const b = Math.min(ep.end ? ms(ep.end) : tcur, tcur);
      const alpha = ep.peak_state === "confirmed" ? 0.22 : ep.peak_state === "active" ? 0.15 : 0.08;
      ctx.fillStyle = ep.outcome === "failed" ? `rgba(239,83,80,${alpha})` : `rgba(38,194,129,${alpha})`;
      ctx.fillRect(X(a), T, Math.max(1, X(b) - X(a)), h - T - B);
    }
    ctx.fillStyle = "#7f8ea0";
    ctx.fillText(hms(t0), L, h - 4); const lastLbl = hms(t1); ctx.fillText(lastLbl, w - Rm - lastLbl.length * 6, h - 4);
    if (!vals.length) {
      ctx.fillText("mid / ltp not observable up to the cursor", L + 6, 14);
    } else {
      let y0 = Math.min(...vals), y1 = Math.max(...vals);
      if (y1 - y0 < 1e-9) { y0 -= 0.5; y1 += 0.5; }
      const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
      const Y = (v) => T + (1 - (v - y0) / (y1 - y0)) * (h - T - B);
      ctx.strokeStyle = "#22303f";
      for (let i = 0; i <= 4; i++) { const v = y0 + (i / 4) * (y1 - y0); ctx.beginPath(); ctx.moveTo(L, Y(v)); ctx.lineTo(w - Rm, Y(v)); ctx.stroke(); ctx.fillText(f(v, 2), 2, Y(v) + 3); }
      for (const [k, color] of CHART_LINES) {
        ctx.strokeStyle = color; ctx.lineWidth = k === "mid" ? 1.6 : 1; ctx.beginPath(); let started = false;
        for (const p of pts) {
          if (!isNum(p[k])) { started = false; continue; }
          const x = X(ms(p.t)), y = Y(p[k]);
          if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true; }
        }
        ctx.stroke();
      }
      ctx.lineWidth = 1;
    }
    ctx.strokeStyle = "#ffb74d"; ctx.beginPath(); ctx.moveTo(X(tcur), T); ctx.lineTo(X(tcur), h - B); ctx.stroke();
    const nEp = (H.episodes || []).filter((e) => ms(e.start) <= tcur).length;
    $("#chart-sub").textContent = `${pts.length}/${H.n_total} states ≤ cursor${H.downsampled ? " (downsampled)" : ""} · ${nEp} mechanism episodes`;
    $("#chart-legend").innerHTML = CHART_LINES.map(([k, c]) => `<span><i style="background:${c}"></i>${k}</span>`).join("") +
      `<span><i style="background:rgba(38,194,129,0.5)"></i>episode</span><span><i style="background:rgba(239,83,80,0.5)"></i>failed episode</span><span><i style="background:#ffb74d"></i>cursor</span>`;
  }

  // ------------------------------------------------------------------ timeline lanes
  const LAYERS = ["pressure", "liquidity", "circuit", "accumulation", "streak"];
  function renderTimeline() {
    const c = $("#timeline");
    const TL = S.timeline, R = S.range, tcur = S.cur ? ms(S.cur.t) : null;
    const trs = (TL ? TL.transitions : []).filter((t) => ms(t.t) <= tcur);
    const layers = LAYERS.slice();
    for (const t of trs) if (!layers.includes(t.layer)) layers.push(t.layer);
    const laneH = 16, L = 150, T = 4, B = 14;
    c.setAttribute("height", String(T + layers.length * laneH + B));
    const { ctx, w, h } = fitCanvas(c);
    ctx.clearRect(0, 0, w, h);
    ctx.font = "10px monospace";
    S.tlSegments = [];
    if (!R || tcur === null) { ctx.fillStyle = "#7f8ea0"; ctx.fillText("no timeline", 6, 12); return; }
    const t0 = R.first, t1 = Math.max(R.last, R.first + 1000);
    const X = (t) => L + ((t - t0) / (t1 - t0)) * (w - L - 6);
    const byLayer = {};
    for (const t of trs) (byLayer[t.layer] = byLayer[t.layer] || []).push(t);
    layers.forEach((layer, i) => {
      const y = T + i * laneH;
      ctx.fillStyle = "#7f8ea0"; ctx.fillText(layer.length > 22 ? layer.slice(0, 21) + "…" : layer, 4, y + 11);
      ctx.fillStyle = "#0e151e"; ctx.fillRect(L, y + 2, w - L - 6, laneH - 4);
      const list = byLayer[layer] || [];
      const cur = S.cur && S.cur.state;
      const segs = [];
      if (list.length) {
        segs.push({ state: list[0].from_state, start: t0, end: ms(list[0].t), from: null, to: list[0].from_state, dur: null });
        list.forEach((t, j) => {
          const end = j + 1 < list.length ? ms(list[j + 1].t) : tcur;
          segs.push({ state: t.to_state, start: ms(t.t), end, from: t.from_state, to: t.to_state, dur: (end - ms(t.t)) / 1000, prev: t.duration_prev_s });
        });
      } else if (cur) {
        const st = layer.startsWith("mechanism:") ? ((cur.mechanisms || {})[layer.slice(10)] || {}).state : (cur.layer_states || {})[layer];
        if (st) segs.push({ state: st, start: t0, end: tcur, from: null, to: st, dur: (tcur - t0) / 1000 });
      }
      for (const sg of segs) {
        const x0 = X(sg.start), x1 = X(sg.end);
        ctx.fillStyle = stateColor(sg.state);
        ctx.fillRect(x0, y + 3, Math.max(1, x1 - x0), laneH - 6);
        S.tlSegments.push({ x0, x1, y0: y, y1: y + laneH, layer, ...sg });
      }
    });
    ctx.strokeStyle = "#ffb74d"; ctx.beginPath(); ctx.moveTo(X(tcur), T); ctx.lineTo(X(tcur), h - B); ctx.stroke();
    ctx.fillStyle = "#7f8ea0"; ctx.fillText(hms(t0), L, h - 3); const lb = hms(t1); ctx.fillText(lb, w - 6 - lb.length * 6, h - 3);
  }
  function timelineHover(ev) {
    const c = $("#timeline"), tip = $("#tl-tip");
    const r = c.getBoundingClientRect();
    const x = ev.clientX - r.left, y = ev.clientY - r.top;
    const sg = S.tlSegments.find((s) => x >= s.x0 && x <= Math.max(s.x1, s.x0 + 1) && y >= s.y0 && y < s.y1);
    if (!sg) { tip.hidden = true; return; }
    tip.hidden = false;
    tip.style.left = Math.min(x + 12, r.width - 260) + "px"; tip.style.top = (y + 12) + "px";
    const trans = sg.from ? `${sg.from} → ${sg.to}` : `${sg.to} (initial)`;
    tip.innerHTML = `<b>${esc(sg.layer)}</b><br>${esc(trans)}<br>since ${esc(hms(sg.start))} · duration ${sg.dur === null ? NA : f(sg.dur, 1) + "s"}${sg.prev !== undefined && sg.prev !== null ? `<br>previous state lasted ${f(sg.prev, 1)}s` : ""}`;
  }

  // ------------------------------------------------------------------ replay / live control
  function stopPlay() { S.playing = false; if (S.playTimer) { clearInterval(S.playTimer); S.playTimer = null; } renderReplay(); }
  function goReplay(t) { S.live = false; stopPoll(); S.virt = t; return showAt(t); }
  function sliderTime() {
    if (!S.range) return null;
    const v = parseInt($("#replay-slider").value, 10) / SLIDER_MAX;
    return S.range.first + v * (S.range.last - S.range.first);
  }
  function startPoll() {
    stopPoll();
    S.pollTimer = setInterval(async () => {
      if (!S.live || S.busy) return;
      try { await loadRange(); await loadSeries(); await loadMetrics(); await showAt(null); } catch (e) { setStatus("poll error: " + e.message, true); }
    }, POLL_MS);
  }
  function stopPoll() { if (S.pollTimer) { clearInterval(S.pollTimer); S.pollTimer = null; } }
  async function goLive() {
    stopPlay(); S.live = true;
    await loadRange(); await loadSeries(); await loadMetrics(); await showAt(null);
    startPoll();
  }
  function togglePlay() {
    if (S.playing) { stopPlay(); return; }
    if (!S.range) return;
    S.live = false; stopPoll();
    S.playing = true;
    if (S.virt === null || S.virt >= S.range.last) S.virt = S.cur && !S.cur.is_last ? ms(S.cur.t) : S.range.first;
    renderReplay();
    S.playTimer = setInterval(async () => {
      if (S.busy) return;
      const speed = parseFloat($("#speed").value) || 1;
      S.virt += speed * PLAY_TICK_MS;
      if (S.virt >= S.range.last) { S.virt = S.range.last; await showAt(S.virt); stopPlay(); return; }
      await showAt(S.virt);
    }, PLAY_TICK_MS);
  }

  async function setSymbol(sym) {
    S.symbol = sym; S.cur = null; S.cross = null; S.history = null; S.timeline = null; S.expanded.clear(); S.virt = null;
    location.hash = sym;
    stopPlay();
    await goLive();
  }

  // ------------------------------------------------------------------ wiring
  async function init() {
    try {
      const syms = await loadSymbols();
      const sel = $("#symbol");
      sel.addEventListener("change", () => setSymbol(sel.value));
      $("#btn-live").addEventListener("click", () => goLive());
      $("#btn-play").addEventListener("click", togglePlay);
      $("#btn-first").addEventListener("click", () => S.cur && goReplay(ms(S.cur.first_t)));
      $("#btn-last").addEventListener("click", () => S.cur && goReplay(ms(S.cur.last_t)));
      $("#btn-prev").addEventListener("click", () => S.cur && S.cur.prev_t && goReplay(ms(S.cur.prev_t)));
      $("#btn-next").addEventListener("click", () => S.cur && S.cur.next_t && goReplay(ms(S.cur.next_t)));
      $("#replay-slider").addEventListener("input", () => { stopPlay(); const t = sliderTime(); if (t !== null) goReplay(t); });
      $("#speed").addEventListener("change", () => { /* read on each tick */ });
      $("#mech-all").addEventListener("change", (e) => { S.showAllMech = e.target.checked; renderMech(S.cur && S.cur.state); });
      $("#mech-table").addEventListener("click", (e) => {
        const tr = e.target.closest("tr.mech-row"); if (!tr) return;
        const n = tr.dataset.name; if (S.expanded.has(n)) S.expanded.delete(n); else S.expanded.add(n);
        renderMech(S.cur && S.cur.state);
      });
      $("#timeline").addEventListener("mousemove", timelineHover);
      $("#timeline").addEventListener("mouseleave", () => { $("#tl-tip").hidden = true; });
      window.addEventListener("resize", () => { renderChart(); renderTimeline(); });
      setInterval(() => { if (S.live) loadSymbols().catch(() => {}); }, POLL_MS * 5);
      if (!syms.length) { setStatus("store has no symbol states yet", true); return; }
      const want = decodeURIComponent(location.hash.slice(1));
      const first = syms.some((s) => s.symbol === want) ? want : syms[0].symbol;
      sel.value = first;
      await setSymbol(first);
    } catch (e) { setStatus("init error: " + e.message, true); }
  }
  window.TOWER = { S, showAt, goReplay, goLive, render };
  document.addEventListener("DOMContentLoaded", init);
})();
