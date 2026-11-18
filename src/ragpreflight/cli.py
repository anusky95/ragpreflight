"""Command line: `ragpreflight PATH`, `ragpreflight demo`, `ragpreflight explain F14`, `ragpreflight coverage`."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from importlib import resources
from pathlib import Path

import click
from rich.console import Console

from . import __version__
from .models import Severity
from .report import fancy, print_terminal, to_html, to_json, to_markdown
from .scan import scan
from .taxonomy import COVERED, MODES, PAPER_URL

COMMANDS = {"scan", "view", "chunks", "ab", "demo", "explain", "coverage", "--help", "-h", "--version"}


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="ragpreflight")
def cli() -> None:
    """Lint your knowledge before your agent reads it.

    \b
      ragpreflight ./docs              scan a folder (or one file)
      ragpreflight demo                see it catch a real-world failure in 2 seconds
      ragpreflight explain F14         what a failure mode means
      ragpreflight coverage            what is and isn't checked
    """


def _run(path: str, fmt: str, out: str | None, fail_on: str, only: str | None, skip: str | None,
         show_all: bool, console: Console) -> int:
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

    t0 = time.perf_counter()
    err = Console(stderr=True)
    try:
        with Progress(TextColumn("Reading documents"), BarColumn(), MofNCompleteColumn(),
                      TimeElapsedColumn(), console=err, transient=True,
                      disable=not err.is_terminal) as bar:
            task = bar.add_task("read", total=None)
            report = scan(path, set(only.split(",")) if only else None,
                          set(skip.split(",")) if skip else None,
                          progress=lambda done, total: bar.update(task, completed=done, total=total))
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 2
    elapsed = time.perf_counter() - t0
    if not report.documents:
        console.print(f"[yellow]No supported files found in {path}[/yellow] (pdf, docx, md, txt, html)")
        return 2
    def html_for_out(r):  # links to source files relative to where the report is saved
        return to_html(r, Path(out).resolve().parent if out else None)

    rendered = {"json": to_json, "md": to_markdown, "html": html_for_out}.get(fmt)
    if rendered:
        text = rendered(report)
        if out:
            Path(out).write_text(text, encoding="utf-8")
            console.print(f"Wrote {out} · {report.verdict} · {report.score}/100", highlight=False)
        else:
            sys.stdout.write(text + "\n")
    else:
        print_terminal(report, console, show_all=show_all, elapsed=elapsed)
        if out:
            suffix = Path(out).suffix.lower()
            writer = {".json": to_json, ".md": to_markdown}.get(suffix, html_for_out)
            Path(out).write_text(writer(report), encoding="utf-8")
            console.print(f"[dim]Report written to {out}[/dim]")
    sev = {f.severity for f in report.findings}
    if fail_on == "error" and Severity.ERROR in sev:
        return 1
    if fail_on == "warn" and sev & {Severity.ERROR, Severity.WARN}:
        return 1
    return 0


def _chunker(spec: str) -> str:
    """Validate --chunker and hand it to the chunk check (env var reaches worker processes too)."""
    import os

    from .chunkers import ENV, is_valid

    if not is_valid(spec):
        raise click.BadParameter("use langchain[:SIZE[:OVERLAP]], llamaindex[:SIZE[:OVERLAP]], openai, "
                                 "tokens:SIZE[:OVERLAP], words:SIZE[:OVERLAP] or off", param_hint="--chunker")
    os.environ[ENV] = spec.lower()
    return spec


@cli.command("scan")
@click.argument("path", default=".", type=click.Path())
@click.option("--format", "fmt", type=click.Choice(["text", "json", "md", "html"]), default="text",
              help="Output format. json/md/html go to stdout unless --out is given.")
@click.option("--out", "-o", type=click.Path(), help="Also write a report file (.html, .json or .md).")
@click.option("--fail-on", type=click.Choice(["error", "warn", "never"]), default="error",
              show_default=True, help="Exit 1 when findings at this level exist (for CI / pre-commit).")
@click.option("--only", help="Comma-separated checks to run: versions,extraction,chunking,pii,metadata")
@click.option("--skip", help="Comma-separated checks to skip.")
@click.option("--all", "show_all", is_flag=True, help="Show every finding, including notes.")
@click.option("--chunker", default="langchain", show_default=True, metavar="SPEC", callback=lambda c, p, v: _chunker(v),
              help="The splitter your pipeline uses, for chunk checks: langchain[:SIZE[:OVERLAP]] "
                   "(RecursiveCharacterTextSplitter, default 4000/200 chars), llamaindex[:SIZE[:OVERLAP]] "
                   "(SentenceSplitter, 1024/200 tokens), openai (800/400 tokens), tokens:SIZE[:OVERLAP], or off.")
def scan_cmd(path, fmt, out, fail_on, only, skip, show_all, chunker):
    """Scan a folder or file of documents (pdf, docx, md, txt, html)."""
    sys.exit(_run(path, fmt, out, fail_on, only, skip, show_all, Console(highlight=False)))


def _run_ab(path, questions, model, top_k, out, no_open, no_answers, console):
    import webbrowser

    from .ab import load_questions
    from .ab import run as ab_run
    from .ab_html import to_ab_html

    qs = load_questions(questions)
    console.print(f"Asking {len(qs)} question(s) before and after ragpreflight…")
    r = ab_run(path, qs, model=model, top_k=top_k, with_answers=not no_answers)
    target = Path(out) if out else Path.cwd() / "ragpreflight-before-after.html"
    target.write_text(to_ab_html(r), encoding="utf-8")
    for c in r.changes:
        console.print(f"  [green]✓[/green] {c}" if fancy(console) else f"  + {c}")
    if r.answers_note:
        console.print(f"[yellow]Answers off:[/yellow] {r.answers_note}. The page shows what the retriever returns.")
    console.print(f"Before/after page: {target}")
    if not no_open:
        webbrowser.open(target.resolve().as_uri())


@cli.command()
@click.argument("path", type=click.Path(exists=True))
@click.option("--questions", "-q", type=click.Path(exists=True), help="JSON list or one question per line.")
@click.option("--model", default="claude-opus-5-5", show_default=True, help="Claude model that answers.")
@click.option("--top-k", default=3, show_default=True, help="Passages retrieved per question.")
@click.option("--chunker", default="langchain", show_default=True, metavar="SPEC", callback=lambda c, p, v: _chunker(v),
              help="Your splitter, used for the BEFORE index.")
@click.option("--out", "-o", type=click.Path(), help="Where to write the page (default: ragpreflight-before-after.html).")
@click.option("--no-open", is_flag=True)
@click.option("--no-answers", is_flag=True, help="Skip calling Claude; show retrieved passages only.")
def ab(path, questions, model, top_k, chunker, out, no_open, no_answers):
    """Ask the same questions of your documents before and after ragpreflight's fixes."""
    if not questions:
        raise click.UsageError("pass --questions FILE (a JSON list, or one question per line)")
    _run_ab(path, questions, model, top_k, out, no_open, no_answers, Console(highlight=False))


@cli.command()
@click.option("--keep", type=click.Path(), help="Copy the demo documents here so you can inspect them.")
@click.option("--chat", is_flag=True, help="Also ask a support bot 5 questions before and after ragpreflight.")
@click.option("--no-open", is_flag=True, hidden=True)
def demo(keep, chat, no_open):
    """Scan a small fictional airline knowledge base with real-world failures planted in it."""
    console = Console(highlight=False)
    src = resources.files("ragpreflight") / "demo"
    target = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="ragpreflight-demo-")) / "skyfare-air-kb"
    target.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.is_file() and not item.name.startswith(("_", ".")):
            (target / item.name).write_bytes(item.read_bytes())
    if chat:
        import os

        os.environ.setdefault("RAGPREFLIGHT_CHUNKER", "langchain:400:40")  # small docs: realistic chunks
        console.print("[bold]Demo:[/bold] SkyFare Air's support bot (fictional), before and after ragpreflight.\n")
        _run_ab(str(target), None, "claude-opus-5-5", 3, None, no_open, False, console)
        return
    console.print("[bold]Demo:[/bold] SkyFare Air's support-bot knowledge base (fictional).\n"
                  "A customer asks: [italic]“Can I claim the bereavement fare after I fly?”[/italic]\n")
    _run(str(target), "text", None, "never", None, None, False, console)
    console.print(f"\n[dim]Demo files: {target}[/dim]\nNow try it on yours:  [bold]ragpreflight ./your-docs[/bold]")
    if not keep:
        shutil.rmtree(target.parent, ignore_errors=True)


@cli.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--out", "-o", type=click.Path(), help="Where to write the viewer (default: FILE.ragpreflight.html here).")
@click.option("--no-open", is_flag=True, help="Don't open the viewer in a browser.")
@click.option("--mask", is_flag=True, help="Blur PII and secret values (use when sharing the viewer).")
@click.option("--chunker", default="langchain", show_default=True, metavar="SPEC", callback=lambda c, p, v: _chunker(v),
              help="The splitter your pipeline uses (see `ragpreflight scan --help`).")
@click.option("--questions", "-q", type=click.Path(exists=True),
              help="Also ask a bot these questions about the file, before vs after ragpreflight's fixes.")
@click.option("--model", default="claude-opus-5-5", show_default=True, help="Claude model for --questions.")
def view(path, out, no_open, chunker, mask, questions, model):
    """See one PDF with every problem highlighted on its pages."""
    import os
    import webbrowser

    from .html_viewer import to_viewer_html

    console = Console(highlight=False)
    src = Path(path)
    report = scan(src)
    ab_result = None
    if questions:
        from .ab import load_questions
        from .ab import run as ab_run

        qs = load_questions(questions)
        console.print(f"Asking {len(qs)} question(s) about {src.name}, before and after ragpreflight…")
        ab_result = ab_run(src, qs, model=model)
        if ab_result.answers_note:
            console.print(f"[yellow]Answers off:[/yellow] {ab_result.answers_note}")
    target = Path(out) if out else Path.cwd() / f"{src.stem}.ragpreflight.html"
    if src.suffix.lower() == ".pdf":
        try:
            href = Path(os.path.relpath(src.resolve(), target.resolve().parent)).as_posix()
        except ValueError:
            href = src.resolve().as_uri()
        page = to_viewer_html(report, src, source_href=href, mask=mask, ab=ab_result)
    else:
        console.print("[dim]The page viewer is PDF-only for now; writing the standard report.[/dim]")
        page = to_html(report, target.resolve().parent)
    target.write_text(page, encoding="utf-8")
    n = report.counts()
    console.print(f"{report.verdict} · {n['error']} errors · {n['warn']} warnings · viewer: {target}")
    if not no_open:
        webbrowser.open(target.resolve().as_uri())


@cli.command()
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
@click.option("--max-tokens", default=8191, show_default=True,
              help="Your embedding model's input limit (8191 OpenAI text-embedding-3; 512 for many open models).")
@click.option("--format", "fmt", type=click.Choice(["text", "json", "md", "html"]), default="text")
@click.option("--out", "-o", type=click.Path(), help="Also write a report file (.html, .json or .md).")
@click.option("--fail-on", type=click.Choice(["error", "warn", "never"]), default="error", show_default=True)
@click.option("--all", "show_all", is_flag=True, help="Show every finding, including notes.")
def chunks(path, max_tokens, fmt, out, fail_on, show_all):
    """Lint the real chunks your pipeline produced (JSONL / JSON export of your index)."""
    from .chunks_lint import lint

    console = Console(highlight=False)
    t0 = time.perf_counter()
    try:
        report = lint(path, max_tokens=max_tokens)
    except (ValueError, json.JSONDecodeError) as e:
        console.print(f"[red]Could not read {path}: {e}[/red]\nExpected JSONL (one chunk per line) or a JSON array with a 'text' field.")
        sys.exit(2)
    rendered = {"json": to_json, "md": to_markdown, "html": lambda r: to_html(r)}.get(fmt)
    if rendered:
        text = rendered(report)
        if out:
            Path(out).write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text + "\n")
    else:
        print_terminal(report, console, show_all=show_all, elapsed=time.perf_counter() - t0)
        if out:
            writer = {".json": to_json, ".md": to_markdown}.get(Path(out).suffix.lower(), to_html)
            Path(out).write_text(writer(report), encoding="utf-8")
    sev = {f.severity for f in report.findings}
    if (fail_on == "error" and Severity.ERROR in sev) or (fail_on == "warn" and sev & {Severity.ERROR, Severity.WARN}):
        sys.exit(1)


@cli.command()
@click.argument("code")
def explain(code):
    """Explain a failure mode, e.g. F14."""
    code = code.upper() if code.upper().startswith("F") else f"F{code}"
    if code not in MODES:
        raise click.BadParameter(f"{code} is not one of F1–F33")
    stage, name, grade = MODES[code]
    click.echo(f"{code}  {name}\nStage: {stage} · evidence: {grade}")
    click.echo(f"Checked by ragpreflight: {COVERED.get(code, 'no (happens at query time or inside agents; use evals/tracing)')}")
    click.echo(f"Source: Garani (2026), ACL TrustNLP · {PAPER_URL}")


@cli.command()
def coverage():
    """Which of the 33 failure modes ragpreflight checks before ingestion."""
    console = Console(highlight=False)
    for code, (stage, name, _grade) in MODES.items():
        mark = ("[green]✓[/green]" if fancy(console) else "[green]+[/green]") if code in COVERED else "[dim]·[/dim]"
        console.print(f" {mark} {code:<4} {stage:<15} {name}" + (f"  [dim]{COVERED[code].split(':')[0]}[/dim]" if code in COVERED else ""))
    console.print(f"\n{len(COVERED)} of 33 checked before ingestion. The rest happen at query time or inside agents.")


def main() -> None:
    # Old Windows consoles use cp1252: never crash on a quote or arrow in a document title.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = sys.argv[1:]
    if not args or args[0] not in COMMANDS:
        sys.argv.insert(1, "scan")  # `ragpreflight ./docs` == `ragpreflight scan ./docs`
    cli()


if __name__ == "__main__":
    main()
