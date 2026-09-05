"""Convert the clinician-adjudicated grounding worksheet into overrides JSONL.

The reviewer answers one question per claim -- is it clinically correct? -- and
supplies an error type and risk only where the answer is no. The taxonomy label
then follows from that answer combined with the mechanically determined grounding
verdict:

    grounded + correct      -> FALSE_POSITIVE     (the judge was wrong)
    ungrounded + correct    -> UNGROUNDED_CORRECT (right, but not from the corpus)
    incorrect               -> the reviewer's error type, at the reviewer's risk

Notes are the reviewer's own words, or empty. Nothing is generated into that
field, so the audit trail never presents machine text as a clinical judgement.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from src.metrics.citation_breakdown import HALLUCINATION_LEGEND, PROVENANCE_TYPES, REAL_ERROR_TYPES

RISK_VALUES = {"none", "low", "moderate", "high"}
GROUNDED_LABEL = "GROUNDED"
# Retired in favour of the pre-existing SCOPE_EXPANSION, which means the same thing.
TYPE_ALIASES = {"OVERGENERALIZATION": "SCOPE_EXPANSION"}


def _row_label(row: dict) -> tuple[str, str, str, list[str]]:
    """Return (corrected_type, clinical_risk, clinical_note, problems)."""
    problems: list[str] = []
    correct = (row.get("clinically_correct") or "").strip().lower()
    grounding = (row.get("grounded_in_corpus") or "").strip().upper()
    note = (row.get("clinical_note") or "").strip()

    if correct == "yes":
        corrected = "FALSE_POSITIVE" if grounding == GROUNDED_LABEL else "UNGROUNDED_CORRECT"
        return corrected, "none", note, problems

    if correct == "no":
        corrected = (row.get("error_type_if_incorrect") or "").strip().upper()
        corrected = TYPE_ALIASES.get(corrected, corrected)
        risk = (row.get("clinical_risk_if_incorrect") or "").strip().lower()
        if corrected not in HALLUCINATION_LEGEND:
            problems.append(f"{row['query_id']}: unknown error type {corrected!r}")
        if risk not in RISK_VALUES:
            problems.append(f"{row['query_id']}: unknown risk {risk!r}")
        return corrected, risk, note, problems

    problems.append(f"{row['query_id']}: clinically_correct is {correct!r}, expected yes/no")
    return "", "", note, problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worksheet", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rows = list(csv.DictReader(args.worksheet.open(encoding="utf-8", newline="")))
    records, problems = [], []
    for row in rows:
        if not (row.get("query_id") or "").strip():
            continue
        corrected, risk, note, row_problems = _row_label(row)
        problems.extend(row_problems)
        if row_problems:
            continue
        records.append(
            {
                "query_id": row["query_id"],
                "judge": None,
                "claim_prefix": row["claim_prefix"],
                "auto_type": row["auto_type"],
                "corrected_type": corrected,
                "clinical_risk": risk,
                "clinical_note": note,
                "grounded_in_corpus": row.get("grounded_in_corpus", ""),
            }
        )

    for problem in problems:
        print(f"PROBLEM: {problem}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    types = Counter(r["corrected_type"] for r in records)
    risks = Counter(r["clinical_risk"] for r in records)
    real_items = {r["query_id"] for r in records if r["corrected_type"] in REAL_ERROR_TYPES}
    prov_items = {r["query_id"] for r in records if r["corrected_type"] in PROVENANCE_TYPES}

    print(f"\nwrote {len(records)} records to {args.out}")
    print(f"types: {dict(types)}")
    print(f"risks: {dict(risks)}")
    print(f"items with >=1 real error : {len(real_items)} {sorted(real_items)}")
    print(f"items with provenance gap : {len(prov_items)} {sorted(prov_items)}")


if __name__ == "__main__":
    main()
