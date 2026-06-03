from __future__ import annotations

import json
from pathlib import Path

from src.agent_client.base import blind_text
from src.agent_client.http_client import HttpAgentClient
from src.agent_client.http_client import main as http_client_main
from src.common.schemas import BenchmarkItem

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "data/benchmark/benchmark_queries.seed.jsonl"


def _seed_item(item_id: str = "Q003") -> BenchmarkItem:
    for line in SEED_PATH.read_text(encoding="utf-8").splitlines():
        item = BenchmarkItem.model_validate(json.loads(line))
        if item.id == item_id:
            return item
    raise AssertionError(f"Missing benchmark item {item_id}")


def test_http_client_normalizes_openwebui_style_response() -> None:
    item = _seed_item()

    def fake_transport(url, payload, headers, timeout):  # type: ignore[no-untyped-def]
        assert url == "https://chat.clinicalguidelines.io/api/chat/completions"
        assert payload["stream"] is False
        assert payload["messages"][0]["role"] == "user"
        assert headers["Authorization"] == "Bearer test-key"
        return {
            "model": "vascular-pipe",
            "choices": [
                {
                    "message": {
                        "content": "As an AI language model, I recommend carotid endarterectomy."
                    }
                }
            ],
            "gate_fired": False,
            "routed_guidelines": ["ESVS_Carotid_2023"],
            "citations": [
                {
                    "rec_id": "CAR-EXAMPLE",
                    "class": "I",
                    "level": "A",
                    "guideline": "ESVS_Carotid_2023",
                    "passage": "CEA is recommended.",
                }
            ],
            "sources": [
                {
                    "id": "chunk-1",
                    "guideline": "ESVS_Carotid_2023",
                    "text": "CEA is recommended.",
                }
            ],
            "uncertainty_statements": ["Evidence is limited in discordant laterality cases."],
        }

    client = HttpAgentClient(
        {
            "base_url": "https://chat.clinicalguidelines.io",
            "api_key": "test-key",
            "endpoint_path": "/api/chat/completions",
            "request_template": {"model": "vascular-pipe"},
            "timeout_seconds": 5,
        },
        transport=fake_transport,
    )

    answer = client.generate_answer(item, run_index=2)

    assert answer.id == item.id
    assert answer.run_index == 2
    assert "carotid endarterectomy" in answer.raw_response.lower()
    assert answer.gate_fired is False
    assert answer.routed_guidelines == ["ESVS_Carotid_2023"]
    assert answer.citations[0].rec_id == "CAR-EXAMPLE"
    assert answer.retrieved_passages[0].chunk_id == "chunk-1"
    assert answer.model_meta["synthesis_model"] == "vascular-pipe"


def test_blind_text_redacts_model_identifiers() -> None:
    blinded = blind_text("As an AI language model from OpenAI, I am Claude-like but not GPT-5.")
    assert "OpenAI" not in blinded
    assert "GPT-5" not in blinded
    assert "Claude" not in blinded
    assert "[redacted]" in blinded


def test_http_client_cli_dry_run_prints_raw_response(tmp_path: Path, monkeypatch, capsys) -> None:
    benchmark_path = tmp_path / "benchmark.jsonl"
    benchmark_path.write_text(SEED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
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
                "paths:",
                f"  benchmark: {benchmark_path}",
            ]
        ),
        encoding="utf-8",
    )

    def fake_dry_run(self, item):  # type: ignore[no-untyped-def]
        from src.agent_client.base import DryRunResult

        return DryRunResult(
            request_payload={"messages": [{"role": "user", "content": item.turns[0].content}]},
            raw_response={"answer": "ok"},
            latency_s=0.25,
        )

    monkeypatch.setattr(HttpAgentClient, "dry_run", fake_dry_run)

    exit_code = http_client_main(["--config", str(config_path), "--dry-run", "--item-id", "Q001"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"raw_response": {' in captured.out
    assert '"answer": "ok"' in captured.out
