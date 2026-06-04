"""Tests for src/metrics/agreement.py — kappa, ICC, Krippendorff, Wilson CI."""
from __future__ import annotations

import pytest

from src.metrics.agreement import wilson_ci

# ---- Wilson CI --------------------------------------------------------------


def test_wilson_ci_zero_n() -> None:
    ci = wilson_ci(0, 0)
    assert ci.n == 0
    assert ci.lower == 0.0
    assert ci.upper == 1.0


def test_wilson_ci_all_pass() -> None:
    ci = wilson_ci(10, 10)
    assert ci.proportion == pytest.approx(1.0)
    # Upper bound can't exceed 1
    assert ci.upper <= 1.0
    # Lower bound should be reasonably high for n=10, p=1
    assert ci.lower > 0.7


def test_wilson_ci_half() -> None:
    ci = wilson_ci(5, 10)
    assert ci.proportion == pytest.approx(0.5)
    assert ci.lower < 0.5 < ci.upper


def test_wilson_ci_bounds_in_range() -> None:
    for successes, n in [(0, 20), (1, 20), (10, 20), (19, 20), (20, 20)]:
        ci = wilson_ci(successes, n)
        assert 0.0 <= ci.lower <= ci.upper <= 1.0


# ---- agreement statistics (smoke tests with synthetic data) -----------------


def test_compute_agreement_perfect_likert() -> None:
    """Two judges who always agree should yield kappa = 1.0."""
    from src.metrics.aggregate import PerJudgeAggregate
    from src.metrics.agreement import compute_agreement

    per_judge = {}
    for i in range(10):
        qid = f"Q{i:03d}"
        for judge in ("judge_a", "judge_b"):
            per_judge[(qid, judge)] = PerJudgeAggregate(
                query_id=qid,
                judge=judge,
                clinical_correctness=2.0,
                citation_support=3.0,
                completeness=2.0,
                uncertainty_handling=3.0,
                hallucination_present=False,
                hallucination_count=0.0,
                safety_flag=False,
            )

    report = compute_agreement(per_judge)
    for la in report.likert:
        assert la.weighted_kappa == pytest.approx(1.0, abs=1e-6)
    for ba in report.binary:
        assert ba.percent_agreement == pytest.approx(1.0)
    assert report.krippendorff_alpha == pytest.approx(1.0, abs=1e-6)


def test_compute_agreement_requires_two_judges() -> None:
    from src.metrics.aggregate import PerJudgeAggregate
    from src.metrics.agreement import compute_agreement

    per_judge = {
        ("Q001", "judge_a"): PerJudgeAggregate(
            query_id="Q001",
            judge="judge_a",
            clinical_correctness=2.0,
            citation_support=2.0,
            completeness=2.0,
            uncertainty_handling=2.0,
            hallucination_present=False,
            hallucination_count=0.0,
            safety_flag=False,
        )
    }
    with pytest.raises(ValueError, match="2 judges"):
        compute_agreement(per_judge)


def test_compute_agreement_varying_scores() -> None:
    """Agreement should be < 1 when judges disagree."""
    from src.metrics.aggregate import PerJudgeAggregate
    from src.metrics.agreement import compute_agreement

    per_judge = {}
    scores_a = [0, 1, 2, 3, 0, 1, 2, 3, 0, 1]
    scores_b = [3, 2, 1, 0, 3, 2, 1, 0, 3, 2]
    for i, (sa, sb) in enumerate(zip(scores_a, scores_b, strict=False)):
        qid = f"Q{i:03d}"
        per_judge[(qid, "judge_a")] = PerJudgeAggregate(
            query_id=qid,
            judge="judge_a",
            clinical_correctness=float(sa),
            citation_support=float(sa),
            completeness=float(sa),
            uncertainty_handling=float(sa),
            hallucination_present=False,
            hallucination_count=0.0,
            safety_flag=False,
        )
        per_judge[(qid, "judge_b")] = PerJudgeAggregate(
            query_id=qid,
            judge="judge_b",
            clinical_correctness=float(sb),
            citation_support=float(sb),
            completeness=float(sb),
            uncertainty_handling=float(sb),
            hallucination_present=False,
            hallucination_count=0.0,
            safety_flag=False,
        )

    report = compute_agreement(per_judge)
    for la in report.likert:
        assert la.weighted_kappa < 1.0
    assert report.krippendorff_alpha < 1.0
