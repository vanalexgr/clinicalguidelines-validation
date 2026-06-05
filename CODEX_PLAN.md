# Codex Implementation Plan — Pipeline Integration & Reporting

**Project:** ClinicalGuidelines.io validation harness  
**Repo:** `/home/vga/clinicalguidelines-validation`  
**Branch:** `main` (all tasks create feature branches and PRs)

---

## Context

This is a quantitative LLM-as-judge validation harness for an ESVS vascular surgery RAG system.
The pipeline collects agent answers, runs two LLM judges (claude-opus-4-7, o3), and computes metrics.

**Current state after this session:**
- 16 benchmark items answered and cached in `outputs/answers/answers.jsonl`
- 83/96 judgment rows in `outputs/judgments/judgments.jsonl` (all 16 items covered by both judges)
- Citation existence: 90.2% tier-A verified (46/51 citations)
- 5 citations in tier-B (Q007, GVG CLTI — numeric class codes vs IIb/IIa format)
- `outputs/report/hallucination_clinical_analysis.md` — full per-claim clinical review (57 flags)
- `scripts/citation_error_breakdown.py` — standalone breakdown script (runs OK)
- `outputs/metrics/summary.json` is STALE (shows 12 items, gpt-4o judge, 0% citation existence)

**What each task does and why it matters is explained inline below.**

---

## Task T11 — GVG CLTI Class/Level Normalizer

**Branch:** `feat/T11-gvg-normalizer`

**Problem:** GVG (Global Vascular Guidelines) CLTI 2019 uses a different evidence classification
system than ESVS. The recommendation index stores GVG classes as `"GPS"` (Good Practice Statement),
and numeric grades `"1"`, `"2"`, `"3"` for recommendation strength, while the agent returns
`"IIb"`, `"I"`, `"Good"` etc. This causes 5 Q007 citations to land in tier-B (metadata mismatch)
instead of tier-A (verified).

**File to modify:** `src/corpus/index.py`

**What to add:** A normalizer function that canonicalises both sides of the comparison before
matching. Add it just before the `evaluate_citation_existence` function.

```python
# GVG CLTI uses a numeric-grade + GPS system instead of ESC I/IIa/IIb/III
_GVG_CLASS_MAP: dict[str, str] = {
    "1": "I",
    "2": "IIa",
    "3": "IIb",
    "good": "GPS",
    "good practice statement": "GPS",
}

def _normalise_class(raw: str | None) -> str | None:
    if raw is None:
        return None
    return _GVG_CLASS_MAP.get(raw.strip().lower(), raw.strip())
```

Then in `evaluate_citation_existence`, replace the raw `.class_` and `.level` comparisons with
normalised versions:
```python
# before:
class_match = exists and citation.class_ is not None and citation.class_ == entry.class_
# after:
class_match = (
    exists
    and citation.class_ is not None
    and _normalise_class(citation.class_) == _normalise_class(entry.class_)
)
```
Do the same for `level_match`.

**Expected result:** Q007's 5 B-tier citations (`GVG_CLTI_2019:6.36`, `:6.14`, `:6.35`, `:6.9`,
`:6.1`) move to tier-A, raising citation existence to 100% (51/51).

**Tests:** Add to `tests/test_corpus_index.py`:
- `test_gvg_class_normalisation`: assert `_normalise_class("2") == "IIa"`, `_normalise_class("Good") == "GPS"`, `_normalise_class("IIb") == "IIb"` (passthrough for already-standard)
- `test_citation_existence_gvg`: create a mock index entry with class `"IIb"` and assert a citation with class `"2"` matches (tier-A).

---

## Task T12 — Hallucination Override Annotation File

**Branch:** `feat/T12-hallucination-annotations`

**Problem:** The `_MANUAL` dict in `scripts/citation_error_breakdown.py` hard-codes 30+ claim
reclassifications as Python strings. This is fragile (key truncation at 38 chars causes misses),
not queryable, and can't carry clinical metadata. The full clinical analysis
(`outputs/report/hallucination_clinical_analysis.md`) produced 57 per-claim assessments that need
to be machine-readable.

**Step 1 — Create annotation schema and data file**

Create `data/annotation/hallucination_overrides.jsonl`. Each line is a JSON object:
```json
{
  "query_id": "Q005",
  "judge": "claude-opus-4-7",
  "claim_prefix": "DOACs preferred Class I A citation tied to rec_id 36",
  "auto_type": "OTHER",
  "corrected_type": "METADATA_INFLATION",
  "clinical_risk": "moderate",
  "clinical_note": "Treatment direction correct (DOACs preferred); evidence level inflated from C to A. Systematic across 3 runs."
}
```

Fields:
- `query_id`: string, e.g. "Q005"
- `judge`: "claude-opus-4-7" | "o3" | null (null = applies to any judge)
- `claim_prefix`: first 60 characters of the claim text (for matching)
- `auto_type`: what `scripts/citation_error_breakdown.py` assigned automatically
- `corrected_type`: the reviewed type (from clinical analysis report)
- `clinical_risk`: "none" | "low" | "moderate" | "high"
- `clinical_note`: ≤150 char explanation

Populate it with ALL 57 entries from the clinical analysis. Use the data in
`outputs/report/hallucination_clinical_analysis.md` and `outputs/report/citation_error_breakdown.md`.
Every flag that appears in those reports must have a row. Some flags appear across multiple runs
with the same claim text — one row with `judge: null` is sufficient if the claim and correction
are identical across judges.

**Step 2 — Update `scripts/citation_error_breakdown.py`**

Replace the `_MANUAL` dict lookup with a loader that reads
`data/annotation/hallucination_overrides.jsonl`:

```python
def _load_overrides(path: Path) -> dict[str, dict]:
    """Returns {(query_id, claim_prefix_60): override_record}."""
    overrides = {}
    if not path.exists():
        return overrides
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        key = (rec["query_id"], rec["claim_prefix"][:60])
        overrides[key] = rec
    return overrides
```

In the classifier, look up `(query_id, claim[:60])` in overrides first; use `corrected_type` if
found, else fall back to the regex classifier.

Also add `clinical_risk` to each flag's output dict (from override, default "unknown").

**Step 3 — Update `outputs/metrics/citation_breakdown.json` schema**

Each flag in per-item output should now include:
```json
{
  "judge": "...",
  "run": 0,
  "type": "METADATA_INFLATION",
  "corrected_type": "METADATA_INFLATION",
  "clinical_risk": "moderate",
  "clinical_note": "...",
  "claim": "..."
}
```

**Tests:** Add `tests/test_citation_breakdown.py`:
- `test_override_loader`: create a temp JSONL, verify loader returns correct dict
- `test_override_takes_precedence`: mock a flag classified as OTHER by regex, assert corrected_type from override file is used instead
- `test_clinical_risk_in_output`: run breakdown on fixture data, assert each flag dict has `clinical_risk` field

---

## Task T13 — Citation Breakdown Integration into Report

**Branch:** `feat/T13-report-citation-integration`

**Problem:** `src/report/build_report.py` currently tracks citation existence as a single
`existence_accuracy` float. It has no awareness of citation tiers (A/B/C/D/E), hallucination
type breakdown, adjusted hallucination rate, or clinical risk distribution. The standalone
`scripts/citation_error_breakdown.py` produces these but is disconnected from the main report.

**Step 1 — Extract breakdown logic into a module**

Create `src/metrics/citation_breakdown.py`. Move the core computation logic out of
`scripts/citation_error_breakdown.py` into this module. Expose:

```python
@dataclass
class CitationBreakdownResult:
    tiers: dict[str, int]           # {"A_VERIFIED": 46, "B_METADATA_ERR": 5, ...}
    tier_totals: int
    hal_type_counts: dict[str, int] # {"FALSE_POSITIVE": 14, "METADATA_INFLATION": 6, ...}
    hal_total: int
    artefact_count: int
    real_error_count: int
    items_clean: list[str]
    items_artefact_only: list[str]
    items_real_error: list[str]
    reported_hal_rate: float
    adjusted_hal_rate: float
    clinical_risk_counts: dict[str, int]  # {"none": N, "low": N, "moderate": N, "high": N}
    per_item: dict[str, dict]

def compute_citation_breakdown(
    answers_path: Path,
    judgments_path: Path,
    index_path: Path,
    bench_path: Path,
    overrides_path: Path,
) -> CitationBreakdownResult: ...
```

`scripts/citation_error_breakdown.py` should become a thin wrapper that calls this function and
writes the JSON/MD outputs. It must still work standalone (`python3 scripts/citation_error_breakdown.py`).

**Step 2 — Integrate into `src/report/build_report.py`**

In `build_report()` (around line 127), after `citation_metrics` is computed, add:
```python
from src.metrics.citation_breakdown import compute_citation_breakdown
breakdown = compute_citation_breakdown(
    answers_path=Path(config["paths"]["answers"]),
    judgments_path=Path(config["paths"]["judgments"]),
    index_path=Path(config["paths"]["corpus_index"]),
    bench_path=Path(config["paths"]["benchmark"]),
    overrides_path=Path("data/annotation/hallucination_overrides.jsonl"),
)
```
Store `breakdown` in the `ReportContext` dataclass (add field `citation_breakdown: CitationBreakdownResult | None = None`).

**Step 3 — Add §8 and §9 to `render_report_markdown`**

In `render_report_markdown()` (around line 232), add two new sections after the existing citation
section:

```
## §8  Citation Correctness Tiers
<table: tier | n | %>

## §9  Hallucination Analysis
### 9.1 Claim Type Distribution
<table: type | category | n>

### 9.2 Adjusted Hallucination Rate
| | Items | Rate |
|---|---|---|
| Reported (any judge flag) | N/16 | X% |
| Artefact-only items | N/16 | — |
| Items with real errors | N/16 | X% |
| Items with high-risk errors | N/16 | X% |

### 9.3 Clinical Risk Distribution
<table: risk level | claim count>

### 9.4 Clean / Artefact-only / Real-error items
Clean: Q012, Q016
Artefact-only: Q001, Q004, Q008, Q013, Q014
Real errors: Q002, Q003, Q005, Q006, Q007, Q009, Q010, Q011, Q015
```

Only render these sections if `context.citation_breakdown is not None`.

**Step 4 — Write breakdown tables to CSV**

In `_write_report_tables()`, add:
- `tables/citation_tiers.csv`
- `tables/hallucination_types.csv`
- `tables/hallucination_rate_summary.csv`
- `tables/clinical_risk_distribution.csv`

**Step 5 — Update `outputs/metrics/summary.json`**

Add to the summary JSON:
```json
"citation_breakdown": {
  "tier_a_pct": 0.902,
  "tier_b_pct": 0.098,
  "reported_hal_rate": 0.688,
  "adjusted_hal_rate": 0.25,
  "high_risk_items": ["Q009"],
  "real_error_items": ["Q005", "Q007", "Q009", "Q015"]
}
```

**Tests:** Add to `tests/test_report.py` (or new `tests/test_report_breakdown.py`):
- `test_breakdown_in_report_context`: mock `compute_citation_breakdown` returning a fixture result, assert `render_report_markdown` output contains "§8 Citation Correctness Tiers" and "Adjusted Hallucination Rate"
- `test_breakdown_tables_written`: assert the four new CSVs are created when breakdown data is present
- `test_breakdown_absent_gracefully`: when overrides file missing, breakdown is None, report renders without §8/§9

---

## Task T14 — Complete Missing Judgment Runs

**Branch:** `feat/T14-complete-judgments`

**Problem:** 83/96 judgment rows exist. 13 runs were lost when the Anthropic account ran out of
credits mid-run. The judge runner has caching — re-running will skip already-completed items and
only attempt the missing ones.

**Action:** This is primarily a runtime task, but Codex should verify the cache logic is correct
and add a `--status` flag to `src/judge/run_judge.py` that prints missing runs without executing.

**In `src/judge/run_judge.py`:**

Add CLI flag `--status` (boolean, default False). When set:
1. Load existing judgments from `outputs/judgments/judgments.jsonl`
2. Compute the full expected set: `{(item_id, judge_name, run_index) for item in benchmark for judge in judges for run_index in range(runs_per_judge)}`
3. Compute the present set from the JSONL
4. Print missing tuples as a table
5. Exit 0 without running any LLM calls

Example output:
```
Missing runs (13):
  Q002  claude-opus-4-7  run 1
  Q003  claude-opus-4-7  run 1
  ...
```

**Tests:** Add `test_status_flag_prints_missing`: create a mock judgments file with 2 entries,
call `main(["--status"])`, assert it prints the missing runs and exits 0 without touching the LLM.

---

## Task T15 — Rebuild Report from Current Outputs

**Branch:** `feat/T15-report-rebuild`

**Problem:** `outputs/metrics/summary.json` is stale — it was generated from a partial 12-item
run with gpt-4o as judge. After T11–T13, the report should be rebuilt end-to-end.

**This task is primarily a validation task.** After T11–T13 are merged:

1. Run the judge to fill missing runs:
   ```bash
   python3 -m src.judge.run_judge
   ```

2. Rebuild the full report:
   ```bash
   python3 -m src.report.build_report
   ```

3. Verify `outputs/metrics/summary.json` shows:
   - `"processed_items": 16`
   - `"judges_present": ["claude-opus-4-7", "o3"]`
   - `"citation": {"existence_accuracy": 1.0}` (after T11 GVG fix)
   - `"citation_breakdown"` key present with adjusted rates

4. Verify `outputs/report/report.md` contains §8 and §9 sections.

5. Commit the refreshed output files:
   - `outputs/metrics/summary.json`
   - `outputs/report/report.md`
   - `outputs/report/citation_error_breakdown.md`
   - `outputs/metrics/citation_breakdown.json`
   - `outputs/report/tables/` (new CSVs)

**Tests for this task:** Add an integration test `tests/test_pipeline_e2e.py` (or extend existing)
that:
- Uses the full cached `outputs/` data (no LLM calls needed)
- Runs `build_report` in dry-run / partial mode
- Asserts `summary.json` has the expected keys and that `report.md` has the expected sections

---

## Task T16 — Reclassification Statistics Module

**Branch:** `feat/T16-reclassification-stats`

**Problem:** There is currently no single place that reports the three-layer hallucination picture:
(1) raw judge flags, (2) automated type classification, (3) clinical review reclassification.
The `citation_breakdown.json` has the data; this task surfaces it cleanly.

**Create `src/metrics/reclassification.py`**:

```python
@dataclass
class ReclassificationReport:
    total_flags: int
    auto_type_counts: dict[str, int]
    corrected_type_counts: dict[str, int]
    flags_reclassified: int           # where auto_type != corrected_type
    reclassification_rate: float      # flags_reclassified / total_flags
    clinical_risk_counts: dict[str, int]
    reported_hal_rate: float
    adjusted_hal_rate: float          # after removing artefact-only items
    clinical_review_rate: float       # items with ≥1 confirmed real error / total
    high_risk_rate: float             # items with ≥1 high-risk error / total

def compute_reclassification_report(breakdown: CitationBreakdownResult) -> ReclassificationReport:
    ...
```

Integrate into `build_report.py` and render a §10 Reclassification Summary section:

```
## §10  Judge Reclassification Summary

| Layer | Hallucination rate |
|---|---|
| Raw judge flags (any flag = hallucination) | 68.8% |
| Automated type classification (artefacts removed) | 56.2% |
| Clinical review (confirmed real errors only) | 25.0% |
| High clinical risk items | 6.3% |

N=57 total flags; 84% reclassified as artefacts after clinical review.
```

**Tests:**
- `test_reclassification_report`: given a mock `CitationBreakdownResult`, assert all rate calculations are correct
- `test_reclassification_in_report`: assert §10 appears in rendered markdown when breakdown present

---

## Task T17 — Benchmark Metadata Completeness Check

**Branch:** `feat/T17-benchmark-completeness`

**Problem:** The benchmark has 16 items. Not all have `"verified": true`. The report already
marks scores as "preliminary" when unverified, but there is no tooling to check which items are
missing metadata (verified flag, key_recommendations, etc.) or to validate completeness.

**Add CLI tool `scripts/validate_benchmark_completeness.py`:**

```
python3 scripts/validate_benchmark_completeness.py
```

Output:
```
Benchmark completeness report (16 items)
─────────────────────────────────────────
verified=true   :  0 / 16  ← needs clinical review
key_recommendations populated: 15 / 16  (Q012 is correct — out-of-scope)
acceptable_refusal set        : 16 / 16
gate_expected set             : 16 / 16

Items needing verification: Q001–Q016
```

The script should:
1. Load `data/benchmark/benchmark_queries.seed.jsonl`
2. For each item check:
   - `verified` field (bool, default False if absent)
   - `gold.key_recommendations` is non-empty OR `gold.acceptable_refusal == true` OR item is Q012 (out-of-scope)
   - `gold.gate_expected` is present
   - `gold.answer_key` is non-empty
3. Print a summary table
4. Exit 1 if any required field is missing (for CI use); exit 0 if only `verified` is false (expected)

**Tests:**
- `test_benchmark_completeness_all_present`: mock JSONL with all fields → assert exit 0
- `test_benchmark_completeness_missing_answer_key`: one item with empty `answer_key` → assert exit 1

---

## Implementation Order

Run in this order (each depends on the previous only where noted):

```
T11 (GVG normalizer)           — independent, do first
T12 (annotation JSONL)         — independent, do in parallel with T11
T13 (report integration)       — depends on T11 + T12 being merged
T14 (--status flag)            — independent
T16 (reclassification module)  — depends on T13
T15 (report rebuild)           — depends on T11 + T12 + T13 + T16 merged
T17 (benchmark check)          — independent
```

---

## Acceptance Criteria (for each PR)

- All existing tests still pass (`pytest` green)
- New tests cover the added logic at unit level
- `python3 scripts/citation_error_breakdown.py` still runs standalone and produces correct output
- `python3 -m src.report.build_report` completes without error after T13 is merged
- No LLM API calls in any test (use fixtures/mocks)
- Type annotations on all new public functions
- `ruff check` + `ruff format` pass

---

## Files Summary

| Task | New files | Modified files |
|---|---|---|
| T11 | — | `src/corpus/index.py`, `tests/test_corpus_index.py` |
| T12 | `data/annotation/hallucination_overrides.jsonl`, `tests/test_citation_breakdown.py` | `scripts/citation_error_breakdown.py` |
| T13 | `src/metrics/citation_breakdown.py`, `tests/test_report_breakdown.py` | `src/report/build_report.py`, `scripts/citation_error_breakdown.py` |
| T14 | `tests/test_judge_status.py` | `src/judge/run_judge.py` |
| T15 | `tests/test_pipeline_e2e.py`, refreshed `outputs/` | — |
| T16 | `src/metrics/reclassification.py` | `src/report/build_report.py` |
| T17 | `scripts/validate_benchmark_completeness.py`, `tests/test_benchmark_completeness.py` | — |

---

## Notes for Codex

- Never call external LLM APIs in tests — all judge/LLM interactions must use fixture data
- `outputs/answers/answers.jsonl`, `outputs/judgments/judgments.jsonl`, and `data/corpus/recommendation_index.json` exist on disk and can be used as fixtures
- The `_MANUAL` dict in `scripts/citation_error_breakdown.py` is replaced by `data/annotation/hallucination_overrides.jsonl` in T12 — do not delete the script, only update it to read from the file
- `GVG_CLTI_2019` recs have dotted IDs like `6.35`, `6.1` — these are valid compound keys in the index
- The `Citation` schema is in `src/common/schemas.py`; `Citation.class_` (aliased from `"class"`) and `Citation.level` are both `str | None`
- Run `pytest -x -q` to verify; the suite currently has 81 passing tests
