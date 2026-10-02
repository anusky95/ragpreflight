"""Download a stratified sample of PDFs from olmOCR-bench for validation.

Usage:
    pip install huggingface_hub
    python scripts/olmocr_bench/sample.py

Downloads 25 PDFs per category (seed=42) from allenai/olmOCR-bench into
scripts/olmocr_bench/pdfs/<category>/. Run ragpreflight audit on each
category directory afterward.
"""

from __future__ import annotations

import json
import pathlib
import random
import shutil

SCRIPT_DIR = pathlib.Path(__file__).parent
OUT_BASE = SCRIPT_DIR / "pdfs"
N_PER_CATEGORY = 25
REPO_ID = "allenai/olmOCR-bench"
SEED = 42

CATEGORIES = [
    "arxiv_math",
    "headers_footers",
    "long_tiny_text",
    "multi_column",
    "old_scans",
    "old_scans_math",
    "table_tests",
]


def download_category(category: str, meta_dir: pathlib.Path) -> int:
    from huggingface_hub import hf_hub_download
    from huggingface_hub.utils import EntryNotFoundError

    jsonl_path = meta_dir / f"{category}.jsonl"
    if not jsonl_path.exists():
        print(f"  Downloading metadata JSONL for {category}...")
        local = hf_hub_download(
            repo_id=REPO_ID,
            repo_type="dataset",
            filename=f"bench_data/{category}.jsonl",
        )
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(local, jsonl_path)

    seen: set[str] = set()
    pdfs: list[str] = []
    for line in jsonl_path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        pdf = rec["pdf"]
        if pdf not in seen:
            seen.add(pdf)
            pdfs.append(pdf)

    rng = random.Random(SEED)
    sample = pdfs[:N_PER_CATEGORY] if len(pdfs) <= N_PER_CATEGORY else rng.sample(pdfs, N_PER_CATEGORY)

    out_dir = OUT_BASE / category
    out_dir.mkdir(parents=True, exist_ok=True)

    success = 0
    for pdf_field in sample:
        hf_path = f"bench_data/pdfs/{pdf_field}"
        dest = out_dir / pathlib.Path(pdf_field).name
        if dest.exists():
            success += 1
            continue
        try:
            local = hf_hub_download(
                repo_id=REPO_ID,
                repo_type="dataset",
                filename=hf_path,
            )
            shutil.copy(local, dest)
            success += 1
        except EntryNotFoundError:
            print(f"  MISSING: {hf_path}")
        except Exception as e:
            print(f"  ERROR {pdf_field}: {e}")

    return success


def main() -> None:
    meta_dir = SCRIPT_DIR / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    OUT_BASE.mkdir(parents=True, exist_ok=True)

    print(f"Downloading {N_PER_CATEGORY} PDFs per category (seed={SEED})")
    print(f"Output: {OUT_BASE}\n")

    total = 0
    for category in CATEGORIES:
        print(f"[{category}]")
        n = download_category(category, meta_dir)
        total += n
        print(f"  {n}/{N_PER_CATEGORY} downloaded\n")

    print(f"Total: {total} PDFs")
    print("\nNext steps:")
    print("  for cat in scripts/olmocr_bench/pdfs/*/; do")
    print('    ragpreflight audit "$cat" --json > "scripts/olmocr_bench/results/$(basename $cat).json"')
    print("  done")


if __name__ == "__main__":
    main()
