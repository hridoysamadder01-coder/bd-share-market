/* Read-only UI bridge for captured public/live layers. No signal logic here. */
'use strict';

/* Nothing is patched into the shell from here.

   This file used to reach into the shell's globals at load time: it spliced the
   invented "quiet accumulation" rule out of ATTENTION_RULES, overwrote
   FEATURES.accumulation_proxy's label, and wrapped renderEvents to swap one
   sentence. All three were stop-gaps written while the invented research layer
   was still on main.

   main now carries the real fix (PR #13): ATTENTION_RULES no longer exists,
   OBSERVATIONS replaced it, accumulation_proxy is labelled for what it measures,
   and the Engine Events provenance line is already correct. Two of those patches
   would now be dead code; the third would silently overwrite the stricter label
   with the weaker one, because this file loads after labels.js. So they are gone.

   This file renders captured layers and does nothing else. */

function clEsc(v) { return F.esc(v === null || v === undefined ? '' : String(v)); }
function clTruth(v) { return `<span class="truth ${clEsc(v || 'OBSERVED')}">${clEsc((v || 'OBSERVED').replace('OBSERVED','OBS'))}</span>`; }

function clDepth(d) {
  if (!d) return '<div class="empty">No captured depth for this stock.</div>';
  const n = Math.min(10, Math.max((d.bid_levels || []).length, (d.ask_levels || []).length));
  const body = Array.from({length:n}, (_,i) => {
    const b=(d.bid_levels||[])[i]||{}, a=(d.ask_levels||[])[i]||{};
    return `<tr><td class="r">${F.qty(b.volume)}</td><td class="r pos">${F.n(b.price)}</td><td class="r neg">${F.n(a.price)}</td><td class="r">${F.qty(a.volume)}</td></tr>`;
  }).join('');
  return `<div class="kv" style="margin-bottom:12px"><div><div class="k">BEST BID</div><div class="v">${F.n(d.best_bid)}</div></div><div><div class="k">BEST ASK</div><div class="v">${F.n(d.best_ask)}</div></div><div><div class="k">SPREAD</div><div class="v">${F.n(d.spread)}</div></div><div><div class="k">BUY / SELL</div><div class="v">${F.n(d.buy_pct,1)}% / ${F.n(d.sell_pct,1)}%</div></div></div>${n ? `<div class="scroll-x"><table class="tbl"><thead><tr><th class="r">BID QTY</th><th class="r">BID</th><th class="r">ASK</th><th class="r">ASK QTY</th></tr></thead><tbody>${body}</tbody></table></div>` : '<div class="empty">No displayed levels in the last capture.</div>'}`;
}

function clHistory(h) {
  if (!h || !h.available) return `<div class="empty">${clEsc(h && h.reason ? h.reason : 'No connected price history.')}</div>`;
  const rows=(h.rows||[]).slice(-12).reverse();
  if (!rows.length) return '<div class="empty">No rows for this symbol.</div>';
  const dateOf = r => r.trade_date || r.ts || r.date || r.trading_date || '';
  return `<div class="scroll-x"><table class="tbl"><thead><tr><th>DATE</th><th class="r">OPEN</th><th class="r">HIGH</th><th class="r">LOW</th><th class="r">CLOSE</th><th class="r">VOLUME</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${clEsc(String(dateOf(r)).slice(0,10))}</td><td class="r">${F.n(r.open)}</td><td class="r">${F.n(r.high)}</td><td class="r">${F.n(r.low)}</td><td class="r">${F.n(r.close===undefined?r.ltp:r.close)}</td><td class="r">${F.qty(r.volume===undefined?r.day_volume:r.volume)}</td></tr>`).join('')}</tbody></table></div>`;
}

function clEvents(e, p) {
  const rows=(e&&e.rows)||[];
  const list=rows.slice(0,12).map(x=>`<div class="ev-item"><span><span class="ev-label">${clEsc(x.type)}</span><br><span class="ev-say">${clEsc(x.date||'')}${F.has(x.value)?' · '+F.n(x.value)+'%':''}${F.has(x.quantity)?' · '+F.qty(x.quantity)+' shares':''}</span></span>${clTruth(x.truth)}</div>`).join('');
  const prof=p?`<div class="ev-item"><span class="ev-label">Listing ${F.int(p.listing_year)} · ${clEsc(p.operational_status||'')} · year end ${clEsc(p.year_end||'')}</span>${clTruth(p.truth)}</div>`:'';
  return list+prof || '<div class="empty">No connected symbol event record.</div>';
}

async function paintConnectedLayers() {
  if (!S || S.route !== 'stock' || !S.param) return;
  const wrap=document.querySelector('#view .wrap');
  if (!wrap || wrap.querySelector('[data-connected-layers]')) return;
  let d;
  try { d=await get('/api/connected/stock/'+encodeURIComponent(S.param), 15000); }
  catch(e) { return; }
  if (!document.querySelector('#view .wrap') || S.route !== 'stock') return;
  const sec=document.createElement('div'); sec.setAttribute('data-connected-layers','1');
  const m=d.market_context||{};
  sec.innerHTML=`
    <section class="sec"><div class="sec-head"><h2>MARKET CONTEXT</h2><span class="note">captured public market state</span></div><div class="card"><div class="kv"><div><div class="k">SESSION</div><div class="v">${clEsc(m.session_phase||F.dash)}</div></div><div><div class="k">ADV / DEC</div><div class="v">${F.int(m.advancing)} / ${F.int(m.declining)}</div></div><div><div class="k">TRADES</div><div class="v">${F.int(m.trades)}</div></div><div><div class="k">TURNOVER</div><div class="v">${F.tk(m.value)}</div></div></div></div></section>
    <section class="sec"><div class="sec-head"><h2>LIVE / LAST CAPTURED DEPTH</h2><span class="note">${d.depth&&d.depth.at?F.when(d.depth.at):'not captured'}</span></div><div class="card">${clDepth(d.depth)}</div></section>
    <section class="sec"><div class="sec-head"><h2>PRICE HISTORY</h2><span class="note">${d.history&&d.history.count?F.int(d.history.count)+' observed rows':''}</span></div><div class="card">${clHistory(d.history)}</div></section>
    <section class="sec"><div class="sec-head"><h2>COMPANY / MARKET EVENTS</h2><span class="note">observed records only</span></div><div class="card">${clEvents(d.events,d.company_profile)}</div></section>`;
  const evidence=[...wrap.querySelectorAll('.sec')].find(x=>x.querySelector('.sec-head h2')&&x.querySelector('.sec-head h2').textContent.trim()==='EVIDENCE');
  if (evidence) wrap.insertBefore(sec,evidence); else wrap.appendChild(sec);
}

/* Run after every existing render, without changing the shell's routing/data model. */
if (typeof render === 'function') {
  const _renderCore=render;
  render=async function(){ const x=await _renderCore.apply(this,arguments); await paintConnectedLayers(); return x; };
}
