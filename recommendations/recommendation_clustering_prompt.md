# Recommendation Clustering Prompt & Vignette Group Selector

## Overview

This prompt helps you discover which recommendations to group together for vignette generation. You upload your recommendation CSVs, and the model suggests clinically coherent clusters of 2–5 recommendations that form interesting test cases for your system.

**Key principle:** The model is suggesting *groupings*, not deciding answers or writing vignettes. You review and filter the suggestions, then feed selected clusters into the vignette generation prompt.

---

## How to Use

1. **Export your recommendations to CSV format** with these columns (at minimum):
   - `rec_id` (e.g., "1.1", "2.3") or recommendation number
   - `text` (full recommendation statement)
   - `class` (I, IIa, IIb, or III)
   - `loe` (A, B, or C — Level of Evidence)
   - `domain` (e.g., Carotid, AAA, PAD, Venous, Renal, Mesenteric, etc.)
   
   Optional columns that help the analysis:
   - `guideline` (which ESVS guideline: "2024 Carotid", "2025 Mesenteric", etc.)
   - `condition` (the clinical condition, e.g., "asymptomatic carotid stenosis")
   - `intervention` (if relevant, e.g., "CEA", "Medical management")

2. **Paste the prompt below** into Claude (here or in a new session).

3. **Paste or upload your CSV(s)** in the Input section.

4. **Review the output** — you'll get a table of suggested clusters with rationale. Filter by difficulty, domain, or coverage.

5. **Select clusters** you want to use and paste them back into the **vignette generation prompt** (from the prior artifact).

---

## Clustering Prompt

```
You are a vascular surgeon analyzing a set of ESVS guideline recommendations to identify clusters that form meaningful, clinically coherent test cases for a clinical decision support system.

Your task: Read the recommendations and suggest groups of 2–5 recommendations that should be tested together in a single clinical vignette.

## Input

[Paste your CSV here, or describe your recommendations]

Example CSV format:
rec_id | text | class | loe | domain | guideline | condition | intervention
1.1 | In symptomatic patients with ... | I | A | Carotid | 2024 Carotid | Symptomatic stenosis | CEA/CAS
2.2 | In asymptomatic patients with ... | IIa | B | Carotid | 2024 Carotid | Asymptomatic stenosis | Surveillance
3.1 | Perioperative antiplatelet therapy ... | I | A | Carotid | 2024 Carotid | All carotid interventions | Medical

## Clustering Logic

Group recommendations if they form a coherent clinical decision scenario. Consider these patterns:

1. **Complementary indications**: Same condition, different severity or patient subgroup.
   - Example: "symptomatic stenosis → revascularize" + "asymptomatic stenosis → surveillance" together test the decision threshold.

2. **Competing approaches**: Same indication, different interventions or strategies.
   - Example: "CEA is preferred" + "CAS is an alternative" + "consider patient risk factors" together test choice points.

3. **Sequential decisions**: One recommendation invokes or depends on another.
   - Example: "obtain imaging" + "if positive, then intervene" + "postoperative antiplatelet coverage" together test a care pathway.

4. **Trade-offs / Nuanced Class/LoE**: Recommendations with different Class or LoE that make decision-making non-obvious.
   - Example: "Class I for option A" + "Class IIa for option B" + "patient preference matters" together test clinical judgment under uncertainty.

5. **Special populations / Contraindications**: Recommendations that apply to specific subgroups and complicate management.
   - Example: "high-risk patient" + "avoid intervention in subgroup X" + "consider less invasive alternative" together test risk-benefit reasoning.

## Output

For each suggested cluster, generate a row in the table below:

| Cluster ID | Recommendations | Rationale (Clinical Decision Tested) | Difficulty | Distractor Opportunities | Domain | Guideline(s) |
|---|---|---|---|---|---|---|
| C1 | 1.1, 2.2 | Tests the threshold for recommending revascularization: when is symptomatic intervention indicated vs. asymptomatic surveillance? System must recognize both severity and symptom status. | Moderate | Age, comorbidities (diabetes, CKD), prior stroke history | Carotid | 2024 Carotid |
| C2 | 1.2, 3.1, 3.2 | Tests the decision to intervene (CEA vs. CAS) and the antiplatelet regimen. System must recognize class differences and adjust perioperative management accordingly. | Hard | Technical anatomy (tortuous vessels), medical comorbidities, anticoagulation status | Carotid | 2024 Carotid |
| ... | ... | ... | ... | ... | ... | ... |

## Guidelines for Suggestions

- **Minimum cluster size:** 2 recommendations (tests at least one relationship).
- **Maximum cluster size:** 5 recommendations (vignettes become unwieldy; system reasoning must stay grounded).
- **Diversity:** Suggest a mix of easy, moderate, and hard clusters to build a balanced test set.
- **Coverage:** If you have multiple domains or guidelines in the input, suggest clusters that cover different domains/guidelines proportionally.
- **Avoid:** Grouping recommendations that have no clinical relationship (e.g., "carotid revascularization" + "AAA size thresholds" do not belong together unless the patient has both).
- **Prioritize:** Clusters that test multi-hop reasoning, uncertainty, or nuanced judgment are more valuable than clusters that are obvious sequential steps.

## Completeness Check

After generating the table:

1. List any recommendations that are **not assigned to a cluster** (i.e., "singleton" recommendations). These are valid — they test one recommendation in isolation — but note them for review. You (the clinician) may want to add them as easy single-recommendation vignettes, or you may decide they're redundant.

2. Note any **domains or guidelines underrepresented** in the clusters. This helps you decide if you need to generate additional clusters for balanced coverage.

3. Estimate **how many vignettes this will yield**: Assume ~80% of clusters will pass human verification (some will be rejected or merged during the checklist step). If you have 30 clusters, expect ~24 accepted vignettes.

## Output Format

Generate the table above in Markdown, then add:

---

**Unclustered recommendations (singletons):**
[List rec_ids that don't belong to any cluster]

**Domain/Guideline coverage summary:**
[e.g., "Carotid: 12 clusters, AAA: 4 clusters, Venous: 2 clusters"]

**Estimated vignette yield:**
[e.g., "32 clusters → expect ~25–26 accepted vignettes after human review"]

---

Do not generate vignettes. Do not decide answer keys. Simply suggest clusters with rationale and let the clinician decide which to use.
```

---

## After You Get the Output

### Step 1: Filter the Suggestions

The model will return 20–50 clusters depending on your input size. **You do not need to use all of them.** Filter by:

- **Difficulty:** If you want a balanced test set, keep a 30/40/30 split of easy/moderate/hard. Remove excess easy clusters.
- **Domain coverage:** Ensure you have clusters across your major domains (Carotid, AAA, PAD, Venous, etc.) in proportion to your system's use.
- **Redundancy:** If two clusters test the same decision (e.g., "CEA in symptomatic patient" appears in C2 and C5), keep the harder one, discard the other.
- **Vignette yield:** You want ~150–300 total vignettes in your final gold set. If you're generating clusters of 2–5 recommendations, plan for ~50–100 clusters. 50 × 0.8 ≈ 40 accepted vignettes. Adjust.

### Step 2: Feed Into Vignette Generation

For each cluster you keep:

```
Recommendations (from ESVS guidelines):
[Copy the text of each recommendation in the cluster from your CSV]

Optional context:
- Difficulty level: [easy / moderate / hard — use what the clustering prompt suggested]
- Additional context: [Any special notes, e.g., "high-risk patient", "include comorbidity"]
```

Paste this into the **Vignette Generation Prompt** from the prior artifact.

### Step 3: Verify and Track

As you generate vignettes from each cluster, track which clusters you've used (keep a list or spreadsheet). This lets you:
- Avoid regenerating the same cluster twice.
- Know how many vignettes you've created so far.
- Sample across clusters uniformly.

---

## Example: Small Carotid Input

If your input is a small carotid subset:

```csv
rec_id,text,class,loe,domain,condition,intervention
1.1,"In symptomatic patients with internal carotid artery stenosis ≥50%, we recommend revascularization",I,A,Carotid,Symptomatic ≥50%,CEA/CAS
1.2,"In asymptomatic patients with internal carotid artery stenosis ≥60%, we recommend screening for revascularization candidacy",IIa,B,Carotid,Asymptomatic ≥60%,Surveillance
2.1,"In patients undergoing carotid revascularization, we recommend dual antiplatelet therapy perioperatively",I,A,Carotid,Perioperative,Medical
2.2,"In patients with aspirin allergy, clopidogrel monotherapy is an alternative",IIb,C,Carotid,Perioperative aspirin allergy,Medical
3.1,"In high-risk patients, CAS may be considered as an alternative to CEA",IIb,B,Carotid,High-risk patient,CAS
```

The clustering prompt might suggest:

| Cluster ID | Recommendations | Rationale | Difficulty | Distractor Opportunities | Domain |
|---|---|---|---|---|---|
| C1 | 1.1, 1.2 | Tests the indications threshold: symptomatic (50%) vs. asymptomatic (60%). System must recognize both severity and symptom status as decision drivers. | Easy | Age, prior TIA, contralateral disease | Carotid |
| C2 | 1.1, 3.1 | Tests the choice between CEA and CAS in a symptomatic patient. System must consider patient risk factors and recommend the modality appropriate to the patient. | Moderate | Age, anatomy, comorbidities, operator availability | Carotid |
| C3 | 1.1, 2.1, 2.2 | Tests intervention decision (revascularize) + perioperative antiplatelet strategy, including handling of aspirin allergy. System must adjust management based on drug allergy. | Hard | Polypharmacy, bleeding risk, renal function, other anticoagulation needs | Carotid |

You'd keep all three, feed them one by one into the vignette generation prompt, verify each vignette with the checklist, and end up with ~2–3 vignettes per cluster.

---

## Tips for Batch Processing

If you have 100+ recommendations:

1. **Run the clustering prompt once** on all CSVs at once. The model will handle it, though the output table may be long.
2. **Export the table as CSV** (ask the model to output in CSV format if markdown is unwieldy). Then filter in Excel/Google Sheets.
3. **Batch vignette generation**: Once you've selected clusters, you can copy 5–10 clusters into a single vignette generation prompt and ask Claude to generate all 5–10 at once. Then verify each with the checklist.

---

## Common Questions

**Q: Should I include Class III ("do not") recommendations?**

A: Yes, but be intentional. Class III recommendations are great for testing whether the system knows what *not* to do. Cluster them with a Class I or IIa recommendation in the same domain so the system learns the distinction. Example: "In X patients, revascularize (Class I)" + "In Y patients, do not revascularize (Class III)" tests the decision boundary.

**Q: What if a recommendation doesn't fit any cluster?**

A: It's a singleton. These are fine — they test one recommendation in isolation. Use them as easy baseline vignettes. You can also set them aside if you're aiming for a more challenging test set focused on multi-hop reasoning.

**Q: Can I manually suggest clusters the model missed?**

A: Absolutely. The model is a suggestion engine, not exhaustive. If you see two recommendations that should be grouped, add them to your final list manually and feed them into vignette generation.

**Q: How do I balance easy vs. hard vignettes?**

A: Aim for a 30/40/30 split (easy/moderate/hard) to match clinical practice: most decisions are straightforward, some are nuanced. The clustering prompt will tag difficulty; filter to maintain the split.

---
