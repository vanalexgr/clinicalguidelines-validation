from __future__ import annotations

from pathlib import Path

import pytest

from src.common.io import Cache, load_benchmark, load_config, read_jsonl, write_jsonl


def test_jsonl_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    rows = [{"id": "Q001"}, {"id": "Q002", "value": 2}]

    write_jsonl(path, rows)

    assert list(read_jsonl(path)) == rows


def test_load_config_resolves_env_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_API_KEY", "secret-value")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "agent:",
                "  base_url_env: FAKE_BASE_URL",
                "  api_key_env: FAKE_API_KEY",
                "judges:",
                "  - provider: openai",
                "    api_key_env: FAKE_API_KEY",
            ]
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config["agent"]["base_url"] is None
    assert config["agent"]["api_key"] == "secret-value"
    assert config["judges"][0]["api_key"] == "secret-value"


def test_load_benchmark_includes_line_number_on_validation_error(tmp_path: Path) -> None:
    path = tmp_path / "benchmark.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"id":"Q001","query_type":"A_knowledge","safety_critical":false,'
                '"turns":[{"role":"user","content":"ok"}],'
                '"gold":{"expected_guidelines":[],"gate_expected":"suppress",'
                '"required_parameters":[],"acceptable_refusal":false,'
                '"answer_key":"key","key_recommendations":[],"notes":""},"verified":false}',
                '{"id":"Q002","query_type":"C_underspecified","safety_critical":true,'
                '"turns":[{"role":"user","content":"bad"}],'
                '"gold":{"expected_guidelines":[],"gate_expected":"suppress",'
                '"required_parameters":[],"acceptable_refusal":false,'
                '"answer_key":"key","key_recommendations":[],"notes":""},"verified":false}',
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        load_benchmark(path)

    assert f"{path}:2:" in str(exc_info.value)


def test_cache_hit_miss_and_stable_key(tmp_path: Path) -> None:
    cache = Cache(tmp_path / "cache")
    key = cache.key("judge", "Q001", "gpt-5", 0)

    assert cache.key("judge", "Q001", "gpt-5", 0) == key
    assert cache.get(key) is None

    payload = {"answer": "cached"}
    cache.set(key, payload)

    assert cache.get(key) == payload
