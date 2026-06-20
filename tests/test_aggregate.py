"""Tests for src/metrics/aggregate.py — within-judge and ensemble aggregation."""
from __future__ import annotations

import pytest

from src.metrics.aggregate import (
    aggregate_ensemble,
    aggregate_within_judge,
)


def _judgment(
    query_id: str,
    judge: str,
    run_index: int,
    *,
    clinical_correctness: int = 3,
    citation_support: int = 3,
    completeness: int = 3,
    uncertainty_handling: int = 3,
    hallucination_present: bool = False,
    hallucination_count: int = 0,
    safety_flag: bool = False,
    safety_reason: str | None = None,
    unsupported_citations: list[str] | None = None,
) -> dict:
    return {
        "query_id": query_id,
        "judge": judge,
        "run_index": run_index,
        "dimensions": {
            "clinical_correctness": {"score": clinical_correctness, "rationale": ""},
            "citation_support": {
                "score": citation_support,
                "rationale": "",
                "unsupported_citations": unsupported_citations or [],
            },
            "completeness": {"score": completeness, "rationale": ""},
            "uncertainty_handling": {"score": uncertainty_handling, "rationale": ""},
        },
        "hallucination": {
            "present": hallucination_present,
            "unsupported_claims": [],
            "count": hallucination_count,
        },
        "safety_critical_error": {"flag": safety_flag, "reason": safety_reason},
        "overall_comment": "",
        "_meta": {},
    }


def _three_runs(query_id: str, judge: str, **kwargs) -> list[dict]:
    return [_judgment(query_id, judge, i, **kwargs) for i in range(3)]


# ---- within-judge aggregation -----------------------------------------------


def test_within_judge_likert_median() -> None:
    runs = [
        _judgment("Q001", "judge_a", 0, clinical_correctness=1),
        _judgment("Q001", "judge_a", 1, clinical_correctness=2),
        _judgment("Q001", "judge_a", 2, clinical_correctness=3),
    ]
    result = aggregate_within_judge(runs)
    pja = result[("Q001", "judge_a")]
    assert pja.clinical_correctness == pytest.approx(2.0)


def test_within_judge_binary_mode_majority_false() -> None:
    runs = [
        _judgment("Q001", "judge_a", 0, hallucination_present=False),
        _judgment("Q001", "judge_a", 1, hallucination_present=False),
        _judgment("Q001", "judge_a", 2, hallucination_present=True),
    ]
    result = aggregate_within_judge(runs)
    assert result[("Q001", "judge_a")].hallucination_present is False


def test_within_judge_binary_mode_majority_true() -> None:
    runs = [
        _judgment("Q001", "judge_a", 0, hallucination_present=True),
        _judgment("Q001", "judge_a", 1, hallucination_present=True),
        _judgment("Q001", "judge_a", 2, hallucination_present=False),
    ]
    result = aggregate_within_judge(runs)
    assert result[("Q001", "judge_a")].hallucination_present is True


def test_within_judge_binary_mode_tie_resolves_conservatively_to_true() -> None:
    # 3 runs can't produce a tie, but _binary_mode logic: ties → True
    from src.metrics.aggregate import _binary_mode
    assert _binary_mode([True, False]) is True


def test_within_judge_safety_reasons_collected() -> None:
    runs = [
        _judgment("Q001", "judge_a", 0, safety_flag=True, safety_reason="wrong laterality"),
        _judgment("Q001", "judge_a", 1, safety_flag=True, safety_reason="wrong threshold"),
        _judgment("Q001", "judge_a", 2, safety_flag=False, safety_reason=None),
    ]
    result = aggregate_within_judge(runs)
    pja = result[("Q001", "judge_a")]
    assert pja.safety_flag is True
    assert set(pja.safety_reasons) == {"wrong laterality", "wrong threshold"}


def test_within_judge_unsupported_citations_deduped() -> None:
    runs = [
        _judgment("Q001", "judge_a", 0, unsupported_citations=["CAR-1", "CAR-2"]),
        _judgment("Q001", "judge_a", 1, unsupported_citations=["CAR-2"]),
        _judgment("Q001", "judge_a", 2, unsupported_citations=[]),
    ]
    result = aggregate_within_judge(runs)
    assert set(result[("Q001", "judge_a")].unsupported_citations) == {"CAR-1", "CAR-2"}


# ---- ensemble aggregation ---------------------------------------------------


def test_ensemble_likert_is_mean_of_judges() -> None:
    judgments = (
        _three_runs("Q001", "judge_a", citation_support=2)
        + _three_runs("Q001", "judge_b", citation_support=0)
    )
    per_judge = aggregate_within_judge(judgments)
    ensemble = aggregate_ensemble(per_judge)
    assert ensemble["Q001"].citation_support == pytest.approx(1.0)


def test_ensemble_drops_clinical_correctness_but_per_judge_retains_it() -> None:
    # clinical_correctness is removed from the scoring/reporting ensemble surface, but
    # MUST remain on PerJudgeAggregate so agreement.py can compute Krippendorff alpha.
    judgments = (
        _three_runs("Q001", "judge_a", clinical_correctness=2)
        + _three_runs("Q001", "judge_b", clinical_correctness=0)
    )
    per_judge = aggregate_within_judge(judgments)
    ensemble = aggregate_ensemble(per_judge)
    assert per_judge[("Q001", "judge_a")].clinical_correctness == pytest.approx(2.0)
    assert not hasattr(ensemble["Q001"], "clinical_correctness")


def test_ensemble_safety_flag_is_or() -> None:
    judgments = (
        _three_runs("Q001", "judge_a", safety_flag=False)
        + _three_runs("Q001", "judge_b", safety_flag=True)
    )
    per_judge = aggregate_within_judge(judgments)
    ensemble = aggregate_ensemble(per_judge)
    assert ensemble["Q001"].safety_flag is True


def test_ensemble_hallucination_is_or() -> None:
    judgments = (
        _three_runs("Q001", "judge_a", hallucination_present=False)
        + _three_runs("Q001", "judge_b", hallucination_present=True)
    )
    per_judge = aggregate_within_judge(judgments)
    ensemble = aggregate_ensemble(per_judge)
    assert ensemble["Q001"].hallucination_present is True


def test_ensemble_per_judge_list_attached() -> None:
    judgments = (
        _three_runs("Q001", "judge_a")
        + _three_runs("Q001", "judge_b")
    )
    per_judge = aggregate_within_judge(judgments)
    ensemble = aggregate_ensemble(per_judge)
    assert len(ensemble["Q001"].per_judge) == 2
