# ClinicalGuidelines.io — LLM-as-Judge Validation Pipeline

Internal validation harness that produces the quantitative evaluation for the architecture manuscript
*"A Multi-Guideline, Context-Aware Clinical Agent for Vascular Surgery Decision Support."* It runs a
benchmark query set through the ClinicalGuidelines.io agent, scores the answers with a calibrated
LLM-as-judge, and reports routing accuracy, Context Gate sensitivity/specificity, citation existence
accuracy, hallucination (unsupported-claim) rate, safety-critical discordance, and by-query-type
performance — as a near-term bridge before the full prospective multicenter study.

This is a **faithfulness and safety-behaviour benchmark**. It measures objective design claims —
corpus constraint, routing, citation grounding, gate behaviour, and the absence of fabricated
content. It deliberately does **not** score *clinical correctness*: a clinical case may admit several
defensible recommendations, so judging an answer against a single reference recommendation cannot
validly separate genuine error from legitimate clinical variation. Clinical acceptability is reserved
for the planned prospective study. `config/rubric.yaml` is the machine-readable rubric. The split
between *deterministic* metrics (routing, gate, citation existence) and *judge-dependent* metrics
(faithfulness, completeness, uncertainty handling, hallucination, safety) is the core defensibility
decision.

> Inter-rater reliability (Krippendorff's α) is computed over the four originally-elicited ordinal
> rating dimensions and is reported as a reliability statistic only; no composite clinical pass is
> derived from it.

## Current snapshot

The repo now includes Benchmark v2:

- `55` benchmark items in `data/benchmark/benchmark_queries.v2.jsonl`
- live Context Gate support in the agent client
- integrated citation-tier / hallucination reclassification reporting
- hardened judge recovery and cache reconstruction tooling
- calibrated judge token budgets in `config/config.yaml` that reproduce the completed `330`-row run

The latest completed rerun is documented in `docs/benchmark_v2_phase3_rerun_2026-06-06.md`.

## Pipeline (7 steps)
1. Benchmark query set with human-authored gold labels (`data/benchmark/`).
2. Generate answers with the agent (`src/runner/`).
3. LLM-as-judge evaluation — judge evaluates *support*, never answers the question (`src/judge/`).
4. Structured rubric — ordinal quality dimensions (Likert 0–3) + binary safety/hallucination
   (`config/rubric.yaml`). Clinical correctness is deliberately not scored.
5. Faithfulness and safety-behaviour metrics reported individually — routing accuracy, scope
   enforcement, gate sensitivity/specificity, citation existence accuracy, and unsupported-claim
   rate — plus a global safety flag (`src/metrics/pass_fail.py`). No composite clinical pass is applied.
6. Discordance review export for the clinical team (`src/discordance/`).
7. Paper-ready report, tables, plots (`src/report/`).

## Quickstart
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in keys + agent endpoint
cp data/benchmark/benchmark_queries.seed.jsonl data/benchmark/benchmark_queries.jsonl
python -m src.runner.generate_answers --config config/config.yaml --dry-run   # confirm agent API shape
bash scripts/run_all.sh
```

## Run records

- `docs/benchmark_v2_run_2026-06-06.md` — initial benchmark-v2 full-run record
- `docs/benchmark_v2_phase2_rerun_2026-06-06.md` — post-fix Phase 2 rerun with final `330` judgments
- `docs/benchmark_v2_phase3_rerun_2026-06-06.md` — metric-fix + single-pass rerun after deployed gate update

## Safety / data
No real patient data in the repo — benchmark items are synthetic vignettes. Secrets live only in `.env`.
Gold answer keys require clinical sign-off (`verified: true`) before the report drops its PRELIMINARY banner.
