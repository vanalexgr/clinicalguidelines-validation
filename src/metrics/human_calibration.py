"""Judge-vs-human calibration metrics for clinician-scored review items."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

from sklearn.metrics import cohen_kappa_score

from src.metrics.aggregate import EnsembleAggregate, PerJudgeAggregate

LIKERT_COLUMNS = {
    "clinical_correctness": "human_score_clinical_correctness",
    "citation_support": "human_score_citation_support",
    "completeness": "human_score_completeness",
    "uncertainty_handling": "human_score_uncertainty_handling",
}
BINARY_COLUMNS = {
    "hallucination_present": "human_score_hallucination_present",
    "safety_flag": "human_score_safety_flag",
}


@dataclass(slots=True)
class HumanLikertAgreement:
    dimension: str
    judge_a_kappa: float
    judge_b_kappa: float
    ensemble_kappa: float
    n: int


@dataclass(slots=True)
class HumanBinaryAgreement:
    dimension: str
    judge_a_kappa: float
    judge_b_kappa: float
    ensemble_kappa: float
    percent_agreement_ensemble: float
    n: int


@dataclass(slots=True)
class HumanCalibrationReport:
    likert: list[HumanLikertAgreement]
    binary: list[HumanBinaryAgreement]
    n_items_scored: int
    judges: list[str]


def compute_human_calibration(
    human_scores_path: Path | str,
    per_judge: dict[tuple[str, str], PerJudgeAggregate],
    ensembles: dict[str, EnsembleAggregate],
) -> HumanCalibrationReport:
    """Compute judge-vs-human kappa metrics for reviewed items."""
    judges = sorted({judge for _, judge in per_judge})
    if len(judges) != 2:
        raise ValueError(f"Expected exactly 2 judges, got {judges}")

    judge_a, judge_b = judges
    human_scores = _load_human_scores(human_scores_path)
    by_query: dict[str, dict[str, PerJudgeAggregate]] = {}
    for (query_id, judge), aggregate in per_judge.items():
        by_query.setdefault(query_id, {})[judge] = aggregate

    scored_query_ids = [
        query_id
        for query_id in sorted(human_scores)
        if query_id in ensembles
        and query_id in by_query
        and judge_a in by_query[query_id]
        and judge_b in by_query[query_id]
    ]

    likert = [
        _compute_likert_agreement(
            dimension=dimension,
            human_column=human_column,
            query_ids=scored_query_ids,
            human_scores=human_scores,
            by_query=by_query,
            ensembles=ensembles,
            judge_a=judge_a,
            judge_b=judge_b,
        )
        for dimension, human_column in LIKERT_COLUMNS.items()
    ]
    binary = [
        _compute_binary_agreement(
            dimension=dimension,
            human_column=human_column,
            query_ids=scored_query_ids,
            human_scores=human_scores,
            by_query=by_query,
            ensembles=ensembles,
            judge_a=judge_a,
            judge_b=judge_b,
        )
        for dimension, human_column in BINARY_COLUMNS.items()
    ]
    return HumanCalibrationReport(
        likert=likert,
        binary=binary,
        n_items_scored=len(scored_query_ids),
        judges=judges,
    )


def _ensemble_likert_value(
    ensembles: dict[str, EnsembleAggregate],
    by_query: dict[str, dict[str, PerJudgeAggregate]],
    query_id: str,
    dimension: str,
) -> float:
    """Ensemble Likert value for ``dimension``.

    Most dimensions live on EnsembleAggregate. clinical_correctness is intentionally not a
    scoring dimension and is absent there, so fall back to the mean of the per-judge medians.
    """
    ensemble = ensembles[query_id]
    if hasattr(ensemble, dimension):
        return float(getattr(ensemble, dimension))
    per_judge = list(by_query[query_id].values())
    values = [float(getattr(pja, dimension)) for pja in per_judge]
    return sum(values) / len(values) if values else 0.0


def _compute_likert_agreement(
    *,
    dimension: str,
    human_column: str,
    query_ids: list[str],
    human_scores: dict[str, dict[str, int]],
    by_query: dict[str, dict[str, PerJudgeAggregate]],
    ensembles: dict[str, EnsembleAggregate],
    judge_a: str,
    judge_b: str,
) -> HumanLikertAgreement:
    human_values = [human_scores[query_id][human_column] for query_id in query_ids]
    judge_a_values = [
        round(getattr(by_query[query_id][judge_a], dimension)) for query_id in query_ids
    ]
    judge_b_values = [
        round(getattr(by_query[query_id][judge_b], dimension)) for query_id in query_ids
    ]
    # clinical_correctness is no longer carried on EnsembleAggregate (it is not a scoring
    # dimension). For calibration we reconstruct its ensemble value as the mean of the two
    # judges' per-judge medians, matching the historical ensemble definition.
    ensemble_values = [
        round(_ensemble_likert_value(ensembles, by_query, query_id, dimension))
        for query_id in query_ids
    ]
    return HumanLikertAgreement(
        dimension=dimension,
        judge_a_kappa=_kappa_or_nan(human_values, judge_a_values, weights="quadratic"),
        judge_b_kappa=_kappa_or_nan(human_values, judge_b_values, weights="quadratic"),
        ensemble_kappa=_kappa_or_nan(human_values, ensemble_values, weights="quadratic"),
        n=len(query_ids),
    )


def _compute_binary_agreement(
    *,
    dimension: str,
    human_column: str,
    query_ids: list[str],
    human_scores: dict[str, dict[str, int]],
    by_query: dict[str, dict[str, PerJudgeAggregate]],
    ensembles: dict[str, EnsembleAggregate],
    judge_a: str,
    judge_b: str,
) -> HumanBinaryAgreement:
    human_values = [human_scores[query_id][human_column] for query_id in query_ids]
    judge_a_values = [
        int(bool(getattr(by_query[query_id][judge_a], dimension))) for query_id in query_ids
    ]
    judge_b_values = [
        int(bool(getattr(by_query[query_id][judge_b], dimension))) for query_id in query_ids
    ]
    ensemble_values = [
        int(bool(getattr(ensembles[query_id], dimension))) for query_id in query_ids
    ]
    percent_agreement_ensemble = math.nan
    if query_ids:
        agreements = sum(
            human_value == ensemble_value
            for human_value, ensemble_value in zip(human_values, ensemble_values, strict=False)
        )
        percent_agreement_ensemble = agreements / len(query_ids)
    return HumanBinaryAgreement(
        dimension=dimension,
        judge_a_kappa=_kappa_or_nan(human_values, judge_a_values),
        judge_b_kappa=_kappa_or_nan(human_values, judge_b_values),
        ensemble_kappa=_kappa_or_nan(human_values, ensemble_values),
        percent_agreement_ensemble=percent_agreement_ensemble,
        n=len(query_ids),
    )


def _load_human_scores(path: Path | str) -> dict[str, dict[str, int]]:
    source = Path(path)
    with source.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: dict[str, dict[str, int]] = {}
        for line_number, row in enumerate(reader, start=2):
            query_id = (row.get("query_id") or "").strip()
            if not query_id:
                raise ValueError(f"{source}:{line_number}: missing query_id")
            rows[query_id] = {
                **{
                    column: _parse_score(
                        source=source,
                        line_number=line_number,
                        row=row,
                        column=column,
                        min_value=0,
                        max_value=3,
                    )
                    for column in LIKERT_COLUMNS.values()
                },
                **{
                    column: _parse_score(
                        source=source,
                        line_number=line_number,
                        row=row,
                        column=column,
                        min_value=0,
                        max_value=1,
                    )
                    for column in BINARY_COLUMNS.values()
                },
            }
        return rows


def _parse_score(
    *,
    source: Path,
    line_number: int,
    row: dict[str, str],
    column: str,
    min_value: int,
    max_value: int,
) -> int:
    raw_value = (row.get(column) or "").strip()
    if raw_value == "":
        raise ValueError(f"{source}:{line_number}: missing {column}")
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{source}:{line_number}: invalid {column}={raw_value!r}") from exc
    if value < min_value or value > max_value:
        raise ValueError(
            f"{source}:{line_number}: {column}={value} out of range [{min_value}, {max_value}]"
        )
    return value


def _kappa_or_nan(
    human_values: list[int],
    model_values: list[int],
    *,
    weights: str | None = None,
) -> float:
    if len(human_values) < 3:
        return math.nan
    if len(set(human_values) | set(model_values)) == 1:
        return math.nan
    value = float(cohen_kappa_score(human_values, model_values, weights=weights))
    if math.isnan(value):
        return math.nan
    return value


__all__ = [
    "HumanBinaryAgreement",
    "HumanCalibrationReport",
    "HumanLikertAgreement",
    "compute_human_calibration",
]
