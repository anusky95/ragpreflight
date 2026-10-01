"""Output formatting for ragpreflight: terminal, JSON, and HTML reports."""

from __future__ import annotations

import html as html_mod
import json
import math
import re
import textwrap
from pathlib import Path

from ragpreflight._constants import SEVERITY_COLOURS, TOOL_SUGGESTIONS
from ragpreflight.models import CorpusReport, DocumentReport, Issue, Severity

# ---------------------------------------------------------------------------
# Document report rendering
# ---------------------------------------------------------------------------


def render_document_report(
    report: DocumentReport,
    fmt: str = "terminal",
    output: str | None = None,
    quiet: bool = False,
) -> None:
    """Render a DocumentReport to stdout or a file."""
    if quiet:
        import click

        click.echo(report.score)
        return

    if fmt == "json":
        _output_json(report.to_dict(), output)
        return
    if fmt == "html":
        _output_html_document(report, output)
        return
    if fmt == "sarif":
        _output_sarif([report], output)
        return
    _render_document_terminal(report)


def render_corpus_report(
    report: CorpusReport,
    fmt: str = "terminal",
    output: str | None = None,
    quiet: bool = False,
) -> None:
    """Render a CorpusReport to stdout or a file."""
    if quiet:
        import click

        click.echo(round(report.average_score))
        return

    if fmt == "json":
        _output_json(report.to_dict(), output)
        return
    if fmt == "html":
        _output_html_corpus(report, output)
        return
    if fmt == "sarif":
        _output_sarif(report.documents, output)
        return
    _render_corpus_terminal(report)


# ---------------------------------------------------------------------------
# Terminal rendering (rich)
# ---------------------------------------------------------------------------


def _render_document_terminal(report: DocumentReport) -> None:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    score_colour = _score_colour(report.score)
    bar = _score_bar(report.score)

    header = (
        f"[bold]ragpreflight — Document Readiness Report[/bold]\n"
        f"File: [cyan]{report.filepath}[/cyan]\n"
        f"Score: [{score_colour}]{report.score}/100  {bar}[/{score_colour}]\n"
        f"Format: {report.file_format.upper()}  ·  "
        f"Size: {_human_size(report.file_size_bytes)}  ·  "
        f"Pages: {report.page_count}  ·  "
        f"Extractable: {report.text_extractable_ratio:.0%}"
    )
    console.print(Panel(header, expand=False))

    if not report.issues:
        console.print("[bold green]✓ No issues found — document looks great![/bold green]")
        return

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
    table.add_column("Severity", width=10)
    table.add_column("Category", width=12)
    table.add_column("Issue")
    table.add_column("Location", width=14)
    table.add_column("Fix")

    for issue in report.issues:
        colour = SEVERITY_COLOURS.get(issue.severity.value, "white")
        issue_text = issue.message
        if issue.context:
            ctx = textwrap.shorten(issue.context, width=90, placeholder="…")
            issue_text += f"\n[dim italic]↳ {ctx}[/dim italic]"
        table.add_row(
            f"[{colour}]{issue.severity.value.upper()}[/{colour}]",
            issue.category.value,
            issue_text,
            issue.location or "—",
            textwrap.shorten(issue.suggestion or "—", width=60),
        )

    console.print(table)
    console.print(
        f"\n[dim]{len(report.issues)} issue(s) found  ·  "
        f"Score: {report.score}  ·  "
        f"{len(report.critical_issues)} critical[/dim]"
    )


def _render_corpus_terminal(report: CorpusReport) -> None:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    score_colour = _score_colour(int(report.average_score))
    bar = _score_bar(int(report.average_score))

    header = (
        f"[bold]ragpreflight — Corpus Readiness Report[/bold]\n"
        f"Directory: [cyan]{report.directory}[/cyan]\n"
        f"Documents: {report.total_documents}  ·  "
        f"Avg Score: [{score_colour}]{report.average_score:.1f}  {bar}[/{score_colour}]\n"
        f"Duplicate Groups: {len(report.duplicate_groups)}"
    )
    console.print(Panel(header, expand=False))

    if report.corpus_issues:
        console.print("\n[bold]Corpus-level Issues[/bold]")
        for issue in report.corpus_issues:
            colour = SEVERITY_COLOURS.get(issue.severity.value, "white")
            console.print(
                f"  [{colour}]{issue.severity.value.upper()}[/{colour}]  {issue.message}"
            )
            if issue.suggestion:
                console.print(f"          [dim]→ {issue.suggestion}[/dim]")

    console.print("\n[bold]Document Scores[/bold]")
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("Score", width=7, justify="right")
    table.add_column("File")
    table.add_column("Format", width=8)
    table.add_column("Issues", width=8, justify="right")
    table.add_column("Critical", width=9, justify="right")

    for doc in sorted(report.documents, key=lambda d: d.score):
        colour = _score_colour(doc.score)
        table.add_row(
            f"[{colour}]{doc.score}[/{colour}]",
            Path(doc.filepath).name,
            doc.file_format.upper(),
            str(len(doc.issues)),
            str(len(doc.critical_issues)),
        )

    console.print(table)

    if report.worst_documents:
        console.print("\n[bold red]Worst Offenders (bottom 10%)[/bold red]")
        for doc in report.worst_documents:
            console.print(f"  [{_score_colour(doc.score)}]{doc.score:3d}[/]  {doc.filepath}")


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


def _output_json(data: dict, output: str | None) -> None:
    text = json.dumps(data, indent=2, default=str)
    if output:
        Path(output).write_text(text, encoding="utf-8")
        import click

        click.echo(f"JSON report written to {output}")
    else:
        import click

        click.echo(text)


# ---------------------------------------------------------------------------
# HTML output
# ---------------------------------------------------------------------------


def _output_html_document(report: DocumentReport, output: str | None) -> None:
    html = _build_html(
        title=f"ragpreflight — {Path(report.filepath).name}",
        body=_html_document_body(report),
    )
    _write_html(html, output)


def _output_html_corpus(report: CorpusReport, output: str | None) -> None:
    html = _build_html(
        title=f"ragpreflight corpus — {report.directory}",
        body=_html_corpus_body(report),
    )
    _write_html(html, output)


def _write_html(html: str, output: str | None) -> None:
    import click

    if output:
        Path(output).write_text(html, encoding="utf-8")
        click.echo(f"HTML report written to {output}")
    else:
        click.echo(html)


# ---------------------------------------------------------------------------
# HTML CSS (module-level constant — not an f-string, no brace escaping)
# ---------------------------------------------------------------------------

_HTML_CSS = """<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {
  --bg: #f8f7f5; --fg: #1c1917; --fg-secondary: #57534e; --fg-muted: #a8a29e;
  --surface: #ffffff; --surface-raised: #fafaf9;
  --border: #e7e5e4; --border-light: #f0efed;
  --accent: #7c3aed; --accent-soft: #ede9fe;
  --green: #16a34a; --green-soft: #dcfce7;
  --amber: #d97706; --amber-soft: #fef3c7;
  --red: #dc2626; --red-soft: #fee2e2;
  --blue: #2563eb; --blue-soft: #dbeafe;
  --code-bg: #1e1b2e; --code-fg: #e2dff0;
  --highlight-bg: #fbbf24; --highlight-fg: #1c1917;
  font-family: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #1a1917; --fg: #e7e5e4; --fg-secondary: #a8a29e; --fg-muted: #78716c;
    --surface: #292524; --surface-raised: #1c1b19;
    --border: #44403c; --border-light: #353330;
    --accent: #a78bfa; --accent-soft: #2e1065;
    --green: #4ade80; --green-soft: #14532d;
    --amber: #fbbf24; --amber-soft: #451a03;
    --red: #f87171; --red-soft: #450a0a;
    --blue: #60a5fa; --blue-soft: #1e3a5f;
    --code-bg: #0f0d1a; --code-fg: #d4d0e8;
    --highlight-bg: #854d0e; --highlight-fg: #fef3c7;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #1a1917; --fg: #e7e5e4; --fg-secondary: #a8a29e; --fg-muted: #78716c;
  --surface: #292524; --surface-raised: #1c1b19;
  --border: #44403c; --border-light: #353330;
  --accent: #a78bfa; --accent-soft: #2e1065;
  --green: #4ade80; --green-soft: #14532d;
  --amber: #fbbf24; --amber-soft: #451a03;
  --red: #f87171; --red-soft: #450a0a;
  --blue: #60a5fa; --blue-soft: #1e3a5f;
  --code-bg: #0f0d1a; --code-fg: #d4d0e8;
  --highlight-bg: #854d0e; --highlight-fg: #fef3c7;
  color-scheme: dark;
}
body {
  background: var(--bg); color: var(--fg);
  max-width: 800px; margin: 0 auto; padding: 32px 20px; line-height: 1.55;
}
*, *::before, *::after { box-sizing: border-box; }
h1, h2, h3 { text-wrap: balance; }

/* Header */
.rp-header { display: flex; align-items: flex-start; gap: 16px; margin-bottom: 24px; }
.rp-logo {
  flex-shrink: 0; width: 48px; height: 48px; background: var(--accent);
  border-radius: 12px; display: grid; place-items: center; color: #fff;
  font-family: 'IBM Plex Mono', monospace; font-weight: 700; font-size: 18px; letter-spacing: -1px;
}
.rp-header h1 { font-size: 1.35rem; font-weight: 700; margin: 0; color: var(--fg); }
.rp-header .subtitle { font-size: 0.85rem; color: var(--fg-muted); margin-top: 2px; }

/* Score card */
.score-card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 16px;
  padding: 28px 32px; display: flex; align-items: center; gap: 32px; margin-bottom: 20px;
}
.gauge { position: relative; width: 120px; height: 120px; flex-shrink: 0; }
.gauge svg { width: 100%; height: 100%; }
.gauge-text {
  position: absolute; inset: 0; display: flex; flex-direction: column;
  align-items: center; justify-content: center;
}
.gauge-num { font-size: 2.4rem; font-weight: 700; line-height: 1; font-variant-numeric: tabular-nums; }
.gauge-label { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--fg-muted); margin-top: 2px; }
.score-details { min-width: 0; }
.verdict-pill {
  display: inline-block; padding: 4px 14px; border-radius: 20px;
  font-size: 0.78rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;
}
.verdict-ready  { background: var(--green-soft); color: var(--green); }
.verdict-review { background: var(--amber-soft); color: var(--amber); }
.verdict-reject { background: var(--red-soft); color: var(--red); }
.meta-grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
  gap: 4px 16px; margin-top: 14px;
}
.meta-item { display: flex; flex-direction: column; }
.meta-label { font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--fg-muted); }
.meta-value { font-size: 0.95rem; font-weight: 600; font-variant-numeric: tabular-nums; }

/* Count chips */
.counts-strip { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 28px; }
.count-chip {
  display: flex; align-items: center; gap: 6px; padding: 6px 14px; border-radius: 10px;
  font-size: 0.82rem; font-weight: 600; font-variant-numeric: tabular-nums;
}
.count-chip .dot { width: 8px; height: 8px; border-radius: 50%; }
.chip-critical { background: var(--red-soft); color: var(--red); }
.chip-critical .dot { background: var(--red); }
.chip-warning { background: var(--amber-soft); color: var(--amber); }
.chip-warning .dot { background: var(--amber); }
.chip-info { background: var(--blue-soft); color: var(--blue); }
.chip-info .dot { background: var(--blue); }
.chip-ok { background: var(--green-soft); color: var(--green); }
.chip-ok .dot { background: var(--green); }

/* Section header */
.section-header {
  font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.1em;
  color: var(--fg-muted); font-weight: 600; margin-bottom: 12px;
  padding-bottom: 8px; border-bottom: 1px solid var(--border-light);
}

/* Issue card */
.issue-card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  margin-bottom: 12px; overflow: hidden; transition: border-color 0.15s;
}
.issue-card:hover, .issue-card[open] { border-color: var(--accent); }
.issue-card summary {
  list-style: none; cursor: pointer; padding: 16px 20px;
  display: flex; align-items: flex-start; gap: 12px;
}
.issue-card summary::-webkit-details-marker { display: none; }
.issue-card summary::after {
  content: ''; width: 20px; height: 20px; flex-shrink: 0; margin-left: auto;
  background: var(--fg-muted);
  -webkit-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20'%3E%3Cpath d='M6 8l4 4 4-4'/%3E%3C/svg%3E") center / contain no-repeat;
  mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 20 20'%3E%3Cpath d='M6 8l4 4 4-4'/%3E%3C/svg%3E") center / contain no-repeat;
  transition: transform 0.2s;
}
.issue-card[open] summary::after { transform: rotate(180deg); }
.sev-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; margin-top: 5px; }
.sev-dot.warning { background: var(--amber); }
.sev-dot.info { background: var(--blue); }
.sev-dot.critical { background: var(--red); }
.issue-title { font-weight: 600; font-size: 0.92rem; line-height: 1.4; }
.issue-title .category-tag {
  display: inline-block; font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.06em;
  padding: 1px 7px; border-radius: 4px; background: var(--border-light);
  color: var(--fg-secondary); font-weight: 500; vertical-align: 2px; margin-right: 4px;
}
.issue-body { padding: 0 20px 20px; display: flex; flex-direction: column; gap: 14px; }
.issue-detail { font-size: 0.84rem; color: var(--fg-secondary); line-height: 1.6; white-space: pre-line; }

/* Snippet block */
.snippet-block {
  background: var(--code-bg); border-radius: 10px; padding: 16px 18px;
  position: relative; overflow-x: auto;
}
.snippet-label {
  position: absolute; top: 8px; right: 12px; font-size: 0.65rem;
  text-transform: uppercase; letter-spacing: 0.08em; color: var(--fg-muted); opacity: 0.6;
}
.snippet-text {
  font-family: 'IBM Plex Mono', monospace; font-size: 0.82rem;
  color: var(--code-fg); line-height: 1.65; white-space: pre-wrap; word-break: break-word;
}
.snippet-text .hl {
  background: var(--highlight-bg); color: var(--highlight-fg);
  padding: 1px 3px; border-radius: 3px; font-weight: 500;
}

/* Impact block */
.impact-block {
  background: var(--surface-raised); border-left: 3px solid var(--amber);
  border-radius: 0 8px 8px 0; padding: 12px 16px;
}
.impact-block.info-impact { border-left-color: var(--blue); }
.impact-block h4 {
  margin: 0; font-size: 0.78rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.05em; color: var(--fg-secondary);
}
.impact-block p { margin: 4px 0 0; font-size: 0.84rem; color: var(--fg-secondary); line-height: 1.5; }

/* Fix block */
.fix-block {
  display: flex; gap: 8px; align-items: flex-start; padding: 10px 14px;
  background: var(--green-soft); border-radius: 8px;
}
.fix-block .fix-icon { flex-shrink: 0; color: var(--green); font-weight: 700; font-size: 1rem; }
.fix-block p { margin: 0; font-size: 0.84rem; color: var(--fg); line-height: 1.5; }

/* Install block */
.install-block {
  background: var(--accent-soft); border-radius: 8px; padding: 12px 16px;
}
.install-block h4 {
  margin: 0 0 8px; font-size: 0.72rem; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.06em; color: var(--accent);
}
.install-item { display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
.install-item:last-child { margin-bottom: 0; }
.install-item code {
  background: var(--code-bg); color: var(--code-fg); padding: 3px 8px; border-radius: 4px;
  font-family: 'IBM Plex Mono', monospace; font-size: 0.78rem; white-space: nowrap;
}
.install-item span { font-size: 0.8rem; color: var(--fg-secondary); }

/* Taxonomy links */
.tax-links { display: flex; flex-wrap: wrap; gap: 6px; }
.tax-link {
  display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px;
  background: var(--accent-soft); border-radius: 6px;
  font-size: 0.78rem; font-weight: 600; color: var(--accent);
  text-decoration: none; transition: opacity 0.15s;
}
.tax-link:hover { opacity: 0.8; }

/* Page map */
.page-map { display: flex; gap: 3px; flex-wrap: wrap; margin: 8px 0; }
.page-cell {
  width: 36px; height: 28px; border-radius: 4px; display: grid; place-items: center;
  font-size: 0.68rem; font-weight: 600; font-variant-numeric: tabular-nums;
  border: 1px solid var(--border); background: var(--surface); color: var(--fg-muted);
}
.page-cell.hit { background: var(--amber-soft); border-color: var(--amber); color: var(--amber); }
.page-cell.hit-critical { background: var(--red-soft); border-color: var(--red); color: var(--red); }
.page-cell.hit-info { background: var(--blue-soft); border-color: var(--blue); color: var(--blue); }

/* Score breakdown tiles */
.breakdown-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.breakdown-tile {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px;
}
.breakdown-tile .bl-label {
  font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--fg-muted);
}
.breakdown-tile .bl-value { font-size: 1.4rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.breakdown-tile .bl-weight { font-size: 0.75rem; color: var(--fg-muted); }

/* Coverage grid */
.coverage-grid { display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0; }
.cov-cell {
  flex: 1; min-width: 80px; text-align: center; padding: 10px 8px; border-radius: 8px;
  font-size: 0.82rem; font-weight: 600;
}
.cov-cell strong { display: block; font-size: 1.3rem; }
.cov-direct  { background: var(--green-soft); color: var(--green); }
.cov-proxy   { background: var(--amber-soft); color: var(--amber); }
.cov-risk    { background: var(--blue-soft); color: var(--blue); }
.cov-runtime { background: var(--surface); border: 1px solid var(--border); color: var(--fg-muted); }

/* Cannot-determine box */
.cannot-box {
  background: var(--surface-raised); border: 1px solid var(--border); border-radius: 8px;
  padding: 16px 20px; font-size: 0.84rem; color: var(--fg-secondary); line-height: 1.6;
}
.cannot-box ul { margin: 8px 0; padding-left: 20px; }
.cannot-box li { margin-bottom: 6px; }
.cannot-box a { color: var(--accent); }

/* Corpus table */
.corpus-table { width: 100%; border-collapse: separate; border-spacing: 0; margin-top: 12px; font-size: 0.88rem; }
.corpus-table th {
  background: var(--surface-raised); color: var(--fg-secondary); padding: 10px 14px;
  text-align: left; font-weight: 600; font-size: 0.75rem; text-transform: uppercase;
  letter-spacing: 0.06em; border-bottom: 2px solid var(--border);
}
.corpus-table td { padding: 10px 14px; border-bottom: 1px solid var(--border-light); vertical-align: top; }
.corpus-table tr:hover td { background: var(--surface-raised); }
.doc-details summary { list-style: none; cursor: pointer; font-weight: 500; }
.doc-details summary::-webkit-details-marker { display: none; }
.doc-issues { padding: 10px 0 4px; }

/* Footer */
.rp-footer {
  margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--border-light);
  display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px;
  font-size: 0.75rem; color: var(--fg-muted);
}
.rp-footer a { color: var(--accent); text-decoration: none; font-weight: 500; }
.rp-footer a:hover { text-decoration: underline; }

@media (max-width: 500px) {
  .score-card { flex-direction: column; gap: 16px; padding: 20px; }
  .gauge { width: 100px; height: 100px; }
  .meta-grid { grid-template-columns: repeat(2, 1fr); }
  .counts-strip { flex-wrap: wrap; }
  .breakdown-grid { grid-template-columns: 1fr; }
}
</style>"""


def _build_html(title: str, body: str) -> str:
    e = html_mod.escape
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{e(title)}</title>
{_HTML_CSS}
</head>
<body>
{body}
<div class="rp-footer">
  <span>Generated by <a href="https://github.com/anusky95/ragpreflight">ragpreflight</a></span>
  <span>Taxonomy: <a href="https://doi.org/10.18653/v1/2026.trustnlp-main.27">Garani 2026, TrustNLP</a></span>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# HTML body generators
# ---------------------------------------------------------------------------


def _html_document_body(report: DocumentReport) -> str:
    e = html_mod.escape
    score = report.score
    colour_var = _score_colour_var(score)

    verdict_cls, verdict_label = _verdict_pill(score, len(report.critical_issues))

    n_crit = len(report.critical_issues)
    n_warn = len(report.warnings)
    n_info = len(report.issues) - n_crit - n_warn

    # Issue cards
    issue_cards = []
    for idx, issue in enumerate(report.issues):
        issue_cards.append(
            _issue_card_html(issue, page_count=report.page_count, open_first=(idx == 0))
        )
    issues_section = (
        "\n".join(issue_cards)
        if issue_cards
        else (
            '<div style="color:var(--green);font-weight:600;padding:16px 0;">'
            "No issues found &mdash; document looks great!</div>"
        )
    )

    # Collect unique tool categories for summary
    seen_cats: set[str] = set()
    all_install_html_parts: list[str] = []
    for issue in report.issues:
        cat = issue.category.value
        if cat not in seen_cats:
            seen_cats.add(cat)
            block = _install_html(cat, issue.message)
            if block:
                all_install_html_parts.append(block)

    install_summary = ""
    if all_install_html_parts:
        install_summary = (
            '<div class="section-header" style="margin-top:36px;">Recommended Tools</div>'
            + "\n".join(all_install_html_parts)
        )

    return f"""
<div class="rp-header">
  <div class="rp-logo">rp</div>
  <div>
    <h1>ragpreflight Document Audit</h1>
    <div class="subtitle">Pre-ingestion quality analysis</div>
  </div>
</div>

<div class="score-card">
  {_svg_gauge(score, colour_var)}
  <div class="score-details">
    <span class="verdict-pill {verdict_cls}">{verdict_label}</span>
    <div class="meta-grid">
      <div class="meta-item">
        <span class="meta-label">File</span>
        <span class="meta-value">{e(Path(report.filepath).name)}</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">Format</span>
        <span class="meta-value">{e(report.file_format.upper())} &middot; {report.page_count} page(s)</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">Size</span>
        <span class="meta-value">{_human_size(report.file_size_bytes)}</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">Extractable</span>
        <span class="meta-value" style="color:var({_score_colour_var(int(report.text_extractable_ratio * 100))})">{report.text_extractable_ratio:.0%}</span>
      </div>
    </div>
  </div>
</div>

<div class="counts-strip">
  <div class="count-chip {"chip-ok" if n_crit == 0 else "chip-critical"}"><span class="dot"></span> {n_crit} Critical</div>
  <div class="count-chip chip-warning"><span class="dot"></span> {n_warn} Warning{"s" if n_warn != 1 else ""}</div>
  <div class="count-chip chip-info"><span class="dot"></span> {n_info} Info</div>
</div>

<div class="section-header">Issues Found</div>
{issues_section}

{install_summary}

{_taxonomy_coverage_html()}
"""


def _html_corpus_body(report: CorpusReport) -> str:
    e = html_mod.escape
    avg = report.average_score
    score = int(round(avg))
    colour_var = _score_colour_var(score)
    critical_count = sum(len(d.critical_issues) for d in report.documents)
    total_issues = sum(len(d.issues) for d in report.documents)

    verdict_cls, verdict_label = _verdict_pill(score, critical_count)

    # Corpus-level issue cards
    corpus_cards = "\n".join(
        _issue_card_html(issue, page_count=0, open_first=False) for issue in report.corpus_issues
    )
    corpus_section = corpus_cards or (
        '<div style="color:var(--fg-muted);padding:8px 0;">No corpus-level issues.</div>'
    )

    # Top 5 actions
    all_issues: list[tuple[Issue, str]] = []
    for doc in report.documents:
        for issue in doc.issues:
            all_issues.append((issue, Path(doc.filepath).name))
    all_issues.sort(
        key=lambda x: (
            x[0].severity != Severity.CRITICAL,
            x[0].severity != Severity.WARNING,
        )
    )
    top_actions = ""
    for issue, fname in all_issues[:5]:
        sev = issue.severity.value
        top_actions += (
            f'<div class="issue-card" style="cursor:default;">'
            f'<div style="padding:14px 20px;display:flex;align-items:flex-start;gap:12px;">'
            f'<span class="sev-dot {sev}"></span>'
            f'<div style="min-width:0;">'
            f'<div class="issue-title"><span class="category-tag">{issue.category.value}</span> '
            f"{e(_summary_line(issue.message))}</div>"
            f'<div style="font-size:0.8rem;color:var(--fg-muted);margin-top:2px;">'
            f"{e(fname)}"
            + (f" &middot; {e(issue.suggestion)}" if issue.suggestion else "")
            + "</div></div></div></div>"
        )

    # Per-document table
    rows = ""
    for doc in sorted(report.documents, key=lambda d: d.score):
        sc_var = _score_colour_var(doc.score)
        crit_badge = (
            ' <span style="color:var(--red);font-size:0.72rem;font-weight:700;"> CRITICAL</span>'
            if doc.critical_issues
            else ""
        )
        # Inline issue cards for each document
        doc_issues_html = ""
        for issue in doc.issues:
            sev = issue.severity.value
            ctx_snip = ""
            if issue.context:
                short_ctx = textwrap.shorten(issue.context, width=120, placeholder="...")
                ctx_snip = (
                    f'<div class="snippet-block" style="margin-top:6px;padding:8px 12px;">'
                    f'<div class="snippet-text" style="font-size:0.76rem;">{e(short_ctx)}</div>'
                    f"</div>"
                )
            fix_note = ""
            if issue.suggestion:
                fix_note = (
                    f'<div style="color:var(--green);font-size:0.8rem;margin-top:4px;">'
                    f"&rarr; {e(issue.suggestion)}</div>"
                )
            tax_pills = ""
            if issue.taxonomy_refs:
                pills = " ".join(
                    f'<a class="tax-link" style="font-size:0.7rem;padding:2px 8px;" '
                    f'href="https://doi.org/10.18653/v1/2026.trustnlp-main.27" target="_blank">'
                    f"{e(r.mode_id)}</a>"
                    for r in issue.taxonomy_refs
                )
                tax_pills = f'<div style="margin-top:4px;">{pills}</div>'
            doc_issues_html += (
                f'<div style="margin-bottom:10px;padding:8px 12px;background:var(--surface-raised);'
                f'border-radius:6px;border-left:3px solid var({"--red" if sev == "critical" else ("--amber" if sev == "warning" else "--blue")});">'
                f'<div style="display:flex;align-items:flex-start;gap:8px;">'
                f'<span class="sev-dot {sev}" style="margin-top:3px;"></span>'
                f'<div style="min-width:0;">'
                f'<span class="category-tag">{issue.category.value}</span> '
                f'<span style="font-size:0.85rem;">{e(_summary_line(issue.message))}</span>'
                f"{ctx_snip}{fix_note}{tax_pills}"
                f"</div></div></div>"
            )

        no_issues = '<em style="color:var(--fg-muted);">No issues.</em>'
        rows += (
            f"<tr>"
            f'<td style="font-weight:700;color:var({sc_var});font-variant-numeric:tabular-nums;">{doc.score}</td>'
            f"<td><details class='doc-details'>"
            f'<summary class="doc-name">{e(Path(doc.filepath).name)}{crit_badge}</summary>'
            f'<div class="doc-issues">{doc_issues_html or no_issues}</div>'
            f"</details></td>"
            f"<td>{e(doc.file_format.upper())}</td>"
            f"<td>{len(doc.issues)}</td>"
            f'<td style="{"color:var(--red);font-weight:700;" if doc.critical_issues else ""}">'
            f"{len(doc.critical_issues)}</td>"
            f"</tr>"
        )

    # Collect all tool categories across corpus
    seen_cats: set[str] = set()
    all_install_parts: list[str] = []
    for doc in report.documents:
        for issue in doc.issues:
            cat = issue.category.value
            if cat not in seen_cats:
                seen_cats.add(cat)
                block = _install_html(cat, issue.message)
                if block:
                    all_install_parts.append(block)

    install_summary = ""
    if all_install_parts:
        install_summary = (
            '<div class="section-header" style="margin-top:36px;">Recommended Tools</div>'
            + "\n".join(all_install_parts)
        )

    return f"""
<div class="rp-header">
  <div class="rp-logo">rp</div>
  <div>
    <h1>ragpreflight Corpus Audit</h1>
    <div class="subtitle">Pre-ingestion quality analysis &middot; {report.total_documents} documents</div>
  </div>
</div>

<div class="score-card">
  {_svg_gauge(score, colour_var)}
  <div class="score-details">
    <span class="verdict-pill {verdict_cls}">{verdict_label}</span>
    <div class="meta-grid">
      <div class="meta-item">
        <span class="meta-label">Directory</span>
        <span class="meta-value">{e(report.directory)}</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">Documents</span>
        <span class="meta-value">{report.total_documents}</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">Duplicates</span>
        <span class="meta-value">{len(report.duplicate_groups)} group(s)</span>
      </div>
      <div class="meta-item">
        <span class="meta-label">Total Issues</span>
        <span class="meta-value">{total_issues}</span>
      </div>
    </div>
  </div>
</div>

<div class="counts-strip">
  <div class="count-chip {"chip-ok" if critical_count == 0 else "chip-critical"}"><span class="dot"></span> {critical_count} Critical</div>
  <div class="count-chip chip-warning"><span class="dot"></span> {sum(len(d.warnings) for d in report.documents)} Warnings</div>
  <div class="count-chip chip-info"><span class="dot"></span> {total_issues - critical_count - sum(len(d.warnings) for d in report.documents)} Info</div>
</div>

<div class="section-header">Corpus-level Issues</div>
{corpus_section}

<div class="section-header" style="margin-top:28px;">Top Actions Required</div>
{top_actions or '<div style="color:var(--fg-muted);padding:8px 0;">No issues found.</div>'}

<div class="section-header" style="margin-top:28px;">
  Document Scores
  <span style="font-weight:400;text-transform:none;letter-spacing:0;color:var(--fg-muted);font-size:0.75rem;">
    &mdash; click a filename to expand issues
  </span>
</div>
<table class="corpus-table">
  <thead><tr><th>Score</th><th>File</th><th>Format</th><th>Issues</th><th>Critical</th></tr></thead>
  <tbody>{rows}</tbody>
</table>

{install_summary}
{_taxonomy_coverage_html()}
{_cannot_determine_html()}
"""


# ---------------------------------------------------------------------------
# HTML helper functions
# ---------------------------------------------------------------------------


def _svg_gauge(score: int, colour_var: str) -> str:
    r = 52
    circumference = 2 * math.pi * r
    offset = circumference * (1 - score / 100)
    return f"""<div class="gauge">
  <svg viewBox="0 0 120 120">
    <circle cx="60" cy="60" r="{r}" fill="none" stroke="var(--border)" stroke-width="8"/>
    <circle cx="60" cy="60" r="{r}" fill="none" stroke="var({colour_var})" stroke-width="8"
            stroke-dasharray="{circumference:.2f}" stroke-dashoffset="{offset:.2f}"
            stroke-linecap="round" transform="rotate(-90 60 60)"/>
  </svg>
  <div class="gauge-text">
    <span class="gauge-num" style="color:var({colour_var})">{score}</span>
    <span class="gauge-label">of 100</span>
  </div>
</div>"""


def _score_colour_var(score: int) -> str:
    if score >= 70:
        return "--green"
    if score >= 40:
        return "--amber"
    return "--red"


def _verdict_pill(score: int, critical_count: int) -> tuple[str, str]:
    if score >= 70 and critical_count == 0:
        return "verdict-ready", "Ingest Ready"
    if score >= 40 or critical_count == 0:
        return "verdict-review", "Needs Review"
    return "verdict-reject", "Do Not Ingest"


def _extract_pages(location: str | None) -> list[int]:
    if not location:
        return []
    pages: set[int] = set()
    for m in re.finditer(r"(\d+)\s*[-–]\s*(\d+)", location):
        start, end = int(m.group(1)), int(m.group(2))
        if 0 < start <= end <= 5000:
            pages.update(range(start, end + 1))
    for m in re.finditer(r"\bpage\s+(\d+)", location, re.IGNORECASE):
        pages.add(int(m.group(1)))
    return sorted(pages)


def _page_map_html(page_count: int, affected_pages: list[int], severity: str) -> str:
    if page_count <= 0 or page_count > 200:
        return ""
    hit_cls = (
        "hit-critical" if severity == "critical" else ("hit-info" if severity == "info" else "hit")
    )
    affected_set = set(affected_pages)
    cells = []
    for p in range(1, page_count + 1):
        cls = hit_cls if p in affected_set else ""
        cells.append(f'<div class="page-cell {cls}">{p}</div>')
    return (
        '<div style="font-size:0.78rem;color:var(--fg-muted);margin-bottom:4px;">Pages affected:</div>'
        f'<div class="page-map">{"".join(cells)}</div>'
    )


def _install_html(category: str, message: str) -> str:
    tools = TOOL_SUGGESTIONS.get(category, [])
    if not tools:
        return ""
    if category == "content":
        msg = message.lower()
        if "pii" in msg or "email" in msg or "personal" in msg:
            filtered = [t for t in tools if "pii" in t[1].lower()]
            if filtered:
                tools = filtered
        elif "math" in msg or "formula" in msg or "equation" in msg:
            filtered = [t for t in tools if "math" in t[1].lower()]
            if filtered:
                tools = filtered
    items = "".join(
        f'<div class="install-item">'
        f"<code>{html_mod.escape(t[2])}</code>"
        f"<span>{html_mod.escape(t[1])}</span>"
        f"</div>"
        for t in tools
    )
    return f'<div class="install-block"><h4>Recommended tools</h4>{items}</div>'


def _summary_line(message: str) -> str:
    first = message.split("\n")[0].strip()
    if first.endswith(":"):
        first = first[:-1]
    if len(first) > 120:
        first = first[:117] + "..."
    return first


def _issue_card_html(issue: Issue, page_count: int = 0, open_first: bool = False) -> str:
    e = html_mod.escape
    sev = issue.severity.value
    cat = issue.category.value
    open_attr = " open" if open_first else ""

    summary_text = _summary_line(issue.message)
    full_message = issue.message.strip()

    # Show full message details inside body if truncated
    detail_html = ""
    if full_message != summary_text and full_message != summary_text + ":":
        detail_html = f'<div class="issue-detail">{e(full_message)}</div>'

    # Context snippet
    ctx_html = ""
    if issue.context:
        ctx_html = (
            '<div class="snippet-block">'
            '<span class="snippet-label">Extracted text</span>'
            f'<div class="snippet-text">{e(issue.context)}</div>'
            "</div>"
        )

    # Page map
    page_html = ""
    affected = _extract_pages(issue.location)
    if affected and page_count > 0:
        page_html = _page_map_html(page_count, affected, sev)

    # Impact (from taxonomy explanation)
    impact_html = ""
    if issue.taxonomy_refs:
        for ref in issue.taxonomy_refs:
            if ref.explanation:
                border_cls = " info-impact" if sev == "info" else ""
                impact_html = (
                    f'<div class="impact-block{border_cls}">'
                    f"<h4>RAG Impact</h4>"
                    f"<p>{e(ref.explanation)}</p>"
                    f"</div>"
                )
                break

    # Fix suggestion
    fix_html = ""
    if issue.suggestion:
        fix_html = (
            '<div class="fix-block">'
            '<span class="fix-icon">&rarr;</span>'
            f"<p>{e(issue.suggestion)}</p>"
            "</div>"
        )

    # Install suggestions
    install = _install_html(cat, issue.message)

    # Taxonomy links
    tax_html = ""
    if issue.taxonomy_refs:
        links = []
        for ref in issue.taxonomy_refs:
            name = f" &middot; {e(ref.mode_name)}" if ref.mode_name else ""
            links.append(
                f'<a class="tax-link" href="https://doi.org/10.18653/v1/2026.trustnlp-main.27" '
                f'target="_blank">{e(ref.mode_id)}{name}</a>'
            )
        tax_html = f'<div class="tax-links">{"".join(links)}</div>'

    loc_note = ""
    if issue.location:
        loc_note = f' <span style="font-weight:400;font-size:0.8rem;color:var(--fg-muted);">&middot; {e(issue.location)}</span>'

    return f"""<details class="issue-card"{open_attr}>
  <summary>
    <span class="sev-dot {sev}"></span>
    <span class="issue-title">
      <span class="category-tag">{cat}</span>
      {e(summary_text)}{loc_note}
    </span>
  </summary>
  <div class="issue-body">
    {detail_html}
    {ctx_html}
    {page_html}
    {impact_html}
    {fix_html}
    {install}
    {tax_html}
  </div>
</details>"""


def _taxonomy_coverage_html() -> str:
    try:
        from ragpreflight.taxonomy import detector_coverage

        cov = detector_coverage()
        return f"""
<div class="section-header" style="margin-top:36px;">Garani 2026 Failure Taxonomy Coverage</div>
<p style="font-size:0.84rem;color:var(--fg-secondary);margin:0 0 12px;">
  Each issue is linked to a failure mode from the
  <a href="https://doi.org/10.18653/v1/2026.trustnlp-main.27" target="_blank" style="color:var(--accent);">
  peer-reviewed taxonomy of 33 RAG failure modes</a> (Garani 2026, TrustNLP @ ACL).
</p>
<div class="coverage-grid">
  <div class="cov-cell cov-direct"><strong>{len(cov.direct)}</strong>direct</div>
  <div class="cov-cell cov-proxy"><strong>{len(cov.proxy)}</strong>proxy</div>
  <div class="cov-cell cov-risk"><strong>{len(cov.risk_signal)}</strong>risk signal</div>
  <div class="cov-cell cov-runtime"><strong>{len(cov.runtime_required)}</strong>runtime req.</div>
</div>
<p style="font-size:0.78rem;color:var(--fg-muted);margin:0;">
  Static pre-ingestion analysis covers {len(cov.direct) + len(cov.proxy) + len(cov.risk_signal)} of 33 modes.
  The remaining {len(cov.runtime_required) + len(cov.unsupported)} require live system traces, LLM outputs, or agent logs.
  For runtime coverage:
  <a href="https://deepeval.com" target="_blank" style="color:var(--accent);">DeepEval</a> &middot;
  <a href="https://docs.ragas.io" target="_blank" style="color:var(--accent);">Ragas</a> &middot;
  <a href="https://pypi.org/project/ragchecker/" target="_blank" style="color:var(--accent);">RAGChecker</a>
</p>"""
    except Exception:
        return ""


def _cannot_determine_html() -> str:
    return """
<div class="section-header" style="margin-top:36px;">What This Audit Cannot Determine</div>
<div class="cannot-box">
<p>Static pre-ingestion scanning cannot assess these failure modes
(Garani 2026 taxonomy) &mdash; they require live system traces, LLM outputs, or agent logs:</p>
<ul>
  <li><strong>F6</strong> Embedding Drift / Model Mismatch &mdash; requires embedding config + version metadata at runtime</li>
  <li><strong>F12</strong> Position-of-Gold Bias &mdash; requires live LLM context window</li>
  <li><strong>F13&ndash;F17</strong> Generation failures (hallucination, conflicting info, partial answers) &mdash; require generated outputs + faithfulness eval</li>
  <li><strong>F19&ndash;F25</strong> Deployment failures (monitoring, latency, authorization, PII leaks) &mdash; require runtime metrics and config</li>
  <li><strong>F26&ndash;F33</strong> Agentic orchestration failures &mdash; require agent traces</li>
</ul>
</div>"""


# ---------------------------------------------------------------------------
# Existing helpers (terminal + shared)
# ---------------------------------------------------------------------------


def _score_colour(score: int) -> str:
    if score >= 70:
        return "green"
    if score >= 40:
        return "yellow"
    return "red"


def _score_bar(score: int, width: int = 20) -> str:
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024**2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024**3:
        return f"{size_bytes / 1024**2:.1f} MB"
    return f"{size_bytes / 1024**3:.1f} GB"


# ---------------------------------------------------------------------------
# SARIF output (GitHub Code Scanning compatible)
# ---------------------------------------------------------------------------

_SARIF_LEVEL = {"critical": "error", "warning": "warning", "info": "note"}

_SARIF_RULES = [
    {
        "id": "OCR001",
        "name": "OcrError",
        "category": "ocr",
        "shortDescription": "OCR character substitution or artifact detected",
    },
    {
        "id": "ENC001",
        "name": "EncodingError",
        "category": "encoding",
        "shortDescription": "Encoding issue or low-confidence character detection",
    },
    {
        "id": "STR001",
        "name": "StructureError",
        "category": "structure",
        "shortDescription": "Document structural problem (broken hierarchy, tables)",
    },
    {
        "id": "CNT001",
        "name": "ContentError",
        "category": "content",
        "shortDescription": "Content quality issue (sparse, empty, PII, formulas)",
    },
    {
        "id": "META001",
        "name": "MetadataError",
        "category": "metadata",
        "shortDescription": "Missing or incomplete document metadata",
    },
    {
        "id": "CHK001",
        "name": "ChunkingError",
        "category": "chunking",
        "shortDescription": "Chunk boundary or coherence problem",
    },
    {
        "id": "DUP001",
        "name": "Duplication",
        "category": "duplication",
        "shortDescription": "Near-duplicate documents detected",
    },
    {
        "id": "STA001",
        "name": "Staleness",
        "category": "staleness",
        "shortDescription": "Document may be outdated",
    },
]

_CATEGORY_TO_RULE_ID = {r["category"]: r["id"] for r in _SARIF_RULES}


def _output_sarif(reports: list[DocumentReport], output: str | None) -> None:
    """Generate a SARIF 2.1.0 report for GitHub Code Scanning integration."""
    from ragpreflight import __version__

    results = []
    for report in reports:
        for issue in report.issues:
            rule_id = _CATEGORY_TO_RULE_ID.get(issue.category.value, "CNT001")
            level = _SARIF_LEVEL.get(issue.severity.value, "note")
            message_text = issue.message
            if issue.suggestion:
                message_text += f" — {issue.suggestion}"
            results.append(
                {
                    "ruleId": rule_id,
                    "level": level,
                    "message": {"text": message_text},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {
                                    "uri": report.filepath,
                                    "uriBaseId": "%SRCROOT%",
                                },
                                "region": {"startLine": 1},
                            }
                        }
                    ],
                    "properties": {
                        "score": report.score,
                        "location": issue.location or "",
                    },
                }
            )

    sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "ragpreflight",
                        "version": __version__,
                        "informationUri": "https://github.com/anusky95/ragpreflight",
                        "rules": [
                            {
                                "id": r["id"],
                                "name": r["name"],
                                "shortDescription": {"text": r["shortDescription"]},
                                "helpUri": "https://github.com/anusky95/ragpreflight#rules",
                            }
                            for r in _SARIF_RULES
                        ],
                    }
                },
                "results": results,
            }
        ],
    }

    _output_json(sarif, output)
