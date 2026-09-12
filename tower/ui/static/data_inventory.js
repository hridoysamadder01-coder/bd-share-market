/* Read-only data inventory. Shows what is physically present; no file contents. */
'use strict';

function diEsc(v){return F.esc(v===null||v===undefined?'':String(v));}
function diBytes(n){if(typeof n!=='number'||!isFinite(n))return F.dash;const u=['B','KB','MB','GB'];let x=n,i=0;while(x>=1024&&i<u.length-1){x/=1024;i++;}return `${x.toFixed(i?1:0)} ${u[i]}`;}

async function paintDataInventory(){
  if(typeof S==='undefined'||(S.route!=='trust'&&S.route!=='evidence'))return;
  const wrap=document.querySelector('#view .wrap');if(!wrap||wrap.querySelector('[data-data-inventory]'))return;
  let d;try{d=await get('/api/data/inventory?limit=1000',30000);}catch(e){return;}
  if(typeof S==='undefined'||(S.route!=='trust'&&S.route!=='evidence'))return;
  const sec=document.createElement('section');sec.className='sec';sec.setAttribute('data-data-inventory','1');
  const roots=(d.roots||[]).map(r=>`<div><div class="k">${diEsc(r.root.toUpperCase())}</div><div class="v">${F.int(r.files)}</div><div class="muted">${diBytes(r.bytes)} · ${r.present?'present':'absent'}</div></div>`).join('');
  const rows=(d.rows||[]).map(r=>`<tr><td class="sym">${diEsc(r.path)}</td><td>${diEsc(r.ext)}</td><td class="r">${diBytes(r.bytes)}</td><td>${diEsc((r.mtime_utc||'').replace('T',' ').slice(0,19))}</td><td>${r.content_exposed?'EXPOSED':'metadata only'}</td></tr>`).join('');
  sec.innerHTML=`<div class="sec-head"><h2>ON-DISK DATA INVENTORY</h2><span class="note">${F.int(d.total_files_on_disk)} files physically visible to this runtime · nothing silently promoted or filled</span></div><div class="card"><div class="kv">${roots}</div></div><div class="card scroll-x" style="margin-top:12px"><table class="tbl"><thead><tr><th>PATH</th><th>TYPE</th><th class="r">SIZE</th><th>MTIME UTC</th><th>ACCESS</th></tr></thead><tbody>${rows}</tbody></table>${d.more?'<div class="empty">More files exist. The API is paged: /api/data/inventory?offset=1000&amp;limit=1000</div>':''}</div><div class="card" style="margin-top:12px">${typeof provLine==='function'?provLine('filesystem metadata only; raw contents stay behind the repo security boundary','data · evidence · results · qa · manifests · reports','OBSERVED',null):''}</div>`;
  wrap.appendChild(sec);
}

if(typeof render==='function'){
  const _inventoryRenderCore=render;
  render=async function(){const out=await _inventoryRenderCore.apply(this,arguments);await paintDataInventory();return out;};
}
