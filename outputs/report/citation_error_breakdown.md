# Citation Correctness & Hallucination Error Breakdown

## 1. Citation Correctness Tiers

| Tier | Description | n | % |
| --- | --- | --- | --- |
| `A_VERIFIED` | rec_id found · guideline + class + level all match | 256 | 98.8% |
| `B_METADATA_ERR` | rec_id found · class or level reported differently from index | 3 | 1.2% |

## 2. Hallucination Claim Types

| Type | Category | Description | Distinct claims | Judgment records |
| --- | --- | --- | --- | --- |
| `FALSE_POSITIVE` | Artefact | Claim matches a real indexed rec — judge flagged incorrectly | 12 | 13 |
| `SCOPE_EXPANSION` | Real error | Broadens a rec's applicability beyond its stated scope | 2 | 2 |
| `UNGROUNDED_CORRECT` | Artefact | Clinically accurate, but no retrievable basis in the locked corpus (e.g. guideline table content that was never indexed as a chunk) | 33 | 35 |
| `WRONG_APPLICATION` | Real error | Correct rec misapplied to wrong patient subgroup or scenario | 7 | 7 |
| `WRONG_THRESHOLD` | Real error | States a numeric decision threshold that does not match the guideline (diameter, stenosis, grade cut-off) | 4 | 6 |

## 3. Adjusted Hallucination Rate

| Category | Items | Rate |
| --- | --- | --- |
| Reported (any flag, any judge) | 10 | 18.2% |
| Items with only artefact flags | 6 | — |
| Items with ≥1 real error | 4 | 7.3% |
| No flags at all (clean) | 45 | — |

**Clean items:** Q001, Q002, Q003, Q004, Q005, Q006, Q008, Q009, Q010, Q011, Q012, Q013, Q014, Q015, Q016, Q100, Q101, Q102, Q103, Q104, Q107, Q108, Q111, Q112, Q113, Q114, Q115, Q116, Q117, Q118, Q119, Q120, Q121, Q122, Q123, Q126, Q128, Q200, Q201, Q203, Q204, Q205, Q210, Q211, Q212  
**Artefact-only items:** Q007, Q106, Q109, Q110, Q125, Q202  
**Items with real errors:** Q105, Q124, Q127, Q129

## 4. Clinical Risk Distribution

| Risk | Claim count |
| --- | --- |
| moderate | 15 |
| none | 48 |

## 5. Hallucination Flags Detail

| Item | Judge | Auto | Corrected | Risk | Claim |
| --- | --- | --- | --- | --- | --- |
| Q007 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | Ankle pressure 40 mmHg corresponds to WIfI ischemia grade 3 |
| Q007 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | Composite WIfI Stage 4 assignment based on the provided data |
| Q007 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | Ankle pressure 40 mmHg corresponds to WIfI ischemia grade 3 |
| Q007 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | Guidelines recommend urgent revascularization for Stage 4 limbs with severe ischemia |
| Q105 | claude-opus-4-7 | `OTHER` | `WRONG_APPLICATION` | moderate | WIfI stage 2 (intermediate limb threat) — derived from wrong ischemia grade |
| Q105 | claude-opus-4-7 | `OTHER` | `WRONG_APPLICATION` | moderate | ischemia grade 2 for ABI 0.38 and TcPO2 22 mmHg |
| Q105 | claude-opus-4-7 | `OTHER` | `WRONG_APPLICATION` | moderate | Ischemia grade 2 for ABI 0.38 / TcPO2 22 mmHg |
| Q105 | claude-opus-4-7 | `OTHER` | `WRONG_APPLICATION` | moderate | WIfI composite Stage 2 |
| Q105 | claude-opus-4-7 | `OTHER` | `WRONG_APPLICATION` | moderate | Applying the '>50% size reduction within 4 weeks' threshold as a general reassessment rule in this severe ischemia scenario |
| Q105 | claude-opus-4-7 | `OTHER` | `UNGROUNDED_CORRECT` | none | citations [8] and [10] referring to narrative/GLASS staging not present in retrieved passages |
| Q105 | claude-opus-4-7 | `OTHER` | `UNGROUNDED_CORRECT` | none | 'PTA-plantar arch target' specific anatomic recommendation not in retrieved passages |
| Q105 | o3 | `OTHER` | `WRONG_APPLICATION` | moderate | Ischemia grade 2 for ABI 0.38/TcPO2 22 |
| Q105 | o3 | `OTHER` | `WRONG_APPLICATION` | moderate | Overall WIfI Stage 2 assignment |
| Q106 | claude-opus-4-7 | `METADATA_INFLATION` | `UNGROUNDED_CORRECT` | none | Class I, Level B for stepwise escalation attributed to rec [8]/rec 45 — rec 45 text is in passages but the agent's citation list does not actually contain rec 45; the labeling within evidence section is inconsistent |
| Q106 | claude-opus-4-7 | `CITATION_MISMATCH` | `UNGROUNDED_CORRECT` | none | Cilostazol/naftidrofuryl recommendation attributed to rec 51 but rec 51 not in citation list provided |
| Q106 | claude-opus-4-7 | `METADATA_INFLATION` | `UNGROUNDED_CORRECT` | none | Class IIa, Level C [6] applied to this focal SFA occlusion (rec 69 actually addresses long/heavily calcified occlusions) |
| Q106 | claude-opus-4-7 | `METADATA_INFLATION` | `UNGROUNDED_CORRECT` | none | Class I, Level B for stepwise escalation — class/level matches rec 45 but is attached correctly; not a hallucination |
| Q106 | claude-opus-4-7 | `METADATA_INFLATION` | `UNGROUNDED_CORRECT` | none | Class I, Level B for stepwise escalation [8] — correct class/level matches Rec 45 |
| Q106 | claude-opus-4-7 | `METADATA_INFLATION` | `UNGROUNDED_CORRECT` | none | Class IIa, Level C [6] applied to a focal 10 cm SFA occlusion — Rec 69 applies to long heavily calcified occlusions, not the lesion described |
| Q106 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | "endovascular therapy is typically considered first-line for most femoropopliteal occlusions" |
| Q109 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | "in situ reconstruction after excision is the usual definitive pathway mentioned in the broader context" |
| Q109 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | "in situ reconstruction after excision is the usual definitive pathway" |
| Q110 | claude-opus-4-7 | `OTHER` | `UNGROUNDED_CORRECT` | none | ESVS Grade 2 injury — pseudoaneurysm with intact bifurcation (grade classification applied to CFA without explicit guideline text for femoral grading shown in retrieved passages — though plausible from general grading table) |
| Q110 | claude-opus-4-7 | `OTHER` | `UNGROUNDED_CORRECT` | none | structured duplex or CTA surveillance post-operatively (no retrieved passage specifies surveillance modality/schedule) |
| Q110 | claude-opus-4-7 | `OTHER` | `UNGROUNDED_CORRECT` | none | antibiotic prophylaxis is advised in trauma surgery when prosthetic material is implanted |
| Q110 | claude-opus-4-7 | `OTHER` | `UNGROUNDED_CORRECT` | none | Surveillance imaging (duplex US or CTA) is recommended after vascular repair |
| Q110 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | Peri-operative antibiotic prophylaxis recommendation |
| Q110 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | Structured duplex or CTA surveillance after repair |
| Q110 | o3 | `OTHER` | `FALSE_POSITIVE` | none | "open surgical repair is the standard" for extremity Grade 2 injuries |
| Q110 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | antibiotic prophylaxis recommendation limited to prosthetic use |
| Q110 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | post-operative imaging surveillance recommendation |
| Q110 | o3 | `OTHER` | `FALSE_POSITIVE` | none | "open surgical repair is the standard" for Grade 2 extremity trauma |
| Q110 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | "peri-operative antibiotic prophylaxis is advised when prosthetic grafts are used" (no support) |
| Q110 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | "surveillance imaging is recommended after vascular repair" |
| Q124 | o3 | `OTHER` | `SCOPE_EXPANSION` | moderate | Symptomatic isolated calf DVT should be treated with anticoagulation |
| Q124 | o3 | `OTHER` | `SCOPE_EXPANSION` | moderate | Symptomatic calf DVT — including isolated soleal vein thrombosis — should be treated with anticoagulation |
| Q125 | o3 | `OTHER` | `FALSE_POSITIVE` | none | "Routine dual antiplatelet therapy ... only for ≤30 days" for vein bypass |
| Q127 | o3 | `OTHER` | `WRONG_THRESHOLD` | moderate | surgery is not recommended for asymptomatic stenosis <70% |
| Q127 | o3 | `OTHER` | `WRONG_THRESHOLD` | moderate | Surgery is not recommended for asymptomatic carotid stenosis <70% |
| Q127 | o3 | `OTHER` | `WRONG_THRESHOLD` | moderate | surgery is not recommended for asymptomatic stenosis <70% |
| Q129 | claude-opus-4-7 | `OTHER` | `WRONG_THRESHOLD` | moderate | Both aneurysms exceed ESVS intervention thresholds |
| Q129 | claude-opus-4-7 | `OTHER` | `FALSE_POSITIVE` | none | Rec 54: TEVAR primary option for chronic thoracic aneurysm (applied to degenerative TAA, but passage specifies chronic type B dissection) |
| Q129 | claude-opus-4-7 | `OTHER` | `WRONG_THRESHOLD` | moderate | Both aneurysms exceed ESVS intervention thresholds |
| Q129 | claude-opus-4-7 | `OTHER` | `FALSE_POSITIVE` | none | Rec 54: TEVAR primary option for chronic thoracic aneurysm (passage actually addresses chronic type B dissection with aneurysm formation) |
| Q129 | o3 | `OTHER` | `WRONG_THRESHOLD` | moderate | complex AAA may justify earlier repair below 55 mm |
| Q202 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | C0s definition and all listed class definitions not present in retrieved text |
| Q202 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | C0s definition |
| Q202 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | C1 definition |
| Q202 | o3 | `OTHER` | `FALSE_POSITIVE` | none | C2 definition |
| Q202 | o3 | `OTHER` | `FALSE_POSITIVE` | none | C3 definition |
| Q202 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | C4/C4a/C4b/C4c definitions |
| Q202 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | C5 definition |
| Q202 | o3 | `OTHER` | `FALSE_POSITIVE` | none | C6 definition |
| Q202 | o3 | `OTHER` | `FALSE_POSITIVE` | none | C2r definition |
| Q202 | o3 | `OTHER` | `FALSE_POSITIVE` | none | C6r definition |
| Q202 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | C0s symptomatic definition |
| Q202 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | C1 telangiectasies definition |
| Q202 | o3 | `OTHER` | `FALSE_POSITIVE` | none | C2 varicose veins definition |
| Q202 | o3 | `OTHER` | `FALSE_POSITIVE` | none | C3 oedema definition |
| Q202 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | C4 subclass definitions |
| Q202 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | C5 healed ulcer |
| Q202 | o3 | `OTHER` | `UNGROUNDED_CORRECT` | none | C6 active ulcer |
| Q202 | o3 | `OTHER` | `FALSE_POSITIVE` | none | C2r, C6r definitions |
