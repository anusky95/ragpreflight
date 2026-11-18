"""Render `ragpreflight demo` output to docs/demo.svg for the README."""

from pathlib import Path

from rich.console import Console
from rich.terminal_theme import MONOKAI

from ragpreflight import scan
from ragpreflight.report import print_terminal

ROOT = Path(__file__).resolve().parents[1]
demo = ROOT / "src" / "ragpreflight" / "demo"

console = Console(record=True, width=104, highlight=False, force_terminal=True)
console.print("[bold green]$[/bold green] uvx ragpreflight demo\n")
report = scan(demo)
report.root = "skyfare-air-kb/  (fictional airline support-bot knowledge base)"
print_terminal(report, console, elapsed=0.4)
(ROOT / "docs").mkdir(exist_ok=True)
console.save_svg(str(ROOT / "docs" / "demo.svg"), title="ragpreflight", theme=MONOKAI)
print("wrote docs/demo.svg")
