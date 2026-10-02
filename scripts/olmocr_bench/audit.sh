#!/usr/bin/env bash
# Audit each olmOCR-bench category and collect JSON results.
# Prerequisites: pip install ragpreflight; python scripts/olmocr_bench/sample.py

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PDF_DIR="$SCRIPT_DIR/pdfs"
RESULTS_DIR="$SCRIPT_DIR/results"

mkdir -p "$RESULTS_DIR"

for cat_dir in "$PDF_DIR"/*/; do
    cat="$(basename "$cat_dir")"
    echo "Auditing $cat..."
    ragpreflight audit "$cat_dir" --json > "$RESULTS_DIR/$cat.json"
done

echo ""
echo "Results saved to $RESULTS_DIR/"
echo "Generate HTML report:"
echo "  ragpreflight audit $PDF_DIR --format html --output docs/olmocr-bench-report.html"
