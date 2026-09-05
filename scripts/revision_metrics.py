"""Revision analyses: the quantities the JAMIA reviewers asked for.

Each block corresponds to a specific reviewer point:

  default-open region      R1 major 6  ("the single most safety-critical parameter")
  per-scenario recall      R1 major 5  (is 0.29 a minimum-information standard?)
  inappropriate refusals   self-disclosed; not previously reported
  latency distribution     R1 minor 3  (hard timeout or observed maximum?)
  citation root causes     R1 major 8  (how does a locked-corpus system cite outside it?)

The gate classifier below mirrors AgentConsultController::inferClarificationScenario
in the deployed Laravel router. That repository's history was rewritten, so the
version live at the 2026-06-06 benchmark cannot be recovered from git; these
figures describe the classifier as currently deployed.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

PATIENT_SPECIFIC = re.compile(
    r"\b(my patient|this patient|the patient|patient\b|case\b|\d{1,3}\s*(?:year[- ]old|yo)|"
    r"male\b|female\b|man\b|woman\b|presents? with|presented with|referred with)\b",
    re.I,
)
SCENARIOS = [
    ("carotid_stenosis", re.compile(r"carotid", re.I)),
    ("aaa_treatment", re.compile(r"\b(aaa|aneurysm)\b", re.I)),
    ("dvt_pe", re.compile(r"\b(dvt|pe|vte)\b", re.I)),
    ("clti", re.compile(r"clti", re.I)),
    ("ali", re.compile(r"\b(acute limb ischaemia|acute limb ischemia|ali)\b", re.I)),
    ("type_b_dissection", re.compile(r"\b(type b|dissection)\b", re.I)),
    ("graft_infection", re.compile(r"\b(graft infection|endograft infection|fistula)\b", re.I)),
]
REFUSAL = re.compile(r"does not (?:explicitly )?address", re.I)


def classify(text: str) -> tuple[bool, str]:
    """Return (gate engages at all, scenario label) for one query."""
    if not PATIENT_SPECIFIC.search(text):
        return False, "not_patient_specific"
    for name, pattern in SCENARIOS:
        if pattern.search(text):
            return True, name
    return True, "generic_case"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args()
    repo = args.repo

    bench = {
        json.loads(line)["id"]: json.loads(line)
        for line in (repo / "data/benchmark/benchmark_queries.v2.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    answers = {
        json.loads(line)["id"]: json.loads(line)
        for line in (repo / "outputs/answers/answers.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    print("=" * 74)
    print("R1.6  DEFAULT-OPEN REGION")
    print("=" * 74)
    engaged, scenarios = {}, Counter()
    for qid, item in bench.items():
        text = " ".join(t.get("content", "") for t in item["turns"])
        gate_on, scenario = classify(text)
        engaged[qid] = gate_on
        scenarios[scenario] += 1
    n = len(bench)
    unguarded = sum(1 for v in engaged.values() if not v)
    generic = scenarios["generic_case"]
    print(f"  benchmark items                        {n}")
    print(f"  gate cannot engage (not patient-specific)  {unguarded:3d}  {unguarded/n:6.1%}")
    print(f"  reaches the generic catch-all              {generic:3d}  {generic/n:6.1%}")
    print(f"  matched one of the 7 named scenarios       {n-unguarded-generic:3d}  {(n-unguarded-generic)/n:6.1%}")
    print("\n  scenario distribution:")
    for name, count in scenarios.most_common():
        print(f"    {name:24s} {count:3d}")
    named = {s for s, _ in SCENARIOS}
    print(f"\n  named scenarios never exercised: {sorted(named - set(scenarios)) or 'none'}")

    print()
    print("=" * 74)
    print("R1.5  PARAMETER RECALL BY SCENARIO (underspecified items only)")
    print("=" * 74)
    per_scenario = defaultdict(list)
    for qid, item in bench.items():
        if item.get("query_type") != "C_underspecified":
            continue
        text = " ".join(t.get("content", "") for t in item["turns"])
        _, scenario = classify(text)
        required = item["gold"].get("required_parameters") or []
        asked = " ".join(answers.get(qid, {}).get("clarification_requested") or [])
        low = asked.lower()
        hits = sum(
            1 for p in required
            if any(t in low for t in [x for x in re.split(r"[^a-z0-9.]+", p.lower()) if len(x) > 3])
        )
        per_scenario[scenario].append((qid, hits, len(required)))
    for scenario, rows in sorted(per_scenario.items()):
        hit = sum(h for _, h, _ in rows)
        tot = sum(t for _, _, t in rows)
        print(f"  {scenario:24s} {len(rows):2d} items   {hit:3d}/{tot:3d} parameters  {hit/tot if tot else 0:6.1%}")

    print()
    print("=" * 74)
    print("INAPPROPRIATE REFUSALS (not previously reported)")
    print("=" * 74)
    wrong = [
        qid for qid, a in answers.items()
        if REFUSAL.search(a.get("raw_response", ""))
        and not bench[qid]["gold"].get("acceptable_refusal")
    ]
    in_scope = [q for q, i in bench.items() if i["query_type"] != "G_should_refuse"]
    print(f"  in-scope items refused  {len(wrong)}/{len(in_scope)} = {len(wrong)/len(in_scope):.1%}")
    print(f"  items: {', '.join(sorted(wrong))}")
    print(f"  by type: {dict(Counter(bench[q]['query_type'] for q in wrong))}")

    print()
    print("=" * 74)
    print("R1.minor.3  LATENCY")
    print("=" * 74)
    lat = sorted(float(a["latency_seconds"]) for a in answers.values() if a.get("latency_seconds"))
    if lat:
        print(f"  n={len(lat)}  min={lat[0]:.1f}s  median={statistics.median(lat):.1f}s  "
              f"p90={lat[int(0.9*len(lat))-1]:.1f}s  max={lat[-1]:.1f}s")
        for ceiling in (30, 40, 45):
            print(f"  within {ceiling}s: {sum(1 for x in lat if x <= ceiling)}/{len(lat)}")

    print()
    print("=" * 74)
    print("R1.8  UNVERIFIABLE CITATIONS -- ROOT CAUSE")
    print("=" * 74)
    breakdown = json.loads((repo / "outputs/metrics/citation_breakdown.json").read_text(encoding="utf-8"))
    missing = [
        (qid, c) for qid, payload in breakdown["per_item"].items()
        for c in payload.get("citations", []) if c.get("tier") == "D_NOT_IN_INDEX"
    ]
    print(f"  {len(missing)} citations not found in the recommendation index")
    by_guideline = Counter(c["guideline"] for _, c in missing)
    for guideline, count in by_guideline.most_common():
        ids = sorted({str(c["rec_id"]) for q, c in missing if c["guideline"] == guideline})
        print(f"    {guideline:46s} {count:3d}   rec_ids: {', '.join(ids[:10])}")
    decimal = sum(1 for _, c in missing if "." in str(c.get("rec_id", "")))
    print(f"\n  rec_ids with decimal numbering (e.g. 6.35): {decimal}/{len(missing)}")
    print(f"  distinct items affected: {len({q for q, _ in missing})}")


if __name__ == "__main__":
    main()
