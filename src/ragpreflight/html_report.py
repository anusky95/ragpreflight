"""Self-contained, offline HTML report.

Layout: verdict → five action categories (who fixes what) → document network (versions, conflicts,
duplicates as edges) → page heatmap (where in each PDF the problems are) → findings grouped by
category, each linked to its source file and page → every scanned document.
No external scripts or fonts: it must open from a CI artifact or an air-gapped laptop.
"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from . import __version__
from .models import Finding, Report, Severity
from .taxonomy import MODES, PAPER_URL

# key, title, owner, why it matters, what to do, checks
CATEGORIES = [
    ("answers", "Wrong or outdated answers", "Content owners",
     "Two versions disagree or an old copy is still indexed: the model answers from whichever it retrieves.",
     "Remove or archive old versions, or mark them superseded so retrieval filters them out.",
     ("version-conflict", "version-stale", "duplicate")),
    ("exposure", "Privacy & security exposure", "Security / compliance",
     "Anything indexed can be retrieved and quoted to any user who asks the right question.",
     "Remove and rotate credentials; redact personal data or restrict access at retrieval time.",
     ("secret", "pii")),
    ("invisible", "Content your AI can't read", "Ingestion / parsing",
     "Scanned pages, broken fonts and flattened tables turn into nothing, or into noise.",
     "OCR scanned pages, re-extract garbled ones with another parser, use a table-aware parser.",
     ("unreadable", "extraction-empty", "extraction-scanned", "extraction-garbled",
      "extraction-images", "extraction-table")),
    ("chunking", "Meaning lost in chunking", "RAG pipeline",
     "A fixed-size splitter cuts rules away from their conditions and tables away from their headers.",
     "Use a sentence/structure-aware splitter, or re-run with --chunker to match your pipeline.",
     ("chunk-split", "chunk-table-split", "chunk-orphan-heading")),
    ("collection", "Collection structure", "Knowledge-base owner",
     "Facts about the corpus as a whole: recurring reports for many periods, missing dates and owners.",
     "Add period, date and owner metadata to every chunk and filter on it at query time.",
     ("version-period-twins", "metadata-no-date", "metadata-no-owner", "metadata-placeholder",
      "metadata-old")),
]
CAT_OF = {c: key for key, *_rest, checks in CATEGORIES for c in checks}
CAT_COLOR = {"answers": "#e05a3a", "exposure": "#d6336c", "invisible": "#8a63d2",
             "chunking": "#d9a23a", "collection": "#3d8bd9", "other": "#7d8aa5"}
COLLECTION_SCOPE = {"version-period-twins"}

_PAGES = re.compile(r"p\.\s?([\d][\d–\-, ]*)")


def category(f: Finding) -> str:
    return CAT_OF.get(f.check, "other")


def pages_of(f: Finding) -> list[int]:
    """Pages a finding points at, parsed from its location ('p.4–7, 9' / 'p.12, line 5; p.30, ...')."""
    out: set[int] = set()
    for m in _PAGES.finditer(f.location or ""):
        for part in m.group(1).split(","):
            part = part.strip()
            r = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
            if r:
                a, b = int(r.group(1)), int(r.group(2))
                out.update(range(a, min(b, a + 2000) + 1))
            elif part.isdigit():
                out.add(int(part))
            else:
                break  # "12, line 5": stop at the line part
    return sorted(out)


def _linker(report: Report, link_base: Path | None):
    root = Path(report.root)
    base_dir = root if root.is_dir() else root.parent

    def href(rel_path: str, page: int | None = None) -> str | None:
        if not rel_path or rel_path.startswith("("):
            return None
        target = (base_dir / rel_path).resolve()
        url = None
        if link_base is not None:
            try:
                url = quote(Path(os.path.relpath(target, link_base.resolve())).as_posix())
            except ValueError:  # different drive on Windows
                url = None
        if url is None:
            url = target.as_uri()
        if page and target.suffix.lower() == ".pdf":
            url += f"#page={page}"
        return url

    return href


def _graph_data(report: Report, href) -> dict:
    from .checks import rel

    root = Path(report.root)
    docs = []
    index: dict[str, int] = {}
    per: dict[str, dict] = {}
    for f in report.findings:
        targets = [f.path] + (f.related if f.check in ("version-conflict", "duplicate") else [])
        for p in targets:
            d = per.setdefault(p, {"error": 0, "warn": 0, "info": 0, "cats": {}, "pages": {}})
            d[f.severity.value] += 1
            if f.severity != Severity.INFO:
                d["cats"][category(f)] = d["cats"].get(category(f), 0) + 1
        if f.path in per and f.severity != Severity.INFO:
            for pg in pages_of(f):
                per[f.path]["pages"].setdefault(pg, set()).add(category(f))
    for d in report.documents:
        rp = rel(d.path, root)
        c = per.get(rp, {"error": 0, "warn": 0, "info": 0, "cats": {}, "pages": {}})
        worst = ("unreadable" if d.read_error else "error" if c["error"] else
                 "warn" if c["warn"] else "info" if c["info"] else "clean")
        index[rp] = len(docs)
        docs.append({"p": rp, "h": href(rp) or "", "n": len(d.pages), "w": d.word_count,
                     "s": worst, "e": c["error"], "wa": c["warn"], "i": c["info"],
                     "c": c["cats"], "pg": {str(k): sorted(v) for k, v in c["pages"].items()}})
    edges = []
    seen = set()

    def add(a: str, b: str, kind: str) -> None:
        if a in index and b in index and a != b:
            key = (min(index[a], index[b]), max(index[a], index[b]), kind)
            if key not in seen:
                seen.add(key)
                edges.append([index[a], index[b], kind])

    for f in report.findings:
        if f.check == "version-conflict":
            for r in f.related:
                add(f.path, r, "conflict")
        elif f.check == "version-stale":
            for r in f.related:
                add(f.path, r, "stale")
        elif f.check == "duplicate":
            for r in f.related:
                add(f.path, r, "dup")
        elif f.check == "version-period-twins":
            chain = f.related + [f.path]  # oldest … newest
            for a, b in zip(chain, chain[1:], strict=False):
                add(a, b, "period")
    return {"docs": docs, "edges": edges, "colors": CAT_COLOR,
            "cats": {k: t for k, t, *_ in CATEGORIES}}


CSS = """
:root{--ink:#14213d;--muted:#66728f;--line:#e3e8f0;--card:#f4f6fa;--bg:#fff;--red:#d64527;--amber:#c48a12;--green:#1f8a6e;--panel:#0d1424;--panelink:#dfe6f3}
@media (prefers-color-scheme:dark){:root{--ink:#e8ecf4;--muted:#98a3bd;--line:#26314a;--card:#161f35;--bg:#0b1120}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 60px}a{color:inherit}
h1{font-size:24px;margin:0}h2{font-size:19px;margin:36px 0 4px}.sub{color:var(--muted);font-size:13px}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:20px 0}
.stat{background:var(--card);border-radius:14px;padding:14px 16px}.stat b{display:block;font-size:28px;line-height:1.2}
.v-NOT{color:var(--red)}.v-REVIEW{color:var(--amber)}.v-READY{color:var(--green)}
.cats{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin-top:14px}
.cat{background:var(--card);border-radius:14px;padding:14px;cursor:pointer;border:2px solid transparent;transition:border-color .15s}
.cat:hover{border-color:var(--c)}.cat h3{margin:0 0 2px;font-size:15px;display:flex;align-items:center;gap:8px}
.dot{width:10px;height:10px;border-radius:50%;background:var(--c);display:inline-block;flex:none}
.cat .num{font-size:30px;font-weight:700;color:var(--c);line-height:1.1;margin:6px 0 2px}
.bar{display:flex;height:6px;border-radius:3px;overflow:hidden;background:var(--line);margin:6px 0}
.bar i{display:block}.owner{font-size:12px;color:var(--muted)}
.panel{background:var(--panel);color:var(--panelink);border-radius:16px;padding:14px;margin-top:12px;position:relative}
.panel canvas{width:100%;display:block;border-radius:10px}.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;margin:6px 4px 2px;color:#a9b4cc}
.legend span{display:inline-flex;align-items:center;gap:6px}.sw{width:18px;height:3px;display:inline-block;border-radius:2px}
#tip{position:fixed;pointer-events:none;background:#0d1424ee;color:#fff;font-size:12px;padding:8px 10px;border-radius:8px;max-width:340px;display:none;z-index:9;border:1px solid #2b3858}
.group{margin-top:22px}.group>header{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 14px;border-bottom:2px solid var(--c);padding-bottom:6px}
.group>header h3{margin:0;font-size:17px}.group .why{font-size:13px;color:var(--muted);margin:6px 0 2px}.group .do{font-size:13px;margin:0 0 8px}
.f{background:var(--card);border-radius:10px;padding:11px 13px;margin:8px 0;border-left:4px solid var(--line)}
.f.error{border-left-color:var(--red)}.f.warn{border-left-color:var(--amber)}
.sev{font-size:11px;font-weight:700;letter-spacing:.05em;margin-right:8px;text-transform:uppercase}.error .sev{color:var(--red)}.warn .sev{color:var(--amber)}.info .sev{color:var(--muted)}
.scope{font-size:11px;border:1px solid var(--muted);color:var(--muted);border-radius:10px;padding:0 7px;margin-left:6px}
.where{font-size:13px;margin-top:3px;word-break:break-all}.where a.src{font-family:ui-monospace,Consolas,monospace;text-decoration-color:var(--muted)}
.loc{color:var(--muted);font-family:ui-monospace,Consolas,monospace}.codes a{font-size:11px;color:var(--muted);margin-left:6px}
.fix{margin-top:5px;font-size:14px}details{margin-top:6px;font-size:13.5px}summary{cursor:pointer;color:var(--muted)}
pre{white-space:pre-wrap;font:12.5px/1.55 ui-monospace,Consolas,monospace;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;margin:6px 0 0}
input#q{width:100%;padding:9px 12px;border-radius:10px;border:1px solid var(--line);background:var(--bg);color:var(--ink);margin:10px 0 0;font:inherit}
.tw{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
td:first-child{white-space:normal;word-break:break-all}.n{text-align:right;font-variant-numeric:tabular-nums}
.st-error,.st-unreadable{color:var(--red);font-weight:600}.st-warn{color:var(--amber);font-weight:600}.st-clean{color:var(--green)}.st-info{color:var(--muted)}
button{font:inherit;font-size:12px;padding:2px 9px;border-radius:7px;border:1px solid var(--line);background:var(--bg);color:var(--ink);cursor:pointer}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.chip{font-size:12px;border-radius:999px;padding:2px 10px;border:1px solid var(--line);cursor:pointer;background:var(--bg)}
.chip.on{background:var(--ink);color:var(--bg)}
"""

JS = r"""
const D=JSON.parse(document.getElementById('data').textContent);
const tip=document.getElementById('tip');
function showTip(e,h){tip.innerHTML=h;tip.style.display='block';tip.style.left=Math.min(e.clientX+14,innerWidth-360)+'px';tip.style.top=(e.clientY+14)+'px'}
function hideTip(){tip.style.display='none'}
function esc(s){return String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
const SEV={unreadable:'#ff5a3c',error:'#ff6b4a',warn:'#f0b429',info:'#4b5878',clean:'#2fbf8f'};
const EDGE={conflict:'#ff5a3c',stale:'#f0b429',dup:'#9aa6bf',period:'#4e9cf5'};
// ---------------- filtering
let catOn=null,q='';
function apply(){for(const el of document.querySelectorAll('.f')){const okc=!catOn||el.dataset.cat===catOn;const t=(el.textContent+' '+(el.dataset.files||'')).toLowerCase();el.style.display=okc&&(!q||t.includes(q))?'':'none'}
 for(const g of document.querySelectorAll('.group'))g.style.display=(!catOn||g.dataset.cat===catOn)?'':'none';
 for(const c of document.querySelectorAll('.chip'))c.classList.toggle('on',c.dataset.cat===catOn)}
function setCat(c){catOn=catOn===c?null:c;apply();document.getElementById('findings').scrollIntoView({behavior:'smooth'})}
function setQ(v){q=v.toLowerCase();document.getElementById('q').value=v;apply()}
function showFile(p){catOn=null;setQ(p);document.getElementById('findings').scrollIntoView({behavior:'smooth'})}
// ---------------- network
(function(){
 const cv=document.getElementById('net');if(!cv)return;const N=D.docs,E=D.edges;
 // keep the graph readable: documents with findings or links first, at most 500
 const linked=new Set();E.forEach(e=>{linked.add(e[0]);linked.add(e[1])});
 let ids=N.map((d,i)=>i).sort((a,b)=>(linked.has(b)-linked.has(a))||((N[b].e*9+N[b].wa*3)-(N[a].e*9+N[a].wa*3)));
 ids=ids.slice(0,500);const pos=new Map(ids.map((id,k)=>[id,k]));
 const nodes=ids.map(id=>({id,d:N[id],x:Math.cos(id*2.4)*200*Math.sqrt((id%97+1)/97),y:Math.sin(id*2.4)*200*Math.sqrt((id%97+1)/97),vx:0,vy:0,r:2.5+Math.min(5,Math.sqrt(N[id].n||1)/4)}));
 const links=E.filter(e=>pos.has(e[0])&&pos.has(e[1])).map(e=>({a:nodes[pos.get(e[0])],b:nodes[pos.get(e[1])],k:e[2]}));
 const deg=new Map();links.forEach(l=>{deg.set(l.a,(deg.get(l.a)||0)+1);deg.set(l.b,(deg.get(l.b)||0)+1)});
 for(let t=0;t<320;t++){const alpha=1-t/320;
  for(let i=0;i<nodes.length;i++){const a=nodes[i];for(let j=i+1;j<nodes.length;j++){const b=nodes[j];let dx=a.x-b.x,dy=a.y-b.y,d2=dx*dx+dy*dy+0.01;if(d2>90000)continue;const f=60/d2;dx*=f;dy*=f;a.vx+=dx;a.vy+=dy;b.vx-=dx;b.vy-=dy}}
  for(const l of links){const dx=l.b.x-l.a.x,dy=l.b.y-l.a.y,d=Math.sqrt(dx*dx+dy*dy)||1,want=l.k==='period'?14:l.k==='conflict'?22:18,f=(d-want)*0.25;const fx=dx/d*f,fy=dy/d*f;l.a.vx+=fx;l.a.vy+=fy;l.b.vx-=fx;l.b.vy-=fy}
  for(const n of nodes){const g=linked.has(n.id)?0.006:0.012;n.vx-=n.x*g;n.vy-=n.y*g;n.x+=n.vx*alpha*0.5;n.y+=n.vy*alpha*0.5;n.vx*=0.55;n.vy*=0.55}}
 const dpr=devicePixelRatio||1;let W,H,sc,ox,oy,hover=null;
 function fit(){W=cv.clientWidth;H=Math.max(360,Math.min(620,W*0.58));cv.style.height=H+'px';cv.width=W*dpr;cv.height=H*dpr;
  let x0=1e9,x1=-1e9,y0=1e9,y1=-1e9;for(const n of nodes){x0=Math.min(x0,n.x);x1=Math.max(x1,n.x);y0=Math.min(y0,n.y);y1=Math.max(y1,n.y)}
  sc=Math.min((W-40)/(x1-x0||1),(H-40)/(y1-y0||1));ox=W/2-(x0+x1)/2*sc;oy=H/2-(y0+y1)/2*sc;draw()}
 function P(n){return[n.x*sc+ox,n.y*sc+oy]}
 function draw(){const c=cv.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,W,H);
  for(const l of links){const[a1,a2]=P(l.a),[b1,b2]=P(l.b);const on=hover&&(l.a===hover||l.b===hover);c.strokeStyle=EDGE[l.k];c.globalAlpha=hover?(on?1:.12):(l.k==='conflict'?.95:.8);c.lineWidth=l.k==='conflict'?2.8:l.k==='stale'?2:1.6;c.setLineDash(l.k==='dup'?[3,3]:[]);c.beginPath();c.moveTo(a1,a2);c.lineTo(b1,b2);c.stroke()}
  c.setLineDash([]);
  for(const n of nodes){const[x,y]=P(n);const col=SEV[n.d.s];c.globalAlpha=hover&&hover!==n&&!links.some(l=>(l.a===hover&&l.b===n)||(l.b===hover&&l.a===n))?.25:1;
   if(n.d.s==='error'||n.d.s==='unreadable'){c.fillStyle=col;c.globalAlpha*=.18;c.beginPath();c.arc(x,y,n.r+6,0,7);c.fill();c.globalAlpha=hover&&hover!==n?.25:1}
   c.fillStyle=col;c.beginPath();c.arc(x,y,n.r,0,7);c.fill();c.strokeStyle='#0d1424';c.lineWidth=1;c.stroke()}
  c.globalAlpha=1;if(hover){const[x,y]=P(hover);c.fillStyle='#fff';c.font='12px system-ui';c.fillText(hover.d.p.split('/').pop(),x+hover.r+4,y-hover.r)}}
 function pick(e){const r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;let best=null,bd=1e9;for(const n of nodes){const[x,y]=P(n),d=(x-mx)**2+(y-my)**2;if(d<bd&&d<(n.r+6)**2){bd=d;best=n}}return best}
 cv.addEventListener('mousemove',e=>{const n=pick(e);if(n!==hover){hover=n;draw()}if(n){const d=n.d,cats=Object.entries(d.c).map(([k,v])=>`<span style="color:${D.colors[k]}">●</span> ${esc(D.cats[k]||k)}: ${v}`).join('<br>');
  const rel=links.filter(l=>l.a===n||l.b===n).length;showTip(e,`<b>${esc(d.p)}</b><br>${d.n} pages · ${d.w.toLocaleString()} words<br>${d.e} errors · ${d.wa} warnings · ${d.i} notes${rel?`<br>${rel} link(s) to other versions`:''}${cats?'<br>'+cats:''}<br><i>click: show findings</i>`)}else hideTip()});
 cv.addEventListener('mouseleave',()=>{hover=null;hideTip();draw()});
 cv.addEventListener('click',e=>{const n=pick(e);if(n)showFile(n.d.p)});
 addEventListener('resize',fit);fit();
})();
// ---------------- page heatmap
(function(){
 const cv=document.getElementById('heat');if(!cv)return;
 const rows=D.docs.filter(d=>Object.keys(d.pg).length&&d.n>1).sort((a,b)=>Object.keys(b.pg).length-Object.keys(a.pg).length||b.e-a.e).slice(0,40);
 if(!rows.length){cv.parentElement.style.display='none';return}
 const dpr=devicePixelRatio||1,RH=16,LW=Math.min(260,cv.clientWidth*0.32);let W,cells=[];
 function draw(){W=cv.clientWidth;const H=rows.length*RH+24;cv.style.height=H+'px';cv.width=W*dpr;cv.height=H*dpr;const c=cv.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,W,H);cells=[];
  const maxP=Math.max(...rows.map(r=>r.n));const PW=(W-LW-10)/maxP;c.font='11px ui-monospace,Consolas,monospace';
  rows.forEach((r,i)=>{const y=i*RH+4;c.fillStyle='#a9b4cc';const name=r.p.split('/').pop();c.fillText(name.length>34?name.slice(0,33)+'…':name,4,y+11);
   c.fillStyle='#1b2540';c.fillRect(LW,y+2,r.n*PW,RH-5);
   for(const [pg,cats] of Object.entries(r.pg)){const x=LW+(pg-1)*PW;const w=Math.max(2,PW);c.fillStyle=D.colors[cats[0]]||'#888';c.fillRect(x,y+2,w,RH-5);cells.push({x,y,w,r,pg,cats})}});
  c.fillStyle='#6f7d9c';c.fillText('page 1',LW,rows.length*RH+18);c.textAlign='right';c.fillText('page '+maxP,W-10,rows.length*RH+18);c.textAlign='left'}
 function at(e){const b=cv.getBoundingClientRect(),mx=e.clientX-b.left,my=e.clientY-b.top;return cells.find(k=>mx>=k.x-1&&mx<=k.x+k.w+1&&my>=k.y&&my<=k.y+RH)}
 cv.addEventListener('mousemove',e=>{const k=at(e);if(k){cv.style.cursor='pointer';showTip(e,`<b>${esc(k.r.p)}</b><br>page ${k.pg}: ${k.cats.map(c=>`<span style="color:${D.colors[c]}">●</span> ${esc(D.cats[c]||c)}`).join(', ')}<br><i>click: open this page</i>`)}else{cv.style.cursor='default';hideTip()}});
 cv.addEventListener('mouseleave',hideTip);
 cv.addEventListener('click',e=>{const k=at(e);if(k&&k.r.h)window.open(k.r.h+(k.r.p.toLowerCase().endsWith('.pdf')?'#page='+k.pg:''),'_blank')});
 addEventListener('resize',draw);draw();
})();
"""


def to_html(report: Report, link_base: Path | None = None) -> str:
    from .checks import rel
    from .report import tag

    e = html.escape
    n = report.counts()
    href = _linker(report, link_base)

    def file_link(rel_path: str, page: int | None = None) -> str:
        url = href(rel_path, page)
        return (f'<a class="src" href="{e(url)}" target="_blank" rel="noopener">{e(rel_path)}</a>'
                if url else e(rel_path))

    # ---- category cards
    by_cat: dict[str, list[Finding]] = {k: [] for k, *_ in CATEGORIES}
    by_cat["other"] = []
    for f in report.findings:
        by_cat[category(f)].append(f)
    cards = []
    for key, title, owner, _why, _do, _checks in CATEGORIES:
        fs = by_cat[key]
        ne = sum(f.severity == Severity.ERROR for f in fs)
        nw = sum(f.severity == Severity.WARN for f in fs)
        ni = len(fs) - ne - nw
        tot = max(1, len(fs))
        bar = (f"<i style='width:{100 * ne / tot:.1f}%;background:var(--red)'></i>"
               f"<i style='width:{100 * nw / tot:.1f}%;background:var(--amber)'></i>"
               f"<i style='width:{100 * ni / tot:.1f}%;background:var(--muted)'></i>") if fs else ""
        cards.append(
            f"<div class='cat' style='--c:{CAT_COLOR[key]}' onclick=\"setCat('{key}')\">"
            f"<h3><span class='dot'></span>{e(title)}</h3><div class='num'>{ne + nw}</div>"
            f"<div class='sub'>{ne} errors · {nw} warnings · {ni} notes</div><div class='bar'>{bar}</div>"
            f"<div class='owner'>Owner: {e(owner)}</div></div>")

    # ---- findings grouped by category
    groups = []
    for key, title, owner, why, do, _checks in CATEGORIES + [("other", "Other", "—", "", "", ())]:
        fs = by_cat.get(key) or []
        if not fs:
            continue
        items = []
        for f in fs:
            codes = "".join(f'<a href="{PAPER_URL}" title="{e(MODES[c][1]) if c in MODES else ""}">{c}</a>'
                            for c in f.f_codes)
            pgs = pages_of(f)
            src = file_link(f.path, pgs[0] if pgs else None)
            if f.location:
                src += f" <span class='loc'>{e(f.location)}</span>"
            if f.related:
                shown = f.related[:8]
                src += " <span class='loc'>↔</span> " + ", ".join(file_link(r) for r in shown)
                if len(f.related) > len(shown):
                    src += f" <span class='loc'>+{len(f.related) - len(shown)} more</span>"
            scope = ("<span class='scope'>collection</span>" if f.check in COLLECTION_SCOPE
                     or f.path == "(corpus)" else "")
            detail = (f"<details{' open' if f.severity == Severity.ERROR else ''}><summary>Evidence</summary>"
                      f"<pre>{e(f.detail)}</pre></details>") if f.detail else ""
            fix = f"<div class='fix'>→ {e(f.fix)}</div>" if f.fix else ""
            items.append(
                f"<div class='f {f.severity.value}' data-cat='{key}' data-files='{e(' '.join([f.path] + f.related))}'>"
                f"<span class='sev'>{f.severity.value}</span><b>{e(tag(f))}</b> · {e(f.title)}{scope}"
                f"<span class='codes'>{codes}</span><div class='where'>{src}</div>{fix}{detail}</div>")
        groups.append(
            f"<section class='group' data-cat='{key}' style='--c:{CAT_COLOR[key]}'><header>"
            f"<h3><span class='dot'></span> {e(title)}</h3><span class='owner'>Owner: {e(owner)} · "
            f"{len(fs)} finding(s)</span></header>"
            + (f"<div class='why'>Why it matters: {e(why)}</div><div class='do'><b>Do this:</b> {e(do)}</div>" if why else "")
            + "".join(items) + "</section>")

    # ---- documents table
    data = _graph_data(report, href)
    order = sorted(range(len(data["docs"])), key=lambda i: (
        -data["docs"][i]["e"], -data["docs"][i]["wa"], data["docs"][i]["p"].lower()))
    rows = []
    for i in order:
        d = data["docs"][i]
        rows.append(
            f"<tr><td>{file_link(d['p'])}</td><td class='n'>{d['n']}</td><td class='n'>{d['w']:,}</td>"
            f"<td class='n'>{d['e']}</td><td class='n'>{d['wa']}</td><td class='n'>{d['i']}</td>"
            f"<td class='st-{d['s']}'>{d['s']}</td>"
            f"<td><button onclick=\"showFile('{e(d['p'])}')\">findings</button></td></tr>")

    chips = "".join(f"<span class='chip' data-cat='{k}' onclick=\"setCat('{k}')\">{e(t)}</span>"
                    for k, t, *_ in CATEGORIES if by_cat[k])
    vclass = report.verdict.split()[0]
    n_linked = len({x for edge in data["edges"] for x in edge[:2]})
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    _ = rel  # keep import used for type checkers
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ragpreflight report</title>
<style>{CSS}</style></head><body><main>
<h1>ragpreflight report</h1><div class="sub">{e(report.root)} · ragpreflight {__version__}</div>
<div class="hero">
<div class="stat"><span class="sub">Verdict</span><b class="v-{vclass}">{e(report.verdict)}</b></div>
<div class="stat"><span class="sub">Readiness</span><b>{report.score}/100</b></div>
<div class="stat"><span class="sub">Documents</span><b>{len(report.documents)}</b><span class="sub">{sum(len(d.pages) for d in report.documents):,} pages</span></div>
<div class="stat"><span class="sub">Errors</span><b class="v-NOT">{n['error']}</b></div>
<div class="stat"><span class="sub">Warnings</span><b class="v-REVIEW">{n['warn']}</b><span class="sub">{n['info']} notes</span></div>
</div>
<h2>What to fix, and who fixes it</h2><div class="sub">Click a category to filter the findings below.</div>
<div class="cats">{''.join(cards)}</div>

<h2>Document network</h2>
<div class="sub">Each dot is a document, sized by pages and colored by its worst finding. Lines connect versions of the same document. {n_linked} documents are linked. Hover for details; click a dot to see its findings.</div>
<div class="panel"><canvas id="net"></canvas>
<div class="legend"><span><i class="sw" style="background:#ff5a3c;height:4px"></i>conflicting versions</span><span><i class="sw" style="background:#f0b429"></i>stale older version</span><span><i class="sw" style="background:#4e9cf5"></i>same report, different period</span><span><i class="sw" style="background:#9aa6bf"></i>duplicate</span>
<span><span class="dot" style="background:#ff6b4a"></span>errors</span><span><span class="dot" style="background:#f0b429"></span>warnings</span><span><span class="dot" style="background:#2fbf8f"></span>clean</span></div></div>

<h2>Where in each document</h2>
<div class="sub">One row per document; each cell is a page with a problem, colored by category. Click a cell to open the PDF at that page.</div>
<div class="panel"><canvas id="heat"></canvas>
<div class="legend">{''.join(f'<span><span class="dot" style="background:{CAT_COLOR[k]}"></span>{e(t)}</span>' for k, t, *_ in CATEGORIES)}</div></div>

<h2 id="findings">Findings</h2>
<div class="chips">{chips}</div>
<input id="q" placeholder="Filter by file name, page, check or text…" oninput="setQ(this.value)">
{''.join(groups) or '<p>No issues found.</p>'}

<h2 id="documents">Every scanned document</h2>
<div class="sub">Worst first. File names open the source; “findings” filters the list above.</div>
<div class="tw"><table><thead><tr><th>File</th><th>Pages</th><th>Words</th><th>Errors</th><th>Warnings</th><th>Notes</th><th>Status</th><th></th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="sub">F-codes are failure modes from Garani (2026), <a href="{PAPER_URL}">A Systematic Taxonomy of Failure Modes in RAG Systems</a>, ACL TrustNLP. Generated offline by ragpreflight {__version__}.</p>
</main><div id="tip"></div>
<script type="application/json" id="data">{payload}</script>
<script>{JS}</script></body></html>"""
