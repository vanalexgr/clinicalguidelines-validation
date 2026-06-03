from __future__ import annotations

import json
from pathlib import Path

from src.benchmark.validate_benchmark import (
    BenchmarkValidationError,
    load_benchmark_validated,
    main,
    summarize_benchmark,
)

SEED_PATH = Path("data/benchmark/benchmark_queries.seed.jsonl")
SCHEMA_PATH = Path("data/benchmark/schema.json")


def test_seed_file_validates_against_schema_and_models() -> None:
    items = load_benchmark_validated(SEED_PATH, schema_path=SCHEMA_PATH)

    assert len(items) == 16
    summary = summarize_benchmark(items)
    assert summary.total_items == 16
    assert summary.verified_count == 0
    assert summary.verified_pct == 0.0
    assert summary.counts_by_type["A_knowledge"] == 2
    assert summary.counts_by_type["B_complete_case"] == 5
    assert summary.counts_by_type["C_underspecified"] == 4
    assert summary.counts_by_type["D_followup"] == 2
    assert summary.counts_by_type["E_multiguideline"] == 2
    assert summary.counts_by_type["G_should_refuse"] == 1


def test_cli_reports_counts_and_verified_percentage(capsys) -> None:
    exit_code = main([str(SEED_PATH), "--schema", str(SCHEMA_PATH)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Benchmark validation OK" in captured.out
    assert "Total items: 16" in captured.out
    assert "Verified: 0/16 (0.0%)" in captured.out
    assert "A_knowledge: 2" in captured.out
    assert "G_should_refuse: 1" in captured.out
    assert captured.err == ""


def test_broken_item_reports_line_number(tmp_path: Path) -> None:
    broken_path = tmp_path / "broken.jsonl"
    seed_lines = SEED_PATH.read_text(encoding="utf-8").splitlines()
    broken_row = json.loads(seed_lines[1])
    del broken_row["turns"]
    broken_path.write_text(
        "\n".join([seed_lines[0], json.dumps(broken_row)]),
        encoding="utf-8",
    )

    try:
        load_benchmark_validated(broken_path, schema_path=SCHEMA_PATH)
    except BenchmarkValidationError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected benchmark validation to fail.")

    assert f"{broken_path}:2:" in message
    assert "'turns' is a required property" in message


def test_cli_returns_nonzero_for_invalid_benchmark(tmp_path: Path, capsys) -> None:
    broken_path = tmp_path / "broken.jsonl"
    broken_path.write_text(
        '{"id":"Q001","query_type":"A_knowledge","safety_critical":false}\n',
        encoding="utf-8",
    )

    exit_code = main([str(broken_path), "--schema", str(SCHEMA_PATH)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Benchmark validation FAILED" in captured.err
    assert f"{broken_path}:1:" in captured.err
