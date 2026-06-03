"""Write a fixture-shaped recommendation index template.

[AUTHOR ACTION] Replace this stub/template workflow with extraction from the real
ESVS corpus when the source material and canonical rec_id mapping are available.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_OUTPUT_PATH = Path("data/corpus/recommendation_index.json")
DEFAULT_INDEX_TEMPLATE = {
    "_comment": (
        "[AUTHOR ACTION] Populate from the real ESVS corpus. Keys are canonical rec_ids used "
        "in citations. Each non-comment entry must contain guideline, class, level, and text."
    ),
    "CAR-EXAMPLE": {
        "guideline": "ESVS_Carotid_2023",
        "class": "I",
        "level": "A",
        "text": (
            "Carotid endarterectomy is recommended in patients with recent symptomatic 50-99% "
            "stenosis, ideally within 14 days."
        ),
    },
    "AAA-EXAMPLE": {
        "guideline": "ESVS_AAA_2024",
        "class": "I",
        "level": "A",
        "text": "Elective AAA repair is recommended at a maximum diameter of 55 mm in men.",
    },
}


def build_recommendation_index_template(
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
    *,
    force: bool = False,
) -> Path:
    """Write the fixture-shaped recommendation index template to disk."""
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and not force:
        raise FileExistsError(
            f"{destination} already exists. Re-run with force=True to overwrite the template."
        )

    destination.write_text(
        json.dumps(DEFAULT_INDEX_TEMPLATE, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path to write the recommendation index template.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing output file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = build_recommendation_index_template(args.output, force=args.force)
    print(f"Wrote recommendation index template to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
