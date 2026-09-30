"""Output formatting for ragpreflight: terminal, JSON, and HTML reports."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

from ragpreflight._constants import SEVERITY_COLOURS
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


def _build_html(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          max-width: 1000px; margin: 40px auto; padding: 0 24px; color: #1a1a2e; line-height: 1.5; }}
  h1 {{ font-size: 1.5rem; color: #1a1a2e; margin-bottom: 4px; }}
  h2 {{ font-size: 1.1rem; color: #2c3e50; margin-top: 32px; border-bottom: 2px solid #ecf0f1; padding-bottom: 6px; }}
  a {{ color: #2980b9; }}

  /* Verdict banner */
  .verdict {{ border-radius: 8px; padding: 16px 20px; margin: 20px 0; display: flex; align-items: center; gap: 16px; }}
  .verdict-ready   {{ background: #eafaf1; border-left: 5px solid #27ae60; }}
  .verdict-review  {{ background: #fef9e7; border-left: 5px solid #f39c12; }}
  .verdict-fail    {{ background: #fdedec; border-left: 5px solid #e74c3c; }}
  .verdict-label {{ font-size: 1.4rem; font-weight: 800; letter-spacing: 0.04em; }}
  .verdict-ready  .verdict-label {{ color: #27ae60; }}
  .verdict-review .verdict-label {{ color: #d68910; }}
  .verdict-fail   .verdict-label {{ color: #c0392b; }}

  /* Score bar */
  .score-row {{ display: flex; align-items: center; gap: 12px; margin: 8px 0; }}
  .score-num {{ font-size: 2.5rem; font-weight: 800; }}
  .score-num.good {{ color: #27ae60; }}
  .score-num.ok   {{ color: #f39c12; }}
  .score-num.bad  {{ color: #e74c3c; }}
  .bar {{ background: #ecf0f1; border-radius: 6px; height: 10px; width: 180px; }}
  .bar-fill {{ height: 100%; border-radius: 6px; }}

  /* Meta pills */
  .meta {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 16px; }}
  .pill {{ background: #f0f3f7; border-radius: 4px; padding: 2px 10px; font-size: 0.82rem; color: #555; }}

  /* Issue badges */
  .badge {{ display: inline-block; padding: 1px 7px; border-radius: 10px;
             font-size: 0.72rem; font-weight: 700; text-transform: uppercase; vertical-align: middle; }}
  .badge-critical {{ background: #fde8e8; color: #c0392b; }}
  .badge-warning  {{ background: #fef9e7; color: #d68910; }}
  .badge-info     {{ background: #eaf4fb; color: #1a5276; }}

  /* Issue list */
  .issue-list {{ list-style: none; padding: 0; margin: 0; }}
  .issue-item {{ border: 1px solid #e8ecef; border-radius: 6px; margin-bottom: 8px; }}
  .issue-summary {{ padding: 10px 14px; cursor: pointer; display: flex; align-items: flex-start; gap: 10px; }}
  .issue-summary::-webkit-details-marker {{ display: none; }}
  .issue-body {{ padding: 8px 14px 12px 14px; background: #f8f9fa;
                  border-top: 1px solid #e8ecef; font-size: 0.88em; }}
  .ctx {{ background: #f0f0f0; border-left: 3px solid #ccc; padding: 5px 10px; margin: 6px 0;
           font-family: monospace; font-size: 0.85em; color: #555; border-radius: 0 3px 3px 0; }}
  .fix {{ color: #27ae60; margin: 4px 0; }}
  .tax-ref {{ background: #f8f4ff; border-left: 3px solid #7c4dff; padding: 6px 10px;
              margin-top: 6px; border-radius: 0 4px 4px 0; font-size: 0.83em; }}
  .tax-ref a {{ color: #7c4dff; font-weight: 600; text-decoration: none; }}
  .tax-ref a:hover {{ text-decoration: underline; }}
  .tax-rel {{ color: #888; }}
  .tax-item {{ display: block; margin-bottom: 4px; }}
  .tax-def {{ color: #555; display: block; margin-top: 3px; font-style: italic; }}
  .tax-expl {{ color: #777; display: block; margin-top: 2px; }}

  /* Top actions */
  .actions {{ list-style: none; padding: 0; }}
  .actions li {{ padding: 8px 0; border-bottom: 1px solid #f0f0f0; display: flex; align-items: flex-start; gap: 10px; }}
  .actions li:last-child {{ border-bottom: none; }}
  .action-body {{ flex: 1; }}
  .action-file {{ font-weight: 600; font-size: 0.9em; }}
  .action-fix {{ color: #555; font-size: 0.85em; margin-top: 2px; }}

  /* Table */
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 0.9em; }}
  th {{ background: #2c3e50; color: white; padding: 9px 12px; text-align: left; font-weight: 600; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #ecf0f1; vertical-align: top; }}
  tr:hover td {{ background: #f8f9fa; }}
  td.good {{ color: #27ae60; font-weight: 700; }}
  td.ok   {{ color: #d68910; font-weight: 700; }}
  td.bad  {{ color: #e74c3c; font-weight: 700; }}

  /* Doc issue expand inside table */
  .doc-details summary {{ list-style: none; cursor: pointer; }}
  .doc-details summary::-webkit-details-marker {{ display: none; }}
  .doc-name {{ font-weight: 500; }}
  .doc-issues {{ padding: 8px 0 4px 4px; }}
  .issue-row {{ margin-bottom: 8px; font-size: 0.85em; padding: 6px 10px;
                 background: #f8f9fa; border-radius: 4px; border-left: 3px solid #ddd; }}
  .issue-row.sev-critical {{ border-left-color: #e74c3c; }}
  .issue-row.sev-warning  {{ border-left-color: #f39c12; }}
  .issue-row.sev-info     {{ border-left-color: #3498db; }}

  /* Coverage grid */
  .coverage-grid {{ display: flex; gap: 10px; flex-wrap: wrap; margin: 12px 0; }}
  .cov-cell {{ flex: 1; min-width: 100px; text-align: center; padding: 12px 8px;
                border-radius: 6px; font-size: 0.85rem; }}
  .cov-cell strong {{ display: block; font-size: 1.5rem; }}
  .cov-direct  {{ background: #eafaf1; color: #1e8449; }}
  .cov-proxy   {{ background: #fef9e7; color: #9a7d0a; }}
  .cov-risk    {{ background: #fdf2e9; color: #a04000; }}
  .cov-runtime {{ background: #eaf2fb; color: #1a5276; }}
  .cov-unsup   {{ background: #f2f3f4; color: #717d7e; }}

  /* Cannot-determine box */
  .cannot-box {{ background: #f8f9fa; border: 1px solid #ddd; border-radius: 6px;
                  padding: 16px 20px; font-size: 0.88em; }}
  .cannot-box ul {{ margin: 8px 0; padding-left: 20px; }}
  .cannot-box li {{ margin-bottom: 6px; }}

  footer {{ margin-top: 48px; padding-top: 16px; border-top: 1px solid #ecf0f1;
             color: #aaa; font-size: 0.8em; }}
</style>
</head>
<body>
{body}
<footer>
  Generated by <a href="https://github.com/anusky95/ragpreflight">ragpreflight</a>
  &nbsp;·&nbsp;
  Taxonomy: <a href="https://doi.org/10.18653/v1/2026.trustnlp-main.27">Garani 2026, TrustNLP</a>
</footer>
</body>
</html>"""


def _html_document_body(report: DocumentReport) -> str:
    score_cls = "good" if report.score >= 70 else ("ok" if report.score >= 40 else "bad")
    bar_colour = (
        "#27ae60" if report.score >= 70 else ("#f39c12" if report.score >= 40 else "#e74c3c")
    )

    if report.score >= 70 and not report.critical_issues:
        verdict_cls, verdict_label, verdict_desc = (
            "verdict-ready",
            "INGEST-READY",
            "Document meets quality thresholds for RAG ingestion.",
        )
    elif report.score >= 40 or not report.critical_issues:
        verdict_cls, verdict_label, verdict_desc = (
            "verdict-review",
            "NEEDS REVIEW",
            f"{len(report.critical_issues)} critical issue(s) should be resolved before ingestion.",
        )
    else:
        verdict_cls, verdict_label, verdict_desc = (
            "verdict-fail",
            "DO NOT INGEST",
            f"Score {report.score}/100 with {len(report.critical_issues)} critical issue(s). Remediate before use.",
        )

    issues_html = ""
    for issue in report.issues:
        badge_cls = f"badge-{issue.severity.value}"
        sev_cls = f"sev-{issue.severity.value}"
        loc = f" <em>({issue.location})</em>" if issue.location else ""
        ctx_html = f"<div class='ctx'>↳ {issue.context}</div>" if issue.context else ""
        fix_html = f"<p class='fix'>→ {issue.suggestion}</p>" if issue.suggestion else ""
        tax_html = ""
        if issue.taxonomy_refs:
            ref_items: list[str] = []
            for r in issue.taxonomy_refs:
                rel_label = {
                    "direct": "directly detected",
                    "proxy": "proxy signal",
                    "risk_signal": "risk signal",
                }.get(r.relationship, r.relationship)
                name_part = f" · {r.mode_name}" if r.mode_name else ""
                defn_part = f"<br><em class='tax-def'>{r.definition}</em>" if r.definition else ""
                expl_part = (
                    f"<br><small class='tax-expl'>{r.explanation}</small>" if r.explanation else ""
                )
                ref_items.append(
                    f"<span class='tax-item'>"
                    f"<a href='https://doi.org/10.18653/v1/2026.trustnlp-main.27' "
                    f"target='_blank' title='Garani 2026 taxonomy'>"
                    f"<strong>{r.mode_id}</strong>{name_part}</a> "
                    f"<span class='tax-rel'>({rel_label})</span>"
                    f"{defn_part}{expl_part}"
                    f"</span>"
                )
            tax_html = (
                "<div class='tax-ref'>"
                "<strong>Garani 2026 failure mode:</strong> " + " ".join(ref_items) + "</div>"
            )

        issues_html += f"""
        <details class='issue-item {sev_cls}'>
          <summary class='issue-summary'>
            <span class='badge {badge_cls}'>{issue.severity.value.upper()}</span>
            <span>[{issue.category.value}] {issue.message}{loc}</span>
          </summary>
          <div class='issue-body'>{ctx_html}{fix_html}{tax_html}</div>
        </details>"""

    return f"""
<h1>ragpreflight — Document Readiness Report</h1>
<div class='verdict {verdict_cls}'>
  <span class='verdict-label'>{verdict_label}</span>
  <span>{verdict_desc}</span>
</div>
<div class='score-row'>
  <span class='score-num {score_cls}'>{report.score}/100</span>
  <span class='bar'><span class='bar-fill' style='width:{report.score}%;background:{bar_colour}'></span></span>
</div>
<div class='meta'>
  <span class='pill'>📄 {report.file_format.upper()}</span>
  <span class='pill'>💾 {_human_size(report.file_size_bytes)}</span>
  <span class='pill'>📑 {report.page_count} page(s)</span>
  <span class='pill'>🔍 {report.text_extractable_ratio:.0%} extractable</span>
</div>
<p style='color:#555;font-size:0.9em'><strong>File:</strong> {report.filepath}</p>
<h2>Issues ({len(report.issues)})</h2>
{issues_html or "<p>✓ No issues found.</p>"}
"""


def _html_corpus_body(report: CorpusReport) -> str:
    avg = report.average_score
    critical_count = sum(len(d.critical_issues) for d in report.documents)
    bar_colour = "#27ae60" if avg >= 70 else ("#f39c12" if avg >= 40 else "#e74c3c")
    score_cls = "good" if avg >= 70 else ("ok" if avg >= 40 else "bad")

    if avg >= 70 and critical_count == 0:
        verdict_cls, verdict_label, verdict_desc = (
            "verdict-ready",
            "INGEST-READY",
            f"Corpus average {avg:.0f}/100 with no critical issues. Safe to ingest.",
        )
    elif avg >= 50 or critical_count <= 2:
        verdict_cls, verdict_label, verdict_desc = (
            "verdict-review",
            "NEEDS REVIEW",
            f"Average {avg:.0f}/100 · {critical_count} critical issue(s) require attention before ingestion.",
        )
    else:
        verdict_cls, verdict_label, verdict_desc = (
            "verdict-fail",
            "DO NOT INGEST",
            f"Average {avg:.0f}/100 · {critical_count} critical issue(s). Remediate before ingestion.",
        )

    # Corpus-level issue summary
    corpus_issues_html = ""
    for issue in report.corpus_issues:
        badge_cls = f"badge-{issue.severity.value}"
        corpus_issues_html += (
            f"<li><span class='badge {badge_cls}'>{issue.severity.value.upper()}</span> "
            f"{issue.message}"
            + (
                f"<br><span class='action-fix'>→ {issue.suggestion}</span>"
                if issue.suggestion
                else ""
            )
            + "</li>"
        )

    # Top 5 actions across corpus
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
    top_actions_html = ""
    for issue, fname in all_issues[:5]:
        badge_cls = f"badge-{issue.severity.value}"
        tax_note = ""
        if issue.taxonomy_refs:
            refs = ", ".join(
                f"{r.mode_id} {r.mode_name or ''} ({r.relationship})".strip()
                for r in issue.taxonomy_refs
            )
            tax_note = f" <span style='color:#888;font-size:0.82em'>[{refs}]</span>"
        top_actions_html += (
            f"<li><span class='badge {badge_cls}'>{issue.severity.value.upper()}</span>"
            f"<div class='action-body'>"
            f"<span class='action-file'>{fname}:</span> {issue.message}{tax_note}"
            + (f"<div class='action-fix'>→ {issue.suggestion}</div>" if issue.suggestion else "")
            + "</div></li>"
        )

    # Per-document rows with expandable issues
    rows = ""
    for doc in sorted(report.documents, key=lambda d: d.score):
        sc = "good" if doc.score >= 70 else ("ok" if doc.score >= 40 else "bad")
        crit_badge = (
            " <span style='color:#e74c3c;font-size:0.75em;font-weight:700'> ● CRITICAL</span>"
            if doc.critical_issues
            else ""
        )
        issues_detail = ""
        for issue in doc.issues:
            badge_cls = f"badge-{issue.severity.value}"
            sev_cls = f"sev-{issue.severity.value}"
            loc = f" <em>({issue.location})</em>" if issue.location else ""
            ctx_html = f"<div class='ctx'>↳ {issue.context}</div>" if issue.context else ""
            fix_html = f"<div class='fix'>→ {issue.suggestion}</div>" if issue.suggestion else ""
            tax_html = ""
            if issue.taxonomy_refs:
                ref_parts: list[str] = []
                for r in issue.taxonomy_refs:
                    rel_label = {
                        "direct": "directly detected",
                        "proxy": "proxy signal",
                        "risk_signal": "risk signal",
                    }.get(r.relationship, r.relationship)
                    name_part = f" · {r.mode_name}" if r.mode_name else ""
                    defn_part = f" — <em>{r.definition}</em>" if r.definition else ""
                    ref_parts.append(
                        f"<a href='https://doi.org/10.18653/v1/2026.trustnlp-main.27' "
                        f"target='_blank'><strong>{r.mode_id}</strong>{name_part}</a>"
                        f" ({rel_label}){defn_part}"
                    )
                tax_html = (
                    "<div class='tax-ref'><strong>Garani 2026:</strong> "
                    + " · ".join(ref_parts)
                    + "</div>"
                )
            issues_detail += (
                f"<div class='issue-row {sev_cls}'>"
                f"<span class='badge {badge_cls}'>{issue.severity.value.upper()}</span> "
                f"<strong>[{issue.category.value}]</strong> {issue.message}{loc}"
                f"{ctx_html}{fix_html}{tax_html}"
                f"</div>"
            )

        no_issues_placeholder = "<em style='color:#888'>No issues.</em>"
        issues_cell = issues_detail or no_issues_placeholder
        crit_cell = (
            "<td><span style='color:#e74c3c;font-weight:700'>"
            + str(len(doc.critical_issues))
            + "</span></td>"
            if doc.critical_issues
            else "<td>0</td>"
        )
        rows += (
            f"<tr>"
            f"<td class='{sc}'>{doc.score}</td>"
            f"<td><details class='doc-details'>"
            f"<summary class='doc-name'>{Path(doc.filepath).name}{crit_badge}</summary>"
            f"<div class='doc-issues'>{issues_cell}</div>"
            f"</details></td>"
            f"<td>{doc.file_format.upper()}</td>"
            f"<td>{len(doc.issues)}</td>" + crit_cell + "</tr>"
        )

    # Taxonomy coverage panel
    tax_html = ""
    try:
        from ragpreflight.taxonomy import detector_coverage

        cov = detector_coverage()
        direct_ids = ", ".join(m.id for m in cov.direct)
        proxy_ids = ", ".join(m.id for m in cov.proxy)
        risk_ids = ", ".join(m.id for m in cov.risk_signal)
        tax_html = f"""
<h2>Taxonomy Coverage — Garani 2026</h2>
<p style='color:#555;font-size:0.88em'>
  Source: Garani, A. (2026). <em>A Systematic Taxonomy of Failure Modes in Retrieval-Augmented Generation Systems.</em>
  TrustNLP 2026.
  <a href='https://doi.org/10.18653/v1/2026.trustnlp-main.27'>doi:10.18653/v1/2026.trustnlp-main.27</a>
</p>
<div class='coverage-grid'>
  <div class='cov-cell cov-direct'><strong>{len(cov.direct)}</strong>direct<br><small>{direct_ids}</small></div>
  <div class='cov-cell cov-proxy'><strong>{len(cov.proxy)}</strong>proxy<br><small>{proxy_ids}</small></div>
  <div class='cov-cell cov-risk'><strong>{len(cov.risk_signal)}</strong>risk signal<br><small>{risk_ids}</small></div>
  <div class='cov-cell cov-runtime'><strong>{len(cov.runtime_required)}</strong>runtime required</div>
  <div class='cov-cell cov-unsup'><strong>{len(cov.unsupported)}</strong>unsupported</div>
</div>
<p style='color:#666;font-size:0.85em'>
  {len(cov.direct)} direct + {len(cov.proxy) + len(cov.risk_signal)} proxy/risk = {len(cov.direct) + len(cov.proxy) + len(cov.risk_signal)} of 33 modes assessed statically.
  A tool claiming 33/33 detection is lying.
</p>"""
    except Exception:
        pass

    cannot_html = """
<h2>What this audit cannot determine</h2>
<div class='cannot-box'>
<p>Static pre-ingestion scanning cannot assess the following failure modes
(Garani 2026 taxonomy) — they require live system traces, LLM outputs, or agent logs:</p>
<ul>
  <li><strong>F6</strong> Embedding Drift / Model Mismatch — requires embedding config + version metadata at runtime</li>
  <li><strong>F12</strong> Position-of-Gold Bias — requires live LLM context window</li>
  <li><strong>F13–F17</strong> Generation failures (hallucination, conflicting info, partial answers, wrong format) — require generated outputs + faithfulness eval</li>
  <li><strong>F19–F22, F24–F25</strong> Deployment failures (monitoring, latency, portability, prompt sensitivity, authorization) — require runtime metrics and config</li>
  <li><strong>F26–F33</strong> Agentic orchestration failures — require agent traces (all Limited evidence in paper; no peer-reviewed benchmarks yet)</li>
</ul>
<p>For runtime coverage:
  <a href='https://deepeval.com'>DeepEval</a> ·
  <a href='https://docs.ragas.io'>Ragas</a> ·
  <a href='https://pypi.org/project/ragchecker/'>RAGChecker</a> ·
  <a href='https://github.com/Arize-ai/openinference'>Phoenix / OpenInference</a>
</p>
</div>"""

    return f"""
<h1>ragpreflight — Corpus Readiness Report</h1>
<div class='verdict {verdict_cls}'>
  <span class='verdict-label'>{verdict_label}</span>
  <span>{verdict_desc}</span>
</div>
<div class='score-row'>
  <span class='score-num {score_cls}'>{avg:.1f}/100</span>
  <span class='bar'><span class='bar-fill' style='width:{min(avg, 100):.0f}%;background:{bar_colour}'></span></span>
  <span style='color:#888;font-size:0.9em'>{report.total_documents} documents · {len(report.duplicate_groups)} duplicate group(s)</span>
</div>
<div class='meta'>
  <span class='pill'>📁 {report.directory}</span>
  <span class='pill'>📄 {report.total_documents} docs</span>
  <span class='pill'>⚠ {critical_count} critical</span>
  <span class='pill'>📋 {sum(len(d.issues) for d in report.documents)} total issues</span>
</div>

<h2>Corpus-level Issues</h2>
<ul class='issue-list'>{corpus_issues_html or '<li style="color:#888">None</li>'}</ul>

<h2>Top Actions Required</h2>
<ul class='actions'>{top_actions_html or '<li style="color:#888">No issues found.</li>'}</ul>

<h2>Document Scores <span style='font-size:0.8rem;font-weight:400;color:#888'>(click a filename to expand issues)</span></h2>
<table>
  <thead><tr><th>Score</th><th>File</th><th>Format</th><th>Issues</th><th>Critical</th></tr></thead>
  <tbody>{rows}</tbody>
</table>
{tax_html}
{cannot_html}
"""


# ---------------------------------------------------------------------------
# Helpers
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
