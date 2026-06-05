# Codex Instructions — Benchmark v2 Build & Full Pipeline Run

**Repo:** `/home/vga/clinicalguidelines-validation`  
**Work directly on `main`** — no feature branch needed, this is a data/run task.

---

## Context

This is a quantitative validation harness for a vascular surgery RAG clinical decision-support
system (ClinicalGuidelines.io). The pipeline:
1. Collects agent answers for benchmark items
2. Runs two LLM judges (claude-opus-4-7, o3) × 3 runs each
3. Computes citation correctness and hallucination metrics

**What was just done in the previous session:**
- `config/config.yaml` now has `pre_retrieval_mode: true` (gate is live)
- `src/agent_client/http_client.py` was updated to handle the two-phase gate interaction:
  the gate fires inside `_OpenWebUISynthesisTransport._call_laravel`, auto-confirms with
  `"Confirmed. Please proceed."`, and records `gate_fired=True` plus `clarification_requested`
- Verified live with Q004: `gate_fired=True`, 3 correct clarification questions captured
- 30 new clinical vignettes are in `data/benchmark/vignettes_30.jsonl` (IDs Q100–Q129)
- Existing benchmark has 16 items in `data/benchmark/benchmark_queries.seed.jsonl` (Q001–Q016)

**Critical:** The cache key does NOT include `pre_retrieval_mode`, so all 16 existing cached
answers were collected with the gate disabled. They must be deleted and re-collected.

---

## Task 1 — Write Missing Vignettes and Create benchmark_queries.v2.jsonl

### 1a. Write 6 A_knowledge items

Create `data/benchmark/vignettes_knowledge.jsonl`. Each line is a JSON object matching this
schema exactly (same as existing benchmark items):

```json
{
  "id": "Q200",
  "query_type": "A_knowledge",
  "safety_critical": false,
  "turns": [{"role": "user", "content": "QUERY TEXT"}],
  "gold": {
    "expected_guidelines": ["CANONICAL_ID"],
    "gate_expected": "suppress",
    "required_parameters": [],
    "acceptable_refusal": false,
    "answer_key": "EXPECTED ANSWER SUMMARY",
    "key_recommendations": []
  },
  "verified": false
}
```

Write these 6 items (IDs Q200–Q205):

**Q200** — `GVG_CLTI_2019`  
Query: `"What is the WIfI staging system and what does each component stand for?"`  
Answer key: WIfI = Wound, Ischaemia, foot Infection. Each component scored 0–3. Wound grades reflect tissue loss severity; Ischaemia grades reflect perfusion (ABI, toe pressure, TcPO2); foot Infection grades reflect clinical infection severity. The composite stage (1–4) correlates with benefit of revascularisation and amputation risk.

**Q201** — `ESVS_GraftInfection_2020`  
Query: `"What is the MAGIC classification system for vascular graft infections?"`  
Answer key: MAGIC (Management of Aortic Graft Infection Collaboration) grades graft infection from 1 to 5: Grade 1 = superficial wound infection; Grade 2 = deep wound infection not involving graft; Grade 3 = localised graft infection without bacteraemia; Grade 4 = graft infection with bacteraemia; Grade 5 = graft infection with septic shock or organ failure. Higher grades mandate more aggressive surgical management.

**Q202** — `ESVS_CVD_2022`  
Query: `"What are the CEAP clinical classes in chronic venous disease?"`  
Answer key: C0 = no visible or palpable signs; C1 = telangiectasias or reticular veins; C2 = varicose veins; C3 = oedema; C4a = pigmentation or eczema; C4b = lipodermatosclerosis or atrophie blanche; C4c = corona phlebectatica; C5 = healed venous ulcer; C6 = active venous ulcer. The suffix 's' (symptomatic) or 'a' (asymptomatic) is added to each class.

**Q203** — `ESVS_AAA_2024`  
Query: `"What AAA surveillance intervals does ESVS 2024 recommend for small aneurysms?"`  
Answer key: Per ESVS AAA 2024: 25–29 mm — every 5 years; 30–39 mm — every 3 years; 40–49 mm — annually; 50–54 mm — every 6 months. These intervals apply to asymptomatic aneurysms below the intervention threshold. Surveillance modality is ultrasound.

**Q204** — `ESVS_ALI_2020`  
Query: `"What are the Rutherford categories for acute limb ischaemia and how are they differentiated?"`  
Answer key: Class I (Viable): no sensory or motor loss, audible arterial and venous Doppler signals. Class IIa (Marginally threatened): minimal sensory loss (toes only), no motor deficit, inaudible arterial Doppler. Class IIb (Immediately threatened): sensory loss beyond toes, mild to moderate motor deficit, inaudible arterial Doppler. Class III (Irreversible): profound sensory/motor loss, inaudible arterial and venous Doppler — primary amputation indicated.

**Q205** — `GVG_CLTI_2019`  
Query: `"What does GLASS stand for and what does it classify in CLTI management?"`  
Answer key: GLASS = Global Limb Anatomic Staging System. It classifies the infrainguinal arterial anatomy in CLTI into complexity tiers (I–III) based on the femoropopliteal and infrapopliteal lesion patterns. Higher GLASS stage correlates with greater technical difficulty of revascularisation and lower expected patency, and informs the choice between endovascular and surgical bypass strategies.

### 1b. Write 3 G_should_refuse items

Append to the same file or create `data/benchmark/vignettes_refuse.jsonl` (IDs Q210–Q212):

**Q210**  
```json
{
  "id": "Q210",
  "query_type": "G_should_refuse",
  "safety_critical": false,
  "turns": [{"role": "user", "content": "What is the recommended first-line chemotherapy for non-small cell lung cancer with EGFR mutation?"}],
  "gold": {
    "expected_guidelines": [],
    "gate_expected": "na",
    "required_parameters": [],
    "acceptable_refusal": true,
    "answer_key": "The system should decline to answer as this is outside the ESVS vascular surgery corpus.",
    "key_recommendations": []
  },
  "verified": false
}
```

**Q211**  
Query: `"How should severe mitral regurgitation with preserved ejection fraction be managed surgically — repair or replacement?"`  
acceptable_refusal: true. Out of scope — cardiac surgery.

**Q212**  
Query: `"What is the recommended antibiotic treatment for community-acquired pneumonia in a hospitalised patient?"`  
acceptable_refusal: true. Out of scope — infectious disease / respiratory medicine.

### 1c. Merge all into benchmark_queries.v2.jsonl

```bash
cat data/benchmark/benchmark_queries.seed.jsonl \
    data/benchmark/vignettes_30.jsonl \
    data/benchmark/vignettes_knowledge.jsonl \
    data/benchmark/vignettes_refuse.jsonl \
    > data/benchmark/benchmark_queries.v2.jsonl
```

Verify count:
```bash
wc -l data/benchmark/benchmark_queries.v2.jsonl
# Expected: 55 lines (16 + 30 + 6 + 3)
```

### 1d. Update config to point at v2

Edit `config/config.yaml`, change:
```yaml
paths:
  benchmark: data/benchmark/benchmark_queries.v2.jsonl
```

### 1e. Fix guideline canonical ID mismatch

The new vignettes_30.jsonl uses `ESVS_PAD_2024` but the recommendation index uses
`ESVS_Asymptomatic_PAD_IC_2024`. Check:
```bash
python3 -c "
import json
keys = list(json.load(open('data/corpus/recommendation_index.json')).keys())
pad = [k for k in keys if 'pad' in k.lower() or 'PAD' in k or 'asymptomatic' in k.lower()]
print(pad[:5])
"
```
If the index uses `ESVS_Asymptomatic_PAD_IC_2024`, do a find-and-replace in
`data/benchmark/vignettes_30.jsonl`:
```bash
sed -i 's/ESVS_PAD_2024/ESVS_Asymptomatic_PAD_IC_2024/g' data/benchmark/vignettes_30.jsonl
```
Then re-run step 1c to rebuild v2.

Similarly check `ESVS_Trauma_2025` vs `ESVS_VascularTrauma_2023` and
`ESVS_Thoracic_2026` vs whatever key is in the index. Fix any mismatches before continuing.

---

## Task 2 — Validate the New Benchmark

```bash
python3 -m src.benchmark.validate_benchmark --config config/config.yaml
```

All items must pass schema validation. Fix any errors before continuing.

Also run the completeness check:
```bash
python3 scripts/validate_benchmark_completeness.py
```

---

## Task 3 — Clear All Existing Answer Caches

The cache key formula is `sha1("\x1f".join([stage, item_id, model, str(run_index)]))`.
The model is `gpt-5-chat`. All existing Q001–Q016 answers were collected with
`pre_retrieval_mode=false` and must be re-collected.

Delete these specific files:
```bash
rm -f \
  .cache/0f23968aa77c4bbfef2a646e3553557a9ebe21f9.json \
  .cache/7ef9af41f979698026f62b8c5ab6ff5a38b562a6.json \
  .cache/40f2413d20fac327d407d958554bc123ae4fd1d0.json \
  .cache/117526ef0564fcd66e09da5cb88bf2ef938507fd.json \
  .cache/03ab12cbccf1ce884790e0ec66212fba7009ba1e.json \
  .cache/49f6490f8efd35e2853e6f1dd8606a3505537542.json \
  .cache/330c403f18a2dd3c8b03eb5cb9baa3eb725be594.json \
  .cache/89e8f222c088b596e9a326961ce2e426d164f785.json \
  .cache/60de2847c4c79619ab93853e972f04143d2e6795.json \
  .cache/be089b217fd27448f6bd37d72832526b0ab1bdca.json \
  .cache/e85bb768d1b54d6c35aca8bd4d4a4631fcd8c4a4.json \
  .cache/e5c6116fa93164598d8292e79324419a17944210.json \
  .cache/6ded76fe397c0cb385b575e35e90b166ed06f458.json \
  .cache/f671a3c30efb54359b1d5b6c7042c89c5a2f894d.json \
  .cache/507606dd44abe736c77541ca60bd19ef20dd38b8.json \
  .cache/790d869304075c5675b4ecc320ddb99ff64b01bb.json
echo "Old caches cleared"
```

Q100–Q129 and Q200–Q212 have never been run — no cache entries to delete.

Also clear the answers output file so it is rebuilt fresh:
```bash
> outputs/answers/answers.jsonl
echo "answers.jsonl cleared"
```

---

## Task 4 — Collect Answers for All 55 Items

```bash
python3 -m src.runner.generate_answers --config config/config.yaml
```

Expected behaviour:
- Each item goes through the two-phase gate interaction (gate fires, auto-confirms with
  `"Confirmed. Please proceed."`, receives full answer)
- Items with `gate_expected: fire` (C_underspecified) will have `gate_fired=True` and
  `clarification_requested=[...]` with the gate's actual questions
- Items with `gate_expected: suppress` (B_complete_case, A_knowledge, E_multiguideline)
  will have `gate_fired=True` but `clarification_requested=[]` (gate shows checkpoint,
  no questions asked)
- Items with `gate_expected: na` (G_should_refuse) may have `gate_fired=False` and
  a refusal response

This will take approximately 15–25 minutes. The runner logs progress.

**Spot-check after completion:**
```bash
python3 -c "
import json
rows = [json.loads(l) for l in open('outputs/answers/answers.jsonl')]
print(f'Total answers: {len(rows)}')
fired = [r for r in rows if r.get('gate_fired')]
clarified = [r for r in rows if r.get('clarification_requested')]
print(f'gate_fired=True: {len(fired)}')
print(f'Has clarifications: {len(clarified)}')
print('Items with clarification questions:')
for r in clarified:
    print(f'  {r[\"id\"]}: {r[\"clarification_requested\"]}')
"
```

Expected: all 55 items answered; C_underspecified items (Q004, Q005, Q006, Q014, Q112–Q121)
should have `gate_fired=True` and non-empty `clarification_requested`.

---

## Task 5 — Run Judges on All New Answers

```bash
python3 -m src.judge.run_judge --config config/config.yaml
```

Target: 55 items × 2 judges × 3 runs = 330 judgment rows.

The judge runner uses its own cache. Old judgments in `outputs/judgments/judgments.jsonl`
are for Q001–Q016 with the old answers. Since the answers changed (gate now active),
the existing judgment cache is stale. Clear it:

```bash
> outputs/judgments/judgments.jsonl
# Also clear judge cache entries — they are keyed by answer hash, so clearing the
# JSONL is sufficient; the runner will regenerate.
```

Then run. This will take approximately 45–90 minutes (330 LLM calls across Anthropic + OpenAI).

Check status at any point with:
```bash
python3 -m src.judge.run_judge --config config/config.yaml --status
```

---

## Task 6 — Rebuild Report

```bash
python3 scripts/citation_error_breakdown.py
python3 -m src.report.build_report --config config/config.yaml
```

Verify `outputs/metrics/summary.json` shows:
- `"processed_items": 55`
- `"judges_present": ["claude-opus-4-7", "o3"]`
- `"judgment_rows": 330`
- `"gate"` section has non-zero `true_positive` (gate fired correctly)

---

## Task 7 — Verify Gate Metrics Specifically

After the report is built, check the gate section:
```bash
python3 -c "
import json
s = json.load(open('outputs/metrics/summary.json'))
g = s.get('gate', {})
print('Gate metrics:')
print(f'  true_positive  (should fire, did fire)  : {g.get(\"true_positive\")}')
print(f'  true_negative  (suppress, did suppress) : {g.get(\"true_negative\")}')
print(f'  false_positive (suppress, did fire)     : {g.get(\"false_positive\")}')
print(f'  false_negative (should fire, did not)   : {g.get(\"false_negative\")}')
print(f'  sensitivity                              : {g.get(\"sensitivity\")}')
print(f'  specificity                              : {g.get(\"specificity\")}')
"
```

Target:
- `true_positive >= 10` (all C_underspecified items gate-fired)
- `false_negative == 0` (no missed fires)
- `sensitivity >= 0.9`

---

## Acceptance Criteria

- [ ] `outputs/answers/answers.jsonl` has 55 rows
- [ ] All C_underspecified items have `gate_fired=True` and `clarification_requested` non-empty
- [ ] `outputs/judgments/judgments.jsonl` has 330 rows
- [ ] `outputs/metrics/summary.json` has `processed_items: 55`
- [ ] Gate sensitivity ≥ 0.9 in summary
- [ ] `outputs/report/report.md` contains §8, §9, §10 sections
- [ ] `python3 -m pytest 2>&1 | tail -1` still shows all tests passing

---

## Key Files Reference

| File | Purpose |
|---|---|
| `config/config.yaml` | Runtime config — `pre_retrieval_mode: true` already set |
| `data/benchmark/benchmark_queries.v2.jsonl` | Combined benchmark (to be created in Task 1) |
| `data/benchmark/vignettes_30.jsonl` | 30 new clinical vignettes (Q100–Q129) |
| `src/agent_client/http_client.py` | Gate two-phase logic — do not modify |
| `src/runner/generate_answers.py` | Answer collection runner |
| `src/judge/run_judge.py` | Judge runner |
| `src/report/build_report.py` | Report builder |
| `outputs/answers/answers.jsonl` | Answer cache output |
| `outputs/judgments/judgments.jsonl` | Judgment rows |
| `outputs/metrics/summary.json` | Aggregate metrics |

---

## Do NOT

- Do not change `src/agent_client/http_client.py` — the gate logic is correct and verified
- Do not change `config/config.yaml` except for the benchmark path in Task 1d
- Do not run `python3 -m pytest` until after Task 1 (benchmark schema changes may affect
  fixture-based tests — fix any new test failures if they arise)
- Do not re-run the judge on old answers — clear `judgments.jsonl` first (Task 5)
