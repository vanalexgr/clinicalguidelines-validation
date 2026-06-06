from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.common.schemas import AgentAnswer, BenchmarkItem, Citation, GateExpected, QueryType
from src.corpus.index import load_recommendation_index
from src.metrics.deterministic import (
    label_routing,
    summarize_citation_existence,
    summarize_gate,
    summarize_latency,
    summarize_routing,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "data/benchmark/benchmark_queries.seed.jsonl"
INDEX_FIXTURE_PATH = REPO_ROOT / "tests/fixtures/recommendation_index.fixture.json"


def _seed_item(item_id: str) -> BenchmarkItem:
    for line in SEED_PATH.read_text(encoding="utf-8").splitlines():
        item = BenchmarkItem.model_validate(json.loads(line))
        if item.id == item_id:
            return item
    raise AssertionError(f"Missing benchmark item {item_id}")


def _answer(
    item: BenchmarkItem,
    *,
    routed_guidelines: list[str] | None = None,
    gate_fired: bool = False,
    clarification_requested: list[str] | None = None,
    citations: list[Citation] | None = None,
    latency_seconds: float = 1.0,
    run_index: int = 0,
) -> AgentAnswer:
    return AgentAnswer(
        id=item.id,
        run_index=run_index,
        raw_response="answer",
        blinded_response="answer",
        gate_fired=gate_fired,
        clarification_requested=clarification_requested or [],
        routed_guidelines=routed_guidelines or [],
        recommendation="recommendation",
        citations=citations or [],
        retrieved_passages=[],
        uncertainty_statements=[],
        latency_seconds=latency_seconds,
        model_meta={},
    )


def test_label_routing_marks_empty_expected_with_reported_guideline_as_wrong() -> None:
    item = _seed_item("Q001").model_copy(
        update={
            "gold": _seed_item("Q001").gold.model_copy(update={"expected_guidelines": []}),
            "safety_critical": False,
        }
    )
    answer = _answer(item, routed_guidelines=["ESVS_Carotid_2023"])

    assert label_routing(item, answer) == "WRONG"


def test_summarize_routing_matches_hand_computed_fixture() -> None:
    item1 = _seed_item("Q001").model_copy(
        update={
            "gold": _seed_item("Q001").gold.model_copy(
                update={"expected_guidelines": ["ESVS_Carotid_2023"]}
            ),
            "safety_critical": False,
        }
    )
    item2 = _seed_item("Q002").model_copy(
        update={
            "gold": _seed_item("Q002").gold.model_copy(
                update={"expected_guidelines": ["ESVS_Carotid_2023"]}
            ),
            "safety_critical": False,
        }
    )
    item3 = _seed_item("Q003").model_copy(
        update={
            "gold": _seed_item("Q003").gold.model_copy(
                update={"expected_guidelines": ["ESVS_Carotid_2023"]}
            ),
            "safety_critical": True,
        }
    )
    item4 = _seed_item("Q004").model_copy(
        update={
            "gold": _seed_item("Q004").gold.model_copy(
                update={"expected_guidelines": ["ESVS_AAA_2024"]}
            ),
            "safety_critical": False,
        }
    )
    answers = [
        _answer(item1, routed_guidelines=["ESVS_Carotid_2023"]),
        _answer(item2, routed_guidelines=["ESVS_Carotid_2023", "ESVS_AAA_2024"]),
        _answer(item3, routed_guidelines=[]),
        _answer(item4, routed_guidelines=["SVS_PAD_2022"]),
    ]

    metrics = summarize_routing([item1, item2, item3, item4], answers)

    assert [decision.label for decision in metrics.decisions] == [
        "CORRECT",
        "PARTIAL",
        "WRONG",
        "WRONG",
    ]
    assert metrics.exact_match_rate == pytest.approx(0.25)
    assert metrics.micro_precision == pytest.approx(0.5)
    assert metrics.micro_recall == pytest.approx(0.5)
    assert metrics.micro_f1 == pytest.approx(0.5)
    assert metrics.macro_f1 == pytest.approx((0.8 + 0.0 + 0.0) / 3)


def test_summarize_gate_matches_hand_computed_fixture() -> None:
    fire_hit = _seed_item("Q003").model_copy(
        update={
            "query_type": QueryType.underspecified,
            "gold": _seed_item("Q003").gold.model_copy(
                update={
                    "gate_expected": GateExpected.fire,
                    "required_parameters": ["symptom duration", "laterality"],
                }
            ),
        }
    )
    fire_miss = _seed_item("Q004").model_copy(
        update={
            "query_type": QueryType.underspecified,
            "gold": _seed_item("Q004").gold.model_copy(
                update={
                    "gate_expected": GateExpected.fire,
                    "required_parameters": ["time from onset"],
                }
            ),
        }
    )
    suppress_true_negative = _seed_item("Q001").model_copy(
        update={
            "query_type": QueryType.complete_case,
            "gold": _seed_item("Q001").gold.model_copy(
                update={"gate_expected": GateExpected.suppress}
            ),
        }
    )
    suppress_false_positive = _seed_item("Q002").model_copy(
        update={
            "query_type": QueryType.complete_case,
            "gold": _seed_item("Q002").gold.model_copy(
                update={"gate_expected": GateExpected.suppress}
            ),
        }
    )
    suppress_followup_false_positive = _seed_item("Q005").model_copy(
        update={
            "query_type": QueryType.followup,
            "gold": _seed_item("Q005").gold.model_copy(
                update={"gate_expected": GateExpected.suppress}
            ),
        }
    )

    answers = [
        _answer(
            fire_hit,
            gate_fired=True,
            clarification_requested=["symptom duration"],
        ),
        _answer(fire_miss, gate_fired=False),
        _answer(suppress_true_negative, gate_fired=False),
        _answer(
            suppress_false_positive,
            gate_fired=True,
            clarification_requested=["extra parameter"],
        ),
        _answer(
            suppress_followup_false_positive,
            gate_fired=True,
            clarification_requested=["follow-up question"],
        ),
    ]

    metrics = summarize_gate(
        [
            fire_hit,
            fire_miss,
            suppress_true_negative,
            suppress_false_positive,
            suppress_followup_false_positive,
        ],
        answers,
    )

    assert metrics.true_positive == 1
    assert metrics.false_negative == 1
    assert metrics.true_negative == 1
    assert metrics.false_positive == 2
    assert metrics.sensitivity == pytest.approx(0.5)
    assert metrics.specificity == pytest.approx(1 / 3)
    assert metrics.over_interrogation_rate == pytest.approx(0.5)
    assert metrics.mean_parameter_recall == pytest.approx(0.5)
    assert metrics.outcomes[0].parameter_recall == pytest.approx(0.5)
    assert metrics.outcomes[1].parameter_recall is None


def test_summarize_citation_existence_and_latency_match_hand_computed_fixture() -> None:
    item1 = _seed_item("Q001")
    item2 = _seed_item("Q002")
    index = load_recommendation_index(INDEX_FIXTURE_PATH)
    answers = [
        _answer(
            item1,
            citations=[
                Citation(
                    rec_id="CAR-EXAMPLE",
                    class_="I",
                    level="A",
                    guideline="ESVS_Carotid_2023",
                ),
                Citation(
                    rec_id="AAA-EXAMPLE",
                    class_="IIa",
                    level="A",
                    guideline="ESVS_AAA_2024",
                ),
            ],
            latency_seconds=1.0,
        ),
        _answer(
            item2,
            citations=[
                Citation(
                    rec_id="MISSING-REC",
                    class_="I",
                    level="A",
                    guideline="ESVS_Carotid_2023",
                )
            ],
            latency_seconds=2.0,
            run_index=1,
        ),
        _answer(item1, citations=[], latency_seconds=4.0, run_index=2),
    ]

    citation_metrics = summarize_citation_existence(answers, index)
    latency_metrics = summarize_latency(answers)

    assert citation_metrics.total_citations == 3
    assert citation_metrics.matched_citations == 2
    assert citation_metrics.existence_accuracy == pytest.approx(2 / 3)
    assert citation_metrics.per_answer[0].result.matched_citations == 2
    assert citation_metrics.per_answer[0].result.total_citations == 2
    assert citation_metrics.per_answer[0].result.metadata_matched_citations == 1
    assert citation_metrics.per_answer[1].result.matched_citations == 0
    assert citation_metrics.per_answer[1].result.total_citations == 1
    assert citation_metrics.per_answer[2].result.existence_accuracy == pytest.approx(1.0)

    assert latency_metrics.count == 3
    assert latency_metrics.mean_seconds == pytest.approx(7 / 3)
    assert latency_metrics.median_seconds == pytest.approx(2.0)
    assert latency_metrics.min_seconds == pytest.approx(1.0)
    assert latency_metrics.max_seconds == pytest.approx(4.0)
    assert latency_metrics.p95_seconds == pytest.approx(4.0)


def test_summarize_latency_handles_empty_input() -> None:
    metrics = summarize_latency([])

    assert metrics.count == 0
    assert metrics.mean_seconds is None
    assert metrics.median_seconds is None
    assert metrics.min_seconds is None
    assert metrics.max_seconds is None
    assert metrics.p95_seconds is None
