"""Click CLI entry point for RAGCheck.

Commands:
    ragpreflight scan <file>         -- single document scan
    ragpreflight audit <directory>   -- corpus audit
    ragpreflight chunks <file>       -- chunk analysis
    ragpreflight simulate <dir>      -- retrieval simulation
    ragpreflight score <file>        -- print score only (for scripting)
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import click

from ragpreflight import __version__
from ragpreflight.config import load_config

# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 100}


@click.group(context_settings=CONTEXT_SETTINGS)
@click.version_option(__version__, "-V", "--version", message="ragpreflight %(version)s")
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Enable debug logging.",
)
@click.pass_context
def main(ctx: click.Context, verbose: bool) -> None:
    """RAGCheck — score document readiness for RAG pipelines.

    \b
    Config file: place a .ragpreflight.toml (TOML) in your project root or home
    directory to set defaults. CLI flags always override config values.

    \b
    Run any command with --help for details:
        ragpreflight scan --help
        ragpreflight audit --help
        ragpreflight chunks --help
        ragpreflight simulate --help
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config()


# ---------------------------------------------------------------------------
# ragpreflight scan
# ---------------------------------------------------------------------------

@main.command("scan")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--profile",
    "-p",
    default="standard",
    show_default=True,
    help="Quality profile: permissive | standard | strict | medical | legal | financial",
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON.")
@click.option("--quiet", "-q", is_flag=True, default=False, help="Print score only.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["terminal", "json", "html", "sarif"]),
    default="terminal",
    show_default=True,
    help="Output format.",
)
@click.option("--output", "-o", default=None, help="Write output to this file (for --format html/json/sarif).")
@click.option("--max-size", default=100.0, show_default=True, help="Max file size in MB.")
@click.pass_context
def cmd_scan(
    ctx: click.Context,
    filepath: str,
    profile: str,
    output_json: bool,
    quiet: bool,
    output_format: str,
    output: Optional[str],
    max_size: float,
) -> None:
    """Scan a single document and report its RAG readiness score.

    \b
    Examples:
        ragpreflight scan document.pdf
        ragpreflight scan document.pdf --profile strict --json
        ragpreflight scan document.pdf --format html --output report.html
    """
    from ragpreflight.scanner import scan_document
    from ragpreflight.report import render_document_report
    from ragpreflight.profiles import get_profile

    # Apply config file defaults (CLI flags take precedence via Click defaults)
    cfg = (ctx.obj or {}).get("config", {})
    if profile == "standard" and "profile" in cfg:
        profile = cfg["profile"]
    if not quiet and cfg.get("quiet"):
        quiet = True
    if output_format == "terminal" and "output_format" in cfg:
        output_format = cfg["output_format"]

    try:
        get_profile(profile)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--profile")

    try:
        report = scan_document(filepath, max_file_size_mb=max_size)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc))
    except Exception as exc:
        raise click.ClickException(f"Unexpected error: {exc}")

    fmt = "json" if output_json else output_format
    render_document_report(report, fmt=fmt, output=output, quiet=quiet)

    # Non-zero exit code when score is 0 or there are critical issues
    if report.critical_issues:
        sys.exit(1)


# ---------------------------------------------------------------------------
# ragpreflight audit
# ---------------------------------------------------------------------------

@main.command("audit")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--profile",
    "-p",
    default="standard",
    show_default=True,
    help="Quality profile: permissive | standard | strict | medical | legal | financial",
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON.")
@click.option("--quiet", "-q", is_flag=True, default=False, help="Print average score only.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["terminal", "json", "html", "sarif"]),
    default="terminal",
    show_default=True,
    help="Output format.",
)
@click.option("--output", "-o", default=None, help="Write output to this file.")
@click.option("--no-progress", is_flag=True, default=False, help="Disable progress bar.")
@click.option("--no-parallel", is_flag=True, default=False, help="Disable parallel scanning (scan sequentially).")
@click.option("--workers", default=4, show_default=True, help="Thread pool size for parallel scanning.")
@click.option("--cache", is_flag=True, default=False, help="Cache scan results; re-use on unchanged files (incremental).")
@click.option("--contradictions", is_flag=True, default=False, help="Detect documents covering the same topic (requires ragpreflight[full]).")
@click.pass_context
def cmd_audit(
    ctx: click.Context,
    directory: str,
    profile: str,
    output_json: bool,
    quiet: bool,
    output_format: str,
    output: Optional[str],
    no_progress: bool,
    no_parallel: bool,
    workers: int,
    cache: bool,
    contradictions: bool,
) -> None:
    """Audit all documents in a directory.

    \b
    Examples:
        ragpreflight audit ./knowledge_base/
        ragpreflight audit ./knowledge_base/ --profile medical
        ragpreflight audit ./knowledge_base/ --format html --output report.html
    """
    from ragpreflight.corpus import audit_corpus
    from ragpreflight.report import render_corpus_report
    from ragpreflight.profiles import get_profile

    cfg = (ctx.obj or {}).get("config", {})
    if profile == "standard" and "profile" in cfg:
        profile = cfg["profile"]
    if not quiet and cfg.get("quiet"):
        quiet = True
    if output_format == "terminal" and "output_format" in cfg:
        output_format = cfg["output_format"]

    try:
        profile_data = get_profile(profile)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--profile")

    try:
        report = audit_corpus(
            directory,
            profile=profile_data,
            show_progress=not no_progress,
            parallel=not no_parallel,
            max_workers=workers,
            use_cache=cache,
            detect_contradictions=contradictions,
        )
    except NotADirectoryError as exc:
        raise click.ClickException(str(exc))
    except Exception as exc:
        raise click.ClickException(f"Unexpected error: {exc}")

    fmt = "json" if output_json else output_format
    render_corpus_report(report, fmt=fmt, output=output, quiet=quiet)

    # Exit 1 if any documents have critical issues
    if any(d.critical_issues for d in report.documents):
        sys.exit(1)


# ---------------------------------------------------------------------------
# ragpreflight chunks
# ---------------------------------------------------------------------------

@main.command("chunks")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--strategy",
    type=click.Choice(["recursive", "sentence", "paragraph"]),
    default="recursive",
    show_default=True,
    help="Chunking strategy.",
)
@click.option("--size", default=512, show_default=True, help="Target chunk size in characters.")
@click.option("--overlap", default=50, show_default=True, help="Chunk overlap in characters.")
@click.option(
    "--profile",
    "-p",
    default="standard",
    show_default=True,
    help="Quality profile.",
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON.")
def cmd_chunks(
    filepath: str,
    strategy: str,
    size: int,
    overlap: int,
    profile: str,
    output_json: bool,
) -> None:
    """Analyse chunk quality for a document.

    Requires: pip install ragpreflight[full]

    \b
    Examples:
        ragpreflight chunks document.pdf --strategy sentence --size 512
        ragpreflight chunks document.pdf --json
    """
    from ragpreflight.chunker import analyze_chunks
    from ragpreflight.profiles import get_profile

    try:
        profile_data = get_profile(profile)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--profile")

    try:
        chunk_reports = analyze_chunks(
            filepath,
            chunk_size=size,
            overlap=overlap,
            strategy=strategy,
            profile=profile_data,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc))
    except Exception as exc:
        raise click.ClickException(f"Unexpected error: {exc}")

    if output_json:
        click.echo(json.dumps([r.to_dict() for r in chunk_reports], indent=2))
        return

    from rich.console import Console
    from rich.table import Table
    from rich import box

    console = Console()
    console.print(f"\n[bold]Chunk Analysis — {Path(filepath).name}[/bold]")
    console.print(f"Strategy: {strategy}  ·  Size: {size}  ·  Overlap: {overlap}")
    console.print(f"Total chunks: {len(chunk_reports)}\n")

    if not chunk_reports:
        console.print("[yellow]No chunks produced.[/yellow]")
        return

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
    table.add_column("#", width=5, justify="right")
    table.add_column("Coherence", width=10)
    table.add_column("Issues", width=7, justify="right")
    table.add_column("Preview")

    for r in chunk_reports:
        if r.coherence_score is None:
            coh_display = "[dim]n/a[/dim]"
        else:
            coh_colour = "green" if r.coherence_score >= 0.65 else "yellow" if r.coherence_score >= 0.4 else "red"
            coh_display = f"[{coh_colour}]{r.coherence_score:.2f}[/{coh_colour}]"
        table.add_row(
            str(r.chunk_index),
            coh_display,
            str(len(r.issues)),
            r.text_preview,
        )
    console.print(table)

    # Print issues for problematic chunks
    for r in chunk_reports:
        if r.issues:
            for issue in r.issues:
                from ragpreflight._constants import SEVERITY_COLOURS
                colour = SEVERITY_COLOURS.get(issue.severity.value, "white")
                console.print(f"  [{colour}]{issue.severity.value.upper()}[/{colour}]  {issue.message}")


# ---------------------------------------------------------------------------
# ragpreflight simulate
# ---------------------------------------------------------------------------

@main.command("simulate")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option("--queries", default=7, show_default=True, help="Queries per document.")
@click.option("--top-k", default=5, show_default=True, help="Top-k chunks to retrieve per query.")
@click.option("--chunk-size", default=512, show_default=True, help="Chunk size in characters.")
@click.option(
    "--use-llm",
    is_flag=True,
    default=False,
    help="Use an LLM endpoint for richer query generation.",
)
@click.option(
    "--llm-endpoint",
    default="http://localhost:11434",
    show_default=True,
    help="OpenAI-compatible API endpoint.",
)
@click.option("--llm-model", default="llama3", show_default=True, help="LLM model name.")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output as JSON.")
def cmd_simulate(
    directory: str,
    queries: int,
    top_k: int,
    chunk_size: int,
    use_llm: bool,
    llm_endpoint: str,
    llm_model: str,
    output_json: bool,
) -> None:
    """Simulate retrieval over a corpus and measure chunk coverage.

    Requires: pip install ragpreflight[full]

    \b
    Examples:
        ragpreflight simulate ./knowledge_base/ --queries 50
        ragpreflight simulate ./knowledge_base/ --use-llm --llm-endpoint http://localhost:11434
    """
    from ragpreflight.retrieval import simulate_retrieval

    try:
        result = simulate_retrieval(
            directory,
            queries_per_doc=queries,
            top_k=top_k,
            chunk_size=chunk_size,
            use_llm=use_llm,
            llm_endpoint=llm_endpoint,
            llm_model=llm_model,
        )
    except Exception as exc:
        raise click.ClickException(f"Unexpected error: {exc}")

    if "error" in result:
        raise click.ClickException(result["error"])

    if output_json:
        click.echo(json.dumps(result, indent=2))
        return

    from rich.console import Console
    from rich.panel import Panel

    console = Console()
    pct = lambda v: f"{v:.1%}"  # noqa: E731

    console.print(
        Panel(
            f"[bold]Retrieval Simulation — {result['directory']}[/bold]\n\n"
            f"Documents: {result['total_documents']}  ·  "
            f"Chunks: {result['total_chunks']}  ·  "
            f"Queries: {result['total_queries']}\n\n"
            f"Similarity hit rate@{top_k}: [green]{pct(result['synthetic_retrieval_hit_rate'])}[/green] (similarity threshold, not labeled precision)\n"
            f"Dead chunk rate:    "
            f"[{'red' if result['dead_chunk_rate'] > 0.3 else 'yellow' if result['dead_chunk_rate'] > 0.1 else 'green'}]"
            f"{pct(result['dead_chunk_rate'])}[/] "
            f"({result['dead_chunks']} chunks never retrieved)\n"
            f"Query failure rate: "
            f"[{'red' if result['query_failure_rate'] > 0.3 else 'yellow' if result['query_failure_rate'] > 0.1 else 'green'}]"
            f"{pct(result['query_failure_rate'])}[/] "
            f"({result['failed_queries']} queries found no good match)",
            expand=False,
        )
    )


# ---------------------------------------------------------------------------
# ragpreflight score
# ---------------------------------------------------------------------------

@main.command("score")
@click.argument("filepath", type=click.Path(exists=True, dir_okay=False))
@click.option("--max-size", default=100.0, show_default=True, help="Max file size in MB.")
def cmd_score(filepath: str, max_size: float) -> None:
    """Print the readiness score (0-100) and nothing else.

    Useful for shell scripting and CI/CD integration.

    \b
    Examples:
        ragpreflight score document.pdf         # prints: 73
        score=$(ragpreflight score doc.pdf)
        [ "$score" -ge 60 ] || exit 1       # fail CI if score < 60
    """
    from ragpreflight.scanner import scan_document

    try:
        report = scan_document(filepath, max_file_size_mb=max_size)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc))
    except Exception as exc:
        raise click.ClickException(f"Unexpected error: {exc}")

    click.echo(report.score)


# ---------------------------------------------------------------------------
# ragpreflight taxonomy
# ---------------------------------------------------------------------------

@main.group("taxonomy")
def cmd_taxonomy() -> None:
    """Browse the Garani 2026 RAG failure taxonomy (F1–F33).

    \b
    Examples:
        ragpreflight taxonomy             # list all 33 modes
        ragpreflight taxonomy F7          # detail for one mode
        ragpreflight taxonomy --stage retrieval
        ragpreflight coverage             # detector coverage summary
    """


@cmd_taxonomy.command("list")
@click.option("--stage", default=None, help="Filter by pipeline stage.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def cmd_taxonomy_list(stage: Optional[str], as_json: bool) -> None:
    """List all 33 failure modes (or filter by stage)."""
    import json as _json
    from ragpreflight.taxonomy import list_failure_modes, modes_by_stage

    from rich.console import Console
    from rich.table import Table

    console = Console()
    modes = modes_by_stage(stage) if stage else list_failure_modes()

    if as_json:
        data = [
            {
                "id": m.id,
                "stage": m.stage,
                "name": m.name,
                "evidence_level": m.evidence_level,
                "detector_status": m.detector_status,
            }
            for m in modes
        ]
        click.echo(_json.dumps(data, indent=2))
        return

    table = Table(title="Garani 2026 RAG Failure Taxonomy", box=None, show_header=True)
    table.add_column("ID", style="bold cyan", width=5)
    table.add_column("Stage", width=22)
    table.add_column("Name", width=36)
    table.add_column("Evidence", width=10)
    table.add_column("Coverage", width=16)

    _EVIDENCE_COLOUR = {"Strong": "green", "Moderate": "yellow", "Limited": "red"}
    _STATUS_COLOUR = {
        "direct": "green",
        "proxy": "yellow",
        "risk_signal": "yellow",
        "runtime_required": "dim",
        "unsupported": "dim",
    }

    for m in modes:
        ev_col = _EVIDENCE_COLOUR.get(m.evidence_level, "white")
        st_col = _STATUS_COLOUR.get(m.detector_status, "white")
        table.add_row(
            m.id,
            m.stage.replace("_", " "),
            m.name,
            f"[{ev_col}]{m.evidence_level}[/{ev_col}]",
            f"[{st_col}]{m.detector_status}[/{st_col}]",
        )
    console.print(table)
    console.print(
        "\n[dim]Source: Garani 2026 · doi:10.18653/v1/2026.trustnlp-main.27[/dim]"
    )


@cmd_taxonomy.command("show")
@click.argument("mode_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def cmd_taxonomy_show(mode_id: str, as_json: bool) -> None:
    """Show full detail for one failure mode (e.g. F7)."""
    import json as _json
    from ragpreflight.taxonomy import get_failure_mode

    from rich.console import Console
    console = Console()

    try:
        m = get_failure_mode(mode_id)
    except KeyError as exc:
        raise click.ClickException(str(exc))

    if as_json:
        click.echo(_json.dumps({
            "id": m.id,
            "stage": m.stage,
            "name": m.name,
            "definition": m.definition,
            "observable_manifestation": m.observable_manifestation,
            "evidence_level": m.evidence_level,
            "detector_status": m.detector_status,
            "source": {"doi": m.source.doi, "url": m.source.url},
        }, indent=2))
        return

    console.print(f"\n[bold cyan]{m.id}  {m.name}[/bold cyan]")
    console.print(f"[dim]Stage:[/dim] {m.stage.replace('_', ' ').title()}")
    console.print(f"[dim]Evidence:[/dim] {m.evidence_level}")
    console.print(f"[dim]ragpreflight coverage:[/dim] {m.detector_status}")
    console.print(f"\n[bold]Definition[/bold]\n{m.definition}")
    console.print(f"\n[bold]Observable Manifestation[/bold]\n{m.observable_manifestation}")
    console.print(
        f"\n[dim]Source: {m.source.author} {m.source.year}, {m.source.venue}[/dim]"
        f"\n[dim]doi:{m.source.doi}[/dim]"
    )


# ---------------------------------------------------------------------------
# ragpreflight coverage
# ---------------------------------------------------------------------------

@main.command("coverage")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def cmd_coverage(as_json: bool) -> None:
    """Show detector coverage across all 33 Garani 2026 failure modes.

    \b
    Examples:
        ragpreflight coverage
        ragpreflight coverage --json
    """
    import json as _json
    from ragpreflight.taxonomy import detector_coverage

    from rich.console import Console
    console = Console()
    cov = detector_coverage()

    if as_json:
        click.echo(_json.dumps({
            "direct": [m.id for m in cov.direct],
            "proxy": [m.id for m in cov.proxy],
            "risk_signal": [m.id for m in cov.risk_signal],
            "runtime_required": [m.id for m in cov.runtime_required],
            "unsupported": [m.id for m in cov.unsupported],
            "total": cov.total,
        }, indent=2))
        return

    console.print("\n[bold]ragpreflight — Taxonomy Coverage (Garani 2026)[/bold]\n")
    console.print(f"  [green]Direct assessment:[/green]  {len(cov.direct)} modes  "
                  f"({', '.join(m.id for m in cov.direct) or '—'})")
    console.print(f"  [yellow]Proxy signal:[/yellow]       {len(cov.proxy)} modes  "
                  f"({', '.join(m.id for m in cov.proxy) or '—'})")
    console.print(f"  [yellow]Risk signal only:[/yellow]   {len(cov.risk_signal)} modes  "
                  f"({', '.join(m.id for m in cov.risk_signal) or '—'})")
    console.print(f"  [dim]Runtime required:[/dim]   {len(cov.runtime_required)} modes  "
                  f"(needs live system traces)")
    console.print(f"  [dim]Not supported:[/dim]      {len(cov.unsupported)} modes")
    console.print(
        f"\n  [dim]Total: {cov.total} modes | Source: doi:10.18653/v1/2026.trustnlp-main.27[/dim]"
    )
    console.print(
        "\n  [dim]Saying '2 direct + 4 proxy/risk + 27 runtime/unsupported' is honest strength,[/dim]"
        "\n  [dim]not a weakness. Run 'ragpreflight taxonomy' for full mode details.[/dim]"
    )
