"""Validate benchmark JSONL files against schema.json and pydantic contracts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
from pydantic import ValidationError

from src.common.schemas import BenchmarkItem, QueryType

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BENCHMARK_PATH = REPO_ROOT / "data/benchmark/benchmark_queries.jsonl"
DEFAULT_SCHEMA_PATH = REPO_ROOT / "data/benchmark/schema.json"


@dataclass(slots=True)
class BenchmarkLineError:
    path: Path
    line_number: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line_number}: {self.message}"


class BenchmarkValidationError(ValueError):
    """Raised when one or more benchmark rows fail validation."""

    def __init__(self, errors: list[BenchmarkLineError]) -> None:
        self.errors = errors
        summary = "\n".join(str(error) for error in errors)
        super().__init__(summary)


@dataclass(slots=True)
class BenchmarkSummary:
    total_items: int
    verified_count: int
    verified_pct: float
    counts_by_type: dict[str, int]


def load_benchmark_validated(
    path: str | Path = DEFAULT_BENCHMARK_PATH,
    *,
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
) -> list[BenchmarkItem]:
    """Load a benchmark file and validate each row against JSON Schema and pydantic."""
    source = Path(path)
    schema = _load_schema(schema_path)
    validator = Draft7Validator(schema)

    items: list[BenchmarkItem] = []
    errors: list[BenchmarkLineError] = []

    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(
                    BenchmarkLineError(
                        path=source,
                        line_number=line_number,
                        message=f"invalid JSON: {exc.msg}",
                    )
                )
                continue

            row_errors = _validate_payload(
                payload=payload,
                validator=validator,
                path=source,
                line_number=line_number,
            )
            if row_errors:
                errors.extend(row_errors)
                continue

            try:
                items.append(BenchmarkItem.model_validate(payload))
            except ValidationError as exc:
                errors.append(
                    BenchmarkLineError(
                        path=source,
                        line_number=line_number,
                        message=_format_pydantic_error(exc),
                    )
                )

    if errors:
        raise BenchmarkValidationError(errors)
    return items


def summarize_benchmark(items: list[BenchmarkItem]) -> BenchmarkSummary:
    """Build a small validation summary for reporting."""
    counts = Counter(item.query_type.value for item in items)
    ordered_counts = {query_type.value: counts.get(query_type.value, 0) for query_type in QueryType}
    verified_count = sum(item.verified for item in items)
    total_items = len(items)
    verified_pct = (verified_count / total_items * 100.0) if total_items else 0.0
    return BenchmarkSummary(
        total_items=total_items,
        verified_count=verified_count,
        verified_pct=verified_pct,
        counts_by_type=ordered_counts,
    )


def format_summary(summary: BenchmarkSummary, *, path: str | Path) -> str:
    """Render a human-readable validation summary."""
    lines = [
        f"Benchmark validation OK: {path}",
        f"Total items: {summary.total_items}",
        f"Verified: {summary.verified_count}/{summary.total_items} ({summary.verified_pct:.1f}%)",
        "Counts by query type:",
    ]
    lines.extend(f"  {query_type}: {count}" for query_type, count in summary.counts_by_type.items())
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_BENCHMARK_PATH),
        help="Path to the benchmark JSONL file.",
    )
    parser.add_argument(
        "--schema",
        default=str(DEFAULT_SCHEMA_PATH),
        help="Path to the benchmark JSON Schema file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        items = load_benchmark_validated(args.path, schema_path=args.schema)
    except BenchmarkValidationError as exc:
        print(f"Benchmark validation FAILED: {args.path}", file=sys.stderr)
        for error in exc.errors:
            print(error, file=sys.stderr)
        return 1

    print(format_summary(summarize_benchmark(items), path=args.path))
    return 0


def _load_schema(path: str | Path) -> dict[str, Any]:
    schema_path = Path(path)
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _validate_payload(
    *,
    payload: Any,
    validator: Draft7Validator,
    path: Path,
    line_number: int,
) -> list[BenchmarkLineError]:
    errors: list[BenchmarkLineError] = []
    for error in sorted(validator.iter_errors(payload), key=_schema_error_sort_key):
        location = ".".join(str(part) for part in error.absolute_path) or "$"
        errors.append(
            BenchmarkLineError(
                path=path,
                line_number=line_number,
                message=f"{location}: {error.message}",
            )
        )
    return errors


def _schema_error_sort_key(error: Any) -> tuple[str, str]:
    location = ".".join(str(part) for part in error.absolute_path)
    return location, error.message


def _format_pydantic_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error["loc"]) or "$"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
