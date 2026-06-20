"""Deterministic metric helpers for routing, gate behavior, citations, and latency."""

from __future__ import annotations

import re
from dataclasses import dataclass
from math import ceil
from statistics import mean, median

from src.common.schemas import AgentAnswer, BenchmarkItem, GateExpected, GateOutcome, RoutingLabel
from src.corpus.index import (
    CitationExistenceResult,
    RecommendationIndexEntry,
    evaluate_citation_existence,
)


@dataclass(slots=True)
class RoutingDecision:
    item_id: str
    answer_run_index: int
    label: RoutingLabel
    expected_guidelines: list[str]
    reported_guidelines: list[str]


@dataclass(slots=True)
class RoutingMetrics:
    exact_match_rate: float
    micro_precision: float
    micro_recall: float
    micro_f1: float
    macro_f1: float
    decisions: list[RoutingDecision]


@dataclass(slots=True)
class GateMetrics:
    true_positive: int
    true_negative: int
    false_positive: int
    false_negative: int
    sensitivity: float | None
    specificity: float | None
    over_interrogation_rate: float | None
    mean_parameter_recall: float | None
    outcomes: list[GateOutcome]


@dataclass(slots=True)
class CitationAnswerMetrics:
    item_id: str
    answer_run_index: int
    result: CitationExistenceResult


@dataclass(slots=True)
class CitationMetrics:
    total_citations: int
    matched_citations: int
    existence_accuracy: float
    per_answer: list[CitationAnswerMetrics]


@dataclass(slots=True)
class LatencyMetrics:
    count: int
    mean_seconds: float | None
    median_seconds: float | None
    min_seconds: float | None
    max_seconds: float | None
    p95_seconds: float | None


def label_routing(item: BenchmarkItem, answer: AgentAnswer) -> RoutingLabel:
    expected = set(item.gold.expected_guidelines)
    reported = set(answer.routed_guidelines)
    missing = expected - reported
    extra = reported - expected

    if not missing and not extra:
        return "CORRECT"
    if expected and expected.isdisjoint(reported):
        return "WRONG"
    if not expected and reported:
        return "WRONG"
    if missing and item.safety_critical:
        return "WRONG"
    return "PARTIAL"


def summarize_routing(items: list[BenchmarkItem], answers: list[AgentAnswer]) -> RoutingMetrics:
    item_by_id = {item.id: item for item in items}
    decisions: list[RoutingDecision] = []
    true_positives = 0
    false_positives = 0
    false_negatives = 0
    label_union: set[str] = set()
    exact_matches = 0

    for answer in answers:
        item = item_by_id.get(answer.id)
        if item is None:
            raise ValueError(f"Answer {answer.id} does not match any benchmark item.")

        label = label_routing(item, answer)
        decisions.append(
            RoutingDecision(
                item_id=item.id,
                answer_run_index=answer.run_index,
                label=label,
                expected_guidelines=list(item.gold.expected_guidelines),
                reported_guidelines=list(answer.routed_guidelines),
            )
        )

        expected = set(item.gold.expected_guidelines)
        reported = set(answer.routed_guidelines)
        exact_matches += int(expected == reported)
        true_positives += len(expected & reported)
        false_positives += len(reported - expected)
        false_negatives += len(expected - reported)
        label_union.update(expected)
        label_union.update(reported)

    total = len(answers)
    micro_precision = _safe_ratio(true_positives, true_positives + false_positives)
    micro_recall = _safe_ratio(true_positives, true_positives + false_negatives)
    micro_f1 = _f1(true_positives, false_positives, false_negatives)

    macro_scores: list[float] = []
    for guideline in sorted(label_union):
        tp = fp = fn = 0
        for answer in answers:
            item = item_by_id[answer.id]
            expected = guideline in item.gold.expected_guidelines
            reported = guideline in answer.routed_guidelines
            tp += int(expected and reported)
            fp += int((not expected) and reported)
            fn += int(expected and (not reported))
        macro_scores.append(_f1(tp, fp, fn))

    macro_f1 = 1.0 if not macro_scores else mean(macro_scores)
    return RoutingMetrics(
        exact_match_rate=1.0 if total == 0 else exact_matches / total,
        micro_precision=micro_precision,
        micro_recall=micro_recall,
        micro_f1=micro_f1,
        macro_f1=macro_f1,
        decisions=decisions,
    )


def summarize_gate(items: list[BenchmarkItem], answers: list[AgentAnswer]) -> GateMetrics:
    item_by_id = {item.id: item for item in items}
    outcomes: list[GateOutcome] = []
    tp = tn = fp = fn = 0
    parameter_recalls: list[float] = []
    complete_case_total = 0
    complete_case_wrongly_fired = 0

    for answer in answers:
        item = item_by_id.get(answer.id)
        if item is None:
            raise ValueError(f"Answer {answer.id} does not match any benchmark item.")
        if item.gold.gate_expected == GateExpected.na:
            continue

        expected_fire = item.gold.gate_expected == GateExpected.fire
        # Use clarification_requested (non-empty) as the operational definition of
        # "gate fired" — gate_fired is True for ALL items because the checkpoint
        # always runs; the meaningful signal is whether clarification questions
        # were actually asked.
        fired = bool(answer.clarification_requested)
        correct = expected_fire == fired

        if expected_fire and fired:
            tp += 1
        elif expected_fire and not fired:
            fn += 1
        elif (not expected_fire) and fired:
            fp += 1
        else:
            tn += 1

        if item.query_type.value == "B_complete_case":
            complete_case_total += 1
            if fired:
                complete_case_wrongly_fired += 1

        parameter_recall = None
        if item.query_type.value == "C_underspecified" and correct and fired:
            parameter_recall = _parameter_recall(
                required=item.gold.required_parameters,
                requested=answer.clarification_requested,
            )
            parameter_recalls.append(parameter_recall)

        outcomes.append(
            GateOutcome(
                item_id=item.id,
                expected=item.gold.gate_expected,
                fired=fired,
                correct=correct,
                parameter_recall=parameter_recall,
            )
        )

    return GateMetrics(
        true_positive=tp,
        true_negative=tn,
        false_positive=fp,
        false_negative=fn,
        sensitivity=_safe_ratio(tp, tp + fn, none_when_zero=True),
        specificity=_safe_ratio(tn, tn + fp, none_when_zero=True),
        over_interrogation_rate=_safe_ratio(
            complete_case_wrongly_fired,
            complete_case_total,
            none_when_zero=True,
        ),
        mean_parameter_recall=None if not parameter_recalls else mean(parameter_recalls),
        outcomes=outcomes,
    )


def summarize_citation_existence(
    answers: list[AgentAnswer],
    index: dict[str, RecommendationIndexEntry],
) -> CitationMetrics:
    per_answer: list[CitationAnswerMetrics] = []
    total_citations = 0
    matched_citations = 0

    for answer in answers:
        result = evaluate_citation_existence(answer.citations, index)
        per_answer.append(
            CitationAnswerMetrics(
                item_id=answer.id,
                answer_run_index=answer.run_index,
                result=result,
            )
        )
        total_citations += result.total_citations
        matched_citations += result.matched_citations

    existence_accuracy = 1.0 if total_citations == 0 else matched_citations / total_citations
    return CitationMetrics(
        total_citations=total_citations,
        matched_citations=matched_citations,
        existence_accuracy=existence_accuracy,
        per_answer=per_answer,
    )


def summarize_latency(answers: list[AgentAnswer]) -> LatencyMetrics:
    latencies = [answer.latency_seconds for answer in answers]
    if not latencies:
        return LatencyMetrics(
            count=0,
            mean_seconds=None,
            median_seconds=None,
            min_seconds=None,
            max_seconds=None,
            p95_seconds=None,
        )

    sorted_latencies = sorted(latencies)
    return LatencyMetrics(
        count=len(sorted_latencies),
        mean_seconds=mean(sorted_latencies),
        median_seconds=median(sorted_latencies),
        min_seconds=sorted_latencies[0],
        max_seconds=sorted_latencies[-1],
        p95_seconds=_nearest_rank_percentile(sorted_latencies, 0.95),
    )


def _parameter_recall(*, required: list[str], requested: list[str]) -> float:
    """Keyword-overlap recall for required parameters vs clarification questions."""
    if not required:
        return 1.0
    normalized_questions = [_normalize_parameter(q) for q in requested]
    low_signal_keywords = {
        "and",
        "an",
        "a",
        "class",
        "findings",
        "grade",
        "history",
        "of",
        "on",
        "or",
        "results",
        "status",
        "the",
        "type",
        "value",
        "vs",
    }
    matched = 0
    for param in required:
        keywords = [
            keyword
            for keyword in _normalize_parameter(param.replace("_", " ")).split()
            if keyword not in low_signal_keywords
        ]
        if not keywords:
            keywords = _normalize_parameter(param.replace("_", " ")).split()
        for question in normalized_questions:
            if all(keyword in question for keyword in keywords):
                matched += 1
                break
    return matched / len(required)


def _normalize_parameter(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def _safe_ratio(
    numerator: int,
    denominator: int,
    *,
    none_when_zero: bool = False,
) -> float | None:
    if denominator == 0:
        return None if none_when_zero else 1.0
    return numerator / denominator


def _f1(tp: int, fp: int, fn: int) -> float:
    denominator = (2 * tp) + fp + fn
    if denominator == 0:
        return 1.0
    return (2 * tp) / denominator


def _nearest_rank_percentile(values: list[float], percentile: float) -> float:
    rank = max(1, ceil(percentile * len(values)))
    return values[rank - 1]


__all__ = [
    "CitationAnswerMetrics",
    "CitationMetrics",
    "GateMetrics",
    "LatencyMetrics",
    "RoutingDecision",
    "RoutingMetrics",
    "label_routing",
    "summarize_citation_existence",
    "summarize_gate",
    "summarize_latency",
    "summarize_routing",
]
