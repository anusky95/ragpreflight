"""Side-by-side page for `ragpreflight ab`: the same questions, before and after ragpreflight."""

from __future__ import annotations

import html
import re

from . import __version__
from .ab import ABResult

CSS = """
:root{--ink:#14213d;--muted:#66728f;--line:#e3e8f0;--card:#f6f7fb;--bg:#fff;--bad:#d64527;--good:#1f8a6e;--warn:#c48a12}
@media (prefers-color-scheme:dark){:root{--ink:#e8ecf4;--muted:#98a3bd;--line:#26314a;--card:#141c30;--bg:#0b1120}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1240px;margin:0 auto;padding:28px 16px 64px}h1{font-size:26px;margin:0}.sub{color:var(--muted);font-size:13px}
.fixes{background:var(--card);border-radius:14px;padding:14px 18px;margin:18px 0}.fixes h2{font-size:15px;margin:0 0 6px}
.fixes li{font-size:13.5px;margin:3px 0}.q{margin:28px 0 8px;font-size:19px}.q span{color:var(--muted);font-weight:400;font-size:14px;margin-right:8px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media (max-width:820px){.cols{grid-template-columns:1fr}}
.col{border:1px solid var(--line);border-radius:14px;padding:14px;background:var(--card);border-top:5px solid var(--c)}
.col h3{margin:0 0 8px;font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--c)}
.ans{font-size:15.5px;background:var(--bg);border-radius:10px;padding:10px 12px;border:1px solid var(--line);white-space:pre-wrap;min-height:44px}
.ans.none{color:var(--muted);font-size:13px}
.hit{font-size:13px;background:var(--bg);border-radius:8px;padding:8px 10px;margin:8px 0;border-left:3px solid var(--line);white-space:pre-wrap;word-break:break-word}
.hit .src{font:600 12px ui-monospace,Consolas,monospace;color:var(--muted);display:block;margin-bottom:3px}
.hit.flag{border-left-color:var(--bad)}.hit.flag .src{color:var(--bad)}
mark{background:#d6452733;color:inherit;border-radius:3px;padding:0 2px}mark.red{background:#1f8a6e33}
.why{font-size:12.5px;color:var(--bad);margin-top:2px}.empty{color:var(--muted);font-size:13px}
details{margin-top:8px}summary{cursor:pointer;color:var(--muted);font-size:13px}
"""


def _mark(text: str) -> str:
    t = html.escape(re.sub(r"\n\s*\n+", "\n", text.strip()))
    t = re.sub(r"\[REDACTED [^\]]+\]", lambda m: f"<mark class='red'>{m.group(0)}</mark>", t)
    return t


def to_ab_html(r: ABResult) -> str:
    e = html.escape
    flagged = {}
    for f in r.report.findings:
        if f.check in ("version-stale", "duplicate", "secret", "pii", "version-conflict"):
            flagged.setdefault(f.path, f.title)
    blocks = []
    for i, q in enumerate(r.questions):
        cols = []
        for name, side, color in (("Before ragpreflight", r.before, "var(--bad)"),
                                  ("After ragpreflight", r.after, "var(--good)")):
            ans = side.answers[i]
            ans_html = (f"<div class='ans'>{e(ans)}</div>" if ans
                        else f"<div class='ans none'>Bot answer not generated: {e(r.answers_note)}. "
                             f"Below is exactly what the retriever hands the bot.</div>")
            hits = []
            for c, score in side.retrieved[i]:
                flag = flagged.get(c.source) if name.startswith("Before") else None
                hits.append(f"<div class='hit{' flag' if flag else ''}'><span class='src'>{e(c.source)} · "
                            f"score {score:.2f}</span>{_mark(c.text[:900])}{'…' if len(c.text) > 900 else ''}"
                            + (f"<div class='why'>⚠ ragpreflight: {e(flag)}</div>" if flag else "") + "</div>")
            cols.append(f"<div class='col' style='--c:{color}'><h3>{name}</h3>{ans_html}"
                        f"<details{' open' if not ans else ''}><summary>Retrieved passages ({len(side.retrieved[i])})</summary>"
                        f"{''.join(hits) or '<p class=empty>Nothing retrieved.</p>'}</details></div>")
        blocks.append(f"<h2 class='q'><span>Q{i + 1}</span>{e(q)}</h2><div class='cols'>{''.join(cols)}</div>")
    fixes = "".join(f"<li>{e(c)}</li>" for c in r.changes)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Before / after ragpreflight</title>
<style>{CSS}</style></head><body><main>
<h1>Same questions, before and after ragpreflight</h1>
<div class="sub">{e(r.report.root)} · retrieval: BM25 top-{len(r.before.retrieved[0]) if r.before.retrieved else 0} · before chunker: {e(r.chunker)} · answers: {e(r.model) if not r.answers_note else 'off'} · ragpreflight {__version__}</div>
<div class="fixes"><h2>What ragpreflight changed ({len(r.before.chunks)} chunks before → {len(r.after.chunks)} after)</h2><ul>{fixes}</ul>
<div class="sub">Only fixes ragpreflight itself reported were applied. Same retriever, same model, same questions on both sides.</div></div>
{''.join(blocks)}
</main></body></html>"""
