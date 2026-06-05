from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.common.schemas import AgentAnswer, BenchmarkItem, Judgment
from src.judge import run_judge as run_judge_module

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "data/benchmark/benchmark_queries.seed.jsonl"


def _seed_item(item_id: str) -> BenchmarkItem:
    for line in SEED_PATH.read_text(encoding="utf-8").splitlines():
        item = BenchmarkItem.model_validate(json.loads(line))
        if item.id == item_id:
            return item
    raise AssertionError(f"Missing benchmark item {item_id}")


def test_status_flag_prints_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    item = _seed_item("Q001")
    benchmark_path = tmp_path / "benchmark.jsonl"
    answers_path = tmp_path / "answers.jsonl"
    judgments_path = tmp_path / "judgments.jsonl"
    config_path = tmp_path / "config.yaml"

    benchmark_path.write_text(
        json.dumps(item.model_dump(by_alias=True), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    answer = AgentAnswer(
        id=item.id,
        run_index=0,
        raw_response="Answer.",
        blinded_response="Answer.",
        gate_fired=False,
        routed_guidelines=["ESVS_ALI_2020"],
        recommendation="Recommendation.",
        citations=[],
        retrieved_passages=[],
        uncertainty_statements=[],
        latency_seconds=0.1,
        model_meta={},
    )
    answers_path.write_text(
        json.dumps(answer.model_dump(by_alias=True), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    existing = Judgment.model_validate(
        {
            "query_id": item.id,
            "judge": "gpt-5",
            "run_index": 0,
            "dimensions": {
                "clinical_correctness": {"score": 2, "rationale": ""},
                "citation_support": {"score": 2, "rationale": "", "unsupported_citations": []},
                "completeness": {"score": 2, "rationale": ""},
                "uncertainty_handling": {"score": 2, "rationale": ""},
            },
            "hallucination": {"present": False, "unsupported_claims": [], "count": 0},
            "safety_critical_error": {"flag": False, "reason": None},
            "overall_comment": "",
            "_meta": {"answer_run_index": 0},
        }
    )
    judgments_path.write_text(
        json.dumps(existing.model_dump(by_alias=True), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    config_path.write_text(
        "\n".join(
            [
                "judges:",
                "  - name: gpt-5",
                "    provider: openai",
                "  - name: claude-opus-4-7",
                "    provider: anthropic",
                "run:",
                "  runs_per_judge: 2",
                "paths:",
                f"  benchmark: {benchmark_path}",
                f"  answers: {answers_path}",
                f"  judgments: {judgments_path}",
                f"  metrics_dir: {tmp_path / 'metrics'}",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        run_judge_module,
        "build_judge_clients",
        lambda config: (_ for _ in ()).throw(AssertionError("status should not build clients")),
    )

    exit_code = run_judge_module.main(["--config", str(config_path), "--status"])

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert "Missing runs (3):" in stdout
    assert f"{item.id}  claude-opus-4-7  run 0" in stdout
    assert f"{item.id}  claude-opus-4-7  run 1" in stdout
    assert f"{item.id}  gpt-5  run 1" in stdout
