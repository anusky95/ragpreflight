"""Render a Report as terminal output, JSON, Markdown or a self-contained HTML page."""

from __future__ import annotations

import json

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from . import __version__
from .models import Finding, Report, Severity
from .taxonomy import PAPER_URL

TAG = {
    "version-conflict": "CONFLICT", "version-stale": "STALE", "version-period-twins": "TWINS",
    "duplicate": "DUPLICATE", "extraction-empty": "EMPTY", "extraction-scanned": "SCANNED",
    "extraction-images": "IMAGES", "extraction-garbled": "GARBLED", "extraction-table": "TABLE",
    "chunk-split": "SPLIT", "chunk-orphan-heading": "HEADING", "chunk-table-split": "TABLE SPLIT",
    "pii": "PII", "secret": "SECRET", "metadata-no-date": "NO DATE", "metadata-no-owner": "NO OWNER",
    "metadata-placeholder": "PLACEHOLDER", "metadata-old": "OLD", "unreadable": "UNREADABLE",
}
ICON = {Severity.ERROR: ("✗", "bold red"), Severity.WARN: ("!", "bold yellow"), Severity.INFO: ("·", "dim")}
VERDICT_STYLE = {"READY": "bold green", "REVIEW": "bold yellow", "NOT READY": "bold red"}


def fancy(console: Console) -> bool:
    """Can this terminal print the nicer glyphs? (Legacy Windows consoles use cp1252.)"""
    try:
        "✗↔→✓".encode(console.encoding or "utf-8")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def tag(f: Finding) -> str:
    return TAG.get(f.check, f.check.upper()[:12])


def where(f: Finding, arrow: str = "↔") -> str:
    s = f.path or ""
    if f.location:
        s += f" {f.location}"
    if f.related and f.check in ("version-conflict", "version-stale", "duplicate"):
        s += f"  {arrow}  " + ", ".join(f.related[:2])
    return s


# ------------------------------------------------------------------ terminal
def print_terminal(report: Report, console: Console | None = None, show_all: bool = False,
                   limit: int = 20, elapsed: float | None = None) -> None:
    c = console or Console(highlight=False)
    nice = fancy(c)
    pages = sum(len(d.pages) for d in report.documents)
    head = f"[bold]ragpreflight[/bold] {__version__} · {len(report.documents)} files · {pages} pages"
    if elapsed is not None:
        head += f" · {elapsed:.1f}s"
    c.print(head)
    c.print(f"[dim]{report.root}[/dim]\n")

    shown = [f for f in report.findings if show_all or f.severity != Severity.INFO]
    hidden_info = len(report.findings) - len(shown)
    if not show_all and len(shown) > limit:
        hidden_more = len(shown) - limit
        shown = shown[:limit]
    else:
        hidden_more = 0
    if not report.findings:
        c.print("[green]No issues found.[/green]\n")
    for f in shown:
        icon, style = ICON[f.severity]
        if not nice and icon == "✗":
            icon = "x"
        line = Text()
        line.append(f" {icon} ", style=style)
        line.append(f"{tag(f):<11}", style=style)
        line.append(f.title)
        c.print(line)
        sub = Text("               ")
        sub.append(where(f, "↔" if nice else "<->"), style="cyan")
        if f.f_codes:
            sub.append("   " + " ".join(f.f_codes), style="magenta")
        c.print(sub)
        if f.severity == Severity.ERROR and f.fix:
            c.print(Text("               " + ("→ " if nice else "-> ") + f.fix, style="dim"))
    notes = []
    if hidden_more:
        notes.append(f"{hidden_more} more")
    if hidden_info:
        notes.append(f"{hidden_info} notes")
    if notes:
        c.print(f"\n[dim]  + {' and '.join(notes)} hidden · use --all to show everything[/dim]")

    n = report.counts()
    def pl(k: int, word: str) -> str:
        return f"{k} {word}" + ("" if k == 1 else "s")

    summary = Text()
    summary.append(pl(n["error"], "error"), style="red" if n["error"] else "dim")
    summary.append(" · ")
    summary.append(pl(n["warn"], "warning"), style="yellow" if n["warn"] else "dim")
    summary.append(" · ")
    summary.append(pl(n["info"], "note"), style="dim")
    summary.append(f"      readiness {report.score}/100", style="bold")
    first = next((f for f in report.findings if f.severity != Severity.INFO), None)
    if first and first.fix:
        summary.append(f"\nFix first: {first.fix}", style="dim")
    c.print()
    c.print(Panel(summary, title=Text(report.verdict, style=VERDICT_STYLE[report.verdict]),
                  title_align="left", expand=False, border_style=VERDICT_STYLE[report.verdict].split()[-1]))
    c.print("[dim]F-codes: failure modes from Garani (2026), ACL TrustNLP · `ragpreflight explain F14`[/dim]")


# ------------------------------------------------------------------ JSON / Markdown
def to_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


def to_markdown(report: Report) -> str:
    n = report.counts()
    emoji = {"READY": "✅", "REVIEW": "⚠️", "NOT READY": "❌"}[report.verdict]
    lines = [
        f"## {emoji} ragpreflight: {report.verdict} — {report.score}/100",
        "",
        f"{len(report.documents)} files · **{n['error']} error{'s' * (n['error'] != 1)}** · "
        f"{n['warn']} warning{'s' * (n['warn'] != 1)} · {n['info']} note{'s' * (n['info'] != 1)}",
        "",
        "| | Issue | File | Modes |",
        "|---|---|---|---|",
    ]
    sev_icon = {Severity.ERROR: "❌", Severity.WARN: "⚠️", Severity.INFO: "ℹ️"}
    for f in report.findings:
        if f.severity == Severity.INFO:
            continue
        title = f.title.replace("|", "\\|")
        lines.append(f"| {sev_icon[f.severity]} | **{tag(f)}** {title} | `{where(f).replace('|', '/')}` | {' '.join(f.f_codes)} |")
    lines += ["", f"<sub>ragpreflight {__version__} · failure modes from [Garani 2026]({PAPER_URL})</sub>"]
    return "\n".join(lines)


# ------------------------------------------------------------------ HTML
from .html_report import to_html  # noqa: E402,F401  (re-exported)
