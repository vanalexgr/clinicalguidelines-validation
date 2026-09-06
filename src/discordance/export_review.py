"""Export discordance-review artifacts for clinician follow-up."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from src.common.io import load_config, read_jsonl
from src.common.schemas import AgentAnswer, BenchmarkItem, GateExpected, Judgment
from src.corpus.index import evaluate_citation_existence, load_recommendation_index
from src.metrics.aggregate import (
    EnsembleAggregate,
    PerJudgeAggregate,
    aggregate_ensemble,
    aggregate_within_judge,
)
from src.metrics.deterministic import RoutingDecision, label_routing

# clinical_correctness is intentionally excluded from the discordance-review surface
# (disagreement triggers and exported scores). It is retained only for inter-rater
# reliability in metrics/agreement.py.
LIKERT_DIMENSIONS = (
    "citation_support",
    "completeness",
    "uncertainty_handling",
)
HUMAN_COLUMNS = (
    "human_score_citation_support",
    "human_score_completeness",
    "human_score_uncertainty_handling",
    "human_score_hallucination_present",
    "human_score_hallucination_count",
    "human_score_safety_flag",
    "human_score_safety_reason",
    "human_notes",
)

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(slots=True)
class ReviewItem:
    item: BenchmarkItem
    answer: AgentAnswer
    ensemble: EnsembleAggregate
    per_judge: list[PerJudgeAggregate]
    judgments_by_judge: dict[str, list[Judgment]]
    routing_decision: RoutingDecision
    gate_correct: bool
    citation_existence_accuracy: float
    trigger_reasons: list[str]
    disagreement_dimensions: list[str]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to config.yaml.")
    parser.add_argument("--benchmark-path", help="Optional benchmark JSONL override.")
    parser.add_argument("--answers-path", help="Optional answers JSONL override.")
    parser.add_argument("--judgments-path", help="Optional judgments JSONL override.")
    parser.add_argument("--corpus-index-path", help="Optional corpus index JSON override.")
    parser.add_argument(
        "--output-dir",
        help="Optional directory override for discordance_review.{csv,md}.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)

    report_dir = Path(args.output_dir or config["paths"]["report_dir"])
    report_dir.mkdir(parents=True, exist_ok=True)

    export_review(
        benchmark_path=args.benchmark_path or config["paths"]["benchmark"],
        answers_path=args.answers_path or config["paths"]["answers"],
        judgments_path=args.judgments_path or config["paths"]["judgments"],
        corpus_index_path=args.corpus_index_path or config["paths"]["corpus_index"],
        csv_path=report_dir / "discordance_review.csv",
        markdown_path=report_dir / "discordance_review.md",
    )
    return 0


def export_review(
    *,
    benchmark_path: str | Path,
    answers_path: str | Path,
    judgments_path: str | Path,
    corpus_index_path: str | Path,
    csv_path: str | Path,
    markdown_path: str | Path,
) -> list[ReviewItem]:
    """Build and write the discordance review CSV + Markdown bundle."""
    items = _load_models(benchmark_path, BenchmarkItem)
    answers = _load_models(answers_path, AgentAnswer)
    judgments = _load_models(judgments_path, Judgment)
    recommendation_index = load_recommendation_index(corpus_index_path)

    review_items = build_review_items(
        items=items,
        answers=answers,
        judgments=judgments,
        recommendation_index=recommendation_index,
    )

    rows = review_rows(review_items)
    _write_csv(Path(csv_path), rows)
    Path(markdown_path).parent.mkdir(parents=True, exist_ok=True)
    Path(markdown_path).write_text(render_review_markdown(review_items), encoding="utf-8")
    return review_items


def build_review_items(
    *,
    items: list[BenchmarkItem],
    answers: list[AgentAnswer],
    judgments: list[Judgment],
    recommendation_index: dict,
) -> list[ReviewItem]:
    """Select discordance-review items and assemble full review context."""
    item_by_id = {item.id: item for item in items}
    judgments_by_answer = _group_judgments_by_answer(judgments)
    selected_answers = _select_review_answers(answers, judgments_by_answer)

    review_items: list[ReviewItem] = []
    for query_id in sorted(selected_answers):
        item = item_by_id.get(query_id)
        if item is None:
            raise ValueError(f"Answer {query_id} does not match any benchmark item.")

        answer = selected_answers[query_id]
        relevant_judgments = judgments_by_answer.get((query_id, answer.run_index), [])
        if not relevant_judgments:
            raise ValueError(
                f"No judgments found for query_id={query_id} answer_run_index={answer.run_index}."
            )

        per_judge_map = aggregate_within_judge(
            [judgment.model_dump(by_alias=True) for judgment in relevant_judgments]
        )
        ensemble = aggregate_ensemble(per_judge_map).get(query_id)
        if ensemble is None:
            raise ValueError(f"Failed to build ensemble aggregate for query_id={query_id}.")

        per_judge = sorted(ensemble.per_judge, key=lambda aggregate: aggregate.judge)
        judgments_grouped = _group_judgments_by_judge(relevant_judgments)

        routing_decision = RoutingDecision(
            item_id=query_id,
            answer_run_index=answer.run_index,
            label=label_routing(item, answer),
            expected_guidelines=list(item.gold.expected_guidelines),
            reported_guidelines=list(answer.routed_guidelines),
        )
        gate_correct = _gate_correct(item, answer)
        citation_existence_accuracy = evaluate_citation_existence(
            answer.citations, recommendation_index
        ).existence_accuracy
        disagreement_dimensions = _likert_disagreement_dimensions(per_judge)
        trigger_reasons = _trigger_reasons(
            item=item,
            per_judge=per_judge,
            routing_decision=routing_decision,
            gate_correct=gate_correct,
            disagreement_dimensions=disagreement_dimensions,
        )
        if not trigger_reasons:
            continue

        review_items.append(
            ReviewItem(
                item=item,
                answer=answer,
                ensemble=ensemble,
                per_judge=per_judge,
                judgments_by_judge=judgments_grouped,
                routing_decision=routing_decision,
                gate_correct=gate_correct,
                citation_existence_accuracy=citation_existence_accuracy,
                    trigger_reasons=trigger_reasons,
                disagreement_dimensions=disagreement_dimensions,
            )
        )

    return review_items


def review_rows(review_items: list[ReviewItem]) -> list[dict[str, str]]:
    """Flatten review items into CSV-friendly rows."""
    judge_names = sorted(
        {aggregate.judge for review_item in review_items for aggregate in review_item.per_judge}
    )
    rows: list[dict[str, str]] = []
    for review_item in review_items:
        rows.append(_review_row(review_item, judge_names))
    return rows


def render_review_markdown(review_items: list[ReviewItem]) -> str:
    """Render a human-readable Markdown review bundle."""
    lines = [
        "# Discordance Review",
        "",
        f"Exported items: {len(review_items)}",
        "",
    ]

    for review_item in review_items:
        item = review_item.item
        answer = review_item.answer
        lines.extend(
            [
                f"## {item.id}",
                "",
                f"- Query type: `{item.query_type.value}`",
                f"- Safety-critical: `{item.safety_critical}`",
                f"- Verified: `{item.verified}`",
                f"- Answer run index: `{answer.run_index}`",
                f"- Triggers: {', '.join(review_item.trigger_reasons)}",
                "",
                "### Question Turns",
                "",
            ]
        )
        for index, turn in enumerate(item.turns, start=1):
            lines.append(f"{index}. **{turn.role}**: {turn.content}")

        lines.extend(
            [
                "",
                "### Agent Answer",
                "",
                "```text",
                answer.raw_response or "(empty)",
                "```",
                "",
                "### Gold Answer Key",
                "",
                item.gold.answer_key or "(empty)",
                "",
                "### Deterministic Summary",
                "",
                "| Field | Value |",
                "|---|---|",
                f"| routing_label | `{review_item.routing_decision.label}` |",
                f"| gate_expected | `{item.gold.gate_expected.value}` |",
                f"| gate_fired | `{answer.gate_fired}` |",
                f"| gate_correct | `{review_item.gate_correct}` |",
                (
                    "| citation_existence_accuracy | "
                    f"`{review_item.citation_existence_accuracy:.3f}` |"
                ),
                "",
                "### Retrieved Passages",
                "",
            ]
        )
        if answer.retrieved_passages:
            for index, passage in enumerate(answer.retrieved_passages, start=1):
                lines.append(
                    f"{index}. **{passage.guideline} / {passage.chunk_id}**: {passage.text}"
                )
        else:
            lines.append("(none)")

        lines.extend(["", "### Judge Summaries", ""])
        for aggregate in review_item.per_judge:
            lines.extend(
                [
                    f"#### {aggregate.judge}",
                    "",
                    "| Metric | Aggregate |",
                    "|---|---:|",
                    f"| citation_support | {aggregate.citation_support} |",
                    f"| completeness | {aggregate.completeness} |",
                    f"| uncertainty_handling | {aggregate.uncertainty_handling} |",
                    f"| hallucination_present | {aggregate.hallucination_present} |",
                    f"| hallucination_count | {aggregate.hallucination_count} |",
                    f"| safety_flag | {aggregate.safety_flag} |",
                    "",
                    "| Run | Dimension | Score / Value | Rationale / Detail |",
                    "|---:|---|---|---|",
                ]
            )
            for judgment in review_item.judgments_by_judge.get(aggregate.judge, []):
                lines.extend(_judgment_run_rows(judgment))
            lines.append("")

        lines.extend(
            [
                "### Human Review",
                "",
                "| Field | Value |",
                "|---|---|",
            ]
        )
        for column in HUMAN_COLUMNS:
            lines.append(f"| {column} |  |")
        lines.extend(["", "---", ""])

    return "\n".join(lines).rstrip() + "\n"


def _load_models(path: str | Path, model_type: type[ModelT]) -> list[ModelT]:
    source = Path(path)
    models: list[ModelT] = []
    for line_number, row in enumerate(read_jsonl(source), start=1):
        try:
            models.append(model_type.model_validate(row))
        except ValidationError as exc:
            raise ValueError(f"{source}:{line_number}: {exc}") from exc
    return models


def _group_judgments_by_answer(
    judgments: list[Judgment],
) -> dict[tuple[str, int], list[Judgment]]:
    grouped: dict[tuple[str, int], list[Judgment]] = defaultdict(list)
    for judgment in judgments:
        grouped[(judgment.query_id, _answer_run_index(judgment))].append(judgment)
    return grouped


def _group_judgments_by_judge(judgments: list[Judgment]) -> dict[str, list[Judgment]]:
    grouped: dict[str, list[Judgment]] = defaultdict(list)
    for judgment in judgments:
        grouped[judgment.judge].append(judgment)
    for runs in grouped.values():
        runs.sort(key=lambda judgment: judgment.run_index)
    return dict(sorted(grouped.items()))


def _select_review_answers(
    answers: list[AgentAnswer],
    judgments_by_answer: dict[tuple[str, int], list[Judgment]],
) -> dict[str, AgentAnswer]:
    grouped: dict[str, list[AgentAnswer]] = defaultdict(list)
    for answer in answers:
        grouped[answer.id].append(answer)

    selected: dict[str, AgentAnswer] = {}
    for query_id, answer_group in grouped.items():
        candidates = sorted(answer_group, key=lambda answer: answer.run_index)
        with_judgments = [
            answer
            for answer in candidates
            if judgments_by_answer.get((query_id, answer.run_index))
        ]
        selected[query_id] = with_judgments[0] if with_judgments else candidates[0]
    return selected


def _answer_run_index(judgment: Judgment) -> int:
    answer_run_index = judgment.meta.get("answer_run_index", 0)
    if isinstance(answer_run_index, int):
        return answer_run_index
    return int(answer_run_index)


def _gate_correct(item: BenchmarkItem, answer: AgentAnswer) -> bool:
    if item.gold.gate_expected == GateExpected.na:
        return True
    expected_fire = item.gold.gate_expected == GateExpected.fire
    return expected_fire == answer.gate_fired


def _likert_disagreement_dimensions(per_judge: list[PerJudgeAggregate]) -> list[str]:
    if len(per_judge) < 2:
        return []

    disagreements: list[str] = []
    judge_a, judge_b = per_judge[:2]
    for dimension in LIKERT_DIMENSIONS:
        if abs(getattr(judge_a, dimension) - getattr(judge_b, dimension)) >= 2:
            disagreements.append(dimension)
    return disagreements


def _trigger_reasons(
    *,
    item: BenchmarkItem,
    per_judge: list[PerJudgeAggregate],
    routing_decision: RoutingDecision,
    gate_correct: bool,
    disagreement_dimensions: list[str],
) -> list[str]:
    reasons: list[str] = []
    if any(aggregate.safety_flag for aggregate in per_judge):
        reasons.append("safety_flag_any_judge")
    if any(aggregate.hallucination_present for aggregate in per_judge):
        reasons.append("hallucination_any_judge")
    if routing_decision.label == "WRONG":
        reasons.append("routing_wrong")
    if item.safety_critical and not gate_correct:
        reasons.append("gate_incorrect_on_safety_critical")
    reasons.extend(
        f"judge_disagreement_ge_2:{dimension}" for dimension in disagreement_dimensions
    )
    return reasons


def _review_row(review_item: ReviewItem, judge_names: list[str]) -> dict[str, str]:
    item = review_item.item
    answer = review_item.answer
    row: dict[str, str] = {
        "query_id": item.id,
        "query_type": item.query_type.value,
        "safety_critical": str(item.safety_critical),
        "verified": str(item.verified),
        "answer_run_index": str(answer.run_index),
        "trigger_reasons": _json(review_item.trigger_reasons),
        "trigger_count": str(len(review_item.trigger_reasons)),
        "trigger_any_safety_flag": str(
            "safety_flag_any_judge" in review_item.trigger_reasons
        ),
        "trigger_any_hallucination": str(
            "hallucination_any_judge" in review_item.trigger_reasons
        ),
        "trigger_routing_wrong": str("routing_wrong" in review_item.trigger_reasons),
        "trigger_gate_incorrect_on_safety_critical": str(
            "gate_incorrect_on_safety_critical" in review_item.trigger_reasons
        ),
        "trigger_likert_disagreement": str(bool(review_item.disagreement_dimensions)),
        "likert_disagreement_dimensions": _json(review_item.disagreement_dimensions),
        "routing_label": review_item.routing_decision.label,
        "expected_guidelines_json": _json(review_item.routing_decision.expected_guidelines),
        "reported_guidelines_json": _json(review_item.routing_decision.reported_guidelines),
        "gate_expected": item.gold.gate_expected.value,
        "gate_fired": str(answer.gate_fired),
        "gate_correct": str(review_item.gate_correct),
        "citation_existence_accuracy": f"{review_item.citation_existence_accuracy:.6f}",
        "question_turns_json": _json([turn.model_dump() for turn in item.turns]),
        "agent_answer": answer.raw_response,
        "recommendation": answer.recommendation,
        "citations_json": _json(
            [citation.model_dump(by_alias=True) for citation in answer.citations]
        ),
        "retrieved_passages_json": _json(
            [passage.model_dump() for passage in answer.retrieved_passages]
        ),
        "gold_answer_key": item.gold.answer_key,
        "gold_required_parameters_json": _json(item.gold.required_parameters),
        "gold_key_recommendations_json": _json(
            [
                recommendation.model_dump(by_alias=True)
                for recommendation in item.gold.key_recommendations
            ]
        ),
        "gold_notes": item.gold.notes,
        "ensemble_citation_support": str(review_item.ensemble.citation_support),
        "ensemble_completeness": str(review_item.ensemble.completeness),
        "ensemble_uncertainty_handling": str(review_item.ensemble.uncertainty_handling),
        "ensemble_hallucination_present": str(review_item.ensemble.hallucination_present),
        "ensemble_hallucination_count": str(review_item.ensemble.hallucination_count),
        "ensemble_safety_flag": str(review_item.ensemble.safety_flag),
        "ensemble_safety_reasons_json": _json(review_item.ensemble.safety_reasons),
        "ensemble_unsupported_citations_json": _json(
            review_item.ensemble.unsupported_citations
        ),
    }

    per_judge_map = {aggregate.judge: aggregate for aggregate in review_item.per_judge}
    for judge_name in judge_names:
        prefix = f"judge_{_slug(judge_name)}"
        aggregate = per_judge_map.get(judge_name)
        judgments = review_item.judgments_by_judge.get(judge_name, [])
        row[f"{prefix}_runs_json"] = _json(
            [judgment.model_dump(by_alias=True) for judgment in judgments]
        )
        if aggregate is None:
            row[f"{prefix}_citation_support"] = ""
            row[f"{prefix}_completeness"] = ""
            row[f"{prefix}_uncertainty_handling"] = ""
            row[f"{prefix}_hallucination_present"] = ""
            row[f"{prefix}_hallucination_count"] = ""
            row[f"{prefix}_safety_flag"] = ""
            row[f"{prefix}_safety_reasons_json"] = ""
            row[f"{prefix}_unsupported_citations_json"] = ""
            continue

        row[f"{prefix}_citation_support"] = str(aggregate.citation_support)
        row[f"{prefix}_completeness"] = str(aggregate.completeness)
        row[f"{prefix}_uncertainty_handling"] = str(aggregate.uncertainty_handling)
        row[f"{prefix}_hallucination_present"] = str(aggregate.hallucination_present)
        row[f"{prefix}_hallucination_count"] = str(aggregate.hallucination_count)
        row[f"{prefix}_safety_flag"] = str(aggregate.safety_flag)
        row[f"{prefix}_safety_reasons_json"] = _json(aggregate.safety_reasons)
        row[f"{prefix}_unsupported_citations_json"] = _json(
            aggregate.unsupported_citations
        )

    for column in HUMAN_COLUMNS:
        row[column] = ""

    return row


def _judgment_run_rows(judgment: Judgment) -> list[str]:
    rows = [
        (
            f"| {judgment.run_index} | citation_support | "
            f"{judgment.dimensions.citation_support.score} | "
            f"{_markdown_text(judgment.dimensions.citation_support.rationale)} |"
        ),
        (
            f"| {judgment.run_index} | completeness | "
            f"{judgment.dimensions.completeness.score} | "
            f"{_markdown_text(judgment.dimensions.completeness.rationale)} |"
        ),
        (
            f"| {judgment.run_index} | uncertainty_handling | "
            f"{judgment.dimensions.uncertainty_handling.score} | "
            f"{_markdown_text(judgment.dimensions.uncertainty_handling.rationale)} |"
        ),
        (
            f"| {judgment.run_index} | hallucination_present | "
            f"{judgment.hallucination.present} (count={judgment.hallucination.count}) | "
            f"{_markdown_text('; '.join(judgment.hallucination.unsupported_claims) or '(none)')} |"
        ),
        (
            f"| {judgment.run_index} | safety_flag | "
            f"{judgment.safety_critical_error.flag} | "
            f"{_markdown_text(judgment.safety_critical_error.reason or '(none)')} |"
        ),
    ]
    return rows


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = _fieldnames(rows)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _fieldnames(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return [
            "query_id",
            "query_type",
            "trigger_reasons",
            *HUMAN_COLUMNS,
        ]

    first_row = rows[0]
    base_fieldnames = [
        "query_id",
        "query_type",
        "safety_critical",
        "verified",
        "answer_run_index",
        "trigger_reasons",
        "trigger_count",
        "trigger_any_safety_flag",
        "trigger_any_hallucination",
        "trigger_routing_wrong",
        "trigger_gate_incorrect_on_safety_critical",
        "trigger_likert_disagreement",
        "likert_disagreement_dimensions",
        "routing_label",
        "expected_guidelines_json",
        "reported_guidelines_json",
        "gate_expected",
        "gate_fired",
        "gate_correct",
        "citation_existence_accuracy",
        "question_turns_json",
        "agent_answer",
        "recommendation",
        "citations_json",
        "retrieved_passages_json",
        "gold_answer_key",
        "gold_required_parameters_json",
        "gold_key_recommendations_json",
        "gold_notes",
        "ensemble_citation_support",
        "ensemble_completeness",
        "ensemble_uncertainty_handling",
        "ensemble_hallucination_present",
        "ensemble_hallucination_count",
        "ensemble_safety_flag",
        "ensemble_safety_reasons_json",
        "ensemble_unsupported_citations_json",
    ]
    dynamic_fieldnames = sorted(
        column
        for column in first_row
        if column.startswith("judge_") and column not in base_fieldnames
    )
    return [*base_fieldnames, *dynamic_fieldnames, *HUMAN_COLUMNS]


def _slug(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", value.lower()).strip("_")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _markdown_text(value: str) -> str:
    return value.replace("\n", "<br>")


__all__ = [
    "ReviewItem",
    "build_parser",
    "build_review_items",
    "export_review",
    "main",
    "render_review_markdown",
    "review_rows",
]


if __name__ == "__main__":
    raise SystemExit(main())
