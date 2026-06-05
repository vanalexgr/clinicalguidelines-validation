from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "validate_benchmark_completeness.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_benchmark_completeness", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_benchmark_completeness_all_present(tmp_path: Path) -> None:
    module = _load_module()
    path = tmp_path / "benchmark.jsonl"
    row = {
        "id": "Q001",
        "query_type": "A_knowledge",
        "safety_critical": False,
        "turns": [{"role": "user", "content": "Question?"}],
        "gold": {
            "expected_guidelines": ["ESVS_Test_2024"],
            "gate_expected": "suppress",
            "required_parameters": [],
            "acceptable_refusal": False,
            "answer_key": "Answer.",
            "key_recommendations": [
                {"rec_id": "1", "class": "I", "level": "A", "summary": "Recommendation."}
            ],
            "notes": "",
        },
        "verified": False,
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert module.main([str(path)]) == 0


def test_benchmark_completeness_missing_answer_key(tmp_path: Path) -> None:
    module = _load_module()
    path = tmp_path / "benchmark.jsonl"
    row = {
        "id": "Q001",
        "query_type": "A_knowledge",
        "safety_critical": False,
        "turns": [{"role": "user", "content": "Question?"}],
        "gold": {
            "expected_guidelines": ["ESVS_Test_2024"],
            "gate_expected": "suppress",
            "required_parameters": [],
            "acceptable_refusal": False,
            "answer_key": "",
            "key_recommendations": [
                {"rec_id": "1", "class": "I", "level": "A", "summary": "Recommendation."}
            ],
            "notes": "",
        },
        "verified": False,
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert module.main([str(path)]) == 1


def test_benchmark_completeness_allows_empty_key_recommendations(tmp_path: Path) -> None:
    module = _load_module()
    path = tmp_path / "benchmark.jsonl"
    row = {
        "id": "Q001",
        "query_type": "A_knowledge",
        "safety_critical": False,
        "turns": [{"role": "user", "content": "Question?"}],
        "gold": {
            "expected_guidelines": ["ESVS_Test_2024"],
            "gate_expected": "suppress",
            "required_parameters": [],
            "acceptable_refusal": False,
            "answer_key": "Answer.",
            "key_recommendations": [],
            "notes": "",
        },
        "verified": False,
    }
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert module.main([str(path)]) == 0
