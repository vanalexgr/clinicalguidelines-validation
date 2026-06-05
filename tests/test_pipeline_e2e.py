from __future__ import annotations

import json
from pathlib import Path

from src.common.io import write_jsonl
from src.common.schemas import AgentAnswer, BenchmarkItem, Citation, Judgment
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


def test_pipeline_e2e_build_report_writes_summary_and_sections(tmp_path: Path) -> None:
    item = _seed_item("Q002")
    benchmark_path = tmp_path / "benchmark.jsonl"
    answers_path = tmp_path / "answers.jsonl"
    judgments_path = tmp_path / "judgments.jsonl"
    report_dir = tmp_path / "report"
    metrics_dir = tmp_path / "metrics"
    overrides_path = tmp_path / "overrides.jsonl"
    config_path = tmp_path / "config.yaml"

    answer = AgentAnswer(
        id=item.id,
        run_index=0,
        raw_response="AAA threshold answer.",
        blinded_response="AAA threshold answer.",
        gate_fired=False,
        routed_guidelines=["ESVS_AAA_2024"],
        recommendation="Repair at 55 mm in men.",
        citations=[
            Citation(rec_id="AAA-EXAMPLE", class_="I", level="A", guideline="ESVS_AAA_2024")
        ],
        retrieved_passages=[],
        uncertainty_statements=[],
        latency_seconds=0.1,
        model_meta={},
    )
    judgments = [
        Judgment.model_validate(
            {
                "query_id": item.id,
                "judge": judge,
                "run_index": run_index,
                "dimensions": {
                    "clinical_correctness": {"score": 2, "rationale": ""},
                    "citation_support": {
                        "score": 2,
                        "rationale": "",
                        "unsupported_citations": [],
                    },
                    "completeness": {"score": 2, "rationale": ""},
                    "uncertainty_handling": {"score": 2, "rationale": ""},
                },
                "hallucination": {
                    "present": True,
                    "unsupported_claims": [
                        "Men with an asymptomatic abdominal aortic aneurysm < 55 mm are not recommended for elective repair."
                    ],
                    "count": 1,
                },
                "safety_critical_error": {"flag": False, "reason": None},
                "overall_comment": "",
                "_meta": {"answer_run_index": 0},
            }
        )
        for judge in ("claude-opus-4-7", "o3")
        for run_index in range(3)
    ]

    write_jsonl(benchmark_path, [item.model_dump(by_alias=True)])
    write_jsonl(answers_path, [answer.model_dump(by_alias=True)])
    write_jsonl(judgments_path, [judgment.model_dump(by_alias=True) for judgment in judgments])
    overrides_path.write_text(
        json.dumps(
            {
                "query_id": item.id,
                "judge": None,
                "claim_prefix": "Men with an asymptomatic abdominal aortic aneurysm < 55 mm a",
                "auto_type": "OTHER",
                "corrected_type": "FALSE_POSITIVE",
                "clinical_risk": "none",
                "clinical_note": "Threshold statement is accurate.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    config_path.write_text(
        "\n".join(
            [
                "paths:",
                f"  benchmark: {benchmark_path}",
                f"  corpus_index: {INDEX_FIXTURE_PATH}",
                f"  answers: {answers_path}",
                f"  judgments: {judgments_path}",
                f"  metrics_dir: {metrics_dir}",
                f"  report_dir: {report_dir}",
            ]
        ),
        encoding="utf-8",
    )

    exit_code = report_module.main(["--config", str(config_path)])

    assert exit_code == 0
    summary = json.loads((metrics_dir / "summary.json").read_text(encoding="utf-8"))
    report_text = (report_dir / "report.md").read_text(encoding="utf-8")

    assert summary["processed_items"] == 1
    assert sorted(summary["judges_present"]) == ["claude-opus-4-7", "o3"]
    assert "citation_breakdown" in summary
    assert "§8 Citation Correctness Tiers" in report_text
    assert "§9 Hallucination Analysis" in report_text
