# Benchmark v2 Phase 2 Rerun Record (2026-06-06)

This document records the post-fix rerun of Tasks 4-7 from `CODEX_BENCHMARK_RUN.md` after the
answer collection bug was fixed in `src/agent_client/http_client.py`.

The later Phase 3 rerun, which applied metric fixes and reran answers + judges against the
deployed gate update, is documented in `docs/benchmark_v2_phase3_rerun_2026-06-06.md`.

## Scope

The goal of this rerun was narrower than the original benchmark-v2 build:

- regenerate all `55` answers with the fixed retrieval path
- rerun the full `2 judges x 3 runs` judgment matrix
- rebuild the report and summary from the corrected answer set
- document what improved and what still failed

## Answer Generation

Command used:

```bash
python3 -m src.runner.generate_answers --config config/config.yaml
```

Observed answer-side state after regeneration:

- `55/55` answers were written to `outputs/answers/answers.jsonl`
- the canned refusal defect was reduced but not eliminated
- `8` answers still returned the canned out-of-context text

Spot-check result:

```text
Total: 55
Canned refusals (should be 0): 8
gate_fired=True: 55, has clarifications: 35
canned_ids: [Q001, Q012, Q106, Q107, Q122, Q210, Q211, Q212]
```

Interpretation:

- the retrieval bug fix materially changed downstream behavior
- the answer pipeline is still not fully correct because five in-scope items
  (`Q001`, `Q012`, `Q106`, `Q107`, `Q122`) continue to collapse into the canned refusal
- all four refusal/out-of-scope items still behaved as refusals, but the system also still
  over-refuses on some in-scope prompts

## Judge Execution

### First rerun attempt

The initial judge replay progressed but stopped short because OpenAI `o3` quota was exhausted.
At the interruption point the cache had been reconstructed to `287` valid judgments, with the
remaining gap concentrated in `o3` runs.

The failure log at `outputs/metrics/judge_failures.jsonl` showed repeated:

```text
Error code: 429 ... code: 'insufficient_quota'
```

### Final rerun after quota top-up

After OpenAI quota was replenished, the remaining `o3` slots were rerun successfully.

Canonical judge budgets were raised in `config/config.yaml` to match the successful rerun:

- `claude-opus-4-7 max_tokens: 1800`
- `o3 max_tokens: 3000`

Final status:

```bash
python3 -m src.judge.run_judge --config config/config.yaml --status
Missing runs (0):
```

Final matrix:

- `55` processed items
- `330` judgment rows
- `2` judges present: `claude-opus-4-7`, `o3`
- `3` runs per judge

## Report Rebuild

Commands used:

```bash
python3 scripts/citation_error_breakdown.py
python3 -m src.report.build_report --config config/config.yaml
```

Outcome:

- report rebuilt successfully at `outputs/report/report.md`
- summary refreshed at `outputs/metrics/summary.json`
- citation breakdown files refreshed successfully

## Final Results

Values below are from the final `outputs/metrics/summary.json` and `outputs/report/report.md`.

| metric | value |
|---|---|
| mode | `strict_full` |
| processed_items | `55` |
| judgment_rows | `330` |
| pass_rate | `7 / 55` (`12.7%`) |
| routing exact match | `78.2%` |
| routing micro F1 | `89.7%` |
| gate sensitivity | `100.0%` |
| gate specificity | `48.6%` |
| gate false_negative | `0` |
| over_interrogation_rate | `58.8%` |
| krippendorff_alpha | `0.6079` |
| hallucination_rate | `58.2%` |
| safety_flag_rate | `21.8%` |
| mean answer latency | `21.757s` |
| median answer latency | `21.306s` |

Compared with the earlier full run documented in `docs/benchmark_v2_run_2026-06-06.md`, the key
changes were:

- pass rate improved from `7.3%` to `12.7%`
- routing exact match improved from `7.3%` to `78.2%`
- gate specificity improved from `0.0%` to `48.6%`
- the full `330`-row matrix was restored after the quota interruption

## Interpretation

### 1. The retrieval fix helped, but the answer bug is not fully closed

The rerun materially improved routing, pass rate, and gate specificity. That confirms the main
retrieval-path bug in `src/agent_client/http_client.py` was real and consequential.

However, the answer generator still produced `8` canned refusals, including five in-scope
benchmark items. The benchmark therefore still contains a live residual defect in the answer path.

### 2. Gate sensitivity is now strong, but specificity remains mediocre

The rerun preserved `false_negative = 0` and `sensitivity = 1.0`, which is the important safety
property for underspecified prompts. The tradeoff is persistent over-interrogation:

- `19` false positives remain
- `specificity` is only `48.6%`

### 3. Judge completion is now structurally sound

The full `330`-row matrix was eventually completed after:

- wrapper/repair normalization in `src/judge/run_judge.py`
- correct OpenAI reasoning-model request handling in `src/common/llm.py`
- a quota top-up for the missing `o3` calls

The pushed repo state should now reproduce the full matrix using the canonical config.

## Files to Inspect

- `src/agent_client/http_client.py`
- `config/config.yaml`
- `outputs/report/report.md` (generated, ignored)
- `outputs/metrics/summary.json` (generated, ignored)
- `docs/benchmark_v2_run_2026-06-06.md`
- `docs/benchmark_v2_phase2_rerun_2026-06-06.md`

## Remaining Open Issue

The next engineering target is not the judge layer. It is the residual answer-generation failure
that still emits canned refusals on some in-scope items:

- `Q001`
- `Q012`
- `Q106`
- `Q107`
- `Q122`

Until those are corrected, the benchmark can reproduce a complete judge matrix, but it still does
not reflect the best attainable answer quality of the underlying system.
