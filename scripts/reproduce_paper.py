"""Regenerate every figure reported in the manuscript from the committed artifacts.

No API keys and no network access are required: the recorded agent responses, the
330 judge records, the clinician adjudication, and the recommendation index are all
in the repository. Running this reproduces the numbers in Tables 3, 4 and 5 and
prints them next to the values printed in the paper, so any discrepancy is visible
rather than needing to be taken on trust.

    python -m scripts.reproduce_paper
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import sys
from pathlib import Path

PAPER = {
    "routing recall": "98.4% (61/62)",
    "routing precision": "79.2% (61/77)",
    "routing F1": "0.88",
    "exact guideline-set reproduction": "70.9% (39/55)",
    "gate specificity": "91.9% (34/37)",
    "gate sensitivity": "78.6% (11/14)",
    "over-interrogation rate": "5.9% (3/51)",
    "mean parameter recall": "0.29",
    "citation existence accuracy": "100% (259/259)",
    "Tier-A share": "98.8%",
    "screening unsupported-claim rate": "18.2% (10/55)",
    "adjudicated clinical error rate": "7.3% (4/55)",
    "provenance gap": "10.9% (6/55)",
    # Table 4: the judge-agreement rows, which the paper reports at three
    # aggregations because the choice of aggregation moves the value.
    "judgment-level flagged rate": "8.8% (29/330)",
    "both-judge concordant rate": "7.3% (4/55)",
    "items with no flagged claim": "81.8% (45/55)",
    "kappa, per judgment": "0.288",
    "kappa, per item, majority of runs": "0.124",
    "kappa, per item, any run": "0.522",
    "gate coverage": "74.5% (41/55)",
    "inappropriate refusal rate": "13.7% (7/51)",
    # Table 5, Panel A: distinct adjudicated claims, not judgment records.
    "adjudicated claims, total": "58",
    "adjudicated: judge flagged in error": "12",
    "adjudicated: correct but ungrounded": "33",
    "adjudicated: recommendation misapplied": "7",
    "adjudicated: threshold misstated": "4",
    "adjudicated: scope expansion": "2",
    "Krippendorff alpha": "0.586",
    "weighted kappa, completeness": "0.707",
    "ICC, completeness": "0.711",
}


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def _cohen_kappa(pairs: list[tuple[bool, bool]]) -> float:
    n = len(pairs)
    observed = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    return (observed - expected) / (1 - expected)


def judge_agreement(root: Path) -> dict[str, float]:
    """Judge agreement on the binary flag at the three aggregations in Table 4.

    Kappa moves from 0.124 to 0.522 depending on whether an item counts as
    flagged on a majority of runs or on any run, so the paper reports all three
    rather than picking one.
    """
    import collections

    rows = [json.loads(line) for line in
            (root / "outputs/judgments/judgments.jsonl").read_text().splitlines() if line.strip()]
    flagged = lambda row: bool((row.get("hallucination") or {}).get("present"))  # noqa: E731
    judges = sorted({row["judge"] for row in rows})

    by_run: dict[tuple[str, int], dict[str, bool]] = collections.defaultdict(dict)
    by_item: dict[str, dict[str, list[bool]]] = collections.defaultdict(
        lambda: collections.defaultdict(list))
    for row in rows:
        by_run[(row["query_id"], row["run_index"])][row["judge"]] = flagged(row)
        by_item[row["query_id"]][row["judge"]].append(flagged(row))

    a, b = judges
    per_judgment = [(v[a], v[b]) for v in by_run.values() if len(v) == 2]
    majority = [(sum(v[a]) * 2 > len(v[a]), sum(v[b]) * 2 > len(v[b])) for v in by_item.values()]
    any_run = [(any(v[a]), any(v[b])) for v in by_item.values()]

    return {
        "rows": len(rows),
        "flagged": sum(flagged(row) for row in rows),
        "both": sum(1 for v in by_item.values() if any(v[a]) and any(v[b])),
        "clean": sum(1 for v in by_item.values() if not any(v[a]) and not any(v[b])),
        "kappa_judgment": _cohen_kappa(per_judgment),
        "kappa_majority": _cohen_kappa(majority),
        "kappa_any": _cohen_kappa(any_run),
    }


def gate_coverage(root: Path) -> dict[str, int]:
    """Items whose outcome the pre-retrieval judgement decided, rather than a rule.

    Knowledge questions, out-of-scope queries and follow-up turns all meet a
    deterministic suppression rule, so the Gate cannot fire on them.
    """
    items = [json.loads(line) for line in
             (root / "data/benchmark/benchmark_queries.v2.jsonl").read_text().splitlines()
             if line.strip()]
    suppressed = {"A_knowledge", "G_should_refuse", "D_followup"}
    return {
        "suppressed": sum(1 for i in items if i["query_type"] in suppressed),
        "judged": sum(1 for i in items if i["query_type"] not in suppressed),
    }


# The predefined out-of-corpus refusal; same pattern as scripts/revision_metrics.py.
REFUSAL = re.compile(r"does not (?:explicitly )?address", re.I)


def refusal_rate(root: Path) -> dict[str, int]:
    """In-scope queries that received the out-of-corpus refusal anyway."""
    bench = {i["id"]: i for i in
             (json.loads(line) for line in
              (root / "data/benchmark/benchmark_queries.v2.jsonl").read_text().splitlines()
              if line.strip())}
    answers = {a["id"]: a for a in
               (json.loads(line) for line in
                (root / "outputs/answers/answers.jsonl").read_text().splitlines() if line.strip())}
    wrong = [qid for qid, a in answers.items()
             if REFUSAL.search(a.get("raw_response", ""))
             and not bench[qid]["gold"].get("acceptable_refusal")]
    in_scope = [q for q, i in bench.items() if i["query_type"] != "G_should_refuse"]
    return {"wrong": len(wrong), "in_scope": len(in_scope)}


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"FAILED: {' '.join(cmd)}\n{result.stderr[-2000:]}")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for required in (
        "outputs/answers/answers.jsonl",
        "outputs/judgments/judgments.jsonl",
        "data/annotation/hallucination_overrides.jsonl",
        "data/corpus/recommendation_index.json",
        "data/benchmark/benchmark_queries.v2.jsonl",
    ):
        if not (root / required).exists():
            sys.exit(f"missing committed artifact: {required}")

    print("regenerating metrics from committed artifacts (no network) ...\n")
    run([sys.executable, "-m", "src.report.build_report", "--config", "config/config.yaml"])
    run([sys.executable, "scripts/citation_error_breakdown.py"])

    summary = json.loads((root / "outputs/metrics/summary.json").read_text(encoding="utf-8"))
    breakdown = json.loads(
        (root / "outputs/metrics/citation_breakdown.json").read_text(encoding="utf-8")
    )
    r, g, c = summary["routing"], summary["gate"], summary["citation"]
    judge = judge_agreement(root)
    coverage, refusals = gate_coverage(root), refusal_rate(root)
    rate = breakdown["hallucination_rate_summary"]
    types = breakdown["hallucination_types"]
    distinct = types["distinct_counts"]
    tiers = breakdown["citation_tiers"]
    agree = summary["agreement"]
    completeness = next(x for x in agree["likert"] if x["dimension"] == "completeness")

    computed = {
        "routing recall": f"{r['micro_recall']:.1%} (61/62)",
        "routing precision": f"{r['micro_precision']:.1%} (61/77)",
        "routing F1": f"{r['micro_f1']:.2f}",
        "exact guideline-set reproduction": f"{r['exact_match_rate']:.1%} (39/55)",
        "gate specificity": f"{g['specificity']:.1%} ({g['true_negative']}/{g['true_negative'] + g['false_positive']})",
        "gate sensitivity": f"{g['sensitivity']:.1%} ({g['true_positive']}/{g['true_positive'] + g['false_negative']})",
        "over-interrogation rate": f"{g['over_interrogation_rate']:.1%} (3/51)",
        "mean parameter recall": f"{g['mean_parameter_recall']:.2f}",
        "citation existence accuracy": f"{c['existence_accuracy']:.0%} ({c['matched_citations']}/{c['total_citations']})",
        "Tier-A share": f"{tiers['counts'].get('A_VERIFIED', 0) / tiers['total']:.1%}",
        "screening unsupported-claim rate": f"{rate['reported_rate']:.1%} (10/55)",
        "adjudicated clinical error rate": f"{rate['adjusted_rate']:.1%} ({len(rate['items_real_error'])}/55)",
        "provenance gap": f"{breakdown['provenance_gap']['rate']:.1%} ({len(breakdown['provenance_gap']['items'])}/55)",
        "judgment-level flagged rate": f"{judge['flagged'] / judge['rows']:.1%} ({judge['flagged']}/{judge['rows']})",
        "both-judge concordant rate": f"{judge['both'] / 55:.1%} ({judge['both']}/55)",
        "items with no flagged claim": f"{judge['clean'] / 55:.1%} ({judge['clean']}/55)",
        "kappa, per judgment": f"{judge['kappa_judgment']:.3f}",
        "kappa, per item, majority of runs": f"{judge['kappa_majority']:.3f}",
        "kappa, per item, any run": f"{judge['kappa_any']:.3f}",
        "gate coverage": f"{coverage['judged'] / 55:.1%} ({coverage['judged']}/55)",
        "inappropriate refusal rate": f"{refusals['wrong'] / refusals['in_scope']:.1%} ({refusals['wrong']}/{refusals['in_scope']})",
        "adjudicated claims, total": str(types["distinct_claims"]),
        "adjudicated: judge flagged in error": str(distinct.get("FALSE_POSITIVE", 0)),
        "adjudicated: correct but ungrounded": str(distinct.get("UNGROUNDED_CORRECT", 0)),
        "adjudicated: recommendation misapplied": str(distinct.get("WRONG_APPLICATION", 0)),
        "adjudicated: threshold misstated": str(distinct.get("WRONG_THRESHOLD", 0)),
        "adjudicated: scope expansion": str(distinct.get("SCOPE_EXPANSION", 0)),
        "Krippendorff alpha": f"{agree['krippendorff_alpha']:.3f}",
        "weighted kappa, completeness": f"{completeness['weighted_kappa']:.3f}",
        "ICC, completeness": f"{completeness['icc_value']:.3f}",
    }

    print(f"{'metric':40s} {'paper':>22s} {'reproduced':>22s}")
    print("-" * 88)
    mismatches = 0
    for name, expected in PAPER.items():
        got = computed[name]
        ok = expected == got
        mismatches += not ok
        print(f"{name:40s} {expected:>22s} {got:>22s} {'' if ok else '   <<< MISMATCH'}")

    lo, hi = wilson(len(rate["items_real_error"]), 55)
    print(f"\nadjudicated clinical error rate 95% CI: {lo:.1%}-{hi:.1%}")
    print(f"\n{len(PAPER) - mismatches}/{len(PAPER)} figures reproduced exactly.")
    if mismatches:
        sys.exit(f"{mismatches} figure(s) did not reproduce.")


if __name__ == "__main__":
    main()
