#!/usr/bin/env bash
set -euo pipefail

# Usage: ./scripts/run_all.sh
# Runs the answer generation, judge evaluation, and report build stages in order.

export $(grep -v '^#' .env | xargs) 2>/dev/null || true

BENCHMARK_PATH="data/benchmark/benchmark_queries.jsonl"
if [[ ! -f "${BENCHMARK_PATH}" && -f "data/benchmark/benchmark_queries.seed.jsonl" ]]; then
  BENCHMARK_PATH="data/benchmark/benchmark_queries.seed.jsonl"
fi

python3 -m src.runner.generate_answers --config config/config.yaml --benchmark-path "${BENCHMARK_PATH}"
python3 -m src.judge.run_judge --config config/config.yaml --benchmark-path "${BENCHMARK_PATH}"
python3 -m src.report.build_report --config config/config.yaml --benchmark-path "${BENCHMARK_PATH}"
