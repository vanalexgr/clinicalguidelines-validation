# ClinicalGuidelines.io — benchmark and evaluation pipeline

Data and code behind the quantitative evaluation in *"A Multi-Guideline Clinical Agent for
Vascular Surgery: Architecture, Implementation, and Benchmark Evaluation"* (Journal of the
American Medical Informatics Association, under review).

Everything reported in the manuscript can be regenerated from this repository. No API keys
and no network access are required.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m scripts.reproduce_paper
```

The script recomputes each figure from the committed artifacts and prints it beside the value
printed in the paper, so any discrepancy is visible rather than taken on trust.

## What is measured

This is a **faithfulness and safety-behaviour benchmark**. It tests objective design claims —
corpus constraint, guideline routing, citation grounding, gate behaviour, and the presence of
unsupported content. It does **not** score the clinical acceptability of complete answers: a
clinical case may admit several defensible recommendations, so scoring a free-text answer
against a single reference cannot separate genuine error from legitimate clinical variation.
Clinical acceptability is reserved for the planned prospective study.

The core design decision is the split between *deterministic* metrics (routing, gate
behaviour, citation existence) and *judge-dependent* metrics (faithfulness, completeness,
uncertainty handling, unsupported claims), with every claim the automated judges flagged
subsequently adjudicated by vascular surgeons.

## Reported results

| Metric | Value | 95% CI |
|---|---|---|
| Guideline routing recall | 98.4% (61/62) | 91.4–99.7% |
| Guideline routing precision | 79.2% (61/77) | — |
| Exact guideline-set reproduction | 70.9% (39/55) | 57.9–81.2% |
| Context Gate specificity | 91.9% (34/37) | 78.7–97.2% |
| Context Gate sensitivity | 78.6% (11/14) | 52.4–92.4% |
| Mean parameter recall when fired | 0.29 | — |
| Citation existence accuracy | 100% (259/259) | 98.5–100% |
| Out-of-scope queries declined | 100% (4/4) | 51.0–100% |
| In-scope queries inappropriately refused | 13.7% (7/51) | — |
| Screening unsupported-claim rate | 18.2% (10/55) | 10.2–30.3% |
| Adjudicated clinical error rate | 5.5–7.3% by rater | — |
| Provenance gap (correct but ungrounded) | 10.9% (6/55) | 5.1–22.2% |
| Krippendorff's α (ordinal dimensions) | 0.586 | — |

Two vascular surgeons adjudicated the flagged claims. Agreement on whether a claim was a
clinical error was 93.3% (Cohen's κ = 0.86). Severity grading initially diverged and
converged after the definition of a harmful error was made explicit; no error was finally
graded capable of causing harm.

## Pipeline

1. Benchmark items with human-authored, surgeon-verified gold keys — `data/benchmark/`
2. Answer generation against the deployed agent — `src/runner/`, `src/agent_client/`
3. LLM-as-judge scoring; the judge evaluates *support*, never answers the question — `src/judge/`
4. Structured rubric, ordinal quality dimensions plus binary safety flags — `config/rubric.yaml`
5. Deterministic and judge-dependent metrics, reported individually — `src/metrics/`
6. Clinician adjudication of every flagged claim — `data/annotation/`
7. Discordance export and report generation — `src/discordance/`, `src/report/`

## What is in the repository

| Path | Contents |
|---|---|
| `data/benchmark/benchmark_queries.v2.jsonl` | The 55 benchmark items with gold keys, all surgeon-verified |
| `data/annotation/hallucination_overrides.jsonl` | First-rater adjudication of all 58 distinct flagged claims |
| `data/annotation/second_rater_adjudication.jsonl` | Blinded second-rater adjudication, both raters' verdicts and the disagreements |
| `data/corpus/recommendation_index.json` | The recommendation index citations are verified against |
| `outputs/answers/answers.jsonl` | The 55 recorded agent responses |
| `outputs/judgments/judgments.jsonl` | All 330 judge records |
| `outputs/metrics/`, `outputs/report/` | Computed metrics, tables, plots, generated report |
| `src/`, `scripts/` | The analysis code, including the adjudication tooling |

`data/benchmark/benchmark_queries.seed.jsonl` is a 16-item fixture used by the test suite
only. It is not part of the reported evaluation.

## Adjudication tooling

The clinician adjudication is reproducible, not just its result. `scripts/` contains the
worksheet builders, the converter that writes verdicts back into the annotation files, the
blinded second-rater form builder, and the agreement scorer. The second-rater form
deliberately mixes claims the first rater called errors with claims they cleared, so
agreement can be measured rather than a re-check performed.

## Running the full pipeline

Regenerating answers or judgments requires API credentials and contacts the deployed system:

```bash
cp .env.example .env          # fill in keys and the agent endpoint
python -m src.runner.generate_answers --config config/config.yaml --dry-run
bash scripts/run_all.sh
```

This is not needed to reproduce the reported figures.

## Safety and data

No real patient data. Benchmark items are synthetic vignettes written by the investigators
and verified by a vascular surgeon. Secrets live only in `.env`, which is not tracked.
