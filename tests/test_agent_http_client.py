"""Tests for src/agent_client/http_client.py — CGIO API normalisation."""
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


def _cgio_client(transport=None, **cfg_overrides) -> HttpAgentClient:  # type: ignore[no-untyped-def]
    cfg = {
        "base_url": "https://api.clinicalguidelines.io",
        "api_key": "test-key",
        "endpoint_path": "/api/v1/vascular-consult",
        "pre_retrieval_mode": True,
        "timeout_seconds": 5,
    }
    cfg.update(cfg_overrides)
    return HttpAgentClient(cfg, transport=transport)


def test_http_client_normalizes_standard_cgio_response() -> None:
    """Standard CGIO response (gate suppressed) maps correctly to AgentAnswer."""
    item = _seed_item("Q001")

    def fake_transport(url, payload, headers, timeout):  # type: ignore[no-untyped-def]
        assert "vascular-consult" in url
        assert payload["question"] == item.turns[-1].content
        assert payload.get("pre_retrieval_mode") is True
        assert headers["Authorization"] == "Bearer test-key"
        return {
            "result": "CEA is recommended within 14 days for symptomatic carotid stenosis.",
            "selected_guidelines": [
                {"key": "carotid_vertebral", "name": "Carotid & Vertebral", "score": 0.98}
            ],
            "citation_chunks": [
                {
                    "recommendation_id": "Rec 6.5.2",
                    "class": "I",
                    "level": "A",
                    "source_guideline": "ESVS_Carotid_2023",
                    "text": "CEA is recommended for symptomatic 50-99% stenosis within 14 days.",
                }
            ],
            "narrative_chunks": [
                {
                    "chunk_id": "carotid-chunk-001",
                    "source_guideline": "ESVS_Carotid_2023",
                    "content": "Early CEA reduces stroke risk after TIA.",
                }
            ],
            "gap_assessment": {"hasGuidelineGap": False, "uncoveredFacets": [], "gapSummary": None},
            "assets": [],
        }

    client = _cgio_client(transport=fake_transport)
    answer = client.generate_answer(item, run_index=0)

    assert answer.id == item.id
    assert answer.run_index == 0
    assert "CEA" in answer.raw_response
    assert answer.gate_fired is False
    assert answer.clarification_requested == []
    assert answer.routed_guidelines == ["ESVS_Carotid_2023"]
    assert answer.citations[0].rec_id == "6.5.2"   # "Rec " prefix stripped
    assert answer.citations[0].rec_id is not None
    assert answer.retrieved_passages[0].chunk_id == "carotid-chunk-001"
    assert answer.retrieved_passages[0].guideline == "ESVS_Carotid_2023"
    assert answer.uncertainty_statements == []


def test_http_client_normalizes_gate_fired_response() -> None:
    """pre_retrieval_mode gate-fired response maps gate_fired=True and clarification questions."""
    item = _seed_item("Q003")  # underspecified item

    def fake_transport(url, payload, headers, timeout):  # type: ignore[no-untyped-def]
        return {
            "phase": "awaiting_confirmation",
            "confirmation_message": (
                "To advise on carotid stenosis management I need: "
                "symptom status and exact stenosis degree."
            ),
            "soft_warn": False,
            "clarification_questions": [
                "Is the stenosis symptomatic or asymptomatic?",
                "What is the stenosis degree (%)?",
            ],
            "pre_retrieval_result": {
                "proceed": True,
                "guidelines": ["carotid_vertebral"],
            },
            "retrieval_payload": {
                "result": "",
                "selected_guidelines": [
                    {"key": "carotid_vertebral", "name": "Carotid & Vertebral", "score": 0.95}
                ],
                "citation_chunks": [],
                "narrative_chunks": [],
                "gap_assessment": {"hasGuidelineGap": False},
            },
        }

    client = _cgio_client(transport=fake_transport)
    answer = client.generate_answer(item, run_index=0)

    assert answer.gate_fired is True
    assert len(answer.clarification_requested) == 2
    assert "symptomatic" in answer.clarification_requested[0].lower()
    assert "symptom" in answer.raw_response.lower()
    # Guidelines still extracted from retrieval_payload
    assert answer.routed_guidelines == ["ESVS_Carotid_2023"]


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
                "  base_url: https://api.clinicalguidelines.io",
                "  endpoint_path: /api/v1/vascular-consult",
                "  api_key: test-key",
                "  timeout_seconds: 5",
                "  pre_retrieval_mode: true",
                "paths:",
                f"  benchmark: {benchmark_path}",
            ]
        ),
        encoding="utf-8",
    )

    def fake_dry_run(self, item):  # type: ignore[no-untyped-def]
        from src.agent_client.base import DryRunResult

        return DryRunResult(
            request_payload={"question": item.turns[-1].content},
            raw_response={"result": "ok"},
            latency_s=0.25,
        )

    monkeypatch.setattr(HttpAgentClient, "dry_run", fake_dry_run)

    exit_code = http_client_main(["--config", str(config_path), "--dry-run", "--item-id", "Q001"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert '"raw_response": {' in captured.out
    assert '"result": "ok"' in captured.out
