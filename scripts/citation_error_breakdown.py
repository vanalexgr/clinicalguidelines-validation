"""Standalone wrapper for citation-tier and hallucination breakdown outputs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from src.metrics.citation_breakdown import (
    breakdown_to_dict,
    compute_citation_breakdown,
    render_citation_breakdown_markdown,
)

CONFIG_PATH = Path("config/config.yaml")
_paths = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["paths"]

ANSWERS_PATH = Path(_paths["answers"])
JUDGMENTS_PATH = Path(_paths["judgments"])
INDEX_PATH = Path("data/corpus/recommendation_index.json")
# Must track the benchmark the run was actually scored against; a hardcoded seed
# path silently reported the 16-item pilot as if it were the full 55-item run.
BENCH_PATH = Path(_paths["benchmark"])
OVERRIDES_PATH = Path("data/annotation/hallucination_overrides.jsonl")
OUT_JSON = Path("outputs/metrics/citation_breakdown.json")
OUT_MD = Path("outputs/report/citation_error_breakdown.md")


def main() -> None:
    result = compute_citation_breakdown(
        answers_path=ANSWERS_PATH,
        judgments_path=JUDGMENTS_PATH,
        index_path=INDEX_PATH,
        bench_path=BENCH_PATH,
        overrides_path=OVERRIDES_PATH,
    )
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(breakdown_to_dict(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    OUT_MD.write_text(render_citation_breakdown_markdown(result), encoding="utf-8")
    print(f"Wrote {OUT_JSON} and {OUT_MD}")


if __name__ == "__main__":
    main()
