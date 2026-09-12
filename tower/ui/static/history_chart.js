/* Read-only OHLC history surface. Every candle comes from one stored row. */
'use strict';

const HIST = { sym: null, data: null, i0: 0, i1: 0, pick: null, geo: null };

function hx(v) { return F.esc(v === null || v === undefined ? '' : String(v)); }
function hnum(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }
function hrgba(col, a) {
  const c = String(col || '').trim();
  let m = c.match(/^#([0-9a-f]{6})$/i);
  if (m) {
    const n = parseInt(m[1], 16);
    return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${a})`;
  }
  m = c.match(/^rgba?\(([^)]+)\)$/i);
  if (m) { const p = m[1].split(',').map(x => x.trim()); return `rgba(${p[0]},${p[1]},${p[2]},${a})`; }
  return c;
}

function hreset() {
  const H = HIST.data;
  if (!H || !H.rows || !H.rows.length) return;
  HIST.i0 = 0; HIST.i1 = H.rows.length - 1; HIST.pick = null;
  hdraw();
}

function hreadout(r) {
  const el = document.getElementById('hist-readout');
  if (!el) return;
  if (!r) { el.innerHTML = '<span class="hr-empty">Hover or tap a candle to read its raw row.</span>'; return; }
  const flags = [];
  if (r.floor === true) flags.push('<span class="tag warn">ON THE FLOOR</span>');
  if (r.locked === true) flags.push('<span class="tag neg">LOCKED BAR</span>');
  if (r.zero_volume === true) flags.push('<span class="tag">ZERO VOLUME</span>');
  if (r.qa_exclude === true) flags.push('<span class="tag neg">QA EXCLUDED</span>');
  if (r.annotated === false) flags.push('<span class="tag verdict v-NOT_TESTED">QA UNKNOWN</span>');
  if (r.annotated === true && !flags.length) flags.push('<span class="tag verdict v-OBSERVED">QA CLEAN</span>');
  const cell = (k, v, cls='') => `<span class="hr-cell"><span class="hr-k">${k}</span><span class="hr-v ${cls}">${v === null ? F.dash : v}</span></span>`;
  const o = hnum(r.o), c = hnum(r.c);
  const cls = (o === null || c === null) ? '' : (c > o ? 'pos' : c < o ? 'neg' : '');
  el.innerHTML = cell('DATE', hx(r.t)) + cell('OPEN', o === null ? null : F.n(o)) +
    cell('HIGH', hnum(r.h) === null ? null : F.n(r.h)) + cell('LOW', hnum(r.l) === null ? null : F.n(r.l)) +
    cell('CLOSE', c === null ? null : F.n(c), cls) + cell('VOLUME', hnum(r.v) === null ? null : F.qty(r.v)) +
    `<span class="hr-flags">${flags.join('')}</span>`;
}

function hsection(H) {
  if (!H || !H.rows || !H.rows.length) {
    return `<section class="sec" data-history-chart><div class="sec-head"><h2>ITS OWN HISTORY</h2></div><div class="card"><div class="empty">${hx((H && H.reason) || 'No stored daily history for this stock.')}</div></div></section>`;
  }
  const c = H.coverage || {}, p = H.position || {};
  const years = c.calendar_days ? (c.calendar_days / 365.25).toFixed(1) : F.dash;
  const pct = v => typeof v === 'number' && isFinite(v) ? v.toFixed(1) + '%' : F.dash;
  const marks = ['<span class="hm hm-up"></span>close above open','<span class="hm hm-down"></span>close below open'];
  if (c.floor_era_sessions) marks.push(`<span class="hm hm-floor"></span>${F.int(c.floor_era_sessions)} floor-era sessions`);
  if (c.locked_sessions) marks.push(`<span class="hm hm-locked"></span>${F.int(c.locked_sessions)} locked bars`);
  if (c.unannotated_sessions) marks.push(`<span class="hm hm-unchecked"></span>${F.int(c.unannotated_sessions)} sessions not QA-checked yet`);
  if (c.gaps_over_10_days) marks.push(`<span class="hm hm-gap"></span>${F.int(c.gaps_over_10_days)} coverage gaps over 10 days`);
  if (c.qa_excluded_sessions) marks.push(`<span class="hm hm-excl"></span>${F.int(c.qa_excluded_sessions)} QA-excluded sessions`);
  return `<section class="sec" data-history-chart>
    <div class="sec-head"><h2>ITS OWN HISTORY</h2><span class="note">${F.int(c.sessions)} sessions · ${hx(c.first||'')} → ${hx(c.last||'')} · ${years} years · every stored row stays available</span></div>
    <div class="card"><div class="hist-bar"><span id="hist-range" class="hist-range">—</span><span class="spacer"></span><button class="hist-btn" id="hist-out" type="button">−</button><button class="hist-btn" id="hist-in" type="button">+</button><button class="hist-btn" id="hist-reset" type="button">All ${F.int(c.sessions)}</button></div>
      <div class="hist-readout" id="hist-readout"></div><div class="hist-wrap"><canvas id="hist-canvas" class="hist-canvas" tabindex="0" aria-label="Daily OHLC candlestick history. Drag to pan, scroll or pinch to zoom, hover or tap to read the stored row."></canvas></div>
      <div class="hist-hint">drag to pan · scroll/pinch to zoom · hover/tap a candle · arrows pan · +/− zoom · Home resets</div><div class="hist-legend">${marks.join('')}</div></div>
    <div class="card" style="margin-top:12px"><div class="kv"><div><div class="k">CLOSE ON ${hx(p.as_of||'')}</div><div class="v">${F.n(p.close)}</div></div><div><div class="k">HIGHER THAN</div><div class="v">${pct(p.close_pct_rank)}<span class="muted hist-small"> of its own ${F.int(p.close_ranked_over)} sessions</span></div></div><div><div class="k">SHARES THAT DAY</div><div class="v">${F.qty(p.volume)}</div></div><div><div class="k">HIGHER THAN</div><div class="v">${pct(p.volume_pct_rank)}<span class="muted hist-small"> of its own ${F.int(p.volume_ranked_over)} sessions</span></div></div></div>
      ${c.longest_gap ? `<p class="rule-note">Longest coverage gap: ${hx(c.longest_gap.from)} → ${hx(c.longest_gap.to)}, ${F.int(c.longest_gap.days)} days. No candle is invented inside it.</p>` : ''}
      ${c.annotated_last && c.unannotated_sessions ? `<p class="rule-note">QA flags stop at ${hx(c.annotated_last)}. Later rows remain QA UNKNOWN, never silently clean.</p>` : ''}
      ${typeof provLine === 'function' ? provLine('each candle is the stored open/high/low/close for that session; no interpolation and no forward claim', 'results/dse_eod_bars_annotated.parquet · data/raw/dse_eod_extended.parquet', 'OBSERVED', null) : ''}
    </div></section>`;
}

function hwidth(map, rows, lo, hi) {
  if (hi - lo < 1) return 8;
  const a = [];
  for (let i = lo + 1; i <= hi; i++) a.push(map(Date.parse(rows[i].t)) - map(Date.parse(rows[i-1].t)));
  a.sort((x,y)=>x-y);
  return Math.max(0.8, Math.min(22, a[Math.floor(a.length/2)] * 0.72));
}

function hdraw() {
  const cv = document.getElementById('hist-canvas'), H = HIST.data;
  if (!cv || !H || !H.rows || !H.rows.length) { HIST.geo = null; return; }
  const rows = H.rows, n = rows.length;
  HIST.i0 = Math.max(0, Math.min(HIST.i0, n-1)); HIST.i1 = Math.max(HIST.i0, Math.min(HIST.i1, n-1));
  const i0 = HIST.i0, i1 = HIST.i1;
  const css = getComputedStyle(document.documentElement), pick=(k,f)=>(css.getPropertyValue(k)||'').trim()||f;
  const ink=pick('--ink-1','#ddd'), ink3=pick('--ink-3','#888'), line=pick('--line','#333'), pos=pick('--pos','#0E8F5B'), neg=pick('--neg','#D32F35'), warn=pick('--warn','#B26A00');
  const dpr=Math.min(window.devicePixelRatio||1,2), w=Math.max(260,cv.parentElement.clientWidth), h=w<560?260:340;
  cv.width=Math.round(w*dpr); cv.height=Math.round(h*dpr); cv.style.width=w+'px'; cv.style.height=h+'px';
  const g=cv.getContext('2d'); g.setTransform(dpr,0,0,dpr,0,0); g.clearRect(0,0,w,h);
  const padL=52,padR=8,padT=10,padB=20,volH=Math.round(h*.20),priceH=h-padT-padB-volH-8,plotW=w-padL-padR;
  const t0=Date.parse(rows[i0].t), t1=Date.parse(rows[i1].t), span=Math.max(1,t1-t0);
  const X0=t=>padL+plotW*(t-t0)/span, inset=Math.min(plotW/4,hwidth(X0,rows,i0,i1)/2+1), X=t=>padL+inset+(plotW-2*inset)*(t-t0)/span;
  let lo=Infinity,hi=-Infinity,vmax=0;
  for(let i=i0;i<=i1;i++){const r=rows[i]; for(const k of ['o','h','l','c']){const x=r[k]; if(typeof x==='number'&&isFinite(x)){lo=Math.min(lo,x);hi=Math.max(hi,x);}} if(typeof r.v==='number'&&isFinite(r.v))vmax=Math.max(vmax,r.v);}
  if(!isFinite(lo)||!isFinite(hi)){HIST.geo=null;return;} if(hi===lo){hi++;lo--;} const pv=(hi-lo)*.06;lo-=pv;hi+=pv;
  const Y=p=>padT+priceH*(1-(p-lo)/(hi-lo)),volTop=padT+priceH+8,VY=v=>volTop+volH*(1-v/(vmax||1)),cw=hwidth(X,rows,i0,i1);
  function band(test,fill){let s=null;for(let i=i0;i<=i1+1;i++){const on=i<=i1&&test(rows[i]);if(on&&s===null)s=i;if(!on&&s!==null){const a=X(Date.parse(rows[s].t))-cw/2,b=X(Date.parse(rows[Math.max(s,i-1)].t))+cw/2;g.fillStyle=fill;g.fillRect(a,padT,Math.max(1,b-a),priceH+8+volH);s=null;}}}
  band(r=>r.floor===true,hrgba(warn,.16)); band(r=>r.annotated===false,hrgba(ink3,.10));
  g.strokeStyle=line;g.lineWidth=1;g.font='10px ui-monospace,monospace';g.fillStyle=ink3;g.textAlign='right';g.textBaseline='middle';
  for(let j=0;j<=4;j++){const p=lo+(hi-lo)*j/4,y=Math.round(Y(p))+.5;g.beginPath();g.moveTo(padL,y);g.lineTo(w-padR,y);g.stroke();g.fillText(p>=1000?Math.round(p).toString():p.toFixed(1),padL-5,y);}
  const GAP=10*864e5;g.save();g.setLineDash([3,3]);g.strokeStyle=hrgba(ink3,.7);for(let i=Math.max(1,i0);i<=i1;i++){const a=Date.parse(rows[i-1].t),b=Date.parse(rows[i].t);if(b-a<=GAP)continue;const x=Math.round((X(a)+X(b))/2)+.5;g.beginPath();g.moveTo(x,padT);g.lineTo(x,volTop+volH);g.stroke();}g.restore();
  for(let i=i0;i<=i1;i++){const r=rows[i];if(typeof r.v==='number'&&isFinite(r.v)){const up=hnum(r.o)!==null&&hnum(r.c)!==null?r.c>=r.o:null;g.fillStyle=up===null?hrgba(ink3,.4):hrgba(up?pos:neg,.38);const y=VY(r.v);g.fillRect(X(Date.parse(r.t))-cw/2,y,Math.max(.6,cw),Math.max(.5,volTop+volH-y));}}
  let drawn=0,skipped=0;
  for(let i=i0;i<=i1;i++){const r=rows[i],o=r.o,hg=r.h,l=r.l,c=r.c;if(![o,hg,l,c].every(x=>typeof x==='number'&&isFinite(x))){skipped++;continue;}const x=X(Date.parse(r.t)),col=c>o?pos:c<o?neg:ink3;g.strokeStyle=col;g.lineWidth=Math.min(1.4,Math.max(.6,cw*.14));const xw=cw>=3?Math.round(x)+.5:x;g.beginPath();g.moveTo(xw,Y(hg));g.lineTo(xw,Y(l));g.stroke();const yo=Y(o),yc=Y(c);let top=Math.min(yo,yc),bh=Math.abs(yc-yo);if(bh<1){top=Math.round(top);bh=1;}g.fillStyle=col;g.fillRect(x-cw/2,top,Math.max(.8,cw),bh);drawn++;}
  for(let i=i0;i<=i1;i++){const r=rows[i],x=X(Date.parse(r.t));if(r.locked===true){g.fillStyle=hrgba(neg,.75);g.fillRect(x-Math.max(.5,cw/2),padT+priceH+1,Math.max(1,cw),2.5);}if(r.qa_exclude===true){g.save();g.setLineDash([1,1]);g.strokeStyle=neg;g.beginPath();g.arc(x,padT+priceH+5,2.6,0,Math.PI*2);g.stroke();g.restore();}}
  if(HIST.pick!==null&&HIST.pick>=i0&&HIST.pick<=i1){const x=X(Date.parse(rows[HIST.pick].t));g.save();g.setLineDash([2,3]);g.strokeStyle=hrgba(ink,.55);g.beginPath();g.moveTo(x,padT);g.lineTo(x,volTop+volH);g.stroke();g.restore();}
  HIST.geo={i0,i1,padL,plotW,t0,t1,span,cw,drawn,skipped};
  const re=document.getElementById('hist-range');if(re)re.textContent=`${rows[i0].t} → ${rows[i1].t} · ${i1-i0+1} sessions drawn`;
  hreadout(HIST.pick!==null?rows[HIST.pick]:rows[i1]);
}

function hindex(px){const H=HIST.data,G=HIST.geo;if(!H||!G)return null;const t=G.t0+G.span*(px-G.padL)/G.plotW;let best=null,bd=Infinity;for(let i=G.i0;i<=G.i1;i++){const d=Math.abs(Date.parse(H.rows[i].t)-t);if(d<bd){bd=d;best=i;}}return best;}
function hzoom(factor,anchor){const H=HIST.data;if(!H)return;const n=H.rows.length,nv=HIST.i1-HIST.i0+1;let a=.5;if(typeof anchor==='number'&&HIST.geo)a=Math.max(0,Math.min(1,(anchor-HIST.geo.padL)/HIST.geo.plotW));const want=Math.max(5,Math.min(n,Math.round(nv*factor))),centre=HIST.i0+a*(nv-1);let i0=Math.round(centre-a*(want-1));i0=Math.max(0,Math.min(n-want,i0));HIST.i0=i0;HIST.i1=i0+want-1;hdraw();}
function hpan(dx){const H=HIST.data,G=HIST.geo;if(!H||!G)return;const n=H.rows.length,nv=HIST.i1-HIST.i0+1,shift=Math.round(dx/G.plotW*nv);if(!shift)return;const i0=Math.max(0,Math.min(n-nv,HIST.i0-shift));HIST.i0=i0;HIST.i1=i0+nv-1;hdraw();}

function hwire(){
  const cv=document.getElementById('hist-canvas'),H=HIST.data;if(!cv||!H||!H.rows.length)return;
  const px=e=>{const b=cv.getBoundingClientRect();return((e.touches&&e.touches[0])?e.touches[0].clientX:e.clientX)-b.left;};
  cv.addEventListener('wheel',e=>{e.preventDefault();hzoom(e.deltaY>0?1.25:.8,px(e));},{passive:false});
  let drag=null;cv.addEventListener('mousedown',e=>{drag={x:px(e)};cv.classList.add('dragging');});window.addEventListener('mouseup',()=>{if(drag)cv.classList.remove('dragging');drag=null;});
  cv.addEventListener('mousemove',e=>{const x=px(e);if(drag){const dx=x-drag.x;if(Math.abs(dx)>=2){hpan(dx);drag.x=x;}return;}const i=hindex(x);if(i!==null&&i!==HIST.pick){HIST.pick=i;hdraw();}});cv.addEventListener('mouseleave',()=>{if(!drag){HIST.pick=null;hdraw();}});cv.addEventListener('dblclick',hreset);
  let one=null,pinch=null;cv.addEventListener('touchstart',e=>{if(e.touches.length===2){pinch=Math.abs(e.touches[0].clientX-e.touches[1].clientX);one=null;}else if(e.touches.length===1){one={x:px(e),y:e.touches[0].clientY,m:false};pinch=null;}},{passive:true});
  cv.addEventListener('touchmove',e=>{if(e.touches.length===2&&pinch!==null){e.preventDefault();const d=Math.abs(e.touches[0].clientX-e.touches[1].clientX);if(d>4&&pinch>4){hzoom(pinch/d,HIST.geo?HIST.geo.padL+HIST.geo.plotW/2:null);pinch=d;}return;}if(e.touches.length===1&&one){const x=px(e),dy=Math.abs(e.touches[0].clientY-one.y),dx=x-one.x;if(Math.abs(dx)>6&&Math.abs(dx)>dy){e.preventDefault();one.m=true;hpan(dx);one.x=x;}}},{passive:false});
  cv.addEventListener('touchend',()=>{if(one&&!one.m){const i=hindex(one.x);if(i!==null){HIST.pick=i;hdraw();}}one=null;pinch=null;},{passive:true});
  cv.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'){e.preventDefault();hpan(HIST.geo?HIST.geo.plotW*.12:40);}else if(e.key==='ArrowRight'){e.preventDefault();hpan(HIST.geo?-HIST.geo.plotW*.12:-40);}else if(e.key==='+'||e.key==='='){e.preventDefault();hzoom(.8);}else if(e.key==='-'||e.key==='_'){e.preventDefault();hzoom(1.25);}else if(e.key==='Home'||e.key==='0'){e.preventDefault();hreset();}});
  const bind=(id,fn)=>{const x=document.getElementById(id);if(x)x.onclick=fn;};bind('hist-in',()=>hzoom(.7));bind('hist-out',()=>hzoom(1.4));bind('hist-reset',hreset);
}

async function paintHistoryChart(){
  if(typeof S==='undefined'||S.route!=='stock'||!S.param)return;
  const wrap=document.querySelector('#view .wrap');if(!wrap||wrap.querySelector('[data-history-chart]'))return;
  const sym=S.param;
  let H;
  try{H=await get('/api/stock/'+encodeURIComponent(sym)+'/history',120000);}catch(e){H={symbol:sym,rows:[],reason:'History endpoint unavailable.'};}
  if(typeof S==='undefined'||S.route!=='stock'||S.param!==sym)return;
  HIST.sym=sym;HIST.data=H;HIST.i0=0;HIST.i1=Math.max(0,(H.rows||[]).length-1);HIST.pick=null;HIST.geo=null;
  const box=document.createElement('div');box.innerHTML=hsection(H);const sec=box.firstElementChild;
  const sections=[...wrap.querySelectorAll('.sec')];const before=sections.find(x=>x.querySelector('.sec-head h2')&&x.querySelector('.sec-head h2').textContent.trim()==='THIS SESSION');
  if(before)wrap.insertBefore(sec,before);else wrap.appendChild(sec);hdraw();hwire();
}

if(typeof render==='function'){
  const _historyRenderCore=render;
  render=async function(){const out=await _historyRenderCore.apply(this,arguments);await paintHistoryChart();return out;};
}
window.addEventListener('resize',()=>{if(document.getElementById('hist-canvas'))hdraw();});
