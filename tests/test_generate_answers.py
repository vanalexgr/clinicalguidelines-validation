from __future__ import annotations

import json
from pathlib import Path

from src.agent_client.base import AgentClient, DryRunResult
from src.common.schemas import AgentAnswer, BenchmarkItem
from src.runner import generate_answers as generate_answers_module

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "data/benchmark/benchmark_queries.seed.jsonl"


def _benchmark_item() -> BenchmarkItem:
    line = SEED_PATH.read_text(encoding="utf-8").splitlines()[0]
    return BenchmarkItem.model_validate(json.loads(line))


class FakeAgentClient(AgentClient):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def cache_model_id(self) -> str:
        return "fake-agent"

    def generate_answer(self, item: BenchmarkItem, *, run_index: int = 0) -> AgentAnswer:
        self.calls += 1
        return AgentAnswer(
            id=item.id,
            run_index=run_index,
            raw_response="As an AI language model from OpenAI, here is the answer.",
            gate_fired=False,
            recommendation="As an AI language model from OpenAI, here is the answer.",
            latency_seconds=0.5,
            model_meta={"synthesis_model": "fake-agent", "endpoint": "/fake"},
        )

    def dry_run(self, item: BenchmarkItem) -> DryRunResult:
        return DryRunResult(
            request_payload={"message": item.turns[-1].content},
            raw_response={"answer": "ok"},
            latency_s=0.1,
        )


def test_generate_answers_uses_cache_and_blinds_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    client = FakeAgentClient()
    item = _benchmark_item()
    config = {
        "run": {
            "cache": True,
            "blind_answers": True,
            "runs_per_agent": 1,
        }
    }

    rows_first = generate_answers_module.generate_answers(
        config=config,
        items=[item],
        client=client,
    )
    rows_second = generate_answers_module.generate_answers(
        config=config,
        items=[item],
        client=client,
    )

    assert client.calls == 1
    assert rows_first == rows_second
    assert rows_first[0]["raw_response"].startswith("As an AI language model")
    assert "[redacted]" in rows_first[0]["blinded_response"]
    assert "OpenAI" not in rows_first[0]["blinded_response"]


def test_generate_answers_cli_dry_run_uses_client_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    benchmark_path = tmp_path / "benchmark.jsonl"
    benchmark_path.write_text(SEED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    output_path = tmp_path / "answers.jsonl"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  client: http",
                "  base_url: https://chat.clinicalguidelines.io",
                "  endpoint_path: /api/chat/completions",
                "  api_key: test-key",
                "  timeout_seconds: 5",
                "  request_template:",
                "    model: vascular-pipe",
                "run:",
                "  cache: true",
                "  blind_answers: true",
                "  runs_per_agent: 1",
                "paths:",
                f"  benchmark: {benchmark_path}",
                f"  answers: {output_path}",
            ]
        ),
        encoding="utf-8",
    )

    class FakeHttpClient(FakeAgentClient):
        pass

    monkeypatch.setattr(
        generate_answers_module,
        "build_agent_client",
        lambda cfg: FakeHttpClient(),
    )

    exit_code = generate_answers_module.main(
        ["--config", str(config_path), "--dry-run", "--item-id", "Q001"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"raw_response": {' in captured.out
    assert '"answer": "ok"' in captured.out


def test_generate_answers_cli_writes_jsonl(tmp_path: Path, monkeypatch) -> None:
    benchmark_path = tmp_path / "benchmark.jsonl"
    benchmark_path.write_text(SEED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    output_path = tmp_path / "answers.jsonl"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  client: http",
                "  base_url: https://chat.clinicalguidelines.io",
                "  endpoint_path: /api/chat/completions",
                "  api_key: test-key",
                "  timeout_seconds: 5",
                "  request_template:",
                "    model: vascular-pipe",
                "run:",
                "  cache: true",
                "  blind_answers: true",
                "  runs_per_agent: 1",
                "paths:",
                f"  benchmark: {benchmark_path}",
                f"  answers: {output_path}",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        generate_answers_module,
        "build_agent_client",
        lambda cfg: FakeAgentClient(),
    )

    exit_code = generate_answers_module.main(["--config", str(config_path), "--item-id", "Q001"])

    assert exit_code == 0
    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["id"] == "Q001"
    assert rows[0]["run_index"] == 0
    assert "[redacted]" in rows[0]["blinded_response"]
