"""Export a state store as ONE self-contained HTML page (no server, no CDN):
the Observation Tower for a phone or a shared link.

    python3 -m tower.ui.export_static --store results/tower/2026-09-06 --out tower_2026-09-06.html \
        [--symbols A,B] [--points 160]

The page embeds, per symbol, a downsampled history of trimmed states (book,
L1, imbalances, liquidity, pressure, tape, price response, resilience, circuit,
mechanisms, layer states, sources) plus the timeline and run metrics, and
renders every panel of the tower from that data with a replay scrubber. Every
number on the page comes from the store; a field the store does not carry is
shown as "—" (NOT_OBSERVABLE), never as 0.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Optional

from ..store import read_states, read_timeline

KEEP = ("t", "seq", "session_phase", "best_bid", "best_ask", "bid_qty1", "ask_qty1", "spread", "spread_ticks", "mid",
        "microprice", "ltp", "tick_size", "bids", "asks", "book_source", "book_age_s", "crossed", "locked", "one_sided",
        "empty_book", "imb_l1", "imb_topk", "imb_weighted", "visible_bid_liq", "visible_ask_liq", "depth_ratio",
        "depth_concentration_bid", "depth_concentration_ask", "depth_slope_bid", "depth_slope_ask", "wall_bid", "wall_ask",
        "depth_migration_bid", "depth_migration_ask", "book_change_velocity", "book_change_acceleration", "ofi",
        "ofi_window", "trade_count", "trade_volume", "trade_value", "interval_trades", "interval_volume", "interval_vwap",
        "trade_flow_direction", "trade_intensity", "trade_acceleration", "signed_flow_window", "last_print", "tape_source",
        "tape_age_s", "price_velocity", "price_acceleration", "price_impact", "price_only_response",
        "volume_only_response", "failed_response", "liquidity_response", "liquidity_depletion",
        "liquidity_replenishment", "liquidity_retreat", "liquidity_vacuum", "pressure_direction", "pressure_strength",
        "pressure_persistence_s", "pressure_reversal", "book_pressure", "trade_pressure", "combined_pressure",
        "pressure_divergence", "resilience_state", "recovery_speed", "recovery_asymmetry", "recovery_curve", "circuit",
        "auction", "cross", "sector", "source_agreement", "source_disagreement", "provenance", "active_mechanisms",
        "layer_states")
CIRCUIT_KEYS = ("upper_limit", "lower_limit", "rule_source", "dist_up_ticks", "dist_down_ticks", "dist_up_pct",
                "dist_down_pct", "approach_velocity", "approach_acceleration", "hit_up", "hit_down", "locked_up",
                "locked_down", "first_hit_time", "time_locked_s", "unlock_count", "relock_count", "queue_at_upper",
                "queue_at_lower", "queue_growth", "queue_persistence_s", "shares_to_door", "door_visible",
                "consecutive_upper_streak", "consecutive_lower_streak", "streak_continuation_strength",
                "streak_weakening", "break_day", "next_session", "exception", "volume_approaching", "volume_while_locked")


def _trim(s: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: s.get(k) for k in KEEP if k in s}
    c = s.get("circuit") or {}
    out["circuit"] = {k: c.get(k) for k in CIRCUIT_KEYS if k in c}
    # a missing score / duration stays None (never a silent 0.0) — the page renders it as "—"
    out["mechanisms"] = {n: {"score": (round(m["score"], 3) if isinstance(m.get("score"), (int, float)) else None),
                             "state": m.get("state"), "family": m.get("family"), "start_time": m.get("start_time"),
                             "duration_s": (round(m["duration_s"], 1) if isinstance(m.get("duration_s"), (int, float)) else None),
                             "evidence": {k: v for k, v in list((m.get("evidence") or {}).items())[:8]}}
                        for n, m in (s.get("mechanisms") or {}).items()}
    out["sources"] = {n: {"freshness_s": v.get("freshness_s"), "stale": v.get("stale"), "duplicate": v.get("duplicate"),
                          "updates": v.get("updates"), "duplicates": v.get("duplicates"), "gaps": v.get("gaps"),
                          "cadence_s": v.get("cadence_s")} for n, v in (s.get("sources") or {}).items()}
    if out.get("recovery_curve"):
        out["recovery_curve"] = out["recovery_curve"][:60]
    cross = s.get("cross") or {}
    out["cross"] = {k: cross.get(k) for k in ("breadth_up", "breadth_down", "breadth_n", "market_return_60s",
                                              "symbol_vs_market_60s", "leaders", "laggers", "basket_sync",
                                              "circuit_cluster", "simultaneous_liquidity_change", "synchronized_expansion")
                    if k in cross}
    return out


HISTORY_KEEP = ("t", "session_phase", "best_bid", "best_ask", "bid_qty1", "ask_qty1", "spread", "spread_ticks", "mid",
                "microprice", "ltp", "tick_size", "bids", "asks", "book_source", "book_age_s", "crossed", "locked",
                "one_sided", "empty_book", "imb_l1", "imb_topk", "imb_weighted", "visible_bid_liq", "visible_ask_liq",
                "depth_ratio", "wall_bid", "wall_ask", "ofi_window", "book_change_velocity", "trade_count",
                "trade_volume", "trade_value", "interval_trades", "interval_volume", "interval_vwap",
                "trade_flow_direction", "trade_intensity", "trade_acceleration", "signed_flow_window", "tape_source",
                "tape_age_s", "price_velocity", "price_acceleration", "price_impact", "price_only_response",
                "volume_only_response", "failed_response", "liquidity_depletion", "liquidity_replenishment",
                "liquidity_retreat", "liquidity_vacuum", "pressure_direction", "pressure_strength",
                "pressure_persistence_s", "pressure_reversal", "book_pressure", "trade_pressure", "combined_pressure",
                "pressure_divergence", "resilience_state", "recovery_speed", "recovery_asymmetry", "circuit",
                "active_mechanisms", "layer_states", "source_agreement", "provenance")


def _trim_history(s: Dict[str, Any]) -> Dict[str, Any]:
    """History points carry the scalar layers and mechanism score/state only; the full
    evidence, sources and cross context are embedded for the latest state alone."""
    full = _trim(s)
    out = {k: full.get(k) for k in HISTORY_KEEP if k in full}
    out["mechanisms"] = {n: {"score": m["score"], "state": m["state"], "family": m["family"]}
                         for n, m in full["mechanisms"].items() if (m["score"] or 0) > 0 or m["state"] != "inactive"}
    out["sources"] = {n: {"freshness_s": v.get("freshness_s"), "stale": v.get("stale"), "duplicate": v.get("duplicate")}
                      for n, v in full.get("sources", {}).items()}
    return out


def _downsample(rows: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    if len(rows) <= n:
        return rows
    idx = sorted({0, len(rows) - 1} | {int(i * (len(rows) - 1) / (n - 1)) for i in range(n)})
    return [rows[i] for i in idx]


def build_data(store: str, symbols: Optional[List[str]] = None, points: int = 160) -> Dict[str, Any]:
    names = sorted(f[:-6] for f in os.listdir(os.path.join(store, "states")) if f.endswith(".jsonl"))
    if symbols:
        names = [s for s in names if s in set(symbols)]
    data: Dict[str, Any] = {"symbols": {}, "timeline": [], "run": {}, "metrics": {}}
    for sym in names:
        rows = read_states(store, sym)
        if not rows:
            continue
        hist = [_trim_history(r) for r in _downsample(rows, points)]
        hist[-1] = _trim(rows[-1])          # the last point is the full latest state (evidence, sources, cross)
        data["symbols"][sym] = {"n_states": len(rows), "history": hist, "latest": hist[-1]}
    tl = read_timeline(store)
    data["timeline"] = [t for t in tl if t.get("symbol") in data["symbols"]][-3000:]
    for name in ("RUN.json", "metrics.json"):
        p = os.path.join(store, name)
        if os.path.exists(p):
            data["run" if name == "RUN.json" else "metrics"] = json.load(open(p))
    if "engine_metrics" in data["run"]:
        data["run"] = {k: data["run"][k] for k in ("capture", "events", "processed", "symbols", "t_from", "t_to",
                                                    "final_state_hash") if k in data["run"]}
    return data


def _finite(x: Any) -> Any:
    """Browsers reject NaN/Infinity tokens; a non-finite number is not an observation → null."""
    if isinstance(x, dict):
        return {k: _finite(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_finite(v) for v in x]
    if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
        return None
    return x


def render(data: Dict[str, Any], title: str = "DSE Observation Tower") -> str:
    payload = json.dumps(_finite(data), separators=(",", ":"), default=str, allow_nan=False).replace("</", "<\\/")
    return TEMPLATE.replace("__TITLE__", title).replace("__DATA__", payload)


TEMPLATE = r"""<title>__TITLE__</title>
<style>
:root{
  --bg:#f3f1ec; --bg2:#ffffff; --ink:#1c1f24; --ink2:#5b616b; --line:#d9d4ca; --line2:#ece8df;
  --accent:#4a6b8a; --accent-ink:#ffffff; --bid:#2f8a63; --ask:#c25a3a; --warn:#b88420; --crit:#b3372a; --ok:#2f8a63;
  --bidfill:rgba(47,138,99,.16); --askfill:rgba(194,90,58,.16); --chip:#e8e4db; --lane:#e2ded4;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --bg:#12161b; --bg2:#1a1f26; --ink:#e7e4dc; --ink2:#9aa1ab; --line:#2c333c; --line2:#232a32;
  --accent:#7ea3c4; --accent-ink:#0e1218; --bid:#4fb886; --ask:#e07a5a; --warn:#d9a441; --crit:#e0604f; --ok:#4fb886;
  --bidfill:rgba(79,184,134,.18); --askfill:rgba(224,122,90,.18); --chip:#242b33; --lane:#232a32; }}
:root[data-theme="dark"]{
  --bg:#12161b; --bg2:#1a1f26; --ink:#e7e4dc; --ink2:#9aa1ab; --line:#2c333c; --line2:#232a32;
  --accent:#7ea3c4; --accent-ink:#0e1218; --bid:#4fb886; --ask:#e07a5a; --warn:#d9a441; --crit:#e0604f; --ok:#4fb886;
  --bidfill:rgba(79,184,134,.18); --askfill:rgba(224,122,90,.18); --chip:#242b33; --lane:#232a32; }
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 "IBM Plex Sans",system-ui,-apple-system,Segoe UI,sans-serif;-webkit-text-size-adjust:100%}
.num,.mono{font-family:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;font-variant-numeric:tabular-nums}
h1,h2,h3,.sym{font-family:"IBM Plex Sans Condensed","IBM Plex Sans",system-ui,sans-serif;text-wrap:balance;margin:0}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 14px}
.hdr{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap}
h1{font-size:20px;font-weight:700;letter-spacing:.2px}
.meta{color:var(--ink2);font-size:12px}
.strip{display:flex;gap:8px;overflow-x:auto;padding:10px 0 2px;scrollbar-width:none}
.strip::-webkit-scrollbar{display:none}
.chip{flex:0 0 auto;display:flex;flex-direction:column;gap:2px;padding:6px 10px;border-radius:8px;background:var(--chip);border:1px solid transparent;cursor:pointer;min-width:92px}
.chip.on{border-color:var(--accent);background:var(--bg2)}
.chip .sym{font-size:14px;font-weight:600}
.chip .sub{font-size:11px;color:var(--ink2)}
.pill{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11px;font-weight:600;letter-spacing:.2px;text-transform:uppercase}
.pill.bid{background:var(--bidfill);color:var(--bid)} .pill.ask{background:var(--askfill);color:var(--ask)} .pill.flat{background:var(--chip);color:var(--ink2)}
main{padding:12px 14px 40px;display:grid;gap:12px;grid-template-columns:1fr}
@media(min-width:760px){main{grid-template-columns:1fr 1fr}}
@media(min-width:1180px){main{grid-template-columns:1fr 1fr 1fr}}
section{background:var(--bg2);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
section.wide{grid-column:1/-1}
h2{font-size:12px;font-weight:600;letter-spacing:.8px;text-transform:uppercase;color:var(--ink2);margin-bottom:8px;display:flex;justify-content:space-between;align-items:baseline}
h2 .r{font-family:"IBM Plex Mono",monospace;font-weight:400;text-transform:none;letter-spacing:0}
.kv{display:grid;grid-template-columns:1fr auto;gap:4px 12px;font-size:13px}
.kv div:nth-child(odd){color:var(--ink2)}
.kv div:nth-child(even){text-align:right}
.ladder{display:grid;grid-template-columns:1fr auto 1fr;gap:2px 8px;font-size:13px}
.ladder .row{display:contents}
.ladder .bar{position:relative;height:22px;display:flex;align-items:center;padding:0 6px;border-radius:4px}
.ladder .bar i{position:absolute;top:0;bottom:0;border-radius:4px}
.ladder .bid i{right:0;background:var(--bidfill)} .ladder .ask i{left:0;background:var(--askfill)}
.ladder .bid{justify-content:flex-end} .ladder .ask{justify-content:flex-start}
.ladder .px{text-align:center;min-width:64px;font-weight:500}
.ladder .best .px{color:var(--accent);font-weight:600}
.ladder .lvl span{position:relative}
.gauge{display:grid;grid-template-columns:auto 1fr auto;gap:6px 10px;align-items:center;font-size:13px}
.gauge .track{height:8px;background:var(--lane);border-radius:4px;position:relative;overflow:hidden}
.gauge .track i{position:absolute;top:0;bottom:0;left:50%;background:var(--accent)}
.gauge .track i.neg{background:var(--ask)} .gauge .track i.pos{background:var(--bid)}
.gauge .track::after{content:"";position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--line)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{text-align:left;color:var(--ink2);font-weight:500;padding:4px 6px;border-bottom:1px solid var(--line)}
td{padding:5px 6px;border-bottom:1px solid var(--line2);vertical-align:top}
td.num,th.num{text-align:right}
.bar2{height:6px;background:var(--lane);border-radius:3px;overflow:hidden;min-width:60px}
.bar2 i{display:block;height:100%;background:var(--accent)}
.st{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.3px}
.st.active,.st.confirmed{color:var(--bid)} .st.building{color:var(--warn)} .st.failed{color:var(--crit)} .st.resolved{color:var(--accent)} .st.inactive{color:var(--ink2)}
canvas{width:100%;height:110px;display:block}
.lanes{display:grid;gap:6px}
.lane{display:grid;grid-template-columns:96px 1fr;gap:8px;align-items:center;font-size:12px}
.lane .name{color:var(--ink2)}
.lane .track{position:relative;height:16px;background:var(--lane);border-radius:3px;overflow:hidden}
.lane .seg{position:absolute;top:0;bottom:0;border-right:1px solid var(--bg2)}
.lane .seg.s-balanced,.lane .seg.s-normal,.lane .seg.s-free,.lane .seg.s-none{background:transparent}
.lane .seg.s-pressure_building,.lane .seg.s-depletion,.lane .seg.s-approach,.lane .seg.s-accumulation_like,.lane .seg.s-streak{background:var(--warn);opacity:.7}
.lane .seg.s-expansion,.lane .seg.s-recovery,.lane .seg.s-continuation,.lane .seg.s-breakout{background:var(--bid);opacity:.8}
.lane .seg.s-rejection,.lane .seg.s-reversal,.lane .seg.s-no_recovery,.lane .seg.s-vacuum,.lane .seg.s-failed_pressure,.lane .seg.s-break,.lane .seg.s-weakening{background:var(--crit);opacity:.8}
.lane .seg.s-hit,.lane .seg.s-lock,.lane .seg.s-relock{background:var(--accent);opacity:.9}
.lane .seg.s-unlock{background:var(--accent);opacity:.45}
.replay{grid-column:1/-1;position:sticky;bottom:0;background:var(--bg2);border-top:1px solid var(--line);padding:10px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.replay input[type=range]{flex:1;min-width:160px}
button{font:inherit;padding:6px 12px;border-radius:6px;border:1px solid var(--line);background:var(--chip);color:var(--ink);cursor:pointer}
button:focus-visible,.chip:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.src{display:grid;grid-template-columns:1fr auto auto auto;gap:4px 10px;font-size:12.5px}
.src .h{color:var(--ink2)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:middle}
.dot.ok{background:var(--ok)} .dot.stale{background:var(--crit)} .dot.dup{background:var(--warn)}
.note{color:var(--ink2);font-size:12px;margin-top:6px}
.trans{font-size:12.5px;display:grid;gap:3px;max-height:220px;overflow:auto}
.trans .t{color:var(--ink2);font-family:"IBM Plex Mono",monospace;font-size:11.5px}
.ev{font-size:11.5px;color:var(--ink2);font-family:"IBM Plex Mono",monospace;white-space:normal;word-break:break-word}
details summary{cursor:pointer}
@media (prefers-reduced-motion: reduce){*{transition:none!important}}
</style>
<header>
  <div class="hdr">
    <h1>__TITLE__</h1>
    <div class="meta" id="hmeta"></div>
  </div>
  <div class="strip" id="strip"></div>
</header>
<main id="main"></main>
<div class="replay">
  <button id="play">Play</button><button id="stepb">Step</button>
  <input type="range" id="scrub" min="0" max="0" value="0" aria-label="replay position">
  <span class="mono" id="pos"></span>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
(function(){
const D = JSON.parse(document.getElementById('data').textContent);
const syms = Object.keys(D.symbols);
let cur = syms.find(s => (D.symbols[s].latest.bids||[]).length || (D.symbols[s].latest.asks||[]).length) || syms[0];
let idx = -1; let playing=false; let timer=null;
const $ = id => document.getElementById(id);
const dash = '\u2014';
const f = (v, d=2) => (v===null||v===undefined||Number.isNaN(v)) ? dash : (typeof v==='number' ? (Number.isInteger(v)? v.toLocaleString() : v.toFixed(d)) : String(v));
const fi = v => (v===null||v===undefined) ? dash : Math.round(v).toLocaleString();
const tt = iso => iso ? new Date(iso).toLocaleTimeString('en-GB',{timeZone:'Asia/Dhaka',hour:'2-digit',minute:'2-digit',second:'2-digit'}) : dash;
const dhaka = iso => iso ? tt(iso)+' Dhaka' : dash;
function hist(){ return D.symbols[cur].history; }
function state(){ const h=hist(); return idx<0 ? h[h.length-1] : h[Math.min(idx,h.length-1)]; }
function pressPill(s){ const d=s.pressure_direction; if(d===1) return '<span class="pill bid">bid</span>'; if(d===-1) return '<span class="pill ask">ask</span>'; return '<span class="pill flat">'+(d===0?'balanced':'\u2014')+'</span>'; }
function strip(){
  $('strip').innerHTML = syms.map(s=>{ const L=D.symbols[s].latest; const act=(L.active_mechanisms||[]).length;
    return `<div class="chip ${s===cur?'on':''}" tabindex="0" data-s="${s}"><span class="sym">${s}</span><span class="sub num">${L.ltp!=null&&L.ltp>0?f(L.ltp,1):dash} \u00b7 ${fi(L.trade_count)} tr</span><span class="sub">${pressPill(L)} ${act?act+' mech':''}</span></div>`; }).join('');
  document.querySelectorAll('.chip').forEach(c=>{ const go=()=>{cur=c.dataset.s; idx=-1; render();}; c.onclick=go; c.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();go();}}; });
}
function ladder(s){
  const asks=(s.asks||[]).slice(0,5).reverse(), bids=(s.bids||[]).slice(0,5);
  const mx=Math.max(1,...asks.map(x=>x[1]),...bids.map(x=>x[1]));
  const row=(px,q,side,best)=>`<div class="row ${best?'best':''}"><div class="bar bid">${side==='bid'?`<i style="width:${100*q/mx}%"></i><span class="num">${fi(q)}</span>`:''}</div><div class="px num">${f(px,1)}</div><div class="bar ask">${side==='ask'?`<i style="width:${100*q/mx}%"></i><span class="num">${fi(q)}</span>`:''}</div></div>`;
  let h='<div class="ladder"><div class="row"><div class="bar bid"><b>Bid qty</b></div><div class="px">Price</div><div class="bar ask"><b>Ask qty</b></div></div>';
  if(!asks.length&&!bids.length) h+='<div class="row"><div></div><div class="px">'+dash+'</div><div></div></div>';
  asks.forEach((x,i)=>h+=row(x[0],x[1],'ask',i===asks.length-1));
  bids.forEach((x,i)=>h+=row(x[0],x[1],'bid',i===0));
  h+='</div>';
  return h;
}
function kv(pairs){ return '<div class="kv">'+pairs.map(([k,v])=>`<div>${k}</div><div class="num">${v}</div>`).join('')+'</div>'; }
function gauge(rows){ return '<div class="gauge">'+rows.map(([k,v])=>{ const x=(v==null)?null:Math.max(-1,Math.min(1,v)); const w=x==null?0:Math.abs(x)*50; const st=x==null?'':(x>=0?`left:50%;width:${w}%`:`left:${50-w}%;width:${w}%`); return `<div>${k}</div><div class="track"><i class="${x==null?'':(x>=0?'pos':'neg')}" style="${st}"></i></div><div class="num">${f(v,2)}</div>`; }).join('')+'</div>'; }
function spark(id, series, color, ref){
  const c=$(id); if(!c) return; const dpr=window.devicePixelRatio||1; const W=c.clientWidth||300, H=110; c.width=W*dpr; c.height=H*dpr; const g=c.getContext('2d'); g.scale(dpr,dpr);
  const cs=getComputedStyle(document.documentElement); const line=cs.getPropertyValue('--line').trim(); const ink2=cs.getPropertyValue('--ink2').trim();
  g.clearRect(0,0,W,H); const pts=series.map((v,i)=>[i,v]).filter(p=>p[1]!=null&&!Number.isNaN(p[1]));
  g.strokeStyle=line; g.lineWidth=1; [0.25,0.5,0.75].forEach(k=>{g.beginPath();g.moveTo(0,H*k);g.lineTo(W,H*k);g.stroke();});
  if(pts.length<2){ g.fillStyle=ink2; g.font='12px IBM Plex Sans'; g.fillText('no observations yet', 8, H/2); return; }
  let lo=Math.min(...pts.map(p=>p[1])), hi=Math.max(...pts.map(p=>p[1])); if(hi===lo){hi+=1;lo-=1;}
  const X=i=>8+(W-16)*i/(series.length-1), Y=v=>H-10-(H-20)*(v-lo)/(hi-lo);
  if(ref!=null&&ref>=lo&&ref<=hi){ g.strokeStyle=ink2; g.setLineDash([3,3]); g.beginPath(); g.moveTo(0,Y(ref)); g.lineTo(W,Y(ref)); g.stroke(); g.setLineDash([]); }
  g.strokeStyle=color; g.lineWidth=1.6; g.beginPath(); pts.forEach((p,i)=>{ i?g.lineTo(X(p[0]),Y(p[1])):g.moveTo(X(p[0]),Y(p[1])); }); g.stroke();
  const last=pts[pts.length-1]; g.fillStyle=color; g.beginPath(); g.arc(X(last[0]),Y(last[1]),3,0,7); g.fill();
  if(idx>=0){ g.strokeStyle=ink2; g.beginPath(); g.moveTo(X(idx),0); g.lineTo(X(idx),H); g.stroke(); }
  g.fillStyle=ink2; g.font='11px IBM Plex Mono'; g.fillText(f(hi,2),4,12); g.fillText(f(lo,2),4,H-2);
}
function lanes(){
  const h=hist(); if(h.length<2) return '<div class="note">no timeline yet</div>';
  const layers=['pressure','liquidity','circuit','accumulation','streak'];
  return '<div class="lanes">'+layers.map(L=>{ let segs=''; let start=0; let st=(h[0].layer_states||{})[L]||'\u2014';
    for(let i=1;i<=h.length;i++){ const s=i<h.length?((h[i].layer_states||{})[L]||'\u2014'):null; if(s!==st||i===h.length){ segs+=`<div class="seg s-${st}" style="left:${100*start/h.length}%;width:${100*(i-start)/h.length}%" title="${st}"></div>`; start=i; st=s; } }
    const now=(state().layer_states||{})[L]||'\u2014';
    return `<div class="lane"><div class="name">${L}</div><div class="track">${segs}</div></div><div class="lane"><div></div><div class="st">${now}</div></div>`; }).join('')+'</div>';
}
function mechTable(s){
  const rows=Object.entries(s.mechanisms||{}).sort((a,b)=>(b[1].score??-1)-(a[1].score??-1));
  if(!rows.length) return '<div class="note">no mechanism readings in this state</div>';
  return '<div style="overflow-x:auto"><table><tr><th>mechanism</th><th>score</th><th>state</th><th class="num">dur s</th><th>evidence</th></tr>'+rows.slice(0,49).map(([n,m])=>`<tr><td>${n}<div class="ev">${m.family}</div></td><td><div class="bar2"><i style="width:${Math.round((m.score??0)*100)}%"></i></div><span class="num">${m.score==null?'\u2014':m.score.toFixed(2)}</span></td><td><span class="st ${m.state}">${m.state}</span></td><td class="num">${f(m.duration_s,0)}</td><td><details><summary class="ev">${Object.keys(m.evidence||{}).length} values</summary><div class="ev">${Object.entries(m.evidence||{}).map(([k,v])=>k+'='+(typeof v==='number'?f(v,3):JSON.stringify(v))).join(' \u00b7 ')}</div></details></td></tr>`).join('')+'</table></div>';
}
function transitions(){
  const rows=D.timeline.filter(t=>t.symbol===cur).slice(-40).reverse();
  if(!rows.length) return '<div class="note">no transitions yet</div>';
  return '<div class="trans">'+rows.map(t=>`<div><span class="t">${tt(t.t)}</span> ${t.layer.replace('mechanism:','\u2699 ')}: ${t.from_state} \u2192 <b>${t.to_state}</b> <span class="t">(${f(t.duration_prev_s,0)} s)</span></div>`).join('')+'</div>';
}
function sources(s){
  const rows=Object.entries(s.sources||{});
  if(!rows.length) return '<div class="note">no source status</div>';
  return '<div class="src"><div class="h">source</div><div class="h">age s</div><div class="h">upd/dup/gap</div><div class="h">state</div>'+rows.map(([n,v])=>`<div><span class="dot ${v.stale?'stale':(v.duplicate?'dup':'ok')}"></span>${n}</div><div class="num">${f(v.freshness_s,1)}</div><div class="num">${fi(v.updates)}/${fi(v.duplicates)}/${fi(v.gaps)}</div><div>${v.stale?'stale':(v.duplicate?'duplicate':'fresh')}</div>`).join('')+'</div>'+
    `<div class="note">book sensors agree: ${s.source_agreement&&s.source_agreement.book!=null?String(s.source_agreement.book):dash}${s.source_disagreement&&Object.keys(s.source_disagreement).length?' \u00b7 disagreement: '+JSON.stringify(s.source_disagreement).slice(0,200):''}</div>`;
}
function render(){
  strip();
  const s=state(); const c=s.circuit||{}; const h=hist();
  $('hmeta').textContent = `${dhaka(s.t)} \u00b7 ${s.session_phase||dash} \u00b7 ${D.symbols[cur].n_states} states \u00b7 ${Object.keys(D.symbols).length} symbols`;
  const wall = w => w? `${f(w.price,1)} \u00d7 ${fi(w.qty)} (${f((w.share||0)*100,0)}%, ${f(w.persistence_s,0)} s)` : dash;
  $('main').innerHTML = `
  <section><h2>Live book <span class="r">${s.book_source||dash} \u00b7 age ${f(s.book_age_s,1)} s</span></h2>${ladder(s)}
    ${kv([['Best bid / ask',`${f(s.best_bid,1)} / ${f(s.best_ask,1)}`],['Spread',`${f(s.spread,2)} (${f(s.spread_ticks,0)} ticks)`],['Mid / micro',`${f(s.mid,3)} / ${f(s.microprice,3)}`],['LTP',s.ltp>0?f(s.ltp,1):dash],['Visible liq bid / ask',`${fi(s.visible_bid_liq)} / ${fi(s.visible_ask_liq)}`],['Wall bid',wall(s.wall_bid)],['Wall ask',wall(s.wall_ask)],['Concentration bid / ask',`${f(s.depth_concentration_bid,2)} / ${f(s.depth_concentration_ask,2)}`],['Slope bid / ask',`${f(s.depth_slope_bid,1)} / ${f(s.depth_slope_ask,1)}`],['Migration bid / ask (ticks)',`${f(s.depth_migration_bid,2)} / ${f(s.depth_migration_ask,2)}`],['Book change vel / acc',`${f(s.book_change_velocity,1)} / ${f(s.book_change_acceleration,2)}`],['Flags',`${s.empty_book?'empty ':''}${s.one_sided?'one-sided ':''}${s.crossed?'crossed ':''}${s.locked?'locked':''}`||'two-sided']])}
  </section>
  <section><h2>Trade flow <span class="r">${s.tape_source||dash} \u00b7 age ${f(s.tape_age_s,1)} s</span></h2>
    ${kv([['Day trades / volume',`${fi(s.trade_count)} / ${fi(s.trade_volume)}`],['Day value (mn)',f(s.trade_value,3)],['Interval trades / volume',`${fi(s.interval_trades)} / ${fi(s.interval_volume)}`],['Interval VWAP',f(s.interval_vwap,2)],['Direction (-1..1)',f(s.trade_flow_direction,2)],['Intensity (trades/min)',f(s.trade_intensity,2)],['Acceleration',f(s.trade_acceleration,2)],['Signed flow (5 min)',fi(s.signed_flow_window)],['Last print',s.last_print?JSON.stringify(s.last_print):'not observable from this source']])}
    <canvas id="c_tr"></canvas><div class="note">day trades over the replay window</div>
  </section>
  <section><h2>Pressure</h2>${gauge([['Book',s.book_pressure],['Trade',s.trade_pressure],['Combined',s.combined_pressure],['Divergence',s.pressure_divergence],['Imb L1',s.imb_l1],['Imb top-5',s.imb_topk],['Imb weighted',s.imb_weighted],['OFI window',s.ofi_window!=null?Math.max(-1,Math.min(1,s.ofi_window/Math.max(1,(s.visible_bid_liq||0)+(s.visible_ask_liq||0)))):null]])}
    ${kv([['Direction',pressPill(s)],['Strength',f(s.pressure_strength,2)],['Persistence',`${f(s.pressure_persistence_s,0)} s`],['Reversal',s.pressure_reversal==null?dash:String(s.pressure_reversal)]])}
    <canvas id="c_imb"></canvas><div class="note">top-5 imbalance (dashed = 0)</div>
  </section>
  <section><h2>Liquidity & resilience</h2>${kv([['Depletion (120 s)',f(s.liquidity_depletion,2)],['Replenishment',f(s.liquidity_replenishment,2)],['Retreat',s.liquidity_retreat==null?dash:String(s.liquidity_retreat)],['Vacuum',s.liquidity_vacuum==null?dash:String(s.liquidity_vacuum)],['Liquidity response',f(s.liquidity_response,2)],['Resilience state',s.resilience_state||dash],['Recovery speed',f(s.recovery_speed,4)],['Recovery asymmetry',f(s.recovery_asymmetry,4)],['Depth ratio',f(s.depth_ratio,2)]])}
    <canvas id="c_liq"></canvas><div class="note">visible liquidity, bid (green) and ask (red)</div>
  </section>
  <section><h2>Price response</h2>${kv([['Velocity (ticks/min)',f(s.price_velocity,2)],['Acceleration',f(s.price_acceleration,3)],['Impact (ticks per unit flow)',f(s.price_impact,5)],['Price-only response (ticks)',f(s.price_only_response,1)],['Volume-only response',fi(s.volume_only_response)],['Failed response',s.failed_response==null?dash:String(s.failed_response)]])}
    <canvas id="c_mid"></canvas><div class="note">mid (or LTP where no two-sided book)</div>
  </section>
  <section><h2>Circuit <span class="r">${c.rule_source||dash}</span></h2>${kv([['Upper / lower limit',`${f(c.upper_limit,1)} / ${f(c.lower_limit,1)}`],['Distance up (ticks / %)',`${f(c.dist_up_ticks,0)} / ${f(c.dist_up_pct,2)}`],['Distance down (ticks / %)',`${f(c.dist_down_ticks,0)} / ${f(c.dist_down_pct,2)}`],['Approach vel / acc',`${f(c.approach_velocity,2)} / ${f(c.approach_acceleration,3)}`],['Hit up / down',`${c.hit_up==null?dash:c.hit_up} / ${c.hit_down==null?dash:c.hit_down}`],['Locked up / down',`${c.locked_up==null?dash:c.locked_up} / ${c.locked_down==null?dash:c.locked_down}`],['First hit',tt(c.first_hit_time)],['Time locked',`${f(c.time_locked_s,0)} s`],['Unlocks / relocks',`${fi(c.unlock_count)} / ${fi(c.relock_count)}`],['Queue at upper / lower',`${fi(c.queue_at_upper)} / ${fi(c.queue_at_lower)}`],['Shares to the door',`${fi(c.shares_to_door)}${c.door_visible===false?' (lower bound)':''}`],['Streak up / down',`${fi(c.consecutive_upper_streak)} / ${fi(c.consecutive_lower_streak)}`],['Continuation / weakening',`${f(c.streak_continuation_strength,2)} / ${c.streak_weakening==null?dash:c.streak_weakening}`],['Break day / next session',`${c.break_day==null?dash:c.break_day} / ${c.next_session||dash}`],['Exception',c.exception||'none']])}</section>
  <section class="wide"><h2>Active mechanics <span class="r">${(s.active_mechanisms||[]).length} active of ${Object.keys(s.mechanisms||{}).length}</span></h2>${mechTable(s)}</section>
  <section class="wide"><h2>State timeline</h2>${lanes()}<div style="height:8px"></div>${transitions()}</section>
  <section><h2>Cross-stock & sector</h2>${kv([['Breadth up / down / n',`${fi((s.cross||{}).breadth_up)} / ${fi((s.cross||{}).breadth_down)} / ${fi((s.cross||{}).breadth_n)}`],['Market return 60 s',f((s.cross||{}).market_return_60s,5)],['Symbol vs market 60 s',f((s.cross||{}).symbol_vs_market_60s,5)],['Leaders',((s.cross||{}).leaders||[]).map(x=>Array.isArray(x)?`${x[0]} (${x[1]} s, ${f(x[2],2)})`:JSON.stringify(x)).join(', ')||dash],['Laggers',((s.cross||{}).laggers||[]).map(x=>Array.isArray(x)?`${x[0]} (${x[1]} s, ${f(x[2],2)})`:JSON.stringify(x)).join(', ')||dash],['Basket sync',f((s.cross||{}).basket_sync,2)],['Circuit cluster',f((s.cross||{}).circuit_cluster,2)],['Sector',(s.sector||{}).sector||dash],['Sector return / breadth',`${f((s.sector||{}).sector_return_60s,5)} / ${f((s.sector||{}).sector_breadth,2)}`],['Sector pressure',f((s.sector||{}).sector_pressure,2)]])}</section>
  <section><h2>Source status</h2>${sources(s)}<div class="note">provenance: ${Object.entries(s.provenance||{}).map(([k,v])=>k+'\u2190'+v).join(', ')||dash}</div></section>
  <section><h2>Auction & session</h2>${kv([['Phase',s.session_phase||dash],['Auction',Object.keys(s.auction||{}).length?JSON.stringify(s.auction).slice(0,300):'no auction fields (DSE pre-open not applicable)']])}<div class="note">Run: ${D.run.events||dash} events from ${D.run.capture||dash}; determinism hash ${(D.run.final_state_hash||{})[cur]?String(D.run.final_state_hash[cur]).slice(0,12):dash}</div></section>`;
  const val=k=>h.map(x=>x[k]); spark('c_mid', h.map(x=>x.mid!=null?x.mid:(x.ltp>0?x.ltp:null)), getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(), null);
  spark('c_imb', val('imb_topk'), getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(), 0);
  spark('c_tr', val('trade_count'), getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(), null);
  const lc=$('c_liq'); if(lc){ spark('c_liq', val('visible_bid_liq'), getComputedStyle(document.documentElement).getPropertyValue('--bid').trim(), null); const g=lc.getContext('2d'); }
  $('scrub').max=h.length-1; $('scrub').value = idx<0? h.length-1 : idx; $('pos').textContent = `${(idx<0?h.length:idx+1)}/${h.length} \u00b7 ${tt(s.t)}`;
}
$('scrub').oninput=e=>{ idx=parseInt(e.target.value,10); if(idx>=hist().length-1) idx=-1; render(); };
$('stepb').onclick=()=>{ const h=hist(); idx = idx<0 ? 0 : Math.min(h.length-1, idx+1); render(); };
$('play').onclick=()=>{ playing=!playing; $('play').textContent=playing?'Pause':'Play'; if(playing){ if(idx<0) idx=0; timer=setInterval(()=>{ const h=hist(); idx++; if(idx>=h.length-1){ idx=-1; playing=false; $('play').textContent='Play'; clearInterval(timer);} render(); }, 400);} else clearInterval(timer); };
window.addEventListener('resize', render);
render();
})();
</script>
"""


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--symbols", default="")
    ap.add_argument("--points", type=int, default=160)
    ap.add_argument("--title", default="DSE Observation Tower")
    a = ap.parse_args(argv)
    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()] or None
    data = build_data(a.store, syms, a.points)
    html = render(data, a.title)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(a.out, len(html), "bytes", len(data["symbols"]), "symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
