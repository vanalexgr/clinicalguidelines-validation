"""Score the returned second-rater form against the first rater.

Reports Cohen's kappa on the binary clinical-error judgement, raw agreement, the
prevalence-adjusted value, agreement on risk grade among claims both raters called
errors, and a list of every disagreement so none is quietly absorbed.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from pathlib import Path

from sklearn.metrics import cohen_kappa_score


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--form", type=Path, required=True, help="completed second-rater form")
    parser.add_argument("--key", type=Path, required=True, help="the withheld rater-1 key")
    args = parser.parse_args()

    form = {r["row_id"]: r for r in csv.DictReader(args.form.open(encoding="utf-8-sig", newline=""))}
    key = {r["row_id"]: r for r in csv.DictReader(args.key.open(encoding="utf-8-sig", newline=""))}

    r1, r2, unscored, disagreements = [], [], [], []
    for row_id, k in key.items():
        f = form.get(row_id)
        verdict = (f or {}).get("is_this_a_clinical_error", "").strip().lower()
        if verdict not in {"yes", "no"}:
            unscored.append(row_id)
            continue
        a = k["rater1_verdict"] == "error"
        b = verdict == "yes"
        r1.append(int(a))
        r2.append(int(b))
        if a != b:
            disagreements.append(
                (row_id, k["query_id"], "error" if a else "not error",
                 "error" if b else "not error", (f.get("reason") or "").strip()[:100])
            )

    if unscored:
        print(f"WARNING: {len(unscored)} rows not completed: {', '.join(unscored)}\n")
    if not r1:
        raise SystemExit("no scored rows")

    agree = sum(a == b for a, b in zip(r1, r2, strict=False))
    pct = agree / len(r1)
    kappa = cohen_kappa_score(r1, r2)
    lo, hi = wilson(agree, len(r1))

    print(f"scored rows          : {len(r1)}")
    print(f"rater 1 called error : {sum(r1)}")
    print(f"rater 2 called error : {sum(r2)}")
    print(f"raw agreement        : {pct:.1%} ({agree}/{len(r1)}); 95% CI {lo:.1%}-{hi:.1%}")
    print(f"Cohen's kappa        : {kappa:+.3f}")
    print(f"PABAK                : {2 * pct - 1:+.3f}")

    both = [
        rid for rid, k in key.items()
        if k["rater1_verdict"] == "error"
        and (form.get(rid, {}).get("is_this_a_clinical_error", "").strip().lower() == "yes")
    ]
    if both:
        same_risk = sum(
            1 for rid in both
            if key[rid]["rater1_risk"].strip().lower()
            == form[rid].get("clinical_risk_if_yes", "").strip().lower()
        )
        print(f"\nrisk grade agreement among claims both called errors: "
              f"{same_risk}/{len(both)}")
        print("  rater 2 risk grades:",
              dict(Counter(form[r].get("clinical_risk_if_yes", "").strip().lower() for r in both)))

    if disagreements:
        print(f"\ndisagreements ({len(disagreements)}):")
        for rid, qid, a, b, why in disagreements:
            print(f"  {rid} {qid}: rater1={a}, rater2={b}")
            if why:
                print(f"      rater2: {why}")
    else:
        print("\nno disagreements")


if __name__ == "__main__":
    main()
