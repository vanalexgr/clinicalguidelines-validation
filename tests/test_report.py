from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from src.common.io import write_jsonl
from src.common.schemas import (
    AgentAnswer,
    BenchmarkItem,
    Citation,
    Judgment,
    RetrievedPassage,
)
from src.metrics.aggregate import aggregate_ensemble, aggregate_within_judge
from src.metrics.human_calibration import compute_human_calibration
from src.report import build_report as report_module

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "data/benchmark/benchmark_queries.seed.jsonl"
INDEX_FIXTURE_PATH = REPO_ROOT / "tests/fixtures/recommendation_index.fixture.json"


def _seed_item(item_id: str) -> BenchmarkItem:
    for line in SEED_PATH.read_text(encoding="utf-8").splitlines():
        item = BenchmarkItem.model_validate(json.loads(line))
        if item.id == item_id:
            return item
    raise AssertionError(f"Missing benchmark item {item_id}")


def _answer(
    item: BenchmarkItem,
    *,
    run_index: int = 0,
    raw_response: str,
    recommendation: str = "",
    routed_guidelines: list[str] | None = None,
    gate_fired: bool | None = None,
    citations: list[Citation] | None = None,
    passage_text: str = "",
) -> AgentAnswer:
    return AgentAnswer(
        id=item.id,
        run_index=run_index,
        raw_response=raw_response,
        blinded_response=raw_response,
        gate_fired=item.gold.gate_expected.value == "fire" if gate_fired is None else gate_fired,
        clarification_requested=[],
        routed_guidelines=routed_guidelines or list(item.gold.expected_guidelines),
        recommendation=recommendation,
        citations=citations or [],
        retrieved_passages=[
            RetrievedPassage(
                guideline=(item.gold.expected_guidelines or ["OUT_OF_SCOPE"])[0],
                chunk_id=f"{item.id}-chunk",
                text=passage_text or f"Retrieved passage for {item.id}.",
            )
        ],
        uncertainty_statements=[],
        latency_seconds=1.0,
        model_meta={},
    )


def _judgment(
    query_id: str,
    judge: str,
    run_index: int,
    *,
    answer_run_index: int = 0,
    clinical_correctness: int = 3,
    citation_support: int = 3,
    completeness: int = 3,
    uncertainty_handling: int = 3,
    hallucination_present: bool = False,
    hallucination_count: int = 0,
    safety_flag: bool = False,
    safety_reason: str | None = None,
) -> Judgment:
    return Judgment.model_validate(
        {
            "query_id": query_id,
            "judge": judge,
            "run_index": run_index,
            "dimensions": {
                "clinical_correctness": {
                    "score": clinical_correctness,
                    "rationale": f"{judge} clinical rationale run {run_index}",
                },
                "citation_support": {
                    "score": citation_support,
                    "rationale": f"{judge} citation rationale run {run_index}",
                    "unsupported_citations": [],
                },
                "completeness": {
                    "score": completeness,
                    "rationale": f"{judge} completeness rationale run {run_index}",
                },
                "uncertainty_handling": {
                    "score": uncertainty_handling,
                    "rationale": f"{judge} uncertainty rationale run {run_index}",
                },
            },
            "hallucination": {
                "present": hallucination_present,
                "unsupported_claims": [],
                "count": hallucination_count,
            },
            "safety_critical_error": {
                "flag": safety_flag,
                "reason": safety_reason,
            },
            "overall_comment": f"{judge} overall comment run {run_index}",
            "_meta": {
                "request_id": f"{judge}-{query_id}-{run_index}",
                "latency_s": 0.1,
                "tokens": {"in": 10, "out": 20},
                "answer_run_index": answer_run_index,
            },
        }
    )


def _three_runs(
    query_id: str,
    judge: str,
    **kwargs: object,
) -> list[Judgment]:
    return [_judgment(query_id, judge, run_index, **kwargs) for run_index in range(3)]


def _write_inputs(
    tmp_path: Path,
    *,
    items: list[BenchmarkItem],
    answers: list[AgentAnswer],
    judgments: list[Judgment],
) -> tuple[Path, Path, Path, Path, Path]:
    benchmark_path = tmp_path / "benchmark.jsonl"
    answers_path = tmp_path / "answers.jsonl"
    judgments_path = tmp_path / "judgments.jsonl"
    report_dir = tmp_path / "report"
    config_path = tmp_path / "config.yaml"

    write_jsonl(benchmark_path, [item.model_dump(by_alias=True) for item in items])
    write_jsonl(answers_path, [answer.model_dump(by_alias=True) for answer in answers])
    write_jsonl(judgments_path, [judgment.model_dump(by_alias=True) for judgment in judgments])
    config_path.write_text(
        "\n".join(
            [
                "paths:",
                f"  benchmark: {benchmark_path}",
                f"  corpus_index: {INDEX_FIXTURE_PATH}",
                f"  answers: {answers_path}",
                f"  judgments: {judgments_path}",
                f"  metrics_dir: {tmp_path / 'metrics'}",
                f"  report_dir: {report_dir}",
            ]
        ),
        encoding="utf-8",
    )
    return benchmark_path, answers_path, judgments_path, report_dir, config_path


def _write_human_scores(
    report_dir: Path,
    *,
    rows: list[dict[str, object]] | None = None,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    human_scores_path = report_dir / "human_scores.csv"
    score_rows = rows or [
        {
            "query_id": "Q001",
            "human_score_clinical_correctness": 3,
            "human_score_citation_support": 3,
            "human_score_completeness": 3,
            "human_score_uncertainty_handling": 3,
            "human_score_hallucination_present": 0,
            "human_score_safety_flag": 0,
            "human_notes": "Strong knowledge answer.",
        },
        {
            "query_id": "Q002",
            "human_score_clinical_correctness": 2,
            "human_score_citation_support": 2,
            "human_score_completeness": 2,
            "human_score_uncertainty_handling": 2,
            "human_score_hallucination_present": 0,
            "human_score_safety_flag": 0,
            "human_notes": "Acceptable threshold answer.",
        },
        {
            "query_id": "Q003",
            "human_score_clinical_correctness": 1,
            "human_score_citation_support": 2,
            "human_score_completeness": 1,
            "human_score_uncertainty_handling": 1,
            "human_score_hallucination_present": 0,
            "human_score_safety_flag": 1,
            "human_notes": "Safety issue noted.",
        },
        {
            "query_id": "Q004",
            "human_score_clinical_correctness": 3,
            "human_score_citation_support": 3,
            "human_score_completeness": 2,
            "human_score_uncertainty_handling": 3,
            "human_score_hallucination_present": 0,
            "human_score_safety_flag": 0,
            "human_notes": "Good clarification behavior.",
        },
        {
            "query_id": "Q008",
            "human_score_clinical_correctness": 2,
            "human_score_citation_support": 2,
            "human_score_completeness": 1,
            "human_score_uncertainty_handling": 2,
            "human_score_hallucination_present": 1,
            "human_score_safety_flag": 0,
            "human_notes": "One unsupported cross-guideline leap.",
        },
        {
            "query_id": "Q010",
            "human_score_clinical_correctness": 3,
            "human_score_citation_support": 3,
            "human_score_completeness": 3,
            "human_score_uncertainty_handling": 3,
            "human_score_hallucination_present": 0,
            "human_score_safety_flag": 0,
            "human_notes": "Strong venous recommendation.",
        },
    ]
    fieldnames = list(score_rows[0].keys())
    with human_scores_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(score_rows)
    return human_scores_path


def _full_report_fixture() -> tuple[list[BenchmarkItem], list[AgentAnswer], list[Judgment]]:
    items = [
        _seed_item("Q001"),
        _seed_item("Q002"),
        _seed_item("Q003"),
        _seed_item("Q004"),
        _seed_item("Q008"),
        _seed_item("Q010"),
    ]
    answers = [
        _answer(
            items[0],
            raw_response="Knowledge answer.",
            recommendation="Rutherford classification answer.",
            citations=[
                Citation(
                    rec_id="CAR-EXAMPLE",
                    class_="I",
                    level="A",
                    guideline="ESVS_Carotid_2023",
                )
            ],
        ),
        _answer(
            items[1],
            raw_response="AAA diameter answer.",
            recommendation="Repair at 55 mm in men.",
            citations=[
                Citation(
                    rec_id="AAA-EXAMPLE",
                    class_="I",
                    level="A",
                    guideline="ESVS_AAA_2024",
                )
            ],
        ),
        _answer(
            items[2],
            raw_response="Safety answer.",
            recommendation="CEA within 14 days.",
        ),
        _answer(
            items[3],
            raw_response="Clarification answer.",
            recommendation="Need symptomatic status and stenosis degree.",
            gate_fired=True,
        ),
        _answer(
            items[4],
            raw_response="Multi-guideline answer.",
            recommendation="Integrate CLTI, carotid, and antithrombotic guidance.",
        ),
        _answer(
            items[5],
            raw_response="Venous answer.",
            recommendation="Endovenous thermal ablation.",
        ),
    ]
    judgments = [
        *_three_runs("Q001", "claude-opus-4-7", clinical_correctness=3),
        *_three_runs("Q001", "gpt-4o", clinical_correctness=3),
        *_three_runs("Q002", "claude-opus-4-7", clinical_correctness=2, citation_support=2),
        *_three_runs("Q002", "gpt-4o", clinical_correctness=2, citation_support=2),
        *_three_runs(
            "Q003",
            "claude-opus-4-7",
            clinical_correctness=1,
            completeness=1,
            uncertainty_handling=1,
            safety_flag=True,
            safety_reason="missed urgent timeline",
        ),
        *_three_runs(
            "Q003",
            "gpt-4o",
            clinical_correctness=2,
            uncertainty_handling=2,
        ),
        *_three_runs("Q004", "claude-opus-4-7", clinical_correctness=3),
        *_three_runs("Q004", "gpt-4o", clinical_correctness=3),
        *_three_runs(
            "Q008",
            "claude-opus-4-7",
            completeness=1,
            hallucination_present=True,
            hallucination_count=1,
        ),
        *_three_runs("Q008", "gpt-4o", completeness=3),
        *_three_runs("Q010", "claude-opus-4-7", clinical_correctness=3),
        *_three_runs("Q010", "gpt-4o", clinical_correctness=3),
    ]
    return items, answers, judgments


def test_build_report_cli_writes_sections_tables_plots_and_banner(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    items, answers, judgments = _full_report_fixture()

    _, _, _, report_dir, config_path = _write_inputs(
        tmp_path,
        items=items,
        answers=answers,
        judgments=judgments,
    )

    exit_code = report_module.main(["--config", str(config_path)])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Processed 6 items" in stdout

    report_path = report_dir / "report.md"
    report_text = report_path.read_text(encoding="utf-8")

    headings = [
        "## 1. Dataset Summary",
        "## 2. Overall Pass Rate",
        "## 3. Mean Judge Score Per Dimension",
        "## 4. Guideline-Routing Accuracy",
        "## 5. Context Gate Sensitivity / Specificity",
        "## 6. Citation Accuracy",
        "## 7. Hallucination Rate",
        "## 8. Safety-Critical Discordance Rate",
        "## 9. Performance by Query Type",
        "## 10. Inter-Judge Agreement",
        "## 11. Judge-vs-Human Agreement",
        "## 12. Plots",
    ]
    for heading in headings:
        assert heading in report_text

    assert "> ⚠️ PRELIMINARY — dataset contains unverified items" in report_text
    assert "Human calibration: pending (see T10)" in report_text

    for relative_path in [
        "tables/score_distributions.csv",
        "tables/routing_accuracy.csv",
        "tables/gate_confusion.csv",
        "tables/pass_fail_summary.csv",
        "tables/agreement.csv",
        "tables/by_query_type.csv",
        "plots/score_distributions.png",
        "plots/gate_confusion.png",
        "plots/by_query_type.png",
    ]:
        target = report_dir / relative_path
        assert target.exists()
        assert target.stat().st_size > 0


def test_compute_human_calibration_returns_numeric_kappas(tmp_path: Path) -> None:
    items, answers, judgments = _full_report_fixture()
    _, _, _, report_dir, _ = _write_inputs(
        tmp_path,
        items=items,
        answers=answers,
        judgments=judgments,
    )
    human_scores_path = _write_human_scores(report_dir)

    per_judge = aggregate_within_judge(
        [judgment.model_dump(by_alias=True) for judgment in judgments]
    )
    ensembles = aggregate_ensemble(per_judge)
    report = compute_human_calibration(human_scores_path, per_judge, ensembles)

    assert report.n_items_scored == 6
    assert report.judges == ["claude-opus-4-7", "gpt-4o"]
    assert len(report.likert) == 4
    assert len(report.binary) == 2
    for entry in report.likert:
        assert isinstance(entry.judge_a_kappa, float)
        assert isinstance(entry.judge_b_kappa, float)
        assert isinstance(entry.ensemble_kappa, float)
        assert not math.isnan(entry.judge_a_kappa)
        assert not math.isnan(entry.judge_b_kappa)
        assert not math.isnan(entry.ensemble_kappa)
    for entry in report.binary:
        assert isinstance(entry.judge_a_kappa, float)
        assert isinstance(entry.judge_b_kappa, float)
        assert isinstance(entry.ensemble_kappa, float)
        assert isinstance(entry.percent_agreement_ensemble, float)
        assert not math.isnan(entry.judge_a_kappa)
        assert not math.isnan(entry.judge_b_kappa)
        assert not math.isnan(entry.ensemble_kappa)


def test_build_report_cli_renders_human_calibration_table_when_present(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    items, answers, judgments = _full_report_fixture()
    _, _, _, report_dir, config_path = _write_inputs(
        tmp_path,
        items=items,
        answers=answers,
        judgments=judgments,
    )
    _write_human_scores(report_dir)

    exit_code = report_module.main(["--config", str(config_path)])

    assert exit_code == 0
    _ = capsys.readouterr().out
    report_text = (report_dir / "report.md").read_text(encoding="utf-8")

    assert "Human calibration: pending (see T10)" not in report_text
    assert "| dimension | ensemble_kappa | judge_a_kappa | judge_b_kappa | n |" in report_text
    assert "| clinical_correctness |" in report_text
    assert "| hallucination_present |" in report_text


def test_build_report_dry_run_uses_partial_outputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    items = [
        _seed_item("Q001"),
        _seed_item("Q002"),
        _seed_item("Q003"),
        _seed_item("Q004"),
        _seed_item("Q008"),
        _seed_item("Q010"),
    ]
    answers = [
        _answer(items[0], raw_response="Answer 1.", recommendation="Recommendation 1."),
        _answer(items[1], raw_response="Answer 2.", recommendation="Recommendation 2."),
        _answer(items[2], raw_response="Answer 3.", recommendation="Recommendation 3."),
        _answer(
            items[3],
            raw_response="Answer 4.",
            recommendation="Recommendation 4.",
            gate_fired=True,
        ),
    ]
    judgments = [
        *_three_runs("Q001", "claude-opus-4-7"),
        *_three_runs("Q001", "gpt-4o"),
        *_three_runs("Q002", "claude-opus-4-7"),
        *_three_runs("Q002", "gpt-4o"),
        *_three_runs("Q003", "claude-opus-4-7"),
        *_three_runs("Q003", "gpt-4o"),
        *_three_runs("Q004", "claude-opus-4-7"),
        *_three_runs("Q004", "gpt-4o"),
    ]

    _, _, _, report_dir, config_path = _write_inputs(
        tmp_path,
        items=items,
        answers=answers,
        judgments=judgments,
    )

    with pytest.raises(ValueError, match="Incomplete report inputs"):
        report_module.build_report(
            benchmark_path=tmp_path / "benchmark.jsonl",
            answers_path=tmp_path / "answers.jsonl",
            judgments_path=tmp_path / "judgments.jsonl",
            corpus_index_path=INDEX_FIXTURE_PATH,
            output_dir=report_dir,
            strict=True,
        )

    exit_code = report_module.main(["--config", str(config_path), "--dry-run"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Processed 4 items" in stdout
    assert (report_dir / "report.md").exists()
