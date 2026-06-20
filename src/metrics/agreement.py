"""Inter-judge agreement statistics.

Computes per Likert dimension:
  - Cohen's quadratic-weighted kappa (judge-vs-judge)
  - ICC(2,1): two-way random, absolute agreement, single measures

Per binary dimension:
  - Cohen's unweighted kappa + raw % agreement

Cross-dimension:
  - Krippendorff's alpha (ordinal weights) across both judges

Wilson 95% CI is provided for pass rate (proportion), callable separately.

Required packages (already in requirements.txt):
  pingouin >= 0.5, scikit-learn >= 1.3, krippendorff >= 0.6, scipy, numpy
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import krippendorff
import numpy as np
import pandas as pd
import pingouin as pg
from sklearn.metrics import cohen_kappa_score

# These are the four originally-elicited ordinal rating dimensions. They are retained
# here for inter-rater reliability ONLY (Cohen's kappa, ICC, and the Krippendorff alpha
# reported in the manuscript). clinical_correctness in particular is no longer a scoring,
# pass/fail, or quality-reporting dimension, but MUST remain in this tuple so that the
# published Krippendorff alpha (computed over all four dimensions) is preserved.
LIKERT_DIMS = ("clinical_correctness", "citation_support", "completeness", "uncertainty_handling")
BINARY_DIMS = ("hallucination_present", "safety_flag")


@dataclass(slots=True)
class LikertAgreement:
    dimension: str
    weighted_kappa: float
    icc_value: float
    icc_ci_lower: float
    icc_ci_upper: float


@dataclass(slots=True)
class BinaryAgreement:
    dimension: str
    kappa: float
    percent_agreement: float


@dataclass(slots=True)
class AgreementReport:
    likert: list[LikertAgreement]
    binary: list[BinaryAgreement]
    krippendorff_alpha: float    # ordinal, across all Likert dims combined


@dataclass(slots=True)
class WilsonCI:
    proportion: float
    lower: float
    upper: float
    n: int


def compute_agreement(
    per_judge_aggregates: dict[tuple[str, str], object],
) -> AgreementReport:
    """Compute full inter-judge agreement from per-judge aggregates.

    ``per_judge_aggregates`` is the output of ``aggregate_within_judge``:
    mapping (query_id, judge) → PerJudgeAggregate.

    Requires exactly 2 distinct judges. Queries present for only one judge
    are silently skipped (logged via warnings).
    """
    import warnings
    from collections import defaultdict

    by_query: dict[str, dict[str, object]] = defaultdict(dict)
    for (query_id, judge), pja in per_judge_aggregates.items():
        by_query[query_id][judge] = pja

    judges = sorted({j for _, j in per_judge_aggregates})
    if len(judges) != 2:
        raise ValueError(f"Expected exactly 2 judges, got {judges}")

    j1, j2 = judges
    paired_query_ids = [
        qid for qid, jmap in by_query.items() if j1 in jmap and j2 in jmap
    ]
    if not paired_query_ids:
        raise ValueError("No queries have judgments from both judges.")

    skipped = len(by_query) - len(paired_query_ids)
    if skipped:
        warnings.warn(f"{skipped} queries skipped (missing one judge).", stacklevel=2)

    likert_results: list[LikertAgreement] = []
    for dim in LIKERT_DIMS:
        scores_j1 = [getattr(by_query[qid][j1], dim) for qid in paired_query_ids]
        scores_j2 = [getattr(by_query[qid][j2], dim) for qid in paired_query_ids]
        likert_results.append(_likert_agreement(dim, scores_j1, scores_j2))

    binary_results: list[BinaryAgreement] = []
    for dim in BINARY_DIMS:
        flags_j1 = [getattr(by_query[qid][j1], dim) for qid in paired_query_ids]
        flags_j2 = [getattr(by_query[qid][j2], dim) for qid in paired_query_ids]
        binary_results.append(_binary_agreement(dim, flags_j1, flags_j2))

    kripp_alpha = _krippendorff_alpha(by_query, j1, j2, paired_query_ids)

    return AgreementReport(
        likert=likert_results,
        binary=binary_results,
        krippendorff_alpha=kripp_alpha,
    )


def wilson_ci(successes: int, n: int, *, confidence: float = 0.95) -> WilsonCI:
    """Wilson score 95% CI for a proportion (pass rate, accuracy, etc.)."""
    if n == 0:
        return WilsonCI(proportion=0.0, lower=0.0, upper=1.0, n=0)
    p = successes / n
    z = _z_score(confidence)
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half_width = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return WilsonCI(
        proportion=p,
        lower=max(0.0, centre - half_width),
        upper=min(1.0, centre + half_width),
        n=n,
    )


def _likert_agreement(
    dim: str, scores_j1: list[float], scores_j2: list[float]
) -> LikertAgreement:
    # Round medians to nearest integer for kappa (kappa needs integer categories)
    int_j1 = [round(s) for s in scores_j1]
    int_j2 = [round(s) for s in scores_j2]

    wk = _weighted_kappa(int_j1, int_j2)
    icc_val, icc_lo, icc_hi = _icc(scores_j1, scores_j2)

    return LikertAgreement(
        dimension=dim,
        weighted_kappa=wk,
        icc_value=icc_val,
        icc_ci_lower=icc_lo,
        icc_ci_upper=icc_hi,
    )


def _binary_agreement(dim: str, flags_j1: list[bool], flags_j2: list[bool]) -> BinaryAgreement:
    labels_j1 = [int(f) for f in flags_j1]
    labels_j2 = [int(f) for f in flags_j2]
    # If all labels are identical, sklearn raises; handle degenerate case.
    try:
        kappa = float(cohen_kappa_score(labels_j1, labels_j2))
    except ValueError:
        kappa = 1.0 if labels_j1 == labels_j2 else 0.0

    agree = sum(a == b for a, b in zip(labels_j1, labels_j2, strict=False))
    pct = agree / len(labels_j1) if labels_j1 else 0.0
    return BinaryAgreement(dimension=dim, kappa=kappa, percent_agreement=pct)


def _weighted_kappa(y1: list[int], y2: list[int]) -> float:
    import math
    try:
        val = float(cohen_kappa_score(y1, y2, weights="quadratic"))
    except ValueError:
        return 1.0 if y1 == y2 else 0.0
    # sklearn returns NaN when only one label class is observed (perfect homogeneity).
    if math.isnan(val):
        return 1.0 if y1 == y2 else 0.0
    return val


def _icc(scores_j1: list[float], scores_j2: list[float]) -> tuple[float, float, float]:
    """ICC(2,1) / ICC(A,1) via pingouin (two-way random, absolute agreement, single measures).

    Requires n >= 5; returns (NaN, NaN, NaN) for smaller samples.
    """
    import math
    n = len(scores_j1)
    if n < 5:
        return math.nan, math.nan, math.nan
    df = pd.DataFrame(
        {
            "target": list(range(n)) * 2,
            "rater": ["j1"] * n + ["j2"] * n,
            "score": scores_j1 + scores_j2,
        }
    )
    icc_df = pg.intraclass_corr(data=df, targets="target", raters="rater", ratings="score")
    # pingouin labels ICC(2,1) as "ICC(A,1)" (absolute agreement, single measures)
    row = icc_df[icc_df["Type"] == "ICC(A,1)"].iloc[0]
    ci = row["CI95"]
    return float(row["ICC"]), float(ci[0]), float(ci[1])


def _krippendorff_alpha(
    by_query: dict,
    j1: str,
    j2: str,
    query_ids: list[str],
) -> float:
    """Krippendorff's alpha (ordinal) across all 4 Likert dims, both judges."""
    all_scores: list[list[float | None]] = []
    for judge in (j1, j2):
        row: list[float | None] = []
        for qid in query_ids:
            pja = by_query[qid][judge]
            for dim in LIKERT_DIMS:
                row.append(float(getattr(pja, dim)))
        all_scores.append(row)
    # krippendorff expects (raters × items) array, missing as np.nan
    data = np.array([[v if v is not None else np.nan for v in row] for row in all_scores])
    return float(krippendorff.alpha(reliability_data=data, level_of_measurement="ordinal"))


def _z_score(confidence: float) -> float:
    from scipy.stats import norm
    return float(norm.ppf(1 - (1 - confidence) / 2))


__all__ = [
    "AgreementReport",
    "BinaryAgreement",
    "LikertAgreement",
    "WilsonCI",
    "compute_agreement",
    "wilson_ci",
]
