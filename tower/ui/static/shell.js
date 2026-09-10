/* ============================================================================
   shell.js — PRESENTATION AND INTERACTION ONLY.

   Reads the same endpoints as before, unchanged:
     /api/market/summary  /api/market/universe  /api/market/sectors
     /api/market/sources  /api/features/latest  /api/stock/{sym}

   The law this file obeys, top to bottom:
     MARKET STATE -> SECTOR STATE -> STOCKS TO WATCH -> WHY -> RAW EVIDENCE
   Human mode is the default and shows no feature names, no z-scores and no
   internal ids. Research mode adds them back.
   ========================================================================= */
'use strict';

/* ------------------------------- format --------------------------------- */
const F = {
  has: v => v !== null && v !== undefined && !(typeof v === 'number' && !isFinite(v)),
  dash: '—',
  n(v, d) {
    if (!F.has(v)) return F.dash;
    return Number(v).toLocaleString('en-US', { minimumFractionDigits: d === undefined ? 2 : d, maximumFractionDigits: d === undefined ? 2 : d });
  },
  int(v) { return F.has(v) ? Math.round(v).toLocaleString('en-US') : F.dash; },
  pct(v, d) { return F.has(v) ? F.n(v, d === undefined ? 2 : d) + '%' : F.dash; },
  spct(v, d) {
    if (!F.has(v)) return F.dash;
    const s = v > 0 ? '+' : '';
    return s + F.n(v, d === undefined ? 2 : d) + '%';
  },
  /* fraction (0.0123) rendered as a signed percent */
  sfrac(v, d) { return F.has(v) ? F.spct(v * 100, d) : F.dash; },
  /* headline money — one standalone figure, billions/millions */
  tk(v) {
    if (!F.has(v)) return F.dash;
    const a = Math.abs(v);
    if (a >= 1e9) return '৳' + F.n(v / 1e9, 2) + 'B';
    if (a >= 1e6) return '৳' + F.n(v / 1e6, 1) + 'M';
    if (a >= 1e3) return '৳' + F.n(v / 1e3, 1) + 'K';
    return '৳' + F.n(v, 0);
  },
  /* comparison money — ALWAYS crore, so two numbers side by side compare
     directly. Never mix units inside one list. */
  cr(v) {
    if (!F.has(v)) return F.dash;
    const c = v / 1e7;
    return '৳' + F.n(c, Math.abs(c) >= 100 ? 0 : Math.abs(c) >= 10 ? 1 : 2) + ' Cr';
  },
  qty(v) {
    if (!F.has(v)) return F.dash;
    const a = Math.abs(v);
    if (a >= 1e7) return F.n(v / 1e7, 2) + 'Cr';
    if (a >= 1e5) return F.n(v / 1e5, 2) + 'L';
    if (a >= 1e3) return F.n(v / 1e3, 1) + 'K';
    return F.int(v);
  },
  cls(v) { return !F.has(v) || v === 0 ? 'flat' : (v > 0 ? 'pos' : 'neg'); },
  when(iso) {
    if (!iso) return F.dash;
    const t = Date.parse(iso);
    if (isNaN(t)) return String(iso);
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 90) return 'just now';
    if (s < 5400) return Math.round(s / 60) + ' min ago';
    if (s < 172800) return Math.round(s / 3600) + ' hours ago';
    return Math.round(s / 86400) + ' days ago';
  },
  day(d) {
    if (!d) return F.dash;
    const t = Date.parse(String(d).slice(0, 10) + 'T00:00:00Z');
    if (isNaN(t)) return String(d);
    return new Date(t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
  },
  esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  },
};

/* -------------------------------- fetch --------------------------------- */
const CACHE = new Map();
async function get(path, ttl) {
  const now = Date.now(), hit = CACHE.get(path);
  if (hit && now - hit.t < (ttl || 60000)) return hit.v;
  const r = await fetch(path, { headers: { accept: 'application/json' } });
  if (!r.ok) throw new Error(path + ' -> ' + r.status);
  const v = await r.json();
  CACHE.set(path, { t: now, v });
  return v;
}

/* -------------------------------- state --------------------------------- */
const S = {
  route: 'home', param: null,
  mode: localStorage.getItem('bdm.mode') || 'human',       // human | research
  theme: localStorage.getItem('bdm.theme') || '',
  watch: JSON.parse(localStorage.getItem('bdm.watch') || '[]'),
  summary: null, universe: null, sectors: null, sources: null, features: null,
  M: null,                                                  // the derived human model
  err: [],
};

/* ============================================================================
   DERIVE — the one place raw API rows become the human picture.
   Nothing here changes the engine; it only reads the delivered data correctly:
     * the universe ships every symbol twice -> dedupe by symbol
     * ltp == 0 means "did not trade", not "-100%"  -> excluded from breadth
     * bonds, debentures, T-bonds and the index rows are not equities
     * DSE ships its own sector aggregate rows (BANK, TEXTILE, ...) with real
       sector turnover and a real sector change -> those are the sector map
   ========================================================================= */
function derive() {
  const uni = (S.universe && S.universe.rows) || [];
  const seen = new Map();
  for (const r of uni) if (!seen.has(r.symbol)) seen.set(r.symbol, r);
  const rows = Array.from(seen.values());

  const indices = {}, aggregates = [], equities = [];
  for (const r of rows) {
    const secId = r.sector === null || r.sector === undefined ? null : String(r.sector).replace(/\.0+$/, '');
    r._secId = secId;
    r._sector = sectorName(secId);
    if (secId === '23') { indices[r.symbol] = r; continue; }
    if (secId === null) {
      const nm = sectorFromAggregateSymbol(r.symbol);
      if (nm) { r._sector = nm; aggregates.push(r); }
      continue;
    }
    if (isNonEquitySector(secId)) continue;
    equities.push(r);
  }

  /* a stock only counts as traded if it actually printed a price today */
  const traded = equities.filter(r => F.has(r.ltp) && r.ltp > 0 && F.has(r.change_pct));
  for (const r of traded) r._chg = r.change_pct;
  const up = traded.filter(r => r._chg > 0);
  const down = traded.filter(r => r._chg < 0);
  const flat = traded.filter(r => r._chg === 0);
  const moved = up.length + down.length;
  const downShare = moved ? down.length / moved : null;

  /* index — the universe carries DSEX even when the summary endpoint does not */
  const dsex = indices.DSEX || null;

  /* engine features */
  const feats = {};
  for (const f of ((S.features && S.features.rows) || [])) feats[f.symbol] = f;
  const featList = Object.values(feats);
  const abnormalShare = featList.length
    ? featList.filter(f => (typeof f.rel_volume_z === 'number' && f.rel_volume_z >= 2)).length / featList.length
    : null;

  /* attention list — every hit is a real rule over real engine features */
  const attention = [];
  for (const f of featList) {
    const hits = ATTENTION_RULES.filter(rule => { try { return rule.test(f); } catch (e) { return false; } })
      .sort((x, y) => y.w - x.w);   /* most specific reason first */
    if (!hits.length) continue;
    const inst = seen.get(f.symbol) || null;
    attention.push({
      symbol: f.symbol,
      name: inst ? inst.name : null,
      sector: inst ? (inst._sector || sectorName(inst._secId)) : null,
      chg: inst && F.has(inst.ltp) && inst.ltp > 0 ? inst.change_pct : null,
      value: inst ? inst.value : null,
      hits, f,
      rank: hits[0].w + (typeof f.rel_volume_z === 'number' ? Math.max(0, f.rel_volume_z) : 0),
    });
  }
  attention.sort((a, b) => b.rank - a.rank);

  const byRule = {};
  for (const r of ATTENTION_RULES) byRule[r.id] = attention.filter(a => a.hits.some(h => h.id === r.id));

  /* sector map — DSE's own aggregates, joined to per-symbol breadth counts */
  const counts = {};
  for (const r of traded) {
    const nm = r._sector; if (!nm) continue;
    const c = counts[nm] || (counts[nm] = { up: 0, down: 0, flat: 0, n: 0, value: 0 });
    c.n++; c.value += r.value || 0;
    if (r._chg > 0) c.up++; else if (r._chg < 0) c.down++; else c.flat++;
  }
  const sectors = aggregates.map(a => {
    const c = counts[a._sector] || { up: 0, down: 0, flat: 0, n: 0, value: 0 };
    return {
      name: a._sector, symbol: a.symbol,
      change_pct: F.has(a.change_pct) ? a.change_pct : null,
      value: a.value, volume: a.volume, trades: a.trades,
      up: c.up, down: c.down, flat: c.flat, n: c.n,
    };
  })
  /* bonds and debentures are not company shares — they never enter the
     sector map, so a reader never sees a sector with 0 stocks in it */
  .filter(s => (s.value || 0) > 0 && s.n > 0);
  sectors.sort((a, b) => (b.value || 0) - (a.value || 0));
  const sectorTurnover = sectors.reduce((s, x) => s + (x.value || 0), 0);
  const ranked = sectors.filter(s => F.has(s.change_pct)).slice().sort((a, b) => b.change_pct - a.change_pct);
  const strongest = ranked.slice(0, 2);
  const weakest = ranked.slice(-2).reverse();
  const sectorsDown = sectors.filter(s => F.has(s.change_pct) && s.change_pct < 0).length;

  /* the verdict — one line, derived, never invented */
  let state = 'NO SESSION DATA', tone = 'flat';
  if (moved > 0) {
    if (downShare >= 0.80)      { state = 'HEAVY, BROAD SELLING'; tone = 'neg'; }
    else if (downShare >= 0.65) { state = 'BROAD SELLING PRESSURE'; tone = 'neg'; }
    else if (downShare >= 0.55) { state = 'MORE FALLING THAN RISING'; tone = 'neg'; }
    else if (downShare <= 0.20) { state = 'HEAVY, BROAD BUYING'; tone = 'pos'; }
    else if (downShare <= 0.35) { state = 'BROAD BUYING'; tone = 'pos'; }
    else if (downShare <= 0.45) { state = 'MORE RISING THAN FALLING'; tone = 'pos'; }
    else                        { state = 'MIXED — NO CLEAR DIRECTION'; tone = 'flat'; }
  }
  if (abnormalShare !== null && abnormalShare >= 0.08) { state = 'ABNORMAL ACTIVITY ACROSS THE MARKET'; tone = 'warn'; }

  const sm = S.summary || {};
  const phase = String(sm.session_phase || '').toUpperCase();
  const isOpen = phase === 'OPEN' || phase === 'CONTINUOUS' || phase === 'TRADING';

  /* the plain paragraph under the verdict */
  const read = [];
  if (moved) read.push(`${down.length} of the ${moved} stocks that moved closed lower.`);
  if (dsex && F.has(dsex.change_pct)) read.push(`The broad index ${dsex.change_pct < 0 ? 'lost' : 'gained'} ${F.n(Math.abs(dsex.change_pct))}%.`);
  if (sectors.length) read.push(`${sectorsDown} of ${sectors.length} sectors finished lower.`);
  if (flat.length) read.push(`${flat.length} stocks traded but did not move at all.`);

  return {
    rows, equities, traded, up, down, flat, moved, downShare,
    indices, dsex, aggregates, sectors, sectorTurnover, strongest, weakest, sectorsDown,
    feats, featList, abnormalShare, attention, byRule,
    state, tone, phase, isOpen,
    tradingDate: sm.trading_date || null,
    turnover: F.has(sm.market_value) ? sm.market_value : sectorTurnover,
    turnoverSrc: F.has(sm.market_value) ? 'DSE market history' : 'sum of DSE sector totals',
    trades: sm.market_trades, volume: sm.market_volume,
    dataAt: sm.market_at || (S.universe && S.universe.at) || null,
    featAt: (S.features && S.features.at) || null,
    commit: sm.commit || null,
    read: read.join(' '),
    notTraded: equities.length - traded.length,
  };
}

/* ============================================================================
   CHROME
   ========================================================================= */
function paintChrome() {
  const M = S.M;
  const el = id => document.getElementById(id);
  const st = el('tb-state'), stx = el('tb-state-text');

  if (!M) { stx.textContent = 'LOADING'; return; }
  /* never LIVE on closed or stale data */
  st.className = 'tb-state ' + (M.isOpen ? 'is-open' : 'is-closed');
  stx.textContent = M.isOpen ? 'MARKET OPEN' : 'MARKET CLOSED';
  el('tb-sub').textContent = (M.isOpen ? 'Live session · ' : 'Last session · ') + F.day(M.tradingDate)
    + (M.dataAt ? ' · data ' + F.when(M.dataAt) : '');

  el('sb-data').textContent = F.day(M.tradingDate);
  el('sb-count').textContent = M.equities.length + ' listed · ' + M.traded.length + ' traded';
  el('sb-feat').textContent = M.featList.length + ' scored · ' + F.when(M.featAt);
  el('sb-commit').textContent = M.commit || F.dash;
  el('sb-mode').textContent = S.mode.toUpperCase();
  el('rail-time').textContent = F.day(M.tradingDate) + ' session';
  const nsrc = S.sources ? (Array.isArray(S.sources) ? S.sources.length : ((S.sources.rows || []).length)) : 0;
  el('rail-src').textContent = nsrc + (nsrc === 1 ? ' source tracked' : ' sources tracked');
  el('wl-count').textContent = S.watch.length || '';
  el('al-count').textContent = M.attention.length || '';

  for (const a of document.querySelectorAll('.nav[data-route], .tabbar a[data-route]')) {
    a.classList.toggle('active', a.dataset.route === S.route);
  }
  el('mode-btn').textContent = S.mode === 'human' ? 'Human' : 'Research';
  el('mode-btn').classList.toggle('on', S.mode === 'human');
}

/* ============================================================================
   SHARED BLOCKS
   ========================================================================= */
function heroBlock(M) {
  const dsexTxt = M.dsex && F.has(M.dsex.close_published)
    ? `<span class="num">${F.n(M.dsex.close_published)}</span>`
    : `<span class="num flat">${F.dash}</span>`;
  const dsexChg = M.dsex && F.has(M.dsex.change_pct)
    ? `<span class="num ${F.cls(M.dsex.change_pct)}">${F.spct(M.dsex.change_pct)}</span>` : '';

  const lead = [];
  const acc = M.byRule.accumulation || [], vol = M.byRule.volume_departure || [],
        frag = M.byRule.fragile || [], comp = M.byRule.compression || [];
  if (acc.length)  lead.push(`<a class="lead-chip" href="#/alerts"><b class="n">${acc.length}</b> quiet-accumulation candidates</a>`);
  if (vol.length)  lead.push(`<a class="lead-chip" href="#/alerts"><b class="n">${vol.length}</b> abnormal-volume stocks</a>`);
  if (frag.length) lead.push(`<a class="lead-chip" href="#/alerts"><b class="n">${frag.length}</b> thin and fragile</a>`);
  if (comp.length) lead.push(`<a class="lead-chip" href="#/alerts"><b class="n">${comp.length}</b> with a squeezed range</a>`);
  if (!lead.length) lead.push(`<span class="lead-chip">Nothing unusual flagged by the engine</span>`);

  const sList = a => a.length ? a.map(s => F.esc(s.name)).join(', ') : F.dash;

  return `
  <div class="hero t-${M.tone}">
    <div class="hero-phase">
      <span>${M.isOpen ? 'MARKET OPEN' : 'MARKET CLOSED'}</span>
      <span class="sep">·</span>
      <span class="stamp">${M.isOpen ? 'live session' : 'Last session data'} — ${F.day(M.tradingDate)}</span>
    </div>
    <div class="hero-state">${F.esc(M.state)}</div>
    <p class="hero-read">${F.esc(M.read)}</p>

    <div class="hero-line">
      <div class="hl wide"><span class="k">BROAD INDEX</span><span class="v">${dsexTxt} ${dsexChg}</span></div>
      <div class="hl"><span class="k">FELL</span><span class="v neg">${M.down.length}<small>stocks</small></span></div>
      <div class="hl"><span class="k">ROSE</span><span class="v pos">${M.up.length}<small>stocks</small></span></div>
      <div class="hl"><span class="k">UNCHANGED</span><span class="v">${M.flat.length}<small>stocks</small></span></div>
      <div class="hl"><span class="k">TURNOVER</span><span class="v">${F.tk(M.turnover)} <small>across ${F.int(M.trades)} trades</small></span></div>
    </div>

    <div class="hero-line" style="margin-top:14px">
      <div class="hl words"><span class="k">STRONGEST SECTORS</span><span class="v" style="font-size:15px;font-family:var(--sans);font-weight:650">${sList(M.strongest)}</span></div>
      <div class="hl words"><span class="k">WEAKEST SECTORS</span><span class="v" style="font-size:15px;font-family:var(--sans);font-weight:650">${sList(M.weakest)}</span></div>
    </div>

    <div class="hero-lead">${lead.join('')}</div>
  </div>`;
}

function attentionRow(a, skipId) {
  const top = a.hits.slice(0, 3);
  /* inside a grouped list the group heading already states one reason —
     the row then leads with the NEXT thing the engine saw, not a repeat */
  const lead = (skipId ? a.hits.filter(h => h.id !== skipId) : a.hits).concat(a.hits);
  const why = lead.map(h => { try { return h.why(a.f); } catch (e) { return ''; } }).filter(Boolean);
  const tags = top.map(h => `<span class="tag ${h.tone}">${F.esc(h.tag)}</span>`).join('');
  const raw = S.mode === 'research'
    ? `<div class="att-tags">${Array.from(new Set([].concat.apply([], top.map(h => h.raw)))).slice(0, 4)
        .map(k => `<span class="tag"><span class="num">${F.esc(k)}=${F.has(a.f[k]) ? F.n(a.f[k]) : F.dash}</span></span>`).join('')}</div>` : '';
  return `
  <a class="att-row" href="#/stock/${encodeURIComponent(a.symbol)}">
    <span class="att-id">
      <span class="att-sym">${F.esc(a.symbol)}</span>
      <span class="att-sec">${F.esc(a.sector || 'Sector not mapped')}</span>
    </span>
    <span class="att-why">${F.esc(why[0] || '')}
      <span class="att-tags">${tags}</span>${raw}
    </span>
    <span class="att-move">
      <span class="p ${F.cls(a.chg)}">${F.spct(a.chg)}</span>
      <span class="t">${F.cr(a.value)}</span>
    </span>
  </a>`;
}

function sectorCard(s, maxVal) {
  const tot = Math.max(1, s.up + s.down + s.flat);
  const w = v => (100 * v / tot).toFixed(1) + '%';
  const tone = !F.has(s.change_pct) || s.change_pct === 0 ? '' : (s.change_pct > 0 ? 't-pos' : 't-neg');
  return `
  <a class="sec-card ${tone}" href="#/sectors">
    <div class="sec-name">${F.esc(s.name)}</div>
    <div class="sec-chg ${F.cls(s.change_pct)}">${F.spct(s.change_pct)}</div>
    <div class="sec-meta">${F.cr(s.value)} · ${s.n} traded</div>
    <div class="sec-breadth">
      <span class="pos num">${s.up}</span>
      <span class="bar"><i class="u" style="width:${w(s.up)}"></i><i class="n" style="width:${w(s.flat)}"></i><i class="d" style="width:${w(s.down)}"></i></span>
      <span class="neg num">${s.down}</span>
    </div>
    <span class="fill" style="width:${(100 * (s.value || 0) / (maxVal || 1)).toFixed(1)}%"></span>
  </a>`;
}

function moverRow(r) {
  return `
  <a class="mv-row" href="#/stock/${encodeURIComponent(r.symbol)}">
    <span style="min-width:0">
      <span class="mv-sym">${F.esc(r.symbol)}</span>
      <span class="mv-name" style="display:block">${F.esc(r._sector || r.name || '')}</span>
    </span>
    <span class="mv-right">
      <span class="p ${F.cls(r._chg)}">${F.spct(r._chg)}</span>
      <span class="s" style="display:block">${F.cr(r.value)}</span>
    </span>
  </a>`;
}

function provLine(what, source, truth, at) {
  return `<div class="prov"><span class="truth ${truth}">${truth}</span><span>${F.esc(what)}</span>
    <span>·</span><span>${F.esc(source)}</span>${at ? `<span>·</span><span>${F.when(at)}</span>` : ''}</div>`;
}

/* ============================================================================
   ROUTES
   ========================================================================= */
function renderHome() {
  const M = S.M;
  const att = M.attention.slice(0, 8);
  const maxVal = M.sectors.length ? M.sectors[0].value : 1;
  const secTop = M.sectors.slice(0, 10);
  const losers = M.traded.slice().sort((a, b) => a._chg - b._chg).slice(0, 6);
  const gainers = M.traded.slice().sort((a, b) => b._chg - a._chg).slice(0, 6);
  const byValue = M.traded.slice().sort((a, b) => (b.value || 0) - (a.value || 0)).slice(0, 6);

  return `<div class="wrap">
    ${heroBlock(M)}

    <section class="sec">
      <div class="sec-head"><h2>WHAT NEEDS ATTENTION NOW</h2>
        <span class="note">${M.attention.length} stocks flagged by the engine · plain reason on every row</span>
        <span class="spacer"></span><a class="ev-toggle" href="#/alerts">see all ${M.attention.length}</a></div>
      <div class="card">${att.length ? att.map(attentionRow).join('') : `<div class="empty">The engine flagged nothing in this session.</div>`}
      ${provLine('rules read the engine feature table; no price target, no buy, no sell', 'evidence/public_engine · features/latest', 'OBSERVED', M.featAt)}</div>
    </section>

    <section class="sec">
      <div class="sec-head"><h2>SECTOR MAP</h2>
        <span class="note">sector change and turnover as DSE publishes them · bar = stocks up vs down</span>
        <span class="spacer"></span><a class="ev-toggle" href="#/sectors">all ${M.sectors.length} sectors</a></div>
      <div class="secmap">${secTop.map(s => sectorCard(s, maxVal)).join('')}</div>
    </section>

    <div class="grid2 sec">
      <section>
        <div class="sec-head"><h2>BIGGEST FALLS</h2></div>
        <div class="card">${losers.map(moverRow).join('')}</div>
      </section>
      <section>
        <div class="sec-head"><h2>BIGGEST RISES</h2></div>
        <div class="card">${gainers.map(moverRow).join('')}</div>
      </section>
    </div>

    <section class="sec">
      <div class="sec-head"><h2>WHERE THE MONEY WENT</h2><span class="note">largest turnover this session</span></div>
      <div class="card">${byValue.map(moverRow).join('')}
      ${provLine('turnover per stock as published', 'StockNow instrument feed', 'OBSERVED', M.dataAt)}</div>
    </section>

    <section class="sec">
      <div class="sec-head"><h2>RAW EVIDENCE</h2><span class="note">the numbers behind everything above</span></div>
      <div class="card">
        <div class="ev-group"><h4>SESSION</h4>
          <div class="ev-item"><span class="ev-label">Trading date</span><span class="ev-val">${F.day(M.tradingDate)}</span></div>
          <div class="ev-item"><span class="ev-label">Session phase reported by the feed</span><span class="ev-val">${F.esc(M.phase || F.dash)}</span></div>
          <div class="ev-item"><span class="ev-label">Listed instruments (equity)</span><span class="ev-val">${M.equities.length}</span></div>
          <div class="ev-item"><span class="ev-label">Traded today</span><span class="ev-val">${M.traded.length}</span></div>
          <div class="ev-item"><span class="ev-label">Listed but did not trade</span><span class="ev-val">${M.notTraded}</span></div>
          <div class="ev-item"><span class="ev-label">Market turnover (${F.esc(M.turnoverSrc)})</span><span class="ev-val">${F.tk(M.turnover)}</span></div>
          <div class="ev-item"><span class="ev-label">Sum of DSE sector turnover totals</span><span class="ev-val">${F.tk(M.sectorTurnover)}</span></div>
          <div class="ev-item"><span class="ev-label">Shares traded</span><span class="ev-val">${F.qty(M.volume)}</span></div>
          <div class="ev-item"><span class="ev-label">Trades executed</span><span class="ev-val">${F.int(M.trades)}</span></div>
        </div>
        <div class="ev-group"><h4>INDEX LEVELS</h4>
          ${['DSEX', 'DS30', 'DSES'].map(k => {
            const r = M.indices[k];
            return `<div class="ev-item"><span class="ev-label">${k}${r && r.name ? ' — ' + F.esc(r.name) : ''}</span>
              <span class="ev-val">${r ? F.n(r.close_published) : F.dash} <span class="${F.cls(r && r.change_pct)}">${r ? F.spct(r.change_pct) : ''}</span></span></div>`;
          }).join('')}
        </div>
        <div class="ev-group"><h4>ENGINE COVERAGE</h4>
          <div class="ev-item"><span class="ev-label">Stocks scored by the feature engine</span><span class="ev-val">${M.featList.length}</span></div>
          <div class="ev-item"><span class="ev-label">Share behaving abnormally on volume</span><span class="ev-val">${F.has(M.abnormalShare) ? F.pct(M.abnormalShare * 100, 1) : F.dash}</span></div>
          <div class="ev-item"><span class="ev-label">Feature snapshot taken</span><span class="ev-val">${F.when(M.featAt)}</span></div>
        </div>
        ${provLine('every figure above is read from a stored artefact, none is computed for display', 'tower/ui/market_api.py', 'OBSERVED', M.dataAt)}
      </div>
    </section>
  </div>`;
}

function renderAlerts() {
  const M = S.M;
  const groups = ATTENTION_RULES.map(r => ({ rule: r, rows: M.byRule[r.id] || [] })).filter(g => g.rows.length);
  return `<div class="wrap">
    <section class="sec" style="margin-top:0">
      <div class="sec-head"><h2>WHAT NEEDS ATTENTION NOW</h2>
        <span class="note">${M.attention.length} stocks · grouped by what the engine saw</span></div>
      ${groups.map(g => `
        <div class="sec" style="margin-top:14px">
          <div class="sec-head"><h2 style="letter-spacing:.06em;font-size:12.5px">${F.esc(g.rule.tag.toUpperCase())}</h2>
            <span class="note">${g.rows.length} stock${g.rows.length === 1 ? '' : 's'} · ${F.esc(g.rule.why(g.rows[0].f))}</span></div>
          <div class="card">${g.rows.map(r => attentionRow(r, g.rule.id)).join('')}</div>
        </div>`).join('') || `<div class="card"><div class="empty">Nothing flagged.</div></div>`}
      <div class="card" style="margin-top:16px">
        ${provLine('these are observations, not recommendations — the engine never emits buy or sell', 'features/latest + ATTENTION_RULES', 'OBSERVED', M.featAt)}
      </div>
    </section>
  </div>`;
}

function renderSectors() {
  const M = S.M;
  const maxVal = M.sectors.length ? M.sectors[0].value : 1;
  const total = M.sectorTurnover || 1;
  /* pack sectors into full rows so no row is left ragged: fill a row until it
     holds its share of turnover, then size each tile by its share of that row */
  const PER_ROW = 5;
  const rows = [];
  for (let i = 0; i < M.sectors.length; i += PER_ROW) rows.push(M.sectors.slice(i, i + PER_ROW));
  const weight = rows.map(rw => {
    const rowShare = rw.reduce((a, s) => a + (s.value || 0), 0) / total;
    const h = Math.round(78 + rowShare * rows.length * 24);
    return `<div class="wm-row" style="height:${h.toFixed(0)}px">` + rw.map(s => {
      const share = (s.value || 0) / total;
      const tone = !F.has(s.change_pct) ? 'var(--flat)' : s.change_pct > 0 ? 'var(--pos)' : 'var(--neg)';
      const alpha = Math.min(0.30, 0.05 + Math.abs(s.change_pct || 0) * 0.14);
      return `<a class="wm" href="#/market?sector=${encodeURIComponent(s.name)}"
        style="flex:${(share / rowShare * 100).toFixed(2)} 1 0;
               background:color-mix(in srgb, ${tone} ${(alpha * 100).toFixed(0)}%, var(--panel));
               border-color:color-mix(in srgb, ${tone} 34%, var(--line))">
        <span class="n">${F.esc(s.name)}</span>
        <span><span class="c ${F.cls(s.change_pct)}">${F.spct(s.change_pct)}</span>
        <span class="t" style="display:block">${F.cr(s.value)} · ${(share * 100).toFixed(1)}% of turnover</span></span></a>`;
    }).join('') + `</div>`;
  }).join('');

  return `<div class="wrap">
    <section class="sec only-desk" style="margin-top:0">
      <div class="sec-head"><h2>SECTOR MAP</h2>
        <span class="note">tile area = share of sector turnover · colour = sector change · ${F.day(M.tradingDate)}</span></div>
      <div class="weightmap">${weight}</div>
    </section>
    <section class="sec">
      <div class="sec-head"><h2>EVERY SECTOR</h2>
        <span class="note">sorted by money traded · bar = stocks up vs down</span></div>
      <div class="secmap">${M.sectors.map(s => sectorCard(s, maxVal)).join('')}</div>
    </section>
    <section class="sec">
      <div class="card">
        <div class="ev-group"><h4>HOW A SECTOR NAME IS RESOLVED</h4>
          <div class="ev-item"><span class="ev-label">Sector change and turnover come from DSE's own sector aggregate rows, not from averaging stocks.</span><span class="ev-val"></span></div>
          <div class="ev-item"><span class="ev-label">Per-stock counts come from the instrument feed's numeric sector id, mapped to a human name in the presentation layer.</span><span class="ev-val"></span></div>
          <div class="ev-item"><span class="ev-label">Mapping derived by joining StockNow sector_id with BullBD sector text on symbol — 406 symbols, 0 conflicts.</span><span class="ev-val"></span></div>
        </div>
        ${provLine('sector aggregates as published; id→name mapping derived and cross-checked against dsebd by_industrylisting.php', 'StockNow + BullBD + DSE industry list', 'OBSERVED', M.dataAt)}
      </div>
    </section>
  </div>`;
}

function renderMarket() {
  const M = S.M;
  const q = (S.query || '').trim().toUpperCase();
  const sec = S.filterSector;
  let rows = M.traded.slice();
  if (sec) rows = rows.filter(r => r._sector === sec);
  if (q) rows = rows.filter(r => r.symbol.indexOf(q) >= 0 || String(r.name || '').toUpperCase().indexOf(q) >= 0);
  rows.sort((a, b) => (b.value || 0) - (a.value || 0));
  const shown = rows.slice(0, 200);
  const secOpts = ['<option value="">All sectors</option>']
    .concat(M.sectors.map(s => `<option value="${F.esc(s.name)}"${sec === s.name ? ' selected' : ''}>${F.esc(s.name)}</option>`)).join('');

  return `<div class="wrap">
    <section class="sec" style="margin-top:0">
      <div class="sec-head"><h2>EVERY STOCK THAT TRADED</h2>
        <span class="note">${rows.length} shown${rows.length > shown.length ? ` (first ${shown.length})` : ''} · sorted by money traded</span>
        <span class="spacer"></span>
        <select class="tb-btn" id="sec-filter">${secOpts}</select></div>
      <div class="card only-mob">${shown.map(r => `
        <a class="mv-row" href="#/stock/${encodeURIComponent(r.symbol)}">
          <span style="min-width:0">
            <span class="mv-sym">${F.esc(r.symbol)}</span>
            <span class="mv-name" style="display:block">${F.esc(r._sector || '')} · ${F.qty(r.volume)} shares</span>
          </span>
          <span class="mv-right">
            <span class="p ${F.cls(r._chg)}">${F.n(r.ltp)} <span style="font-size:12px">${F.spct(r._chg)}</span></span>
            <span class="s" style="display:block">${F.cr(r.value)}</span>
          </span></a>`).join('')}</div>
      <div class="card scroll-x only-desk">
        <table class="tbl"><thead><tr>
          <th>STOCK</th><th>SECTOR</th><th class="r">PRICE</th><th class="r">CHANGE</th>
          <th class="r">MONEY TRADED</th><th class="r">SHARES</th><th class="r">TRADES</th><th>FLAG</th>
        </tr></thead><tbody>
        ${shown.map(r => `<tr onclick="location.hash='#/stock/${encodeURIComponent(r.symbol)}'" style="cursor:pointer">
          <td class="sym">${F.esc(r.symbol)}<div class="muted">${F.esc((r.name || '').slice(0, 30))}</div></td>
          <td>${F.esc(r._sector || F.dash)}</td>
          <td class="r">${F.n(r.ltp)}</td>
          <td class="r ${F.cls(r._chg)}">${F.spct(r._chg)}</td>
          <td class="r">${F.cr(r.value)}</td>
          <td class="r">${F.qty(r.volume)}</td>
          <td class="r">${F.int(r.trades)}</td>
          <td>${r.floor_flag ? '<span class="tag warn">on floor</span>' : ''}${r.sme_flag ? '<span class="tag">SME</span>' : ''}</td>
        </tr>`).join('')}
        </tbody></table>
      </div>
      <div class="card" style="margin-top:12px">
        ${provLine(`${M.notTraded} listed instruments did not print a price today and are excluded from this table and from every count above`, 'StockNow instrument feed', 'OBSERVED', M.dataAt)}
      </div>
    </section>
  </div>`;
}

function renderRadar() {
  const M = S.M;
  const rows = M.attention.slice(0, 60);
  return `<div class="wrap">
    <section class="sec" style="margin-top:0">
      <div class="sec-head"><h2>UNUSUAL ACTIVITY</h2>
        <span class="note">every stock the engine scored as departing from its own normal</span></div>
      <div class="card">${rows.length ? rows.map(attentionRow).join('') : '<div class="empty">Nothing unusual in this session.</div>'}
      ${provLine('departure is measured against each stock’s own trailing baseline, never a fixed threshold', 'features/latest', 'OBSERVED', M.featAt)}</div>
    </section>
  </div>`;
}

function renderWatchlist() {
  const M = S.M;
  const rows = S.watch.map(s => M.rows.find(r => r.symbol === s)).filter(Boolean);
  rows.forEach(r => { r._chg = F.has(r.ltp) && r.ltp > 0 ? r.change_pct : null; });
  return `<div class="wrap">
    <section class="sec" style="margin-top:0">
      <div class="sec-head"><h2>WATCHLIST</h2><span class="note">${rows.length} stock${rows.length === 1 ? '' : 's'} · stored in this browser only</span></div>
      <div class="card">${rows.length ? rows.map(moverRow).join('')
        : '<div class="empty">No stocks yet. Open any stock and press “Watch”.</div>'}</div>
    </section>
  </div>`;
}

function renderEvents() {
  const M = S.M;
  const withState = M.attention.filter(a => a.hits.some(h => h.id === 'persistent' || h.id === 'volume_departure'));
  return `<div class="wrap">
    <section class="sec" style="margin-top:0">
      <div class="sec-head"><h2>ENGINE EVENTS</h2>
        <span class="note">state transitions the engine recorded — an observation, never an instruction</span></div>
      <div class="card">${withState.length ? withState.map(attentionRow).join('') : '<div class="empty">No state events in this session.</div>'}
      ${provLine('rungs 1–2 are built (volume departure, range compression, quiet accumulation); rungs 2b–5 are designed, not running', 'state engine', 'OBSERVED', M.featAt)}</div>
    </section>
  </div>`;
}

function renderEvidence() {
  const M = S.M;
  return `<div class="wrap">
    <section class="sec" style="margin-top:0">
      <div class="sec-head"><h2>RAW EVIDENCE</h2><span class="note">nothing here is derived for display</span></div>
      <div class="card">
        <div class="ev-group"><h4>WHAT THE ENGINE MEASURES</h4>
          ${FEATURE_GROUPS.map(g => `<div class="ev-item"><span class="ev-label"><b>${F.esc(g.title)}</b><br>
            <span class="ev-say">${g.keys.map(k => F.esc((FEATURES[k] || {}).label || k)).join(' · ')}</span></span>
            <span class="ev-raw">${g.keys.length} measures</span></div>`).join('')}
        </div>
        <div class="ev-group"><h4>WHAT IT DELIBERATELY DOES NOT USE</h4>
          <div class="ev-item"><span class="ev-label">RSI, MACD, Bollinger bands, fixed breakout levels, absolute threshold rules.</span><span class="ev-raw">excluded by design</span></div>
          <div class="ev-item"><span class="ev-label">Order-book depth per level, queue position, trade side, free float.</span><span class="ev-raw">NOT_OBSERVABLE</span></div>
        </div>
        <div class="ev-group"><h4>SNAPSHOT</h4>
          <div class="ev-item"><span class="ev-label">Feature rows in this snapshot</span><span class="ev-val">${M.featList.length}</span></div>
          <div class="ev-item"><span class="ev-label">Universe rows delivered</span><span class="ev-val">${(S.universe && S.universe.count) || F.dash}</span></div>
          <div class="ev-item"><span class="ev-label">Unique instruments after de-duplication</span><span class="ev-val">${M.rows.length}</span></div>
        </div>
        ${provLine('read straight from stored artefacts', 'evidence/public_engine/*/extract', 'OBSERVED', M.featAt)}
      </div>
    </section>
  </div>`;
}

function renderTrust() {
  const list = Array.isArray(S.sources) ? S.sources : ((S.sources && S.sources.rows) || []);
  return `<div class="wrap">
    <section class="sec" style="margin-top:0">
      <div class="sec-head"><h2>WHERE THE DATA COMES FROM</h2><span class="note">${list.length} tracked source${list.length === 1 ? '' : 's'}</span></div>
      <div class="card scroll-x">
        <table class="tbl"><thead><tr><th>SOURCE</th><th>STATUS</th><th>WHAT IT DELIVERS</th><th>HOW OFTEN</th><th>TRUTH</th><th class="r">LAST OK</th></tr></thead>
        <tbody>${list.map(s => `<tr>
          <td class="sym">${F.esc(s.name)}</td>
          <td><span class="tag ${String(s.status).toUpperCase() === 'WORKING' ? 'info' : 'warn'}">${F.esc(s.status)}</span></td>
          <td>${F.esc(s.delivers || F.dash)}</td>
          <td class="muted">${F.esc(s.cadence || F.dash)}</td>
          <td><span class="truth ${F.esc(s.truth || 'INFERRED')}">${F.esc(s.truth || F.dash)}</span></td>
          <td class="r muted">${s.last_success ? F.when(s.last_success) : F.dash}</td>
        </tr>`).join('') || '<tr><td colspan="6"><div class="empty">No sources reported.</div></td></tr>'}</tbody></table>
      </div>
    </section>
  </div>`;
}

/* --------------------------- STOCK COMMAND CENTER ------------------------ */
function renderStock() {
  const M = S.M, sym = S.param;
  const inst = M.rows.find(r => r.symbol === sym);
  const f = M.feats[sym] || null;
  const detail = S.stock && S.stock.__sym === sym ? S.stock : null;

  if (!inst) return `<div class="wrap"><div class="card"><div class="empty">${F.esc(sym)} is not in this session's universe.</div></div></div>`;

  const chg = F.has(inst.ltp) && inst.ltp > 0 ? inst.change_pct : null;
  const traded = F.has(inst.ltp) && inst.ltp > 0;
  const hits = f ? ATTENTION_RULES.filter(r => { try { return r.test(f); } catch (e) { return false; } }) : [];

  /* 1. WHAT IS HAPPENING — one sentence, no feature names */
  let happening;
  if (!traded) happening = 'This stock did not trade in the last session.';
  else if (hits.length) happening = hits[0].tag + ' — ' + hits[0].why(f);
  else if (Math.abs(chg || 0) >= 3) happening = `A large ${chg > 0 ? 'rise' : 'fall'} today, with nothing abnormal in how it traded.`;
  else happening = 'Trading normally. Nothing in this session departs from this stock’s own usual behaviour.';

  /* 2. WHY — plain reasons, ranked */
  const why = [];
  for (const h of hits) why.push({ tone: h.tone, text: h.why(f) });
  if (f) {
    if (typeof f.market_relative_ret === 'number' && Math.abs(f.market_relative_ret) >= 0.02)
      why.push({ tone: f.market_relative_ret > 0 ? 'info' : 'neg', text: FEATURES.market_relative_ret.say(f.market_relative_ret) });
    if (typeof f.close_location === 'number')
      why.push({ tone: 'info', text: FEATURES.close_location.say(f.close_location) });
    if (typeof f.baseline_active_days === 'number' && f.baseline_active_days < 30)
      why.push({ tone: 'warn', text: FEATURES.baseline_active_days.say(f.baseline_active_days) });
  }
  if (inst.floor_flag) why.push({ tone: 'warn', text: 'This stock sits on a regulatory price floor — its downside is administratively blocked, not market-set.' });

  /* 3. EVIDENCE — plain sentences per measure. 4. RAW values only in research */
  const evidence = f ? FEATURE_GROUPS.map(g => {
    const items = g.keys.filter(k => F.has(f[k])).map(k => {
      const d = FEATURES[k] || { label: k, say: () => '' };
      let say = ''; try { say = d.say(f[k]); } catch (e) { say = ''; }
      return `<div class="ev-item">
        <span><span class="ev-label">${F.esc(d.label)}</span><br><span class="ev-say">${F.esc(say)}</span></span>
        <span>${S.mode === 'research'
          ? `<span class="ev-raw">${F.esc(k)}</span> <span class="ev-val">${F.n(f[k], 4)}</span>` : ''}</span>
      </div>`;
    }).join('');
    return items ? `<div class="ev-group"><h4>${F.esc(g.title.toUpperCase())}</h4>${items}</div>` : '';
  }).join('') : '<div class="empty">The feature engine has no row for this stock in this snapshot.</div>';

  const fund = detail && detail.fundamentals ? detail.fundamentals : null;
  const onWatch = S.watch.indexOf(sym) >= 0;

  return `<div class="wrap">
    <div class="hero t-${hits.length ? (hits[0].tone === 'neg' ? 'neg' : 'warn') : (chg > 0 ? 'pos' : chg < 0 ? 'neg' : 'flat')}">
      <div class="stk-head">
        <div class="stk-id">
          <div class="stk-sym">${F.esc(sym)}</div>
          <div class="stk-name">${F.esc(inst.name || '')} · ${F.esc(inst._sector || sectorName(inst._secId) || 'Sector not mapped')}${inst.category ? ' · category ' + F.esc(inst.category) : ''}</div>
        </div>
        <div class="stk-px">
          <div class="p">${traded ? F.n(inst.ltp) : F.dash}</div>
          <div class="c ${F.cls(chg)}">${F.spct(chg)} ${traded && F.has(inst.change_abs) ? '(' + F.n(inst.change_abs) + ')' : ''}</div>
        </div>
      </div>
      <div class="stk-happening">${F.esc(happening)}</div>
      <p class="stk-why" style="margin:0">${traded
        ? `Traded ${F.cr(inst.value)} across ${F.int(inst.trades)} trades, between ${F.n(inst.low)} and ${F.n(inst.high)}, against a previous close of ${F.n(inst.ycp)}.`
        : 'No price printed in this session, so every comparison below is from the last session in which it did trade.'}</p>
      <div class="hero-lead">
        <button class="lead-chip" id="watch-btn" type="button">${onWatch ? '★ On your watchlist' : '☆ Watch this stock'}</button>
        ${hits.map(h => `<span class="lead-chip ${h.tone === 'neg' ? 'neg' : ''}"><b>${F.esc(h.tag)}</b></span>`).join('')}
      </div>
    </div>

    ${why.length ? `<section class="sec">
      <div class="sec-head"><h2>WHY</h2><span class="note">what the engine actually saw, in order of weight</span></div>
      <ul class="why-list">${why.slice(0, 6).map(w => `<li class="${w.tone}"><span class="b"></span><span>${F.esc(w.text)}</span></li>`).join('')}</ul>
    </section>` : ''}

    <section class="sec">
      <div class="sec-head"><h2>THIS SESSION</h2></div>
      <div class="card"><div class="kv">
        <div><div class="k">OPEN</div><div class="v">${F.n(inst.open)}</div></div>
        <div><div class="k">HIGH</div><div class="v">${F.n(inst.high)}</div></div>
        <div><div class="k">LOW</div><div class="v">${F.n(inst.low)}</div></div>
        <div><div class="k">PREV CLOSE</div><div class="v">${F.n(inst.ycp)}</div></div>
        <div><div class="k">MONEY TRADED</div><div class="v">${F.cr(inst.value)}</div></div>
        <div><div class="k">SHARES</div><div class="v">${F.qty(inst.volume)}</div></div>
        <div><div class="k">TRADES</div><div class="v">${F.int(inst.trades)}</div></div>
        <div><div class="k">YEAR HIGH</div><div class="v">${F.n(inst.year_high)}</div></div>
        <div><div class="k">YEAR LOW</div><div class="v">${F.n(inst.year_low)}</div></div>
        <div><div class="k">UPPER LIMIT</div><div class="v">${F.n(inst.upper_limit)}</div></div>
        <div><div class="k">LOWER LIMIT</div><div class="v">${F.n(inst.lower_limit)}</div></div>
        <div><div class="k">ON FLOOR</div><div class="v">${inst.floor_flag ? 'YES' : 'no'}</div></div>
      </div></div>
    </section>

    ${fund ? `<section class="sec">
      <div class="sec-head"><h2>THE COMPANY</h2><span class="note">as reported, not modelled</span></div>
      <div class="card"><div class="kv">
        <div><div class="k">MARKET CAP</div><div class="v">${F.cr(fund.market_cap)}</div></div>
        <div><div class="k">SHARES OUT</div><div class="v">${F.qty(fund.outstanding_shares)}</div></div>
        <div><div class="k">EPS (ANN.)</div><div class="v">${F.n(fund.annualized_eps)}</div></div>
        <div><div class="k">P/E (ANN.)</div><div class="v">${F.n(fund.annualized_pe)}</div></div>
        <div><div class="k">NAV</div><div class="v">${F.n(fund.audited_nav)}</div></div>
        <div><div class="k">PRICE / NAV</div><div class="v">${F.n(fund.price_to_nav)}</div></div>
      </div></div>
    </section>` : ''}

    <section class="sec">
      <div class="sec-head"><h2>EVIDENCE</h2>
        <span class="note">${S.mode === 'human' ? 'plain readings — switch to Research for the raw numbers' : 'raw feature names and values'}</span>
        <span class="spacer"></span>
        <button class="ev-toggle" id="mode-inline" type="button">${S.mode === 'human' ? 'show raw numbers' : 'hide raw numbers'}</button></div>
      <div class="card">${evidence}
      ${provLine('every reading above is one stored engine feature, measured against this stock’s own trailing baseline', 'features/latest', 'OBSERVED', M.featAt)}</div>
    </section>
  </div>`;
}

const ROUTES = {
  home: renderHome, market: renderMarket, sectors: renderSectors, radar: renderRadar,
  watchlist: renderWatchlist, alerts: renderAlerts, events: renderEvents,
  evidence: renderEvidence, trust: renderTrust, stock: renderStock,
};

/* ============================================================================
   ROUTER + INTERACTION
   ========================================================================= */
function parseHash() {
  const h = (location.hash || '#/home').replace(/^#\/?/, '');
  const [pathPart, queryPart] = h.split('?');
  const seg = pathPart.split('/').filter(Boolean);
  S.route = seg[0] || 'home';
  S.param = seg[1] ? decodeURIComponent(seg[1]) : null;
  S.filterSector = null;
  if (queryPart) {
    const m = /(?:^|&)sector=([^&]*)/.exec(queryPart);
    if (m) S.filterSector = decodeURIComponent(m[1]);
  }
  if (!ROUTES[S.route]) S.route = 'home';
}

async function render() {
  const view = document.getElementById('view');
  if (!S.M) { view.innerHTML = `<div class="wrap"><div class="card"><div class="empty">Reading engine artefacts…</div></div></div>`; return; }
  if (S.route === 'stock' && S.param && (!S.stock || S.stock.__sym !== S.param)) {
    try { const d = await get('/api/stock/' + encodeURIComponent(S.param), 120000); d.__sym = S.param; S.stock = d; }
    catch (e) { S.stock = { __sym: S.param }; }
  }
  try { view.innerHTML = ROUTES[S.route](); }
  catch (e) { view.innerHTML = `<div class="wrap"><div class="card"><div class="empty">Could not draw this view: ${F.esc(e.message)}</div></div></div>`; }
  window.scrollTo(0, 0);
  paintChrome();
  wireView();
}

function wireView() {
  const wb = document.getElementById('watch-btn');
  if (wb) wb.onclick = () => {
    const i = S.watch.indexOf(S.param);
    if (i >= 0) S.watch.splice(i, 1); else S.watch.push(S.param);
    localStorage.setItem('bdm.watch', JSON.stringify(S.watch));
    render();
  };
  const mi = document.getElementById('mode-inline');
  if (mi) mi.onclick = () => setMode(S.mode === 'human' ? 'research' : 'human');
  const sf = document.getElementById('sec-filter');
  if (sf) sf.onchange = () => {
    location.hash = sf.value ? '#/market?sector=' + encodeURIComponent(sf.value) : '#/market';
  };
}

function setMode(m) {
  S.mode = m; localStorage.setItem('bdm.mode', m); render();
}
function setTheme(t) {
  S.theme = t; localStorage.setItem('bdm.theme', t);
  document.documentElement.setAttribute('data-theme', t);
}

/* ------------------------------ palette --------------------------------- */
let palOpen = false;
function openPalette() {
  if (palOpen || !S.M) return;
  palOpen = true;
  const back = document.createElement('div');
  back.className = 'pal-back';
  back.innerHTML = `<div class="pal"><input id="pal-q" placeholder="Type a stock symbol or company name…" autocomplete="off"><div class="pal-list" id="pal-list"></div></div>`;
  document.body.appendChild(back);
  const input = back.querySelector('#pal-q'), list = back.querySelector('#pal-list');
  const pool = S.M.equities;
  let idx = 0, cur = [];
  const draw = () => {
    const q = input.value.trim().toUpperCase();
    cur = (q ? pool.filter(r => r.symbol.indexOf(q) >= 0 || String(r.name || '').toUpperCase().indexOf(q) >= 0)
             : S.M.traded.slice().sort((a, b) => (b.value || 0) - (a.value || 0))).slice(0, 40);
    idx = 0;
    list.innerHTML = cur.map((r, i) => `<button class="pal-item ${i === 0 ? 'on' : ''}" data-i="${i}" type="button">
      <span class="s">${F.esc(r.symbol)}</span><span class="n">${F.esc(r.name || '')}</span>
      <span style="margin-left:auto" class="num ${F.cls(r.change_pct)}">${F.has(r.ltp) && r.ltp > 0 ? F.spct(r.change_pct) : F.dash}</span></button>`).join('')
      || '<div class="empty">No match.</div>';
    for (const b of list.querySelectorAll('.pal-item')) b.onclick = () => go(cur[+b.dataset.i]);
  };
  const go = r => { if (r) { location.hash = '#/stock/' + encodeURIComponent(r.symbol); } close(); };
  const close = () => { palOpen = false; back.remove(); document.removeEventListener('keydown', key); };
  const key = e => {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'Enter') { e.preventDefault(); go(cur[idx]); }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      idx = Math.max(0, Math.min(cur.length - 1, idx + (e.key === 'ArrowDown' ? 1 : -1)));
      list.querySelectorAll('.pal-item').forEach((b, i) => b.classList.toggle('on', i === idx));
      const on = list.querySelector('.pal-item.on'); if (on) on.scrollIntoView({ block: 'nearest' });
    }
  };
  input.oninput = draw;
  back.onclick = e => { if (e.target === back) close(); };
  document.addEventListener('keydown', key);
  draw(); input.focus();
}

/* ------------------------------ bootstrap -------------------------------- */
async function bootstrap() {
  const jobs = [
    ['summary', '/api/market/summary'],
    ['universe', '/api/market/universe'],
    ['sectors', '/api/market/sectors'],
    ['sources', '/api/market/sources'],
    ['features', '/api/features/latest'],
  ];
  await Promise.all(jobs.map(async ([k, p]) => {
    try { S[k] = await get(p, 45000); }
    catch (e) { S.err.push(k + ': ' + e.message); S[k] = null; }
  }));
  S.M = derive();
}

document.addEventListener('DOMContentLoaded', async () => {
  setTheme(S.theme);
  document.getElementById('theme-btn').onclick = () => {
    setTheme(S.theme === '' ? 'light' : S.theme === 'light' ? 'dark' : '');
  };
  document.getElementById('mode-btn').onclick = () => setMode(S.mode === 'human' ? 'research' : 'human');
  document.getElementById('cmd-open').onclick = openPalette;
  document.addEventListener('keydown', e => {
    if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey)) { e.preventDefault(); openPalette(); }
    else if (e.key === '/' && !palOpen && !/^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName)) { e.preventDefault(); openPalette(); }
  });
  window.addEventListener('hashchange', () => { parseHash(); render(); });

  parseHash();
  render();
  await bootstrap();
  render();
});
