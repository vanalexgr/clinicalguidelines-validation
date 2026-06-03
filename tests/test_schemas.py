from __future__ import annotations

import copy
import json
import warnings
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.common.schemas import BenchmarkItem

SEED_PATH = Path("data/benchmark/benchmark_queries.seed.jsonl")


def _seed_items() -> list[dict]:
    return [json.loads(line) for line in SEED_PATH.read_text(encoding="utf-8").splitlines() if line]


def _item(item_id: str) -> dict:
    for item in _seed_items():
        if item["id"] == item_id:
            return copy.deepcopy(item)
    raise AssertionError(f"Missing seed item: {item_id}")


def test_seed_items_round_trip_through_benchmark_item() -> None:
    payloads = _seed_items()
    assert len(payloads) == 16

    for payload in payloads:
        item = BenchmarkItem.model_validate(payload)
        dumped = item.model_dump(by_alias=True)
        reparsed = BenchmarkItem.model_validate(dumped)
        assert reparsed.model_dump(by_alias=True) == dumped


def test_gold_requires_parameters_when_gate_expected_is_fire() -> None:
    payload = _item("Q004")
    payload["gold"]["required_parameters"] = []

    with pytest.raises(ValidationError):
        BenchmarkItem.model_validate(payload)


def test_gold_warns_when_acceptable_refusal_without_na_gate() -> None:
    payload = _item("Q012")
    payload["gold"]["gate_expected"] = "suppress"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        item = BenchmarkItem.model_validate(payload)

    assert item.gold.acceptable_refusal is True
    assert any("gold.acceptable_refusal is true" in str(warning.message) for warning in caught)


def test_underspecified_items_require_gate_to_fire() -> None:
    payload = _item("Q004")
    payload["gold"]["gate_expected"] = "suppress"

    with pytest.raises(ValidationError):
        BenchmarkItem.model_validate(payload)


def test_should_refuse_items_require_acceptable_refusal() -> None:
    payload = _item("Q012")
    payload["gold"]["acceptable_refusal"] = False

    with pytest.raises(ValidationError):
        BenchmarkItem.model_validate(payload)
