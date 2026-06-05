# Clinical Vignette Generation Prompt & Verification Checklist

## How to Use This

1. **Pick 2–5 recommendations** from your ESVS CSV that you want a vignette to exercise.
2. **Copy the generation prompt below** into a Claude conversation (or this one).
3. **Paste your recommendations** in the Input section, optionally add context (age, domain, difficulty).
4. **Paste Claude's output** into the verification checklist.
5. **Tick the checklist** — all ✓ means the vignette enters your gold set. ❌ or ⚠️ means regenerate or revise.

---

## Generation Prompt

```
You are an expert vascular surgeon writing realistic clinical vignettes for evaluating a clinical decision support system.

Your task: Generate a realistic clinical vignette that requires the reader to reason through the following recommendations.

## Input

Recommendations (from ESVS guidelines):
[Paste or type the full text of each recommendation + Class/LoE here]

Optional context:
- Patient age range: [e.g., 60–75]
- Clinical domain: [e.g., carotid artery disease, venous disease, PAD]
- Difficulty level: [easy / moderate / hard]
- Additional context: [e.g., "include a comorbidity that complicates decision-making"]

## Constraints

1. **No answer leakage**: Do not copy the exact indications or thresholds from the recommendations. If a recommendation specifies ">50% stenosis," use a different plausible value (e.g., "moderate stenosis" or "60% narrowing") or describe it clinically rather than numerically.

2. **Include irrelevant detail**: Add 2–3 clinical facts that are true but don't change the clinical decision (e.g., a medication, a comorbidity, an exam finding). This tests whether the system is distracted by noise.

3. **Realistic, not textbook**: The case should feel like real clinical practice — some ambiguity, multiple possible considerations, not a perfect fit to one recommendation.

4. **Clear clinical question**: End with a clear question or decision point, phrased clinically (e.g., "What is your next step?" or "What intervention would you recommend?") rather than "Which recommendation applies?"

5. **For multi-recommendation vignettes (2–5)**: The recommendations should not be obviously sequential steps. They should represent parallel considerations, trade-offs, or layered decisions. This tests multi-hop reasoning, not just checklist-following.

## Output

Generate a realistic clinical vignette (3–5 paragraphs, ~150–250 words) that reads like a real patient case.

Format your output as:

---
**Vignette [#]**

[Patient demographics and chief complaint in one sentence]

[Clinical history, exam findings, and relevant investigations in 2–3 paragraphs]

[Clear clinical question or decision point]

---

**Recommendations this vignette requires:** [List the recommendation numbers/titles from your input, comma-separated]

**Reasoning (for your own notes, optional):** [Why this vignette tests these recommendations]

---

Do not explain your reasoning for the constraints. Simply generate the vignette and move on.
```

---

## Example: Input → Output → Checklist

### Example Input

```
Recommendations (from ESVS guidelines):

1. "In symptomatic patients with asymptomatic contralateral carotid artery stenosis of ≥50%, we recommend performing bilateral carotid revascularization." [Class I, LoE A]

2. "In patients with carotid artery stenosis undergoing revascularization, we recommend antiplatelet therapy (acetylsalicylic acid 75–100 mg daily or clopidogrel 75 mg daily) perioperatively and long-term after the intervention." [Class I, LoE A]

Optional context:
- Patient age range: 65–75
- Clinical domain: carotid artery disease
- Difficulty level: moderate
```

### Example Output (Claude generates this)

---
**Vignette 1**

A 68-year-old man with hypertension and type 2 diabetes presents with a 3-week history of transient visual loss in his left eye, now resolved. He is on metoprolol for blood pressure control and sertraline for depression. Duplex ultrasound shows left carotid artery stenosis of 75–80%, with right carotid stenosis of approximately 60–65%. Magnetic resonance angiography confirms these findings with no evidence of intracranial disease. His neurological examination is now normal. He is not currently on aspirin or clopidogrel.

He asks about his stroke risk and what you would recommend given both vessels are narrowed. He is fit for surgery and has no contraindications to dual antiplatelet therapy.

What is your treatment strategy, and how would you manage his antiplatelet regimen?

---

**Recommendations this vignette requires:** 1, 2

**Reasoning (for your own notes, optional):** Tests whether the system recognizes the need for bilateral revascularization (not just contralateral surveillance) when both stenoses meet threshold, and requires reasoning about perioperative antiplatelet coverage.

---

### Example Checklist Completion

- [x] **No leakage**: The vignette states "60–65%" and "75–80%" rather than exactly "≥50%". It doesn't name "bilateral revascularization"; it asks "treatment strategy."
- [x] **Realistic**: Unilateral symptoms with bilateral disease; patient on unrelated medications (metoprolol, sertraline); this is common in practice.
- [x] **Clear question**: "What is your treatment strategy, and how would you manage his antiplatelet regimen?" — specific and clinical.
- [x] **Distractors present**: Type 2 diabetes, sertraline use, normal neuro exam despite prior amaurosis — all true but don't change the recommendation.
- [x] **All recommendations required**: Yes. Rec 1 applies (symptomatic + contralateral ≥50%). Rec 2 applies (both will undergo revascularization, so perioperative antiplatelet management is active).
- [x] **No hidden recommendations**: Correct. No other carotid rec is newly invoked (e.g., imaging, timing, stent vs. endarterectomy choice are not required by *these* recommendations).
- [x] **Multi-hop logic**: Parallel not sequential — bilaterality + antiplatelet coverage are layered, not step-by-step.

**Notes / rationale for ticks:** Good real-world case. System must recognize contralateral threshold, not just ipsilateral symptoms. Antiplatelet detail tests completeness.

**Decision:** ✓ Accept

---

## Blank Verification Checklist Template

Use this for each vignette Claude generates. Copy the block below and fill in per vignette.

```markdown
### Vignette [#] Verification

**Generated vignette:** [Paste Claude's output here, or just keep the vignette title]

**Recommendations being tested:** [List them]

- [ ] **No leakage**: The vignette doesn't copy the exact indications or thresholds from the recommendations. *(Can you describe the case without quoting the recommendation?)*
- [ ] **Realistic**: The case feels like real clinical practice, not a textbook example. *(Would this presentation happen in your clinic?)*
- [ ] **Clear question**: The endpoint is clear (a decision or next step to make), not vague.
- [ ] **Distractors present**: There are 2–3 clinical facts that are true but don't change the decision. *(Could a system be distracted by them?)*
- [ ] **All recommendations required**: All N recommendations you picked actually apply to this case. *(Would you cite each one?)*
- [ ] **No hidden recommendations**: No *other* ESVS recommendation has become newly applicable. *(Is the answer key complete as stated?)*
- [ ] **Multi-hop logic (if N ≥ 2)**: The recommendations are not obviously sequential; they represent parallel or layered reasoning. *(Does it test reasoning, not just sequence-following?)*

**Notes / rationale for ticks:** 

**Decision:** ✓ Accept | ⚠️ Revise | ❌ Regenerate
```

---

## CSV Tracking Template

If you want to track vignettes in a spreadsheet, use this structure:

```csv
vignette_id,recommendations_tested,no_leakage,realistic,clear_question,distractors,all_recs_required,no_hidden_recs,multi_hop_logic,decision,notes
1,"1,2",✓,✓,✓,✓,✓,✓,✓,Accept,Good real-world case; contralateral threshold + antiplatelet coverage layered
2,"4,5,6",✓,⚠️,✓,✓,✓,✓,❌,Revise,Recommendations appear sequential; rewrite to make parallel. Unclear clinical context.
```

---

## Tips for Speed

1. **Read the vignette once**, then **tick in order**. Most vignettes pass 5–6 boxes quickly.
2. **The "No hidden recommendations" box is your safety valve.** If it makes you pause (is there another rec that applies?), either add it to the key or reword the vignette. Takes 30 seconds per vignette, catches a lot of incompleteness.
3. **For multi-recommendation vignettes, multi-hop logic is non-negotiable.** If the recommendations are obviously sequential (step 1, then step 2, then step 3), regenerate with "make them parallel considerations, not steps."
4. **Keep the notes line short** — just enough for you to remember why you rejected a vignette if you revisit it later.

---

## Common Regeneration Prompts

If a vignette fails a box, use these follow-ups in Claude:

**Failed "No leakage":**
> Regenerate that vignette with different threshold values and describe stenosis severity clinically (e.g., "moderate" or "severe") instead of percentages. Don't name the intervention; end with "what intervention would you recommend?"

**Failed "Multi-hop logic":**
> Rewrite so the two recommendations represent *parallel trade-offs* or *layered decisions*, not sequential steps. For example, make it unclear which intervention is best, requiring consideration of both recommendations to decide.

**Failed "No hidden recommendations":**
> Reword the case so it doesn't mention [the factor that invokes the hidden rec]. Keep the core clinical scenario the same.

---
