# Benchmark v2 Run Record (2026-06-06)

This document captures the repo state, pipeline changes, benchmark composition, execution
procedure, recovery steps, and final outcomes for the full Benchmark v2 run completed on
2026-06-06.

## Scope

The objective was to expand the benchmark from the original 16 synthetic items to a 55-item
evaluation set, run the full answer and judge pipeline with the live Context Gate enabled, and
regenerate the metrics and manuscript-style report from a complete `55 x 2 judges x 3 runs = 330`
judgment matrix.

## Benchmark Composition

Benchmark v2 is stored at `data/benchmark/benchmark_queries.v2.jsonl` and contains:

| query_type | count | notes |
|---|---:|---|
| `A_knowledge` | 8 | includes new terminology / framework items `Q200–Q205` |
| `B_complete_case` | 17 | answerable full cases |
| `C_underspecified` | 14 | gate-sensitive incomplete cases |
| `D_followup` | 2 | follow-up clarification items |
| `E_multiguideline` | 10 | multi-guideline routing cases |
| `G_should_refuse` | 4 | out-of-scope refusal items including `Q210–Q212` |

New benchmark assets added during this cycle:

- `data/benchmark/vignettes_30.jsonl`
- `data/benchmark/vignettes_knowledge.jsonl`
- `data/benchmark/vignettes_refuse.jsonl`
- `data/benchmark/benchmark_queries.v2.jsonl`

## Repository Changes Included in This Push

### Data and configuration

- `config/config.yaml`
  - benchmark path now targets `benchmark_queries.v2.jsonl`
  - judge configuration remains `claude-opus-4-7` and `o3`
  - live gate mode remains enabled via `pre_retrieval_mode: true`
- `data/benchmark/benchmark_queries.seed.jsonl`
  - seed benchmark refreshed to align with the newer benchmark and evaluation flow
- `data/annotation/hallucination_overrides.jsonl`
  - machine-readable hallucination review overrides for report integration

### Agent/runtime

- `src/agent_client/http_client.py`
  - expanded OpenWebUI/Laravel handling for live gate interaction and normalized answer capture
- `src/common/io.py`
  - supporting IO adjustments for the expanded pipeline
- `src/common/llm.py`
  - OpenAI reasoning-model request handling uses `max_completion_tokens` for `o3`
- `src/common/schemas.py`
  - schema hardening for citation support normalization
- `src/corpus/index.py`
  - recommendation matching / normalization work needed by citation verification

### Judge pipeline

- `src/judge/run_judge.py`
  - normalizes malformed judge wrapper payloads such as `response`, `$JSON`,
    `$STRUCTURED_OUTPUT`, `parameter`, and stray single-key objects
  - flattens nested `dimensions` payloads returned by repair calls
  - logs judge call failures without aborting the full batch
  - strengthened repair prompt to require exact top-level keys and shape
- `scripts/reconstruct_judgments.py`
  - cache-to-JSONL reconstruction helper for recovery scenarios

### Metrics and reporting

- `src/metrics/citation_breakdown.py`
- `src/metrics/reclassification.py`
- `src/report/build_report.py`
- `scripts/citation_error_breakdown.py`
- `scripts/validate_benchmark_completeness.py`

These changes integrate citation-tier reporting, hallucination reclassification, and the
extended report sections (§8–§15) into the main reporting path.

### Tests and validation coverage

New or expanded test coverage includes:

- `tests/test_run_judge.py`
- `tests/test_judge_status.py`
- `tests/test_benchmark_completeness.py`
- `tests/test_citation_breakdown.py`
- `tests/test_pipeline_e2e.py`
- `tests/test_reclassification.py`
- `tests/test_report_breakdown.py`
- updated `tests/test_corpus_index.py`
- updated `tests/test_report.py`

## Execution Record

### 1. Benchmark validation

Commands used:

```bash
python3 -m src.benchmark.validate_benchmark data/benchmark/benchmark_queries.v2.jsonl
python3 scripts/validate_benchmark_completeness.py data/benchmark/benchmark_queries.v2.jsonl
```

Outcome:

- schema validation passed
- completeness validation passed for all 55 items
- `verified: true` count remains `0/55`, so reports stay preliminary

### 2. Answer generation

Command used:

```bash
python3 -m src.runner.generate_answers --config config/config.yaml
```

Outcome:

- `55/55` answers written to `outputs/answers/answers.jsonl`
- live gate checkpoint engaged throughout the run
- the resulting agent behavior showed universal over-interrogation on non-gate items

### 3. Judge execution and recovery

Command used:

```bash
python3 -m src.judge.run_judge --config config/config.yaml
```

Observed issues during the first full pass:

- malformed judge JSON from `claude-opus-4-7`
  - missing `dimensions`
  - nested `dimensions.dimensions`
  - wrapper objects such as `{"{": ...}` and `{"$STRUCTURED_OUTPUT": ...}`
- intermittent empty `o3` outputs on some runs
- the batch writer only flushes `judgments.jsonl` at the end, so partial success had to be
  reconstructed from cache during recovery

Recovery procedure:

1. normalize malformed judge payloads in `src/judge/run_judge.py`
2. harden the repair prompt to require the exact JSON schema
3. rebuild `outputs/judgments/judgments.jsonl` directly from cached valid judgment objects
4. replay missing items one-by-one using `--item-id`, with cache reconstruction between passes

Final status after recovery:

```bash
python3 -m src.judge.run_judge --config config/config.yaml --status
Missing runs (0):
```

Final judgment matrix:

- `330` judgment rows
- `55` items
- `2` judges
- `3` runs per judge

### 4. Metrics and report refresh

Commands used:

```bash
python3 -m src.metrics.deterministic --config config/config.yaml
python3 -m src.metrics.pass_fail --config config/config.yaml
python3 -m src.metrics.agreement --config config/config.yaml
python3 -m src.metrics.aggregate --config config/config.yaml
python3 -m src.report.build_report --config config/config.yaml
```

Outcome:

- report rebuilt successfully from the full matrix
- final report path: `outputs/report/report.md`
- final summary path: `outputs/metrics/summary.json`

## Final Results

Values below are taken from `outputs/metrics/summary.json` after the final rebuild.

| metric | value |
|---|---|
| mode | `strict_full` |
| processed_items | `55` |
| benchmark_total_items | `55` |
| judgment_rows | `330` |
| pass_rate | `4 / 55` (`7.3%`) |
| routing exact match | `7.3%` |
| gate sensitivity | `100.0%` |
| gate specificity | `0.0%` |
| over_interrogation_rate | `100.0%` |
| citation existence accuracy | `100.0%` |
| reported hallucination rate | `78.2%` |
| adjusted hallucination rate | `0.0%` |
| safety flag rate | `27.3%` |
| krippendorff_alpha | `-0.0417` |

Additional report-level observations from `outputs/report/report.md`:

- mean answer latency: `39.837s`
- median latency: `36.724s`
- safety-critical items in the processed benchmark: `21`
- all four `G_should_refuse` items passed
- all other query-type buckets had `0` passes in this run

## Interpretation and Caveats

### 1. The report remains preliminary

All 55 benchmark items are still marked `verified: false`. The quantitative outputs are usable for
internal iteration, but not yet for a final manuscript claim set without clinical sign-off.

### 2. Gate behavior is the dominant failure mode

The live Context Gate fired on all non-refusal benchmark items. That produced:

- perfect gate sensitivity on underspecified items
- zero specificity on answerable items
- widespread routing and completeness failure downstream

### 3. Citation existence is now structurally clean

The deterministic citation existence metric reached `100.0%`, and the breakdown pipeline reports no
remaining real citation-support errors in this benchmark state.

### 4. Raw hallucination flags overstate model failure

The breakdown and reclassification layers now separate artefact-only flags from real clinical
errors. In this run:

- raw judge hallucination rate: `78.2%`
- adjusted real-error hallucination rate: `0.0%`

This is a reporting improvement, not proof that the system is clinically acceptable. The dominant
practical failure remains refusal / non-answer behavior on answerable items.

## Files to Inspect After Checkout

- `README.md`
- `STATUS.md`
- `CODEX_PLAN.md`
- `CODEX_BENCHMARK_RUN.md`
- `outputs/report/report.md` (generated, not committed because `outputs/**` is ignored)
- `outputs/metrics/summary.json` (generated, not committed because `outputs/**` is ignored)

## Reproduction Notes

After checkout and environment setup:

```bash
python3 -m src.benchmark.validate_benchmark data/benchmark/benchmark_queries.v2.jsonl
python3 scripts/validate_benchmark_completeness.py data/benchmark/benchmark_queries.v2.jsonl
python3 -m src.runner.generate_answers --config config/config.yaml
python3 -m src.judge.run_judge --config config/config.yaml
python3 -m src.metrics.deterministic --config config/config.yaml
python3 -m src.metrics.pass_fail --config config/config.yaml
python3 -m src.metrics.agreement --config config/config.yaml
python3 -m src.metrics.aggregate --config config/config.yaml
python3 -m src.report.build_report --config config/config.yaml
```

If a judge run crashes after partial cache population, reconstruct first:

```bash
python3 scripts/reconstruct_judgments.py
python3 -m src.judge.run_judge --config config/config.yaml --status
```
