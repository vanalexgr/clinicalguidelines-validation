from __future__ import annotations

import json
from pathlib import Path

from src.common.llm import LLMResult
from src.common.schemas import AgentAnswer, BenchmarkItem
from src.judge import run_judge as run_judge_module

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "data/benchmark/benchmark_queries.seed.jsonl"


def _benchmark_item(item_id: str = "Q001") -> BenchmarkItem:
    for line in SEED_PATH.read_text(encoding="utf-8").splitlines():
        item = BenchmarkItem.model_validate(json.loads(line))
        if item.id == item_id:
            return item
    raise AssertionError(f"Missing benchmark item {item_id}")


def _answer(item: BenchmarkItem, *, run_index: int = 0) -> AgentAnswer:
    return AgentAnswer(
        id=item.id,
        run_index=run_index,
        raw_response="As an AI language model from OpenAI, I recommend carotid endarterectomy.",
        blinded_response="[redacted], I recommend carotid endarterectomy.",
        gate_fired=False,
        routed_guidelines=["ESVS_Carotid_2023"],
        recommendation="Recommend carotid endarterectomy.",
        citations=[],
        retrieved_passages=[],
        uncertainty_statements=[],
        latency_seconds=0.4,
        model_meta={"synthesis_model": "vascular-pipe", "endpoint": "/api/chat/completions"},
    )


def _valid_judgment_text() -> str:
    return json.dumps(
        {
            "dimensions": {
                "clinical_correctness": {"score": 3, "rationale": "Matches the gold key."},
                "citation_support": {
                    "score": 2,
                    "rationale": "No unsupported citations were found.",
                    "unsupported_citations": [],
                },
                "completeness": {"score": 2, "rationale": "Minor omissions only."},
                "uncertainty_handling": {"score": 3, "rationale": "Appropriate caveats included."},
            },
            "hallucination": {"present": False, "unsupported_claims": [], "count": 0},
            "safety_critical_error": {"flag": False, "reason": None},
            "overall_comment": "The answer is acceptable.",
        }
    )


class FakeJudgeClient:
    def __init__(self, texts: list[str]) -> None:
        self.texts = texts
        self.calls: list[dict] = []

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 1500,
        json_mode: bool = False,
    ) -> LLMResult:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "json_mode": json_mode,
            }
        )
        text = self.texts.pop(0)
        return LLMResult(
            text=text,
            request_id=f"req-{len(self.calls)}",
            latency_s=0.2,
            tokens_in=10,
            tokens_out=20,
            raw={"text": text},
        )


class FailingJudgeClient:
    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 1500,
        json_mode: bool = False,
    ) -> LLMResult:
        raise RuntimeError("synthetic judge transport failure")


def test_render_judge_prompt_uses_blinded_response() -> None:
    item = _benchmark_item()
    answer = _answer(item)
    prompts = run_judge_module.load_judge_prompts()

    rendered = run_judge_module.render_judge_user_prompt(
        item=item,
        answer=answer,
        user_template=prompts.user_template,
    )

    assert "[redacted], I recommend carotid endarterectomy." in rendered
    assert "As an AI language model from OpenAI" not in rendered
    assert item.gold.answer_key in rendered


def test_run_judges_repairs_once_and_then_uses_cache(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    item = _benchmark_item()
    answer = _answer(item)
    client = FakeJudgeClient(["{not-json", _valid_judgment_text()])
    config = {
        "judges": [{"name": "gpt-5", "temperature": 0, "max_tokens": 900}],
        "run": {"runs_per_judge": 1, "cache": True},
        "paths": {"metrics_dir": str(tmp_path / "metrics")},
    }
    prompts = run_judge_module.load_judge_prompts()

    rows_first = run_judge_module.run_judges(
        config=config,
        items=[item],
        answers=[answer],
        prompts=prompts,
        clients={"gpt-5": client},
        failures_path=tmp_path / "metrics" / "judge_failures.jsonl",
    )
    rows_second = run_judge_module.run_judges(
        config=config,
        items=[item],
        answers=[answer],
        prompts=prompts,
        clients={"gpt-5": client},
        failures_path=tmp_path / "metrics" / "judge_failures.jsonl",
    )

    assert len(client.calls) == 2
    assert "Previous invalid response" in client.calls[1]["user"]
    assert rows_first == rows_second
    assert rows_first[0]["judge"] == "gpt-5"
    assert rows_first[0]["_meta"]["answer_run_index"] == 0


def test_run_judges_logs_failure_after_failed_repair(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    item = _benchmark_item()
    answer = _answer(item, run_index=1)
    client = FakeJudgeClient(["{not-json", "{still-bad"])
    config = {
        "judges": [{"name": "claude-opus-4-8", "temperature": 0, "max_tokens": 900}],
        "run": {"runs_per_judge": 1, "cache": True},
        "paths": {"metrics_dir": str(tmp_path / "metrics")},
    }
    prompts = run_judge_module.load_judge_prompts()
    failures_path = tmp_path / "metrics" / "judge_failures.jsonl"

    rows = run_judge_module.run_judges(
        config=config,
        items=[item],
        answers=[answer],
        prompts=prompts,
        clients={"claude-opus-4-8": client},
        failures_path=failures_path,
    )

    assert rows == []
    failure_rows = [
        json.loads(line) for line in failures_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(failure_rows) == 1
    assert failure_rows[0]["query_id"] == item.id
    assert failure_rows[0]["answer_run_index"] == 1
    assert failure_rows[0]["judge"] == "claude-opus-4-8"
    assert failure_rows[0]["initial_raw_output"] == "{not-json"
    assert failure_rows[0]["repair_raw_output"] == "{still-bad"


def test_run_judges_logs_failure_after_call_exception(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    item = _benchmark_item()
    answer = _answer(item)
    config = {
        "judges": [{"name": "o3", "temperature": 0, "max_tokens": 900}],
        "run": {"runs_per_judge": 1, "cache": True},
        "paths": {"metrics_dir": str(tmp_path / "metrics")},
    }
    prompts = run_judge_module.load_judge_prompts()
    failures_path = tmp_path / "metrics" / "judge_failures.jsonl"

    rows = run_judge_module.run_judges(
        config=config,
        items=[item],
        answers=[answer],
        prompts=prompts,
        clients={"o3": FailingJudgeClient()},
        failures_path=failures_path,
    )

    assert rows == []
    failure_rows = [
        json.loads(line) for line in failures_path.read_text(encoding="utf-8").splitlines()
    ]
    assert len(failure_rows) == 1
    assert failure_rows[0]["query_id"] == item.id
    assert failure_rows[0]["judge"] == "o3"
    assert "transport failure" in failure_rows[0]["initial_error"]
    assert failure_rows[0]["repair_error"] is None


def test_run_judge_cli_writes_jsonl(tmp_path: Path, monkeypatch) -> None:
    item = _benchmark_item()
    answer = _answer(item)

    benchmark_path = tmp_path / "benchmark.jsonl"
    benchmark_path.write_text(SEED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    answers_path = tmp_path / "answers.jsonl"
    answers_path.write_text(
        json.dumps(answer.model_dump(by_alias=True), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "judgments.jsonl"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "judges:",
                "  - name: gpt-5",
                "    provider: openai",
                "    temperature: 0",
                "    max_tokens: 900",
                "run:",
                "  runs_per_judge: 1",
                "  cache: true",
                "paths:",
                f"  benchmark: {benchmark_path}",
                f"  answers: {answers_path}",
                f"  judgments: {output_path}",
                f"  metrics_dir: {tmp_path / 'metrics'}",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        run_judge_module,
        "build_judge_clients",
        lambda config: {"gpt-5": FakeJudgeClient([_valid_judgment_text()])},
    )

    exit_code = run_judge_module.main(["--config", str(config_path), "--item-id", item.id])

    assert exit_code == 0
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["query_id"] == item.id
    assert rows[0]["judge"] == "gpt-5"


def test_parse_judgment_normalises_wrapped_payload_and_citation_ids() -> None:
    result = LLMResult(
        text=json.dumps(
            {
                "response": {
                    "dimensions": json.dumps(
                        {
                            "clinical_correctness": {
                                "score": 3,
                                "rationale": "Matches the gold key.",
                            },
                            "citation_support": {
                                "score": 1,
                                "rationale": "IDs came back as ints.",
                                "unsupported_citations": [61, 50, 100],
                            },
                            "completeness": {
                                "score": 2,
                                "rationale": "Mostly complete.",
                            },
                            "uncertainty_handling": {
                                "score": 3,
                                "rationale": "Appropriate refusal.",
                            },
                        }
                    ),
                    "hallucination": {
                        "present": False,
                        "unsupported_claims": [],
                        "count": 0,
                    },
                    "safety_critical_error": {"flag": False, "reason": None},
                    "overall_comment": "Valid after normalization.",
                }
            }
        ),
        request_id="req-normalized",
        latency_s=0.3,
        tokens_in=10,
        tokens_out=20,
        raw={},
    )

    judgment = run_judge_module._parse_judgment(
        result=result,
        query_id="Q012",
        answer_run_index=0,
        judge_name="claude-opus-4-7",
        run_index=2,
    )

    assert judgment.query_id == "Q012"
    assert judgment.dimensions.citation_support.unsupported_citations == ["61", "50", "100"]


def test_parse_judgment_normalises_nested_wrapper_shapes() -> None:
    result = LLMResult(
        text=json.dumps(
            {
                "$STRUCTURED_OUTPUT": {
                    "dimensions": {
                        "clinical_correctness": {
                            "score": 1,
                            "rationale": "Missed the main recommendation.",
                        },
                        "citation_support": {
                            "score": 2,
                            "rationale": "No unsupported citations.",
                            "unsupported_citations": [],
                        },
                        "completeness": {
                            "score": 0,
                            "rationale": "Omitted key next steps.",
                        },
                        "uncertainty_handling": {
                            "score": 2,
                            "rationale": "Declined safely but vaguely.",
                        },
                    },
                    "hallucination": {
                        "present": False,
                        "unsupported_claims": [],
                        "count": 0,
                    },
                    "safety_critical_error": {"flag": False, "reason": None},
                    "overall_comment": "Valid after wrapper normalization.",
                }
            }
        ),
        request_id="req-structured",
        latency_s=0.3,
        tokens_in=10,
        tokens_out=20,
        raw={},
    )
    judgment = run_judge_module._parse_judgment(
        result=result,
        query_id="Q016",
        answer_run_index=0,
        judge_name="claude-opus-4-7",
        run_index=0,
    )

    assert judgment.query_id == "Q016"
    assert judgment.overall_comment == "Valid after wrapper normalization."
    assert judgment.dimensions.completeness.score == 0
