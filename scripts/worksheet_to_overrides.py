"""Convert a filled annotation worksheet back into the overrides JSONL.

Validates every value against the permitted vocabularies before writing, so a
typo in the spreadsheet fails loudly rather than silently dropping an
adjudication at report time.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from src.metrics.citation_breakdown import HALLUCINATION_LEGEND

RISK_VALUES = {"none", "low", "moderate", "high"}


def _propagate(rows: list[dict]) -> list[str]:
    """Fill blank rows from a sibling verdict, so one judgment covers its repeats.

    A reviewer annotates a claim once. Its restatements by the other judge share
    a ``group_id``; a whole item answered by a single verdict (Q202's CEAP class
    list, say) propagates across the item. Every propagation is reported so the
    audit trail shows which adjudications were entered and which were inherited.
    """
    notes: list[str] = []
    fields = ("corrected_type", "clinical_risk", "clinical_note")

    def _filled(row: dict) -> bool:
        return bool((row.get("corrected_type") or "").strip())

    for key in ("group_id", "query_id"):
        buckets: dict[str, list[dict]] = {}
        for row in rows:
            buckets.setdefault(row.get(key) or "", []).append(row)
        for bucket_key, bucket in buckets.items():
            if not bucket_key:
                continue
            sources = [row for row in bucket if _filled(row)]
            targets = [row for row in bucket if not _filled(row)]
            if len(sources) != 1 or not targets:
                continue
            for target in targets:
                for field in fields:
                    target[field] = sources[0][field]
                target["_inherited"] = bucket_key
            notes.append(
                f"{bucket_key}: 1 verdict propagated to {len(targets)} repeat claim(s)"
            )
    return notes


def convert(worksheet: Path, out_path: Path, *, strict: bool = True) -> int:
    records = []
    problems = []
    with worksheet.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    for note in _propagate(rows):
        print(f"propagated: {note}")

    for line_number, row in enumerate(rows, start=2):
            corrected = (row.get("corrected_type") or "").strip()
            risk = (row.get("clinical_risk") or "").strip().lower()
            note = (row.get("clinical_note") or "").strip()
            if not corrected and not risk:
                problems.append(f"{worksheet}:{line_number}: {row['query_id']}: not annotated")
                continue
            if corrected not in HALLUCINATION_LEGEND:
                problems.append(
                    f"{worksheet}:{line_number}: {row['query_id']}: "
                    f"unknown corrected_type {corrected!r}"
                )
                continue
            if risk not in RISK_VALUES:
                problems.append(
                    f"{worksheet}:{line_number}: {row['query_id']}: unknown clinical_risk {risk!r}"
                )
                continue
            if not note:
                problems.append(
                    f"{worksheet}:{line_number}: {row['query_id']}: clinical_note is empty"
                )
                continue
            record = {
                "query_id": row["query_id"],
                "judge": None,
                "claim_prefix": row["claim_prefix"],
                "auto_type": row["auto_type"],
                "corrected_type": corrected,
                "clinical_risk": risk,
                "clinical_note": note,
            }
            if row.get("_inherited"):
                record["inherited_from"] = row["_inherited"]
            records.append(record)

    for problem in problems:
        print(problem)
    if problems and strict:
        raise SystemExit(f"{len(problems)} unresolved row(s); fix them or pass --allow-partial.")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worksheet", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    written = convert(args.worksheet, args.out, strict=not args.allow_partial)
    print(f"wrote {written} override records to {args.out}")


if __name__ == "__main__":
    main()
