"""Check benchmark metadata completeness for reporting and CI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_PATH = Path("data/benchmark/benchmark_queries.v2.jsonl")


def load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
    return rows


def main(argv: list[str] | None = None) -> int:
    args = argv or []
    path = Path(args[0]) if args else DEFAULT_PATH
    rows = load_rows(path)

    verified_true = 0
    acceptable_refusal_set = 0
    gate_expected_set = 0
    key_recommendations_populated = 0
    answer_key_ok = 0
    missing_required: list[str] = []
    items_needing_verification: list[str] = []

    for row in rows:
        item_id = str(row.get("id", "(missing-id)"))
        gold = row.get("gold") or {}
        verified = bool(row.get("verified", False))
        if verified:
            verified_true += 1
        else:
            items_needing_verification.append(item_id)

        acceptable_refusal = gold.get("acceptable_refusal")
        if isinstance(acceptable_refusal, bool):
            acceptable_refusal_set += 1
        else:
            missing_required.append(f"{item_id}: missing gold.acceptable_refusal")

        if gold.get("gate_expected"):
            gate_expected_set += 1
        else:
            missing_required.append(f"{item_id}: missing gold.gate_expected")

        answer_key = str(gold.get("answer_key") or "").strip()
        if answer_key:
            answer_key_ok += 1
        else:
            missing_required.append(f"{item_id}: missing gold.answer_key")

        key_recommendations = gold.get("key_recommendations") or []
        if bool(key_recommendations):
            key_recommendations_populated += 1

    total = len(rows)
    print(f"Benchmark completeness report ({total} items)")
    print("─────────────────────────────────────────")
    print(f"verified=true   : {verified_true:2d} / {total}")
    print(f"key_recommendations populated: {key_recommendations_populated:2d} / {total}")
    print(f"acceptable_refusal set        : {acceptable_refusal_set:2d} / {total}")
    print(f"gate_expected set             : {gate_expected_set:2d} / {total}")
    print(f"answer_key populated          : {answer_key_ok:2d} / {total}")
    print()
    print(
        "Items needing verification: "
        + (", ".join(items_needing_verification) if items_needing_verification else "(none)")
    )

    if missing_required:
        print()
        print("Missing required metadata:")
        for message in missing_required:
            print(f"- {message}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
