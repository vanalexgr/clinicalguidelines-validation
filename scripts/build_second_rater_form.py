"""Build a blinded adjudication form for an independent second rater.

The point is to measure agreement, not to re-check the first rater, so the form
must not reveal what the first rater decided. It mixes every claim the first rater
called a clinical error with a random sample of claims they cleared, shuffles them,
and strips all first-rater fields. Without the cleared claims the second rater
would know every row was a suspected error and agreement would be meaningless.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

REAL_ERROR = {
    "WRONG_APPLICATION", "WRONG_THRESHOLD", "SCOPE_EXPANSION",
    "METADATA_INFLATION", "SEVERITY_INFLATION", "GENUINE_ERROR", "OTHER",
}
COLUMNS = [
    "row_id", "query_id", "clinical_question", "flagged_statement",
    "what_the_agent_said", "cited_recommendations", "gold_answer_key",
    "is_this_a_clinical_error", "error_type_if_yes", "clinical_risk_if_yes",
    "reason",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--worksheet", type=Path, required=True,
                        help="the grounding worksheet holding claim context")
    parser.add_argument("--controls", type=int, default=17,
                        help="cleared claims to mix in alongside the flagged ones")
    parser.add_argument("--seed", type=int, default=20260906)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    overrides = {
        (r["query_id"], r["claim_prefix"][:60].rstrip()): r
        for r in (
            json.loads(line)
            for line in (args.repo / "data/annotation/hallucination_overrides.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()
        )
        if str(r.get("run", "")).startswith("phase3")
    }
    context = list(csv.DictReader(args.worksheet.open(encoding="utf-8-sig", newline="")))
    answers = {
        json.loads(line)["id"]: json.loads(line)
        for line in (args.repo / "outputs/answers/answers.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    }
    bench = {
        json.loads(line)["id"]: json.loads(line)
        for line in (args.repo / "data/benchmark/benchmark_queries.v2.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    }

    errors, cleared = [], []
    for row in context:
        key = (row["query_id"], row["claim_prefix"][:60].rstrip())
        rec = overrides.get(key)
        if rec is None:
            continue
        (errors if rec["corrected_type"] in REAL_ERROR else cleared).append(row)

    rng = random.Random(args.seed)
    sample = errors + rng.sample(cleared, min(args.controls, len(cleared)))
    rng.shuffle(sample)

    rows = []
    for n, row in enumerate(sample, start=1):
        qid = row["query_id"]
        cites = answers.get(qid, {}).get("citations") or []
        rows.append({
            "row_id": f"R{n:02d}",
            "query_id": qid,
            "clinical_question": (bench.get(qid, {}).get("turns", [{}])[0].get("content", ""))[:900],
            "flagged_statement": row["claim"],
            "what_the_agent_said": row["what_the_agent_actually_said"],
            "cited_recommendations": " | ".join(
                f"{c.get('guideline')} rec {c.get('rec_id')} "
                f"(Class {c.get('class')} Level {c.get('level')})" for c in cites
            )[:700],
            "gold_answer_key": (bench.get(qid, {}).get("gold", {}).get("answer_key", ""))[:700],
            "is_this_a_clinical_error": "",
            "error_type_if_yes": "",
            "clinical_risk_if_yes": "",
            "reason": "",
        })

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    # The key is written separately so it is never in the file the rater opens.
    key_path = args.out.with_name(args.out.stem + "_KEY_DO_NOT_SHARE.csv")
    with key_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["row_id", "query_id", "rater1_verdict",
                                                    "rater1_type", "rater1_risk"])
        writer.writeheader()
        for out_row, src in zip(rows, sample, strict=False):
            rec = overrides[(src["query_id"], src["claim_prefix"][:60].rstrip())]
            writer.writerow({
                "row_id": out_row["row_id"],
                "query_id": src["query_id"],
                "rater1_verdict": "error" if rec["corrected_type"] in REAL_ERROR else "not_error",
                "rater1_type": rec["corrected_type"],
                "rater1_risk": rec["clinical_risk"],
            })

    print(f"{len(rows)} rows: {len(errors)} flagged as errors by rater 1, "
          f"{len(sample) - len(errors)} cleared controls")
    print(f"form: {args.out}")
    print(f"key : {key_path}  (do not send this to the second rater)")


if __name__ == "__main__":
    main()
