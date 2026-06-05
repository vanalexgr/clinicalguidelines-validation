#!/usr/bin/env python3
"""Generate 30 clinical vignettes for the clinicalguidelines.io benchmark.

Covers:
  - B_complete_case  (gate: suppress) – 12 vignettes
  - C_underspecified (gate: fire)     – 10 vignettes
  - E_multiguideline (gate: suppress) – 8 vignettes

Output: data/benchmark/vignettes_30.jsonl  (schema-compatible, verified=false)

Usage:
    ANTHROPIC_API_KEY=sk-... python scripts/generate_vignettes.py
    # or with --dry-run to print prompts without calling the API
    python scripts/generate_vignettes.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.common.llm import AnthropicClient

OUT_PATH = REPO_ROOT / "data" / "benchmark" / "vignettes_30.jsonl"
MODEL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Guideline ID registry (matches existing benchmark entries)
# ---------------------------------------------------------------------------
GUIDELINE_IDS: dict[str, str] = {
    "carotid": "ESVS_Carotid_2023",
    "aaa": "ESVS_AAA_2024",
    "ali": "ESVS_ALI_2020",
    "pad": "ESVS_PAD_2024",
    "clti": "GVG_CLTI_2019",
    "dvt": "ESVS_Venous_Thrombosis_2021",
    "cvd": "ESVS_CVD_2022",
    "antithrombotic": "ESVS_Antithrombotic_2023",
    "thoracic": "ESVS_Thoracic_2026",
    "aortic_arch": "ESVS_AorticArch_2019",
    "graft_infection": "ESVS_GraftInfection_2020",
    "trauma": "ESVS_Trauma_2025",
    "vascular_access": "ESVS_VascularAccess",
}

# ---------------------------------------------------------------------------
# 30 cluster definitions
# Each "topic" field is the briefing passed to Claude for vignette generation.
# ---------------------------------------------------------------------------
CLUSTERS: list[dict] = [
    # ── B_complete_case (gate: suppress) ─────────────────────────────────
    {
        "id": "Q100",
        "query_type": "B_complete_case",
        "safety_critical": True,
        "gate": "suppress",
        "guidelines": ["carotid"],
        "topic": (
            "65-year-old man, recent left-eye amaurosis fugax (3 weeks ago, resolved), "
            "duplex shows 75% left ICA stenosis and 50% right ICA stenosis. Fit for surgery. "
            "On aspirin 75mg and ramipril. Test the system on: symptomatic threshold for "
            "revascularisation (left side), age preference for CEA vs CAS, and whether the "
            "asymptomatic contralateral 50% stenosis changes management. "
            "Distractor: mild right-hand tremor (unrelated neurology), sertraline use."
        ),
        "required_parameters": [],
        "difficulty": "moderate",
    },
    {
        "id": "Q101",
        "query_type": "B_complete_case",
        "safety_critical": False,
        "gate": "suppress",
        "guidelines": ["carotid"],
        "topic": (
            "72-year-old woman, asymptomatic 65% right ICA stenosis on routine screening. "
            "No prior neurological events, well-controlled hypertension, non-smoker. "
            "Test: asymptomatic stenosis management — medical optimisation first, "
            "surveillance duplex interval, and criteria that would shift towards intervention. "
            "Distractor: incidental mild left subclavian stenosis (non-haemodynamic)."
        ),
        "required_parameters": [],
        "difficulty": "easy",
    },
    {
        "id": "Q102",
        "query_type": "B_complete_case",
        "safety_critical": False,
        "gate": "suppress",
        "guidelines": ["aaa"],
        "topic": (
            "68-year-old fit man, 57mm infrarenal AAA on surveillance CT, growing 3mm/year. "
            "Asymptomatic. EVAR-suitable anatomy (neck 22mm, angulation 35°). "
            "Test: elective repair threshold for men, EVAR vs open preference, "
            "standard operative mortality benchmark. "
            "Distractor: incidental 4mm right common iliac aneurysm, well-controlled hypertension."
        ),
        "required_parameters": [],
        "difficulty": "easy",
    },
    {
        "id": "Q103",
        "query_type": "B_complete_case",
        "safety_critical": False,
        "gate": "suppress",
        "guidelines": ["aaa"],
        "topic": (
            "63-year-old woman, 48mm AAA with growth of 7mm over 6 months. CKD (eGFR 52). "
            "No symptoms. Test: lower size threshold in women, rapid growth rate as an additional "
            "indication, and how CKD affects EVAR vs open selection. "
            "Distractor: mild aortic valve regurgitation on echo, seasonal allergies."
        ),
        "required_parameters": [],
        "difficulty": "moderate",
    },
    {
        "id": "Q104",
        "query_type": "B_complete_case",
        "safety_critical": True,
        "gate": "suppress",
        "guidelines": ["ali"],
        "topic": (
            "58-year-old man, known AF not anticoagulated, 4-hour history of sudden cold pale "
            "pulseless right leg with motor weakness and absent sensation below the knee. "
            "Femoral pulse present, no popliteal pulse right. "
            "Test: Rutherford IIb classification, immediate revascularisation (surgical embolectomy "
            "vs CDT), and heparin initiation. "
            "Distractor: mildly elevated CRP, dental procedure 2 weeks ago."
        ),
        "required_parameters": [],
        "difficulty": "hard",
    },
    {
        "id": "Q105",
        "query_type": "B_complete_case",
        "safety_critical": True,
        "gate": "suppress",
        "guidelines": ["clti"],
        "topic": (
            "70-year-old diabetic woman, rest pain and dry gangrenous right first toe. "
            "ABI 0.38, TcPO2 22mmHg. CTA: SFA and popliteal occlusion, tibial vessels patent to ankle. "
            "Test: WIfI staging, angiosome-targeted revascularisation, endovascular vs surgical bypass "
            "decision, and wound care integration. "
            "Distractor: UTI on admission culture, controlled hypothyroidism."
        ),
        "required_parameters": [],
        "difficulty": "hard",
    },
    {
        "id": "Q106",
        "query_type": "B_complete_case",
        "safety_critical": False,
        "gate": "suppress",
        "guidelines": ["pad"],
        "topic": (
            "62-year-old male smoker, disabling claudication at 150m despite completing "
            "a 6-month supervised exercise programme. ABI 0.62, CTA shows 10cm right SFA occlusion. "
            "No rest pain, no tissue loss. Test: failed conservative therapy as prerequisite, "
            "endovascular-first strategy for SFA disease, statin and antiplatelet continuation. "
            "Distractor: well-controlled hypercholesterolaemia, moderate BPH on tamsulosin."
        ),
        "required_parameters": [],
        "difficulty": "moderate",
    },
    {
        "id": "Q107",
        "query_type": "B_complete_case",
        "safety_critical": False,
        "gate": "suppress",
        "guidelines": ["dvt"],
        "topic": (
            "42-year-old woman, first-ever proximal DVT (femoral and popliteal veins) confirmed "
            "on ultrasound, provoked by prolonged air travel. Haemodynamically stable. "
            "No family history of thrombophilia, no cancer. "
            "Test: outpatient management, DOAC choice, duration (3 months for provoked proximal), "
            "and indication for follow-up imaging. "
            "Distractor: iron-deficiency anaemia on FBC, OCP use (contributing risk factor)."
        ),
        "required_parameters": [],
        "difficulty": "easy",
    },
    {
        "id": "Q108",
        "query_type": "B_complete_case",
        "safety_critical": False,
        "gate": "suppress",
        "guidelines": ["cvd"],
        "topic": (
            "55-year-old woman, CEAP C5 (healed ulcer) with symptomatic right great saphenous vein "
            "reflux confirmed on duplex (SFJ to below knee). Two years of compression hosiery. "
            "Test: thermal ablation preference over foam sclerotherapy, hosiery as adjunct, "
            "post-ablation surveillance plan. "
            "Distractor: mild left-leg pitting oedema without reflux (likely lymphoedema)."
        ),
        "required_parameters": [],
        "difficulty": "easy",
    },
    {
        "id": "Q109",
        "query_type": "B_complete_case",
        "safety_critical": True,
        "gate": "suppress",
        "guidelines": ["graft_infection"],
        "topic": (
            "68-year-old man with a 3-year-old aortofemoral Dacron bypass. Presents with fever, "
            "raised CRP/WBC, and perigraft air + fluid on CT (MAGIC grade 3). No bacteraemia. "
            "Test: MAGIC grading, indication for complete graft excision with in-situ reconstruction "
            "vs extra-anatomical bypass, antibiotic duration. "
            "Distractor: mild post-op anaemia, recent cystoscopy 3 weeks prior."
        ),
        "required_parameters": [],
        "difficulty": "hard",
    },
    {
        "id": "Q110",
        "query_type": "B_complete_case",
        "safety_critical": True,
        "gate": "suppress",
        "guidelines": ["trauma"],
        "topic": (
            "28-year-old man, stab wound right groin, BP 105/70 (haemodynamically stable). "
            "Hard sign: expanding haematoma with bruit. CTA: 2cm pseudoaneurysm of right common "
            "femoral artery. Test: hard signs mandate intervention, endovascular vs open repair, "
            "antibiotic prophylaxis and follow-up. "
            "Distractor: mild hyponatraemia, recreational cannabis use."
        ),
        "required_parameters": [],
        "difficulty": "moderate",
    },
    {
        "id": "Q111",
        "query_type": "B_complete_case",
        "safety_critical": False,
        "gate": "suppress",
        "guidelines": ["vascular_access"],
        "topic": (
            "64-year-old woman with ESKD, haemodialysis starting in 6 weeks. "
            "Adequate cephalic vein >2.5mm on duplex. Non-dominant left arm preferred. "
            "No prior access surgery. Test: radiocephalic AVF as first-choice access, "
            "pre-op vein mapping indication, and maturation assessment criteria. "
            "Distractor: type 2 diabetes, mild left-shoulder osteoarthritis."
        ),
        "required_parameters": [],
        "difficulty": "easy",
    },

    # ── C_underspecified (gate: fire) ─────────────────────────────────────
    {
        "id": "Q112",
        "query_type": "C_underspecified",
        "safety_critical": True,
        "gate": "fire",
        "guidelines": ["carotid"],
        "topic": (
            "Clinician asks about managing a patient with carotid stenosis but gives NO "
            "information on symptomatic status or stenosis degree. "
            "Vignette should read as a brief, incomplete query. "
            "The system must ask for symptomatic status and degree of stenosis before advising."
        ),
        "required_parameters": ["symptomatic_status", "degree_of_stenosis"],
        "difficulty": "easy",
    },
    {
        "id": "Q113",
        "query_type": "C_underspecified",
        "safety_critical": False,
        "gate": "fire",
        "guidelines": ["aaa"],
        "topic": (
            "Clinician asks 'my patient has an aortic aneurysm, should we repair it?' "
            "without providing diameter, patient sex, symptoms, or fitness for surgery. "
            "Vignette is intentionally brief and vague. "
            "System must request diameter, sex, symptoms, and fitness before advising."
        ),
        "required_parameters": ["aneurysm_diameter", "patient_sex", "symptomatic_status", "surgical_fitness"],
        "difficulty": "easy",
    },
    {
        "id": "Q114",
        "query_type": "C_underspecified",
        "safety_critical": False,
        "gate": "fire",
        "guidelines": ["dvt"],
        "topic": (
            "Clinician asks what anticoagulant to use and for how long in a patient with DVT. "
            "No proximal vs distal location, no provoked vs unprovoked status, no cancer history. "
            "System must request location, provoked status, prior VTE history, and cancer history."
        ),
        "required_parameters": ["dvt_location_proximal_vs_distal", "provoked_vs_unprovoked", "prior_vte_history", "cancer_history"],
        "difficulty": "easy",
    },
    {
        "id": "Q115",
        "query_type": "C_underspecified",
        "safety_critical": False,
        "gate": "fire",
        "guidelines": ["pad"],
        "topic": (
            "Clinician mentions 'intermittent claudication in an elderly patient, what treatment?' "
            "No ABI provided, no claudication distance, no mention of exercise programme. "
            "System must request ABI, functional severity, and whether supervised exercise was tried."
        ),
        "required_parameters": ["abi_value", "claudication_distance", "supervised_exercise_status"],
        "difficulty": "easy",
    },
    {
        "id": "Q116",
        "query_type": "C_underspecified",
        "safety_critical": True,
        "gate": "fire",
        "guidelines": ["clti"],
        "topic": (
            "Clinician asks 'diabetic patient with foot problems — is revascularisation needed?' "
            "No wound grade, no perfusion measurements (ABI/TcPO2), no infection grade given. "
            "System must request WIfI staging parameters before recommending revascularisation."
        ),
        "required_parameters": ["wound_grade", "ischaemia_level_abi_or_tcpo2", "infection_grade"],
        "difficulty": "moderate",
    },
    {
        "id": "Q117",
        "query_type": "C_underspecified",
        "safety_critical": True,
        "gate": "fire",
        "guidelines": ["ali"],
        "topic": (
            "Clinician reports 'cold leg, what do I do?' with no symptom duration, "
            "no Rutherford classification, no motor or sensory status, no Doppler findings. "
            "System must request onset timing, motor/sensory status, and Rutherford class "
            "before recommending CDT vs surgical embolectomy."
        ),
        "required_parameters": ["symptom_duration", "motor_status", "sensory_status", "rutherford_class"],
        "difficulty": "moderate",
    },
    {
        "id": "Q118",
        "query_type": "C_underspecified",
        "safety_critical": False,
        "gate": "fire",
        "guidelines": ["cvd"],
        "topic": (
            "Clinician asks about treatment for varicose veins with no CEAP class, "
            "no duplex findings, and no reflux anatomy described. "
            "System must request CEAP clinical class and duplex ultrasound results "
            "before recommending ablation vs compression-only."
        ),
        "required_parameters": ["ceap_clinical_class", "duplex_reflux_findings"],
        "difficulty": "easy",
    },
    {
        "id": "Q119",
        "query_type": "C_underspecified",
        "safety_critical": False,
        "gate": "fire",
        "guidelines": ["antithrombotic"],
        "topic": (
            "Clinician asks 'what antiplatelet after arterial surgery?' without specifying "
            "type of procedure, arterial territory, or prior antiplatelet status. "
            "System must clarify procedure type and territory before advising."
        ),
        "required_parameters": ["procedure_type", "arterial_territory", "prior_antiplatelet_status"],
        "difficulty": "easy",
    },
    {
        "id": "Q120",
        "query_type": "C_underspecified",
        "safety_critical": False,
        "gate": "fire",
        "guidelines": ["thoracic"],
        "topic": (
            "Clinician reports 'thoracic aortic aneurysm on CT, should we repair?' "
            "No diameter, no anatomical segment (descending vs arch), no symptoms provided. "
            "System must request diameter, anatomical segment, and symptomatic status."
        ),
        "required_parameters": ["aneurysm_diameter", "anatomical_segment", "symptomatic_status"],
        "difficulty": "moderate",
    },
    {
        "id": "Q121",
        "query_type": "C_underspecified",
        "safety_critical": True,
        "gate": "fire",
        "guidelines": ["graft_infection"],
        "topic": (
            "Clinician mentions 'possible graft infection in my patient' with no CT findings, "
            "no MAGIC grade, no microbiology, no graft location or type. "
            "System must request CT/imaging findings, MAGIC grade, microbiology, "
            "and graft type/location before recommending management."
        ),
        "required_parameters": ["imaging_findings_ct", "magic_grade", "microbiology_results", "graft_type_and_location"],
        "difficulty": "hard",
    },

    # ── E_multiguideline (gate: suppress) ────────────────────────────────
    {
        "id": "Q122",
        "query_type": "E_multiguideline",
        "safety_critical": True,
        "gate": "suppress",
        "guidelines": ["carotid", "antithrombotic"],
        "topic": (
            "67-year-old man, symptomatic 70% left ICA stenosis (TIA 10 days ago), "
            "scheduled CEA, also on warfarin for AF (INR 2.4). "
            "Test: timing of CEA after TIA, bridging anticoagulation peri-operatively, "
            "and transition to antiplatelet post-CEA. "
            "Requires Carotid + Antithrombotic guidelines. "
            "Distractor: mild thrombocytopaenia (platelets 112), aortic sclerosis."
        ),
        "required_parameters": [],
        "difficulty": "hard",
    },
    {
        "id": "Q123",
        "query_type": "E_multiguideline",
        "safety_critical": False,
        "gate": "suppress",
        "guidelines": ["aaa", "antithrombotic"],
        "topic": (
            "72-year-old man, 6 weeks post-EVAR for 59mm infrarenal AAA, no endoleak. "
            "Currently on aspirin 75mg only. GP asks about optimal long-term antithrombotic. "
            "Test: post-EVAR antiplatelet strategy and statin prescription. "
            "Requires AAA + Antithrombotic guidelines. "
            "Distractor: GI bleed 4 months prior (resolved, no active lesion), eGFR 71."
        ),
        "required_parameters": [],
        "difficulty": "moderate",
    },
    {
        "id": "Q124",
        "query_type": "E_multiguideline",
        "safety_critical": False,
        "gate": "suppress",
        "guidelines": ["pad", "dvt"],
        "topic": (
            "58-year-old man, known PAD (ABI 0.71, stable claudication), "
            "presents with acute right calf DVT (soleal vein) after transatlantic flight. "
            "Test: anticoagulation for isolated distal DVT, bleeding risk context of PAD, "
            "and conflict between exercise walking advice and DVT treatment. "
            "Requires PAD + DVT guidelines. "
            "Distractor: mild CKD (eGFR 58), recent NSAID use for back pain."
        ),
        "required_parameters": [],
        "difficulty": "hard",
    },
    {
        "id": "Q125",
        "query_type": "E_multiguideline",
        "safety_critical": True,
        "gate": "suppress",
        "guidelines": ["clti", "antithrombotic"],
        "topic": (
            "74-year-old woman, CLTI with tissue loss (WIfI 3-3-1), femoral-to-peroneal GSV bypass "
            "done 3 days ago. What antithrombotic regimen post-bypass? "
            "Test: post-bypass antithrombotic strategy (aspirin, DOAC, or VKA) based on conduit "
            "type and tibial runoff, and wound care integration. "
            "Requires CLTI + Antithrombotic guidelines. "
            "Distractor: HbA1c 9.2%, microalbuminuria, mild post-op fever."
        ),
        "required_parameters": [],
        "difficulty": "hard",
    },
    {
        "id": "Q126",
        "query_type": "E_multiguideline",
        "safety_critical": True,
        "gate": "suppress",
        "guidelines": ["ali", "antithrombotic"],
        "topic": (
            "52-year-old woman, Rutherford IIa ALI right leg, CTA: embolus at popliteal "
            "bifurcation. CDT initiated. Telemetry catches paroxysmal AF (presumed embolic source). "
            "Test: heparin co-infusion protocol and APTT monitoring during CDT, "
            "then transition to long-term anticoagulation for AF post-procedure. "
            "Requires ALI + Antithrombotic guidelines. "
            "Distractor: mild troponin rise, prior GI intolerance to NSAIDs."
        ),
        "required_parameters": [],
        "difficulty": "hard",
    },
    {
        "id": "Q127",
        "query_type": "E_multiguideline",
        "safety_critical": False,
        "gate": "suppress",
        "guidelines": ["carotid", "pad"],
        "topic": (
            "69-year-old man, incidental 62% asymptomatic right ICA stenosis found on workup "
            "for severe bilateral claudication (ABI 0.55), both confirmed on imaging. "
            "Patient asks which problem gets treated first. "
            "Test: asymptomatic carotid below intervention threshold (surveillance only) vs. "
            "SFA claudication intervention after failed exercise therapy. Priority decision. "
            "Requires Carotid + PAD guidelines. "
            "Distractor: 38mm infrarenal aorta (no repair threshold), mild anaemia."
        ),
        "required_parameters": [],
        "difficulty": "hard",
    },
    {
        "id": "Q128",
        "query_type": "E_multiguideline",
        "safety_critical": False,
        "gate": "suppress",
        "guidelines": ["dvt", "cvd"],
        "topic": (
            "48-year-old woman, proximal DVT 2 years ago, anticoagulated 6 months then stopped. "
            "Now: post-thrombotic syndrome with popliteal occlusion and GSV reflux on duplex, "
            "CEAP C4 skin changes. Should anticoagulation be restarted? Can venous intervention help? "
            "Test: extended anticoagulation decision (residual obstruction + D-dimer) vs. "
            "CVI management (compression, endovenous therapy). "
            "Requires DVT + CVD guidelines. "
            "Distractor: BMI 32, sedentary desk job, no malignancy."
        ),
        "required_parameters": [],
        "difficulty": "hard",
    },
    {
        "id": "Q129",
        "query_type": "E_multiguideline",
        "safety_critical": False,
        "gate": "suppress",
        "guidelines": ["aaa", "thoracic"],
        "topic": (
            "71-year-old man: 52mm infrarenal AAA extending to involve the left renal artery, "
            "AND a separate 58mm descending thoracic aortic aneurysm on the same CT. "
            "Test: which segment to repair first (thoracic at threshold vs infrarenal just below), "
            "staged vs simultaneous repair, and endovascular options for each. "
            "Requires AAA + Thoracic guidelines. "
            "Distractor: COPD (FEV1 68%), moderate aortic valve regurgitation, CKD."
        ),
        "required_parameters": [],
        "difficulty": "hard",
    },
]

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are a vascular surgeon creating realistic clinical vignettes for a guideline decision support system benchmark.

Rules for vignette generation:
1. No leakage — do not copy exact numeric thresholds from guidelines; use clinical language.
2. Add 2-3 true-but-irrelevant clinical details as distractors.
3. Realistic clinical tone — not a textbook, not an exam question.
4. For C_underspecified cases: the query must be deliberately vague on key parameters so the system MUST ask before answering.
5. For E_multiguideline: the case must genuinely require reasoning across both named guidelines.
6. Keep vignette text concise: 80-180 words for B/E, 30-60 words for C (short vague queries).

Return a JSON object with exactly these fields:
{
  "query": "<the clinical query as the clinician would type it>",
  "answer_key": "<concise gold standard answer, 2-5 sentences>",
  "notes": "<1-2 sentences on what makes this case interesting or tricky>"
}
Do not include any text outside the JSON object."""


def make_user_prompt(cluster: dict) -> str:
    guide_names = [GUIDELINE_IDS[g] for g in cluster["guidelines"]]
    return (
        f"Query type: {cluster['query_type']}\n"
        f"Gate expected: {cluster['gate']}\n"
        f"Guidelines: {', '.join(guide_names)}\n"
        f"Difficulty: {cluster['difficulty']}\n\n"
        f"Scenario brief:\n{cluster['topic']}\n\n"
        + (
            f"Required parameters (for gate=fire, the answer_key should say the system must ask for these): "
            f"{', '.join(cluster['required_parameters'])}\n"
            if cluster["required_parameters"]
            else ""
        )
        + "\nGenerate the vignette JSON now."
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Generate 30 clinical vignettes")
    parser.add_argument("--dry-run", action="store_true", help="Print prompts without calling the API")
    parser.add_argument("--start-from", type=str, default=None, help="Skip clusters before this ID (e.g. Q110)")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not args.dry_run and not api_key:
        sys.exit("ANTHROPIC_API_KEY not set. Run with --dry-run to test without the API.")

    client = AnthropicClient(api_key=api_key) if not args.dry_run else None

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    skip = args.start_from is not None

    with OUT_PATH.open("w", encoding="utf-8") as fout:
        for cluster in CLUSTERS:
            cid = cluster["id"]

            if skip:
                if cid == args.start_from:
                    skip = False
                else:
                    print(f"  skip {cid}")
                    continue

            user_prompt = make_user_prompt(cluster)

            if args.dry_run:
                print(f"\n{'='*60}\n{cid}  {cluster['query_type']}  gate={cluster['gate']}")
                print(user_prompt)
                continue

            print(f"  generating {cid} ...", end=" ", flush=True)
            try:
                result = client.complete(
                    system=SYSTEM_PROMPT,
                    user=user_prompt,
                    model=MODEL,
                    max_tokens=800,
                    json_mode=True,
                )
                llm_data = json.loads(result.text)
            except Exception as exc:
                print(f"ERROR: {exc}")
                continue

            guide_ids = [GUIDELINE_IDS[g] for g in cluster["guidelines"]]

            entry = {
                "id": cid,
                "query_type": cluster["query_type"],
                "safety_critical": cluster["safety_critical"],
                "turns": [{"role": "user", "content": llm_data["query"]}],
                "gold": {
                    "expected_guidelines": guide_ids,
                    "gate_expected": cluster["gate"],
                    "required_parameters": cluster["required_parameters"],
                    "acceptable_refusal": False,
                    "answer_key": llm_data["answer_key"],
                    "notes": llm_data.get("notes", ""),
                },
                "verified": False,
            }

            fout.write(json.dumps(entry, ensure_ascii=False) + "\n")
            print(f"ok  ({result.tokens_out} tokens)")

    if not args.dry_run:
        count = sum(1 for _ in OUT_PATH.read_text().splitlines() if _.strip())
        print(f"\nWrote {count} vignettes → {OUT_PATH}")


if __name__ == "__main__":
    main()
