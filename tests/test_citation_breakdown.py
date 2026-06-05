from __future__ import annotations

import json
from pathlib import Path

from src.common.io import write_jsonl
from src.common.schemas import AgentAnswer, BenchmarkItem, Judgment
from src.metrics.citation_breakdown import _load_overrides, compute_citation_breakdown

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "data/benchmark/benchmark_queries.seed.jsonl"
INDEX_FIXTURE_PATH = REPO_ROOT / "tests/fixtures/recommendation_index.fixture.json"


def _seed_item(item_id: str) -> BenchmarkItem:
    for line in SEED_PATH.read_text(encoding="utf-8").splitlines():
        item = BenchmarkItem.model_validate(json.loads(line))
        if item.id == item_id:
            return item
    raise AssertionError(f"Missing benchmark item {item_id}")


def test_override_loader(tmp_path: Path) -> None:
    path = tmp_path / "overrides.jsonl"
    row = {
        "query_id": "Q001",
        "judge": None,
        "claim_prefix": "Example claim",
        "auto_type": "OTHER",
        "corrected_type": "VALID_INFERENCE",
        "clinical_risk": "low",
        "clinical_note": "Reviewed override.",
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    overrides = _load_overrides(path)

    assert overrides[("Q001", None, "Example claim")]["corrected_type"] == "VALID_INFERENCE"


def test_override_takes_precedence(tmp_path: Path) -> None:
    item = _seed_item("Q002")
    benchmark_path = tmp_path / "benchmark.jsonl"
    answers_path = tmp_path / "answers.jsonl"
    judgments_path = tmp_path / "judgments.jsonl"
    overrides_path = tmp_path / "overrides.jsonl"

    answer = AgentAnswer(
        id=item.id,
        run_index=0,
        raw_response="AAA answer.",
        blinded_response="AAA answer.",
        gate_fired=False,
        routed_guidelines=["ESVS_AAA_2024"],
        recommendation="Repair at 55 mm in men.",
        citations=[],
        retrieved_passages=[],
        uncertainty_statements=[],
        latency_seconds=0.1,
        model_meta={},
    )
    judgment = Judgment.model_validate(
        {
            "query_id": item.id,
            "judge": "o3",
            "run_index": 0,
            "dimensions": {
                "clinical_correctness": {"score": 2, "rationale": ""},
                "citation_support": {"score": 2, "rationale": "", "unsupported_citations": []},
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

    write_jsonl(benchmark_path, [item.model_dump(by_alias=True)])
    write_jsonl(answers_path, [answer.model_dump(by_alias=True)])
    write_jsonl(judgments_path, [judgment.model_dump(by_alias=True)])
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

    result = compute_citation_breakdown(
        answers_path=answers_path,
        judgments_path=judgments_path,
        index_path=INDEX_FIXTURE_PATH,
        bench_path=benchmark_path,
        overrides_path=overrides_path,
    )

    flag = result.per_item[item.id]["hallucination_flags"][0]
    assert flag["type"] == "OTHER"
    assert flag["corrected_type"] == "FALSE_POSITIVE"


def test_clinical_risk_in_output(tmp_path: Path) -> None:
    item = _seed_item("Q002")
    benchmark_path = tmp_path / "benchmark.jsonl"
    answers_path = tmp_path / "answers.jsonl"
    judgments_path = tmp_path / "judgments.jsonl"
    overrides_path = tmp_path / "overrides.jsonl"

    answer = AgentAnswer(
        id=item.id,
        run_index=0,
        raw_response="AAA answer.",
        blinded_response="AAA answer.",
        gate_fired=False,
        routed_guidelines=["ESVS_AAA_2024"],
        recommendation="Repair at 55 mm in men.",
        citations=[],
        retrieved_passages=[],
        uncertainty_statements=[],
        latency_seconds=0.1,
        model_meta={},
    )
    judgment = Judgment.model_validate(
        {
            "query_id": item.id,
            "judge": "o3",
            "run_index": 0,
            "dimensions": {
                "clinical_correctness": {"score": 2, "rationale": ""},
                "citation_support": {"score": 2, "rationale": "", "unsupported_citations": []},
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
    write_jsonl(benchmark_path, [item.model_dump(by_alias=True)])
    write_jsonl(answers_path, [answer.model_dump(by_alias=True)])
    write_jsonl(judgments_path, [judgment.model_dump(by_alias=True)])
    overrides_path.write_text(
        json.dumps(
            {
                "query_id": item.id,
                "judge": None,
                "claim_prefix": "Men with an asymptomatic abdominal aortic aneurysm < 55 mm a",
                "auto_type": "OTHER",
                "corrected_type": "FALSE_POSITIVE",
                "clinical_risk": "low",
                "clinical_note": "Threshold statement is accurate.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = compute_citation_breakdown(
        answers_path=answers_path,
        judgments_path=judgments_path,
        index_path=INDEX_FIXTURE_PATH,
        bench_path=benchmark_path,
        overrides_path=overrides_path,
    )

    flag = result.per_item[item.id]["hallucination_flags"][0]
    assert flag["clinical_risk"] == "low"
    assert "clinical_note" in flag
