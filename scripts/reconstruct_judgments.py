#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = PROJECT_ROOT / ".cache"
OUTPUT_PATH = PROJECT_ROOT / "outputs" / "judgments" / "judgments.jsonl"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.common.schemas import Judgment


def main() -> int:
    judgments: list[Judgment] = []

    for path in sorted(CACHE_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text())
            judgment = Judgment.model_validate(payload)
        except Exception:
            continue
        judgments.append(judgment)

    judgments.sort(
        key=lambda judgment: (
            judgment.query_id,
            judgment.meta.get("answer_run_index", 0),
            judgment.judge,
            judgment.run_index,
        )
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as handle:
        for judgment in judgments:
            handle.write(json.dumps(judgment.model_dump(by_alias=True), ensure_ascii=False))
            handle.write("\n")

    print(f"Wrote {len(judgments)} judgments to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
