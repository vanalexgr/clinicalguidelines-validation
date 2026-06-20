"""Within-judge and ensemble aggregation of multi-run judgments.

Aggregation strategy:
  - Within judge: median of 3 Likert runs; mode of 3 binary runs (ties → conservative: True).
  - Ensemble (two judges): mean of per-judge medians for Likert; logical OR for binary flags.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median


@dataclass(slots=True)
class PerJudgeAggregate:
    query_id: str
    judge: str
    # clinical_correctness is retained ONLY as an input to inter-rater reliability
    # (Krippendorff alpha in agreement.py reads it via LIKERT_DIMS). It is intentionally
    # NOT carried onto EnsembleAggregate, which is the scoring/pass-fail/reporting surface.
    clinical_correctness: float      # median of 3 runs
    citation_support: float
    completeness: float
    uncertainty_handling: float
    hallucination_present: bool      # mode of 3 runs (ties → True)
    hallucination_count: float       # median count
    safety_flag: bool                # mode (ties → True)
    safety_reasons: list[str] = field(default_factory=list)   # non-null reasons across runs
    unsupported_citations: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EnsembleAggregate:
    query_id: str
    # NOTE: clinical_correctness is deliberately omitted from the ensemble (scoring/
    # reporting) surface. The manuscript reframes the benchmark as faithfulness/safety;
    # clinical correctness is no longer a scored or pass/fail dimension. It remains on
    # PerJudgeAggregate so agreement.py can still compute Krippendorff alpha.
    citation_support: float
    completeness: float
    uncertainty_handling: float
    hallucination_present: bool      # OR of two judges' modes
    hallucination_count: float       # mean of median counts
    safety_flag: bool                # OR of two judges' modes
    safety_reasons: list[str] = field(default_factory=list)
    unsupported_citations: list[str] = field(default_factory=list)
    per_judge: list[PerJudgeAggregate] = field(default_factory=list)


def aggregate_within_judge(
    judgments: list[dict],
) -> dict[tuple[str, str], PerJudgeAggregate]:
    """Aggregate runs for each (query_id, judge) pair.

    ``judgments`` is a list of raw Judgment dicts (as stored on disk).
    Returns a mapping from (query_id, judge) → PerJudgeAggregate.
    """
    from collections import defaultdict

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for j in judgments:
        key = (j["query_id"], j["judge"])
        groups[key].append(j)

    result: dict[tuple[str, str], PerJudgeAggregate] = {}
    for (query_id, judge), runs in groups.items():
        result[(query_id, judge)] = _aggregate_runs(query_id, judge, runs)
    return result


def aggregate_ensemble(
    per_judge: dict[tuple[str, str], PerJudgeAggregate],
) -> dict[str, EnsembleAggregate]:
    """Combine two judges' per-judge aggregates into one ensemble per query.

    Returns a mapping from query_id → EnsembleAggregate.
    """
    from collections import defaultdict

    by_query: dict[str, list[PerJudgeAggregate]] = defaultdict(list)
    for pja in per_judge.values():
        by_query[pja.query_id].append(pja)

    result: dict[str, EnsembleAggregate] = {}
    for query_id, pjas in by_query.items():
        result[query_id] = _ensemble(query_id, pjas)
    return result


def _aggregate_runs(query_id: str, judge: str, runs: list[dict]) -> PerJudgeAggregate:
    dims = [r["dimensions"] for r in runs]

    hal_flags = [r["hallucination"]["present"] for r in runs]
    hal_counts = [r["hallucination"]["count"] for r in runs]
    safety_flags = [r["safety_critical_error"]["flag"] for r in runs]
    safety_reasons = [
        r["safety_critical_error"]["reason"]
        for r in runs
        if r["safety_critical_error"]["reason"]
    ]
    unsupported = list(
        {
            c
            for r in runs
            for c in r["dimensions"]["citation_support"].get("unsupported_citations", [])
        }
    )

    return PerJudgeAggregate(
        query_id=query_id,
        judge=judge,
        clinical_correctness=median(d["clinical_correctness"]["score"] for d in dims),
        citation_support=median(d["citation_support"]["score"] for d in dims),
        completeness=median(d["completeness"]["score"] for d in dims),
        uncertainty_handling=median(d["uncertainty_handling"]["score"] for d in dims),
        hallucination_present=_binary_mode(hal_flags),
        hallucination_count=median(hal_counts),
        safety_flag=_binary_mode(safety_flags),
        safety_reasons=safety_reasons,
        unsupported_citations=unsupported,
    )


def _ensemble(query_id: str, pjas: list[PerJudgeAggregate]) -> EnsembleAggregate:
    all_safety_reasons = list({r for p in pjas for r in p.safety_reasons})
    all_unsupported = list({c for p in pjas for c in p.unsupported_citations})
    return EnsembleAggregate(
        query_id=query_id,
        citation_support=_mean_attr(pjas, "citation_support"),
        completeness=_mean_attr(pjas, "completeness"),
        uncertainty_handling=_mean_attr(pjas, "uncertainty_handling"),
        hallucination_present=any(p.hallucination_present for p in pjas),
        hallucination_count=_mean_attr(pjas, "hallucination_count"),
        safety_flag=any(p.safety_flag for p in pjas),
        safety_reasons=all_safety_reasons,
        unsupported_citations=all_unsupported,
        per_judge=pjas,
    )


def _binary_mode(flags: list[bool]) -> bool:
    """Mode of a list of booleans; ties resolve conservatively to True."""
    true_count = sum(flags)
    false_count = len(flags) - true_count
    if true_count >= false_count:
        return True
    return False


def _mean_attr(pjas: list[PerJudgeAggregate], attr: str) -> float:
    values = [getattr(p, attr) for p in pjas]
    if not values:
        return 0.0
    return sum(values) / len(values)


__all__ = [
    "EnsembleAggregate",
    "PerJudgeAggregate",
    "aggregate_ensemble",
    "aggregate_within_judge",
]
