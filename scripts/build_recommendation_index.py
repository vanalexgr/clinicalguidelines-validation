"""Import recommendation CSVs into the recommendation index JSON.

Usage:
    python3 scripts/build_recommendation_index.py \
        --recommendations-dir recommendations/ \
        --output data/corpus/recommendation_index.json
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

FILE_TO_GUIDELINE = {
    "Management of Atherosclerotic Carotid and Vertebral Artery Disease.csv": (
        "ESVS_Carotid_2023"
    ),
    "ESVS_2024_AAA_recommendations_FINAL_v2.csv": "ESVS_AAA_2024",
    "ESVS_2020_ALI_recommendations_FINAL (1).csv": "ESVS_ALI_2020",
    "Antithrombotic Therapy for Vascular Diseases.csv": "ESVS_Antithrombotic_2023",
    "ESVS 2024 Management of Asymptomatic Lower Limb Peripheral Arterial Disease and "
    "Intermittent Claudication.csv": "ESVS_Asymptomatic_PAD_IC_2024",
    "CLTI_2019_recommendations_export_chapter_numbered.csv": "GVG_CLTI_2019",
    "Chronic Venous Disease of the Lower Limbs.csv": "ESVS_Chronic_Venous_Disease_2022",
    "venous_thrombosis.csv": "ESVS_Venous_Thrombosis_2021",
    "European Thoraco-Abdominal Aortic Diseases.csv": "ESVS_ThoracoAbdominal_2026",
    "Thoracic Aortic Pathologies Involving the Aortic Arch.csv": "ESVS_AorticArch_2024",
    "Management of Diseases of the Mesenteric and Renal Arteries and Veins.csv": (
        "ESVS_MesentericRenal_2017"
    ),
    "Management of Vascular Trauma.csv": "ESVS_VascularTrauma_2023",
    "Management of Vascular Graft and Endograft Infections recommendations.csv": (
        "ESVS_GraftInfections_2020"
    ),
    "vascular_access.csv": "ESVS_VascularAccess_2023",
}

GVG_CLASS_MAP = {
    "": "",
    "1 (Strong)": "I",
    "2 (Moderate)": "IIa",
    "2 (Weak)": "IIb",
    "3 (Weak)": "IIb",
    "Good practice statement": "GPS",
    "Good research statement": "GRS",
}

DEFAULT_RECOMMENDATIONS_DIR = Path("recommendations")
DEFAULT_OUTPUT_PATH = Path("data/corpus/recommendation_index.json")


def build_recommendation_index(
    recommendations_dir: str | Path = DEFAULT_RECOMMENDATIONS_DIR,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
) -> tuple[Path, int, int]:
    recommendations_path = Path(recommendations_dir)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    payload = _load_existing_payload(destination)
    payload["_comment"] = (
        "Recommendation index populated from guideline CSV exports. Imported recommendations "
        "use compound keys of the form '{canonical_guideline_id}:{rec_id}' to avoid cross-"
        "guideline collisions. Legacy bare-key placeholders are retained for backwards-compat "
        "with existing fixtures and sample citations."
    )
    payload["_format"] = {
        "key": "compound string '{canonical_guideline_id}:{rec_id}'",
        "legacy_keys": [
            "CAR-EXAMPLE",
            "AAA-EXAMPLE",
            "6.5.2",
            "7.2.1",
            "4.3.1",
            "3.1.2",
        ],
        "value": {
            "guideline": "canonical benchmark guideline ID, e.g. ESVS_Carotid_2023",
            "class": "I | IIa | IIb | III | GPS | GRS | ''",
            "level": "A | B | C | ''",
            "text": "verbatim recommendation text from the guideline",
        },
    }

    imported_entries = 0
    for filename, canonical_guideline_id in FILE_TO_GUIDELINE.items():
        csv_path = recommendations_path / filename
        imported_entries += _import_csv(
            csv_path=csv_path,
            canonical_guideline_id=canonical_guideline_id,
            payload=payload,
        )

    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return destination, imported_entries, len(FILE_TO_GUIDELINE)


def _load_existing_payload(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: recommendation index must be a JSON object.")
    return payload


def _import_csv(
    *,
    csv_path: Path,
    canonical_guideline_id: str,
    payload: dict[str, object],
) -> int:
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing recommendations CSV: {csv_path}")

    imported_rows = 0
    with csv_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"{csv_path}: missing CSV header row")

        for line_number, row in enumerate(reader, start=2):
            rec_id = (row.get("rec_id") or "").strip()
            text = (row.get("rec_text_verbatim") or "").strip()
            if not rec_id:
                raise ValueError(f"{csv_path}:{line_number}: missing rec_id")
            if not text:
                raise ValueError(f"{csv_path}:{line_number}: missing rec_text_verbatim")

            key = f"{canonical_guideline_id}:{rec_id}"
            if key in payload:
                raise ValueError(f"{csv_path}:{line_number}: duplicate recommendation key {key}")

            payload[key] = {
                "guideline": canonical_guideline_id,
                "class": _normalize_class(
                    row.get("class") or row.get("Class") or "",
                    guideline=canonical_guideline_id,
                ),
                "level": _normalize_level(row.get("level") or row.get("Level") or ""),
                "text": text,
            }
            imported_rows += 1
    return imported_rows


def _normalize_class(raw_value: str, *, guideline: str) -> str:
    value = raw_value.strip()
    if guideline == "GVG_CLTI_2019":
        return GVG_CLASS_MAP.get(value, value)

    if not value:
        return ""
    if "(" in value:
        value = value.split("(", 1)[0].strip()

    upper = value.upper().replace("L", "I")
    if upper in {"I", "IA", "IB"}:
        return "I"
    if upper == "III":
        return "III"
    if upper.endswith("A"):
        return "IIa"
    if upper.endswith("B"):
        return "IIb"
    return value


def _normalize_level(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if "(" in value:
        value = value.split("(", 1)[0].strip()
    upper = value.upper()
    if upper.startswith(("A", "B", "C")):
        return upper[0]
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recommendations-dir",
        default=str(DEFAULT_RECOMMENDATIONS_DIR),
        help="Directory containing the 14 recommendations CSV exports.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path to write the populated recommendation index JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _, imported_entries, guideline_count = build_recommendation_index(
        recommendations_dir=args.recommendations_dir,
        output_path=args.output,
    )
    print(f"Loaded {imported_entries} recommendations from {guideline_count} guidelines.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
