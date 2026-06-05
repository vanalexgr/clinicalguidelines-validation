# ClinicalGuidelines.io — LLM-as-Judge Validation Pipeline

Internal validation harness that produces the quantitative evaluation for the architecture manuscript
*"A Multi-Guideline, Context-Aware Clinical Agent for Vascular Surgery Decision Support."* It runs a
benchmark query set through the ClinicalGuidelines.io agent, scores the answers with a calibrated
LLM-as-judge, and reports routing accuracy, Context Gate sensitivity/specificity, citation accuracy,
hallucination rate, safety-critical discordance, and by-query-type performance — as a near-term bridge
before the full prospective multicenter study.

**Read `CODEX.md` first** — it is the authoritative design and the task list. `config/rubric.yaml` is the
machine-readable rubric. The split between *deterministic* metrics (routing, gate, citation existence)
and *judge-dependent* metrics (correctness, faithfulness, completeness, uncertainty, hallucination,
safety) is the core defensibility decision.

## Current snapshot

The repo now includes Benchmark v2:

- `55` benchmark items in `data/benchmark/benchmark_queries.v2.jsonl`
- live Context Gate support in the agent client
- integrated citation-tier / hallucination reclassification reporting
- hardened judge recovery and cache reconstruction tooling

The latest full-run record is documented in `docs/benchmark_v2_run_2026-06-06.md`.

## Pipeline (7 steps)
1. Benchmark query set with human-authored gold labels (`data/benchmark/`).
2. Generate answers with the agent (`src/runner/`).
3. LLM-as-judge evaluation — judge evaluates *support*, never answers the question (`src/judge/`).
4. Structured rubric — 8 dimensions, Likert 0–3 + binary safety/hallucination (`config/rubric.yaml`).
5. Per-item pass/fail + global safety flag (`src/metrics/pass_fail.py`).
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

## Runbooks

- `CODEX_PLAN.md` — implementation plan for corpus normalization, citation breakdown, and reporting
- `CODEX_BENCHMARK_RUN.md` — benchmark-v2 build and full-pipeline execution runbook
- `docs/benchmark_v2_run_2026-06-06.md` — completed run record with results and caveats

## Status & roles
- **Orchestrator (Claude):** owns design, rubric, prompts, statistics; reviews every PR.
- **Worker (Codex):** implements one task at a time (see `CODEX.md` §7), opens a PR per task, updates
  `STATUS.md`, and surfaces ambiguities instead of guessing.

## Create the GitHub repo and push
```bash
cd clinicalguidelines-validation
git init -b main
git add .
git commit -m "Scaffold: LLM-as-judge validation pipeline (design + seed data)"
# with GitHub CLI:
gh repo create clinicalguidelines-validation --private --source=. --remote=origin --push
# or manually: create the repo on github.com, then:
# git remote add origin git@github.com:<you>/clinicalguidelines-validation.git && git push -u origin main
```

## Safety / data
No real patient data in the repo — benchmark items are synthetic vignettes. Secrets live only in `.env`.
Gold answer keys require clinical sign-off (`verified: true`) before the report drops its PRELIMINARY banner.
