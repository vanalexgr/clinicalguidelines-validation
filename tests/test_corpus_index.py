from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.common.schemas import Citation
from src.corpus.build_index import DEFAULT_INDEX_TEMPLATE, build_recommendation_index_template
from src.corpus.index import evaluate_citation_existence, load_recommendation_index

FIXTURE_PATH = Path("tests/fixtures/recommendation_index.fixture.json")


def test_load_recommendation_index_fixture() -> None:
    index = load_recommendation_index(FIXTURE_PATH)

    assert sorted(index) == ["AAA-EXAMPLE", "CAR-EXAMPLE"]
    assert index["CAR-EXAMPLE"].guideline == "ESVS_Carotid_2023"
    assert index["CAR-EXAMPLE"].class_ == "I"
    assert index["CAR-EXAMPLE"].level == "A"


def test_load_recommendation_index_rejects_bad_entry(tmp_path: Path) -> None:
    path = tmp_path / "bad_index.json"
    path.write_text(
        json.dumps(
            {
                "BAD-ENTRY": {
                    "guideline": "ESVS_Test_2024",
                    "class": "I",
                    "text": "missing level field",
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        load_recommendation_index(path)

    assert f"{path}:BAD-ENTRY:" in str(exc_info.value)
    assert "level: Field required" in str(exc_info.value)


def test_evaluate_citation_existence_returns_accuracy_and_details() -> None:
    index = load_recommendation_index(FIXTURE_PATH)
    citations = [
        Citation(rec_id="CAR-EXAMPLE", class_="I", level="A", guideline="ESVS_Carotid_2023"),
        Citation(rec_id="AAA-EXAMPLE", class_="IIa", level="A", guideline="ESVS_AAA_2024"),
        Citation(rec_id="MISSING-REC", class_="I", level="A"),
    ]

    result = evaluate_citation_existence(citations, index)

    assert result.total_citations == 3
    assert result.matched_citations == 1
    assert result.existence_accuracy == pytest.approx(1 / 3)

    assert result.details[0].matched is True
    assert result.details[0].exists is True
    assert result.details[1].exists is True
    assert result.details[1].class_match is False
    assert result.details[1].level_match is True
    assert result.details[1].matched is False
    assert result.details[2].exists is False
    assert result.details[2].matched is False


def test_evaluate_citation_existence_vacuous_accuracy_for_empty_list() -> None:
    index = load_recommendation_index(FIXTURE_PATH)

    result = evaluate_citation_existence([], index)

    assert result.total_citations == 0
    assert result.matched_citations == 0
    assert result.existence_accuracy == 1.0
    assert result.details == []


def test_build_recommendation_index_template_writes_stub(tmp_path: Path) -> None:
    output_path = tmp_path / "recommendation_index.json"

    built_path = build_recommendation_index_template(output_path)
    payload = json.loads(built_path.read_text(encoding="utf-8"))

    assert built_path == output_path
    assert payload == DEFAULT_INDEX_TEMPLATE


def test_build_recommendation_index_template_requires_force_to_overwrite(tmp_path: Path) -> None:
    output_path = tmp_path / "recommendation_index.json"
    build_recommendation_index_template(output_path)

    with pytest.raises(FileExistsError):
        build_recommendation_index_template(output_path)
