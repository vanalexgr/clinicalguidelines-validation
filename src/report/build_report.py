"""Build the validation report and publication-ready report artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, TypeVar

import matplotlib
import numpy as np
from pydantic import BaseModel, ValidationError

from src.benchmark.validate_benchmark import load_benchmark_validated, summarize_benchmark
from src.common.io import load_config, read_jsonl
from src.common.schemas import AgentAnswer, BenchmarkItem, Judgment, QueryType
from src.discordance.export_review import (
    build_review_items,
    render_review_markdown,
    review_rows,
)
from src.metrics.aggregate import (
    EnsembleAggregate,
    PerJudgeAggregate,
    aggregate_ensemble,
    aggregate_within_judge,
)
from src.metrics.agreement import AgreementReport, compute_agreement, wilson_ci
from src.metrics.deterministic import (
    CitationMetrics,
    GateMetrics,
    RoutingDecision,
    RoutingMetrics,
    summarize_citation_existence,
    summarize_gate,
    summarize_latency,
    summarize_routing,
)
from src.metrics.human_calibration import (
    HumanCalibrationReport,
    compute_human_calibration,
)
from src.metrics.citation_breakdown import (
    ARTEFACT_TYPES,
    CitationBreakdownResult,
    REAL_ERROR_TYPES,
    compute_citation_breakdown,
)
from src.metrics.pass_fail import PassFailResult, evaluate_pass_fail_batch
from src.metrics.reclassification import (
    ReclassificationReport,
    compute_reclassification_report,
)

matplotlib.use("Agg")
from matplotlib import pyplot as plt

ModelT = TypeVar("ModelT", bound=BaseModel)
# clinical_correctness is intentionally excluded from the reported quality dimensions.
# It remains elicited and is used only for inter-rater reliability (see metrics/agreement.py).
LIKERT_DIMENSIONS = (
    "citation_support",
    "completeness",
    "uncertainty_handling",
)


@dataclass(slots=True)
class ReportContext:
    items: list[BenchmarkItem]
    answers: list[AgentAnswer]
    judgments: list[Judgment]
    recommendation_index: dict[str, Any]
    routing_metrics: RoutingMetrics
    gate_metrics: GateMetrics
    citation_metrics: CitationMetrics
    citation_breakdown: CitationBreakdownResult | None
    reclassification_report: ReclassificationReport | None
    per_judge: dict[tuple[str, str], PerJudgeAggregate]
    ensembles: dict[str, EnsembleAggregate]
    pass_fail_results: list[PassFailResult]
    review_items: list[Any]
    agreement_report: AgreementReport | None
    latency_mean_seconds: float | None
    latency_median_seconds: float | None
    benchmark_total_items: int
    benchmark_verified_count: int
    benchmark_counts_by_type: dict[str, int]
    strict_mode: bool
    output_dir: Path
    metrics_dir: Path | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to config.yaml.")
    parser.add_argument("--benchmark-path", help="Optional benchmark JSONL override.")
    parser.add_argument("--answers-path", help="Optional answers JSONL override.")
    parser.add_argument("--judgments-path", help="Optional judgments JSONL override.")
    parser.add_argument("--corpus-index-path", help="Optional corpus index JSON override.")
    parser.add_argument(
        "--output-dir",
        help="Optional report output directory override.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Rebuild the report from whatever validated answers/judgments already exist on disk "
            "instead of requiring a complete benchmark coverage."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    benchmark_path = _resolve_benchmark_path(config, args.benchmark_path)
    output_dir = Path(args.output_dir or config["paths"]["report_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    context = build_report(
        benchmark_path=benchmark_path,
        answers_path=args.answers_path or config["paths"]["answers"],
        judgments_path=args.judgments_path or config["paths"]["judgments"],
        corpus_index_path=args.corpus_index_path or config["paths"]["corpus_index"],
        output_dir=output_dir,
        overrides_path=Path("data/annotation/hallucination_overrides.jsonl"),
        metrics_dir=config["paths"]["metrics_dir"],
        strict=not args.dry_run,
    )
    print(
        f"Processed {len(context.items)} items; report written to {output_dir / 'report.md'}"
    )
    return 0


def build_report(
    *,
    benchmark_path: str | Path,
    answers_path: str | Path,
    judgments_path: str | Path,
    corpus_index_path: str | Path,
    output_dir: str | Path,
    overrides_path: str | Path | None = None,
    metrics_dir: str | Path | None = None,
    strict: bool = True,
) -> ReportContext:
    """Load data, compute metrics, and write the report artifacts."""
    output_dir_path = Path(output_dir)
    tables_dir = output_dir_path / "tables"
    plots_dir = output_dir_path / "plots"
    tables_dir.mkdir(parents=True, exist_ok=True)
    plots_dir.mkdir(parents=True, exist_ok=True)

    benchmark_items = load_benchmark_validated(benchmark_path)
    benchmark_summary = summarize_benchmark(benchmark_items)
    answers = _load_models(answers_path, AgentAnswer)
    judgments = _load_models(judgments_path, Judgment)
    recommendation_index = _load_recommendation_index(corpus_index_path)

    processed_items, selected_answers, filtered_judgments = _select_report_dataset(
        benchmark_items=benchmark_items,
        answers=answers,
        judgments=judgments,
        strict=strict,
    )
    if not processed_items:
        raise ValueError("No benchmark items have both answers and judgments to report.")

    routing_metrics = summarize_routing(processed_items, selected_answers)
    gate_metrics = summarize_gate(processed_items, selected_answers)
    citation_metrics = summarize_citation_existence(selected_answers, recommendation_index)
    latency_metrics = summarize_latency(selected_answers)
    overrides_source = Path(overrides_path) if overrides_path is not None else None
    citation_breakdown = (
        compute_citation_breakdown(
            answers_path=Path(answers_path),
            judgments_path=Path(judgments_path),
            index_path=Path(corpus_index_path),
            bench_path=Path(benchmark_path),
            overrides_path=overrides_source,
        )
        if overrides_source is not None and overrides_source.exists()
        else None
    )
    reclassification_report = (
        compute_reclassification_report(citation_breakdown)
        if citation_breakdown is not None
        else None
    )

    per_judge = aggregate_within_judge(
        [judgment.model_dump(by_alias=True) for judgment in filtered_judgments]
    )
    ensembles = aggregate_ensemble(per_judge)

    routing_by_id = {
        decision.item_id: decision for decision in routing_metrics.decisions
    }
    gate_by_id = {outcome.item_id: outcome.correct for outcome in gate_metrics.outcomes}
    citation_by_id = {
        metric.item_id: metric.result.existence_accuracy
        for metric in citation_metrics.per_answer
    }
    pass_fail_results = evaluate_pass_fail_batch(
        processed_items,
        ensembles,
        routing_by_id,
        gate_by_id,
        citation_by_id,
    )

    review_items = build_review_items(
        items=processed_items,
        answers=selected_answers,
        judgments=filtered_judgments,
        recommendation_index=recommendation_index,
    )
    _write_rows_csv(output_dir_path / "discordance_review.csv", review_rows(review_items))
    (output_dir_path / "discordance_review.md").write_text(
        render_review_markdown(review_items),
        encoding="utf-8",
    )

    try:
        agreement_report = compute_agreement(per_judge)
    except ValueError:
        agreement_report = None

    context = ReportContext(
        items=processed_items,
        answers=selected_answers,
        judgments=filtered_judgments,
        recommendation_index=recommendation_index,
        routing_metrics=routing_metrics,
        gate_metrics=gate_metrics,
        citation_metrics=citation_metrics,
        citation_breakdown=citation_breakdown,
        reclassification_report=reclassification_report,
        per_judge=per_judge,
        ensembles=ensembles,
        pass_fail_results=pass_fail_results,
        review_items=review_items,
        agreement_report=agreement_report,
        latency_mean_seconds=latency_metrics.mean_seconds,
        latency_median_seconds=latency_metrics.median_seconds,
        benchmark_total_items=benchmark_summary.total_items,
        benchmark_verified_count=benchmark_summary.verified_count,
        benchmark_counts_by_type=benchmark_summary.counts_by_type,
        strict_mode=strict,
        output_dir=output_dir_path,
        metrics_dir=Path(metrics_dir) if metrics_dir is not None else None,
    )

    _write_report_tables(context, tables_dir)
    _write_report_plots(context, plots_dir)
    (output_dir_path / "report.md").write_text(
        render_report_markdown(context),
        encoding="utf-8",
    )
    if context.metrics_dir is not None:
        _write_summary_json(context, context.metrics_dir)
    return context


def render_report_markdown(context: ReportContext) -> str:
    """Render the main Markdown report."""
    preliminary = any(not item.verified for item in context.items)
    by_query_type_rows = _by_query_type_rows(context)
    human_section = _render_human_calibration_section(context)

    passes = sum(result.verdict == "PASS" for result in context.pass_fail_results)
    pass_ci = wilson_ci(passes, len(context.pass_fail_results))

    exact_match_successes = sum(
        decision.label == "CORRECT" for decision in context.routing_metrics.decisions
    )
    exact_match_ci = wilson_ci(
        exact_match_successes,
        len(context.routing_metrics.decisions),
    )

    citation_support_successes = sum(
        aggregate.citation_support >= 2 for aggregate in context.ensembles.values()
    )
    citation_support_ci = wilson_ci(citation_support_successes, len(context.ensembles))

    hallucination_successes = sum(
        aggregate.hallucination_present for aggregate in context.ensembles.values()
    )
    hallucination_ci = wilson_ci(hallucination_successes, len(context.ensembles))

    safety_successes = sum(
        aggregate.safety_flag for aggregate in context.ensembles.values()
    )
    safety_ci = wilson_ci(safety_successes, len(context.ensembles))

    judge_score_rows = _judge_score_summary_rows(context)
    routing_rows = _routing_summary_rows(context)
    gate_summary_rows = _gate_summary_rows(context)
    citation_rows = _citation_summary_rows(context, citation_support_ci)
    citation_tier_rows = _citation_tier_rows(context)
    hallucination_type_rows = _hallucination_type_rows(context)
    hallucination_rate_rows = _hallucination_analysis_rows(context)
    clinical_risk_rows = _clinical_risk_rows(context)
    reclassification_rows = _reclassification_rows(context.reclassification_report)
    safety_rows = _safety_rows(context)
    agreement_rows = _agreement_rows(context.agreement_report)

    lines = [
        "# ClinicalGuidelines.io Validation Report",
        "",
    ]
    if preliminary:
        lines.extend(
            [
                "> ⚠️ PRELIMINARY — dataset contains unverified items",
                "",
            ]
        )

    lines.extend(
        [
            "## 1. Dataset Summary",
            "",
            _markdown_table(
                [
                    {
                        "metric": "benchmark_total_items",
                        "value": context.benchmark_total_items,
                        "notes": "All benchmark rows after schema validation.",
                    },
                    {
                        "metric": "processed_items",
                        "value": len(context.items),
                        "notes": (
                            "Items with both answers and judgments."
                            if context.strict_mode
                            else "Items present in outputs/ and included in this dry-run rebuild."
                        ),
                    },
                    {
                        "metric": "benchmark_verified_count",
                        "value": context.benchmark_verified_count,
                        "notes": "Clinical-team verified benchmark items.",
                    },
                    {
                        "metric": "safety_critical_items",
                        "value": sum(item.safety_critical for item in context.items),
                        "notes": "Processed items flagged safety-critical.",
                    },
                    {
                        "metric": "mean_latency_seconds",
                        "value": _fmt_float(context.latency_mean_seconds),
                        "notes": "Mean latency over selected answers.",
                    },
                    {
                        "metric": "median_latency_seconds",
                        "value": _fmt_float(context.latency_median_seconds),
                        "notes": "Median latency over selected answers.",
                    },
                ]
            ),
            "",
            _markdown_table(
                [
                    {
                        "query_type": query_type,
                        "count": count,
                    }
                    for query_type, count in context.benchmark_counts_by_type.items()
                ]
            ),
            "",
            "## 2. Overall Pass Rate",
            "",
            _markdown_table(
                [
                    {
                        "passes": passes,
                        "total": len(context.pass_fail_results),
                        "pass_rate": _fmt_pct(pass_ci.proportion),
                        "ci_95": _fmt_ci(pass_ci.lower, pass_ci.upper),
                    }
                ]
            ),
            "",
            "## 3. Mean Judge Score Per Dimension",
            "",
            _markdown_table(judge_score_rows),
            "",
            "## 4. Guideline-Routing Accuracy",
            "",
            _markdown_table(
                [
                    {
                        "metric": "exact_match_rate",
                        "value": _fmt_pct(context.routing_metrics.exact_match_rate),
                        "ci_95": _fmt_ci(exact_match_ci.lower, exact_match_ci.upper),
                    },
                    {
                        "metric": "micro_precision",
                        "value": _fmt_float(context.routing_metrics.micro_precision),
                        "ci_95": "n/a",
                    },
                    {
                        "metric": "micro_recall",
                        "value": _fmt_float(context.routing_metrics.micro_recall),
                        "ci_95": "n/a",
                    },
                    {
                        "metric": "micro_f1",
                        "value": _fmt_float(context.routing_metrics.micro_f1),
                        "ci_95": "n/a",
                    },
                    {
                        "metric": "macro_f1",
                        "value": _fmt_float(context.routing_metrics.macro_f1),
                        "ci_95": "n/a",
                    },
                ]
            ),
            "",
            _markdown_table(routing_rows),
            "",
            "## 5. Context Gate Sensitivity / Specificity",
            "",
            _markdown_table(gate_summary_rows),
            "",
            (
                "See [gate_confusion.csv](tables/gate_confusion.csv) "
                "and ![gate confusion](plots/gate_confusion.png)."
            ),
            "",
            "## 6. Citation Accuracy",
            "",
            _markdown_table(citation_rows),
            "",
            "## 7. Hallucination Rate",
            "",
            _markdown_table(
                [
                    {
                        "metric": "hallucination_rate",
                        "value": _fmt_pct(hallucination_ci.proportion),
                        "ci_95": _fmt_ci(hallucination_ci.lower, hallucination_ci.upper),
                    },
                    {
                        "metric": "mean_unsupported_claim_count",
                        "value": _fmt_float(
                            mean(
                                aggregate.hallucination_count
                                for aggregate in context.ensembles.values()
                            )
                        ),
                        "ci_95": "n/a",
                    },
                ]
            ),
            "",
        ]
    )
    if context.citation_breakdown is not None:
        lines.extend(
            [
                "## §8 Citation Correctness Tiers",
                "",
                _markdown_table(citation_tier_rows),
                "",
                "## §9 Hallucination Analysis",
                "",
                "### 9.1 Claim Type Distribution",
                "",
                _markdown_table(hallucination_type_rows),
                "",
                "### 9.2 Adjusted Hallucination Rate",
                "",
                _markdown_table(hallucination_rate_rows),
                "",
                "### 9.3 Clinical Risk Distribution",
                "",
                _markdown_table(clinical_risk_rows),
                "",
                "### 9.4 Clean / Artefact-only / Real-error Items",
                "",
                f"Clean: {', '.join(context.citation_breakdown.items_clean) or '(none)'}",
                f"Artefact-only: {', '.join(context.citation_breakdown.items_artefact_only) or '(none)'}",
                f"Real errors: {', '.join(context.citation_breakdown.items_real_error) or '(none)'}",
                "",
                "## §10 Judge Reclassification Summary",
                "",
                _markdown_table(reclassification_rows),
                "",
            ]
        )
    lines.extend(
        [
            "## §11 Safety-Critical Discordance Rate",
            "",
            _markdown_table(
                [
                    {
                        "metric": "safety_flag_rate",
                        "value": _fmt_pct(safety_ci.proportion),
                        "ci_95": _fmt_ci(safety_ci.lower, safety_ci.upper),
                    }
                ]
            ),
            "",
            _markdown_table(safety_rows),
            "",
            "## §12 Performance by Query Type",
            "",
            _markdown_table(by_query_type_rows),
            "",
            "## §13 Inter-Judge Agreement",
            "",
            _markdown_table(agreement_rows),
            "",
            "## §14 Judge-vs-Human Agreement",
            "",
            human_section,
            "",
            "## §15 Plots",
            "",
            (
                "- [score_distributions.csv](tables/score_distributions.csv) "
                "and ![score distributions](plots/score_distributions.png)"
            ),
            (
                "- [by_query_type.csv](tables/by_query_type.csv) "
                "and ![by query type](plots/by_query_type.png)"
            ),
            "- [agreement.csv](tables/agreement.csv)",
            "- [pass_fail_summary.csv](tables/pass_fail_summary.csv)",
            "- [routing_accuracy.csv](tables/routing_accuracy.csv)",
            "- [discordance_review.csv](discordance_review.csv)",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_report_tables(context: ReportContext, tables_dir: Path) -> None:
    item_by_id = {item.id: item for item in context.items}
    score_rows = [
        {
            "query_id": query_id,
            "query_type": item_by_id[query_id].query_type.value,
            "citation_support": aggregate.citation_support,
            "completeness": aggregate.completeness,
            "uncertainty_handling": aggregate.uncertainty_handling,
        }
        for query_id, aggregate in sorted(context.ensembles.items())
    ]
    _write_rows_csv(tables_dir / "score_distributions.csv", score_rows)

    routing_rows = [
        {
            "query_id": decision.item_id,
            "answer_run_index": decision.answer_run_index,
            "routing_label": decision.label,
            "expected_guidelines_json": json.dumps(
                decision.expected_guidelines, ensure_ascii=False
            ),
            "reported_guidelines_json": json.dumps(
                decision.reported_guidelines, ensure_ascii=False
            ),
            "true_positives": _routing_counts(decision)[0],
            "false_positives": _routing_counts(decision)[1],
            "false_negatives": _routing_counts(decision)[2],
            "precision": _fmt_float(_routing_precision(decision)),
            "recall": _fmt_float(_routing_recall(decision)),
            "f1": _fmt_float(_routing_f1(decision)),
        }
        for decision in context.routing_metrics.decisions
    ]
    _write_rows_csv(tables_dir / "routing_accuracy.csv", routing_rows)

    gate_rows = [
        {
            "actual_gate_expected": "fire",
            "predicted_fire": context.gate_metrics.true_positive,
            "predicted_suppress": context.gate_metrics.false_negative,
        },
        {
            "actual_gate_expected": "suppress",
            "predicted_fire": context.gate_metrics.false_positive,
            "predicted_suppress": context.gate_metrics.true_negative,
        },
    ]
    _write_rows_csv(tables_dir / "gate_confusion.csv", gate_rows)

    pass_fail_rows = [
        {
            "query_id": result.query_id,
            "verdict": result.verdict,
            "failed_conditions_json": json.dumps(
                result.failed_conditions, ensure_ascii=False
            ),
            "routing_label": result.routing_label,
            "gate_correct": result.gate_correct,
            "hallucination_present": result.hallucination_present,
            "safety_flag": result.safety_flag,
            "citation_existence_accuracy": result.citation_existence_accuracy,
        }
        for result in context.pass_fail_results
    ]
    _write_rows_csv(tables_dir / "pass_fail_summary.csv", pass_fail_rows)

    agreement_rows = _agreement_rows(context.agreement_report)
    _write_rows_csv(tables_dir / "agreement.csv", agreement_rows)

    by_query_type_rows = _by_query_type_rows(context)
    _write_rows_csv(tables_dir / "by_query_type.csv", by_query_type_rows)

    if context.citation_breakdown is not None:
        _write_rows_csv(tables_dir / "citation_tiers.csv", _citation_tier_rows(context))
        _write_rows_csv(tables_dir / "hallucination_types.csv", _hallucination_type_rows(context))
        _write_rows_csv(
            tables_dir / "hallucination_rate_summary.csv",
            _hallucination_analysis_rows(context),
        )
        _write_rows_csv(
            tables_dir / "clinical_risk_distribution.csv",
            _clinical_risk_rows(context),
        )


def _write_report_plots(context: ReportContext, plots_dir: Path) -> None:
    _plot_score_distributions(context, plots_dir / "score_distributions.png")
    _plot_gate_confusion(context.gate_metrics, plots_dir / "gate_confusion.png")
    _plot_by_query_type(context, plots_dir / "by_query_type.png")


def _plot_score_distributions(context: ReportContext, path: Path) -> None:
    data = [
        [
            getattr(aggregate, dimension)
            for aggregate in context.ensembles.values()
        ]
        for dimension in LIKERT_DIMENSIONS
    ]
    labels = [
        "Citation\nSupport",
        "Completeness",
        "Uncertainty\nHandling",
    ]

    fig, ax = plt.subplots(figsize=(9, 5))
    boxplot = ax.boxplot(data, patch_artist=True, tick_labels=labels)
    colors = ["#72B7B2", "#F58518", "#E45756"]
    for patch, color in zip(boxplot["boxes"], colors, strict=False):
        patch.set_facecolor(color)
        patch.set_alpha(0.65)
    ax.set_ylabel("Ensemble score")
    ax.set_ylim(-0.1, 3.1)
    ax.set_title("Ensemble Likert Score Distributions")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_gate_confusion(gate_metrics: GateMetrics, path: Path) -> None:
    matrix = np.array(
        [
            [gate_metrics.true_positive, gate_metrics.false_negative],
            [gate_metrics.false_positive, gate_metrics.true_negative],
        ]
    )
    fig, ax = plt.subplots(figsize=(5, 4))
    image = ax.imshow(matrix, cmap="Blues")
    ax.set_xticks([0, 1], labels=["Pred fire", "Pred suppress"])
    ax.set_yticks([0, 1], labels=["Actual fire", "Actual suppress"])
    ax.set_title("Gate Confusion Matrix")
    for (row_index, col_index), value in np.ndenumerate(matrix):
        ax.text(col_index, row_index, str(value), ha="center", va="center", color="black")
    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _plot_by_query_type(context: ReportContext, path: Path) -> None:
    rows = _by_query_type_rows(context)
    query_types = [row["query_type"] for row in rows]
    pass_rates = [float(row["pass_rate_fraction"]) for row in rows]

    fig, ax = plt.subplots(figsize=(10, 5))
    indices = np.arange(len(query_types))
    ax.bar(indices, pass_rates, color="#4C78A8", width=0.6)
    ax.set_xticks(indices, labels=query_types, rotation=25, ha="right")
    ax.set_ylabel("Pass rate")
    ax.set_ylim(0.0, 1.0)
    ax.set_title("Pass Rate by Query Type")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _select_report_dataset(
    *,
    benchmark_items: list[BenchmarkItem],
    answers: list[AgentAnswer],
    judgments: list[Judgment],
    strict: bool,
) -> tuple[list[BenchmarkItem], list[AgentAnswer], list[Judgment]]:
    answers_by_id: dict[str, list[AgentAnswer]] = defaultdict(list)
    for answer in answers:
        answers_by_id[answer.id].append(answer)

    judgments_by_key: dict[tuple[str, int], list[Judgment]] = defaultdict(list)
    for judgment in judgments:
        judgments_by_key[(judgment.query_id, _answer_run_index(judgment))].append(judgment)

    selected_items: list[BenchmarkItem] = []
    selected_answers: list[AgentAnswer] = []
    filtered_judgments: list[Judgment] = []
    missing_messages: list[str] = []

    for item in benchmark_items:
        item_answers = sorted(answers_by_id.get(item.id, []), key=lambda answer: answer.run_index)
        if not item_answers:
            if strict:
                missing_messages.append(f"{item.id}: missing answer")
            continue

        judged_answers = [
            answer
            for answer in item_answers
            if judgments_by_key.get((item.id, answer.run_index))
        ]
        chosen_answer = judged_answers[0] if judged_answers else item_answers[0]
        chosen_judgments = judgments_by_key.get((item.id, chosen_answer.run_index), [])
        if not chosen_judgments:
            if strict:
                missing_messages.append(
                    f"{item.id}: missing judgments for answer_run_index={chosen_answer.run_index}"
                )
            continue

        selected_items.append(item)
        selected_answers.append(chosen_answer)
        filtered_judgments.extend(chosen_judgments)

    if strict and missing_messages:
        raise ValueError(
            "Incomplete report inputs:\n" + "\n".join(sorted(missing_messages))
        )
    return selected_items, selected_answers, filtered_judgments


def _load_models(path: str | Path, model_type: type[ModelT]) -> list[ModelT]:
    source = Path(path)
    models: list[ModelT] = []
    for line_number, row in enumerate(read_jsonl(source), start=1):
        try:
            models.append(model_type.model_validate(row))
        except ValidationError as exc:
            raise ValueError(f"{source}:{line_number}: {exc}") from exc
    return models


def _load_recommendation_index(path: str | Path) -> dict[str, Any]:
    from src.corpus.index import load_recommendation_index

    return load_recommendation_index(path)


def _resolve_benchmark_path(config: dict, override: str | None) -> Path:
    if override is not None:
        return Path(override)

    configured = Path(config["paths"]["benchmark"])
    if configured.exists():
        return configured

    fallback = configured.with_name("benchmark_queries.seed.jsonl")
    if fallback.exists():
        return fallback

    raise FileNotFoundError(
        f"Benchmark file not found: {configured} (and no seed fallback at {fallback})."
    )


def _answer_run_index(judgment: Judgment) -> int:
    answer_run_index = judgment.meta.get("answer_run_index", 0)
    if isinstance(answer_run_index, int):
        return answer_run_index
    return int(answer_run_index)


def _routing_counts(decision: RoutingDecision) -> tuple[int, int, int]:
    expected = set(decision.expected_guidelines)
    reported = set(decision.reported_guidelines)
    true_positives = len(expected & reported)
    false_positives = len(reported - expected)
    false_negatives = len(expected - reported)
    return true_positives, false_positives, false_negatives


def _routing_precision(decision: RoutingDecision) -> float:
    tp, fp, _ = _routing_counts(decision)
    denominator = tp + fp
    if denominator == 0:
        return 1.0
    return tp / denominator


def _routing_recall(decision: RoutingDecision) -> float:
    tp, _, fn = _routing_counts(decision)
    denominator = tp + fn
    if denominator == 0:
        return 1.0
    return tp / denominator


def _routing_f1(decision: RoutingDecision) -> float:
    tp, fp, fn = _routing_counts(decision)
    denominator = (2 * tp) + fp + fn
    if denominator == 0:
        return 1.0
    return (2 * tp) / denominator


def _judge_score_summary_rows(context: ReportContext) -> list[dict[str, str]]:
    ensemble_aggregates = list(context.ensembles.values())
    judge_names = sorted({aggregate.judge for aggregate in context.per_judge.values()})
    judge_aggregates_by_name: dict[str, list[PerJudgeAggregate]] = defaultdict(list)
    for aggregate in context.per_judge.values():
        judge_aggregates_by_name[aggregate.judge].append(aggregate)

    rows: list[dict[str, str]] = []
    for dimension in LIKERT_DIMENSIONS:
        row = {
            "dimension": dimension,
            "ensemble_mean_sd": _fmt_mean_sd(
                [getattr(aggregate, dimension) for aggregate in ensemble_aggregates]
            ),
        }
        for judge_name in judge_names:
            row[f"{judge_name}_mean_sd"] = _fmt_mean_sd(
                [
                    getattr(aggregate, dimension)
                    for aggregate in judge_aggregates_by_name[judge_name]
                ]
            )
        rows.append(row)
    return rows


def _routing_summary_rows(context: ReportContext) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for decision in context.routing_metrics.decisions:
        tp, fp, fn = _routing_counts(decision)
        rows.append(
            {
                "query_id": decision.item_id,
                "routing_label": decision.label,
                "true_positives": str(tp),
                "false_positives": str(fp),
                "false_negatives": str(fn),
                "f1": _fmt_float(_routing_f1(decision)),
            }
        )
    return rows


def _gate_summary_rows(context: ReportContext) -> list[dict[str, str]]:
    rows = []
    sensitivity_ci = wilson_ci(
        context.gate_metrics.true_positive,
        context.gate_metrics.true_positive + context.gate_metrics.false_negative,
    )
    specificity_ci = wilson_ci(
        context.gate_metrics.true_negative,
        context.gate_metrics.true_negative + context.gate_metrics.false_positive,
    )

    rows.append(
        {
            "metric": "sensitivity",
            "value": _fmt_optional_pct(context.gate_metrics.sensitivity),
            "ci_95": _fmt_ci(sensitivity_ci.lower, sensitivity_ci.upper),
        }
    )
    rows.append(
        {
            "metric": "specificity",
            "value": _fmt_optional_pct(context.gate_metrics.specificity),
            "ci_95": _fmt_ci(specificity_ci.lower, specificity_ci.upper),
        }
    )

    complete_case_total = sum(item.query_type == QueryType.complete_case for item in context.items)
    complete_case_wrongly_fired = sum(
        item.query_type == QueryType.complete_case and answer.gate_fired
        for item, answer in zip(context.items, context.answers, strict=False)
    )
    over_interrogation_ci = wilson_ci(
        complete_case_wrongly_fired,
        complete_case_total,
    )
    rows.append(
        {
            "metric": "over_interrogation_rate",
            "value": _fmt_optional_pct(context.gate_metrics.over_interrogation_rate),
            "ci_95": _fmt_ci(over_interrogation_ci.lower, over_interrogation_ci.upper),
        }
    )
    rows.append(
        {
            "metric": "mean_parameter_recall",
            "value": _fmt_optional_float(context.gate_metrics.mean_parameter_recall),
            "ci_95": "n/a",
        }
    )
    return rows


def _citation_summary_rows(
    context: ReportContext,
    citation_support_ci: Any,
) -> list[dict[str, str]]:
    existence_ci = wilson_ci(
        context.citation_metrics.matched_citations,
        context.citation_metrics.total_citations,
    )
    mean_citation_support = mean(
        aggregate.citation_support for aggregate in context.ensembles.values()
    )
    return [
        {
            "metric": "citation_existence_accuracy",
            "value": _fmt_pct(context.citation_metrics.existence_accuracy),
            "ci_95": _fmt_ci(existence_ci.lower, existence_ci.upper),
        },
        {
            "metric": "citation_support_score_mean",
            "value": _fmt_float(mean_citation_support),
            "ci_95": "n/a",
        },
        {
            "metric": "citation_support_accuracy_score_ge_2",
            "value": _fmt_pct(citation_support_ci.proportion),
            "ci_95": _fmt_ci(citation_support_ci.lower, citation_support_ci.upper),
        },
    ]


def _citation_tier_rows(context: ReportContext) -> list[dict[str, str]]:
    breakdown = context.citation_breakdown
    if breakdown is None:
        return []
    return [
        {
            "tier": label,
            "n": str(count),
            "%": _fmt_ratio(count, breakdown.tier_totals),
        }
        for label, count in breakdown.tiers.items()
    ]


def _hallucination_type_rows(context: ReportContext) -> list[dict[str, str]]:
    breakdown = context.citation_breakdown
    if breakdown is None:
        return []
    return [
        {
            "type": label,
            "category": "Artefact" if label in ARTEFACT_TYPES else "Real error",
            "n": str(count),
        }
        for label, count in breakdown.hal_type_counts.items()
    ]


def _high_risk_items(context: ReportContext) -> list[str]:
    breakdown = context.citation_breakdown
    if breakdown is None:
        return []
    items: list[str] = []
    for item_id, payload in breakdown.per_item.items():
        if any(flag["clinical_risk"] == "high" for flag in payload["hallucination_flags"]):
            items.append(item_id)
    return sorted(items)


def _hallucination_analysis_rows(context: ReportContext) -> list[dict[str, str]]:
    breakdown = context.citation_breakdown
    if breakdown is None:
        return []
    total_items = len(breakdown.per_item)
    high_risk_items = _high_risk_items(context)
    reported_count = total_items - len(breakdown.items_clean)
    return [
        {
            "category": "Reported (any judge flag)",
            "items": f"{reported_count}/{total_items}",
            "rate": _fmt_pct(breakdown.reported_hal_rate),
        },
        {
            "category": "Artefact-only items",
            "items": f"{len(breakdown.items_artefact_only)}/{total_items}",
            "rate": "—",
        },
        {
            "category": "Items with real errors",
            "items": f"{len(breakdown.items_real_error)}/{total_items}",
            "rate": _fmt_pct(breakdown.adjusted_hal_rate),
        },
        {
            "category": "Items with high-risk errors",
            "items": f"{len(high_risk_items)}/{total_items}",
            "rate": _fmt_pct(len(high_risk_items) / total_items) if total_items else "0.0%",
        },
    ]


def _clinical_risk_rows(context: ReportContext) -> list[dict[str, str]]:
    breakdown = context.citation_breakdown
    if breakdown is None:
        return []
    return [
        {"risk_level": risk, "claim_count": str(count)}
        for risk, count in breakdown.clinical_risk_counts.items()
    ]


def _reclassification_rows(
    report: ReclassificationReport | None,
) -> list[dict[str, str]]:
    if report is None:
        return []
    return [
        {
            "layer": "Raw judge flags (any flag = hallucination)",
            "hallucination_rate": _fmt_pct(report.reported_hal_rate),
        },
        {
            "layer": "Automated type classification (artefacts removed)",
            "hallucination_rate": _fmt_pct(report.auto_classified_hal_rate),
        },
        {
            "layer": "Clinical review (confirmed real errors only)",
            "hallucination_rate": _fmt_pct(report.clinical_review_rate),
        },
        {
            "layer": "High clinical risk items",
            "hallucination_rate": _fmt_pct(report.high_risk_rate),
        },
        {
            "layer": "Flags reclassified after review",
            "hallucination_rate": f"{report.flags_reclassified}/{report.total_flags} ({_fmt_pct(report.reclassification_rate)})",
        },
    ]


def _safety_rows(context: ReportContext) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for review_item in context.review_items:
        if not review_item.ensemble.safety_flag:
            continue
        rows.append(
            {
                "query_id": review_item.item.id,
                "query_type": review_item.item.query_type.value,
                "safety_reasons": "; ".join(review_item.ensemble.safety_reasons) or "(none)",
                "triggers": ", ".join(review_item.trigger_reasons),
            }
        )
    if not rows:
        rows.append(
            {
                "query_id": "(none)",
                "query_type": "",
                "safety_reasons": "No ensemble safety flags.",
                "triggers": "",
            }
        )
    return rows


def _agreement_rows(report: AgreementReport | None) -> list[dict[str, str]]:
    if report is None:
        return [
            {
                "dimension": "pending",
                "scale": "n/a",
                "weighted_kappa": "",
                "binary_kappa": "",
                "percent_agreement": "",
                "icc_value": "",
                "icc_ci_95": "",
                "krippendorff_alpha": "",
                "notes": "Agreement unavailable: incomplete paired judge data.",
            }
        ]

    rows: list[dict[str, str]] = []
    for likert in report.likert:
        rows.append(
            {
                "dimension": likert.dimension,
                "scale": "likert",
                "weighted_kappa": _fmt_float(likert.weighted_kappa),
                "binary_kappa": "",
                "percent_agreement": "",
                "icc_value": _fmt_float(likert.icc_value),
                "icc_ci_95": _fmt_ci(likert.icc_ci_lower, likert.icc_ci_upper),
                "krippendorff_alpha": "",
                "notes": "",
            }
        )
    for binary in report.binary:
        rows.append(
            {
                "dimension": binary.dimension,
                "scale": "binary",
                "weighted_kappa": "",
                "binary_kappa": _fmt_float(binary.kappa),
                "percent_agreement": _fmt_pct(binary.percent_agreement),
                "icc_value": "",
                "icc_ci_95": "",
                "krippendorff_alpha": "",
                "notes": "",
            }
        )
    rows.append(
        {
            "dimension": "all_likert",
            "scale": "likert",
            "weighted_kappa": "",
            "binary_kappa": "",
            "percent_agreement": "",
            "icc_value": "",
            "icc_ci_95": "",
            "krippendorff_alpha": _fmt_float(report.krippendorff_alpha),
            "notes": "Krippendorff alpha across all Likert dimensions.",
        }
    )
    return rows


def _by_query_type_rows(context: ReportContext) -> list[dict[str, str]]:
    pass_fail_by_id = {result.query_id: result for result in context.pass_fail_results}
    grouped: dict[str, list[str]] = defaultdict(list)
    for item in context.items:
        grouped[item.query_type.value].append(item.id)

    rows: list[dict[str, str]] = []
    for query_type in [query_type.value for query_type in QueryType]:
        query_ids = grouped.get(query_type, [])
        if not query_ids:
            rows.append(
                {
                    "query_type": query_type,
                    "n": "0",
                    "passes": "0",
                    "pass_rate_fraction": "0.0",
                    "pass_rate": "0.0%",
                    "pass_rate_ci_95": "n/a",
                    "mean_citation_support": "",
                    "mean_completeness": "",
                    "mean_uncertainty_handling": "",
                }
            )
            continue

        passes = sum(
            pass_fail_by_id[query_id].verdict == "PASS" for query_id in query_ids
        )
        ci = wilson_ci(passes, len(query_ids))
        ensemble_values = [context.ensembles[query_id] for query_id in query_ids]
        rows.append(
            {
                "query_type": query_type,
                "n": str(len(query_ids)),
                "passes": str(passes),
                "pass_rate_fraction": f"{ci.proportion:.6f}",
                "pass_rate": _fmt_pct(ci.proportion),
                "pass_rate_ci_95": _fmt_ci(ci.lower, ci.upper),
                "mean_citation_support": _fmt_float(
                    mean(value.citation_support for value in ensemble_values)
                ),
                "mean_completeness": _fmt_float(
                    mean(value.completeness for value in ensemble_values)
                ),
                "mean_uncertainty_handling": _fmt_float(
                    mean(value.uncertainty_handling for value in ensemble_values)
                ),
            }
        )
    return rows


def _render_human_calibration_section(context: ReportContext) -> str:
    human_scores_path = context.output_dir / "human_scores.csv"
    if not human_scores_path.exists():
        return _markdown_table(
            [
                {
                    "status": "Human calibration: pending (see T10)",
                    "details": f"Missing {human_scores_path.name}.",
                }
            ]
        )
    report = compute_human_calibration(
        human_scores_path=human_scores_path,
        per_judge=context.per_judge,
        ensembles=context.ensembles,
    )
    return _markdown_table(_human_calibration_rows(report))


def _human_calibration_rows(
    report: HumanCalibrationReport,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for entry in report.likert:
        rows.append(
            {
                "dimension": entry.dimension,
                "ensemble_kappa": _fmt_float(entry.ensemble_kappa),
                "judge_a_kappa": _fmt_float(entry.judge_a_kappa),
                "judge_b_kappa": _fmt_float(entry.judge_b_kappa),
                "n": entry.n,
            }
        )
    for entry in report.binary:
        rows.append(
            {
                "dimension": entry.dimension,
                "ensemble_kappa": _fmt_float(entry.ensemble_kappa),
                "judge_a_kappa": _fmt_float(entry.judge_a_kappa),
                "judge_b_kappa": _fmt_float(entry.judge_b_kappa),
                "n": entry.n,
            }
        )
    return rows


def _markdown_table(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "_No rows._"

    headers = list(rows[0].keys())
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        cells = [str(row.get(header, "")) for header in headers]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _write_rows_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        fieldnames = list(rows[0].keys())
    else:
        fieldnames = []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if fieldnames:
            writer.writeheader()
            writer.writerows(rows)


def _write_summary_json(context: ReportContext, metrics_dir: Path) -> None:
    metrics_dir.mkdir(parents=True, exist_ok=True)
    passes = sum(result.verdict == "PASS" for result in context.pass_fail_results)
    total = len(context.pass_fail_results)
    summary: dict[str, Any] = {
        "mode": "strict_full" if context.strict_mode else "dry_run",
        "processed_items": len(context.items),
        "benchmark_total_items": context.benchmark_total_items,
        "judgment_rows": len(context.judgments),
        "judges_present": sorted({aggregate.judge for aggregate in context.per_judge.values()}),
        "query_ids": [item.id for item in context.items],
        "pass_rate": {
            "passes": passes,
            "total": total,
            "rate": (passes / total) if total else 0.0,
        },
        "routing": {
            "exact_match_rate": context.routing_metrics.exact_match_rate,
            "micro_precision": context.routing_metrics.micro_precision,
            "micro_recall": context.routing_metrics.micro_recall,
            "micro_f1": context.routing_metrics.micro_f1,
            "macro_f1": context.routing_metrics.macro_f1,
        },
        "gate": {
            "true_positive": context.gate_metrics.true_positive,
            "true_negative": context.gate_metrics.true_negative,
            "false_positive": context.gate_metrics.false_positive,
            "false_negative": context.gate_metrics.false_negative,
            "sensitivity": context.gate_metrics.sensitivity,
            "specificity": context.gate_metrics.specificity,
            "over_interrogation_rate": context.gate_metrics.over_interrogation_rate,
            "mean_parameter_recall": context.gate_metrics.mean_parameter_recall,
        },
        "citation": {
            "total_citations": context.citation_metrics.total_citations,
            "matched_citations": context.citation_metrics.matched_citations,
            "existence_accuracy": context.citation_metrics.existence_accuracy,
        },
    }
    if context.agreement_report is not None:
        summary["agreement"] = {
            "krippendorff_alpha": context.agreement_report.krippendorff_alpha,
            "likert": [
                {
                    "dimension": row.dimension,
                    "weighted_kappa": row.weighted_kappa,
                    "icc_value": row.icc_value,
                    "icc_ci_lower": row.icc_ci_lower,
                    "icc_ci_upper": row.icc_ci_upper,
                }
                for row in context.agreement_report.likert
            ],
            "binary": [
                {
                    "dimension": row.dimension,
                    "kappa": row.kappa,
                    "percent_agreement": row.percent_agreement,
                }
                for row in context.agreement_report.binary
            ],
        }
    if context.citation_breakdown is not None:
        total_tiers = context.citation_breakdown.tier_totals
        summary["citation_breakdown"] = {
            "tier_a_pct": (
                context.citation_breakdown.tiers.get("A_VERIFIED", 0) / total_tiers
                if total_tiers
                else 0.0
            ),
            "tier_b_pct": (
                context.citation_breakdown.tiers.get("B_METADATA_ERR", 0) / total_tiers
                if total_tiers
                else 0.0
            ),
            "reported_hal_rate": context.citation_breakdown.reported_hal_rate,
            "adjusted_hal_rate": context.citation_breakdown.adjusted_hal_rate,
            "high_risk_items": _high_risk_items(context),
            "real_error_items": context.citation_breakdown.items_real_error,
        }
    if context.reclassification_report is not None:
        summary["reclassification"] = {
            "total_flags": context.reclassification_report.total_flags,
            "flags_reclassified": context.reclassification_report.flags_reclassified,
            "reclassification_rate": context.reclassification_report.reclassification_rate,
            "reported_hal_rate": context.reclassification_report.reported_hal_rate,
            "auto_classified_hal_rate": context.reclassification_report.auto_classified_hal_rate,
            "clinical_review_rate": context.reclassification_report.clinical_review_rate,
            "high_risk_rate": context.reclassification_report.high_risk_rate,
        }
    (metrics_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _fmt_ci(lower: float | None, upper: float | None) -> str:
    if lower is None or upper is None:
        return "n/a"
    if np.isnan(lower) or np.isnan(upper):
        return "n/a"
    return f"[{lower:.3f}, {upper:.3f}]"


def _fmt_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    if np.isnan(value):
        return "n/a"
    return f"{value:.3f}"


def _fmt_optional_float(value: float | None) -> str:
    return _fmt_float(value)


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _fmt_ratio(count: int, total: int) -> str:
    return _fmt_pct(count / total) if total else "0.0%"


def _fmt_optional_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return _fmt_pct(value)


def _fmt_mean_sd(values: list[float]) -> str:
    if not values:
        return "n/a"
    mean_value = mean(values)
    sd_value = pstdev(values) if len(values) > 1 else 0.0
    return f"{mean_value:.3f} ± {sd_value:.3f}"


__all__ = [
    "ReportContext",
    "build_parser",
    "build_report",
    "main",
    "render_report_markdown",
]


if __name__ == "__main__":
    raise SystemExit(main())
