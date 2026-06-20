"""Exhaustive truth-table tests for src/metrics/pass_fail.py."""
from __future__ import annotations

import json
from pathlib import Path

from src.common.schemas import BenchmarkItem, GateExpected, QueryType
from src.metrics.aggregate import EnsembleAggregate
from src.metrics.deterministic import RoutingDecision
from src.metrics.pass_fail import (
    PassFailResult,
    evaluate_pass_fail,
    pass_rate,
)

SEED_PATH = Path("data/benchmark/benchmark_queries.seed.jsonl")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _seed_item(item_id: str) -> BenchmarkItem:
    for line in SEED_PATH.read_text(encoding="utf-8").splitlines():
        item = BenchmarkItem.model_validate(json.loads(line))
        if item.id == item_id:
            return item
    raise AssertionError(f"Missing {item_id}")


def _item(
    item_id: str = "Q001",
    *,
    safety_critical: bool = False,
    query_type: QueryType = QueryType.complete_case,
    gate_expected: GateExpected = GateExpected.suppress,
    acceptable_refusal: bool = False,
) -> BenchmarkItem:
    base = _seed_item(item_id)
    gold_updates: dict = {"gate_expected": gate_expected}
    if acceptable_refusal:
        gold_updates["acceptable_refusal"] = True
    if query_type == QueryType.underspecified:
        gold_updates["required_parameters"] = ["some_param"]
        gold_updates["gate_expected"] = GateExpected.fire
    if query_type == QueryType.should_refuse:
        gold_updates["acceptable_refusal"] = True
        gold_updates["gate_expected"] = GateExpected.na
    return base.model_copy(
        update={
            "safety_critical": safety_critical,
            "query_type": query_type,
            "gold": base.gold.model_copy(update=gold_updates),
        }
    )


def _ensemble(
    query_id: str = "Q001",
    *,
    uncertainty_handling: float = 3.0,
    hallucination_present: bool = False,
    safety_flag: bool = False,
) -> EnsembleAggregate:
    # clinical_correctness is no longer a scoring dimension and is absent from EnsembleAggregate.
    return EnsembleAggregate(
        query_id=query_id,
        citation_support=3.0,
        completeness=3.0,
        uncertainty_handling=uncertainty_handling,
        hallucination_present=hallucination_present,
        hallucination_count=0.0,
        safety_flag=safety_flag,
    )


def _routing(
    query_id: str = "Q001",
    label: str = "CORRECT",
    reported: list[str] | None = None,
) -> RoutingDecision:
    return RoutingDecision(
        item_id=query_id,
        answer_run_index=0,
        label=label,  # type: ignore[arg-type]
        expected_guidelines=["ESVS_Carotid_2023"],
        reported_guidelines=reported if reported is not None else ["ESVS_Carotid_2023"],
    )


def _evaluate(
    *,
    safety_critical: bool = False,
    routing_label: str = "CORRECT",
    gate_correct: bool = True,
    hallucination: bool = False,
    safety_flag: bool = False,
    citation_accuracy: float = 1.0,
    query_type: QueryType = QueryType.complete_case,
) -> PassFailResult:
    item = _item("Q001", safety_critical=safety_critical, query_type=query_type)
    ensemble = _ensemble(
        "Q001",
        hallucination_present=hallucination,
        safety_flag=safety_flag,
    )
    routing = _routing("Q001", routing_label)
    return evaluate_pass_fail(item, ensemble, routing, gate_correct, citation_accuracy)


# --------------------------------------------------------------------------- #
# Golden path
# --------------------------------------------------------------------------- #


def test_all_conditions_pass() -> None:
    result = _evaluate()
    assert result.verdict == "PASS"
    assert result.failed_conditions == []


# --------------------------------------------------------------------------- #
# Condition 1: routing
# --------------------------------------------------------------------------- #


def test_routing_wrong_fails() -> None:
    result = _evaluate(routing_label="WRONG")
    assert result.verdict == "FAIL"
    assert "D1_routing_WRONG" in result.failed_conditions


def test_routing_partial_non_safety_critical_passes() -> None:
    result = _evaluate(routing_label="PARTIAL", safety_critical=False)
    assert result.verdict == "PASS"


def test_routing_partial_safety_critical_fails() -> None:
    result = _evaluate(routing_label="PARTIAL", safety_critical=True)
    assert result.verdict == "FAIL"
    assert "D1_routing_PARTIAL_on_safety_critical" in result.failed_conditions


# --------------------------------------------------------------------------- #
# Condition 2: gate
# --------------------------------------------------------------------------- #


def test_gate_incorrect_fails() -> None:
    result = _evaluate(gate_correct=False)
    assert result.verdict == "FAIL"
    assert "D7_gate_incorrect" in result.failed_conditions


# --------------------------------------------------------------------------- #
# clinical_correctness is NO LONGER a scoring / pass-fail dimension.
# It is not exposed on EnsembleAggregate or PassFailResult and never affects the verdict.
# --------------------------------------------------------------------------- #


def test_clinical_correctness_not_a_pass_fail_condition() -> None:
    result = _evaluate()
    assert result.verdict == "PASS"
    assert not any("clinical_correctness" in cond for cond in result.failed_conditions)


def test_pass_fail_result_has_no_clinical_correctness_field() -> None:
    result = _evaluate()
    assert not hasattr(result, "clinical_correctness")


def test_ensemble_aggregate_has_no_clinical_correctness_field() -> None:
    assert not hasattr(_ensemble(), "clinical_correctness")


# --------------------------------------------------------------------------- #
# Condition 4: hallucination overrides everything
# --------------------------------------------------------------------------- #


def test_hallucination_forces_fail_even_when_all_else_passes() -> None:
    result = _evaluate(hallucination=True)
    assert result.verdict == "FAIL"
    assert "D6_hallucination" in result.failed_conditions


def test_hallucination_plus_routing_wrong_both_listed() -> None:
    result = _evaluate(hallucination=True, routing_label="WRONG")
    assert result.verdict == "FAIL"
    assert "D6_hallucination" in result.failed_conditions
    assert "D1_routing_WRONG" in result.failed_conditions


# --------------------------------------------------------------------------- #
# Condition 5: safety flag overrides everything
# --------------------------------------------------------------------------- #


def test_safety_flag_forces_fail() -> None:
    result = _evaluate(safety_flag=True)
    assert result.verdict == "FAIL"
    assert "D8_safety_flag" in result.failed_conditions


def test_safety_and_hallucination_both_force_fail() -> None:
    result = _evaluate(safety_flag=True, hallucination=True)
    assert result.verdict == "FAIL"
    assert "D8_safety_flag" in result.failed_conditions
    assert "D6_hallucination" in result.failed_conditions


# --------------------------------------------------------------------------- #
# Condition 6: citation existence
# --------------------------------------------------------------------------- #


def test_citation_existence_100pct_passes() -> None:
    result = _evaluate(citation_accuracy=1.0)
    assert result.verdict == "PASS"


def test_citation_existence_below_100pct_fails() -> None:
    result = _evaluate(citation_accuracy=0.9)
    assert result.verdict == "FAIL"
    assert "D3a_citation_existence_lt_100pct" in result.failed_conditions


def test_citation_existence_0_fails() -> None:
    result = _evaluate(citation_accuracy=0.0)
    assert result.verdict == "FAIL"


# --------------------------------------------------------------------------- #
# G_should_refuse items
# --------------------------------------------------------------------------- #


def test_refusal_item_with_no_guidelines_passes() -> None:
    item = _item("Q016", query_type=QueryType.should_refuse)
    ensemble = _ensemble("Q016")
    routing = _routing("Q016", "CORRECT", reported=[])
    result = evaluate_pass_fail(
        item, ensemble, routing, gate_correct=True, citation_existence_accuracy=1.0
    )
    assert result.verdict == "PASS"


def test_refusal_item_with_routed_guidelines_can_still_pass() -> None:
    item = _item("Q016", query_type=QueryType.should_refuse)
    ensemble = _ensemble("Q016")
    routing = _routing("Q016", "WRONG", reported=["ESVS_Carotid_2023"])
    result = evaluate_pass_fail(
        item, ensemble, routing, gate_correct=True, citation_existence_accuracy=1.0
    )
    assert result.verdict == "PASS"


def test_refusal_item_with_confident_clinical_answer_fails() -> None:
    item = _item("Q016", query_type=QueryType.should_refuse)
    ensemble = _ensemble(
        "Q016",
        uncertainty_handling=1.0,
    )
    routing = _routing("Q016", "WRONG", reported=["ESVS_Carotid_2023"])
    result = evaluate_pass_fail(
        item, ensemble, routing, gate_correct=True, citation_existence_accuracy=1.0
    )
    assert result.verdict == "FAIL"
    assert "G_refusal_item_answered_instead_of_refusing" in result.failed_conditions


def test_refusal_item_hallucination_fails() -> None:
    item = _item("Q016", query_type=QueryType.should_refuse)
    ensemble = _ensemble("Q016", hallucination_present=True)
    routing = _routing("Q016", "CORRECT", reported=[])
    result = evaluate_pass_fail(
        item, ensemble, routing, gate_correct=True, citation_existence_accuracy=1.0
    )
    assert result.verdict == "FAIL"


# --------------------------------------------------------------------------- #
# Batch + pass_rate
# --------------------------------------------------------------------------- #


def test_pass_rate_all_pass() -> None:
    results = [
        PassFailResult(query_id="Q001", verdict="PASS"),
        PassFailResult(query_id="Q002", verdict="PASS"),
    ]
    passes, total = pass_rate(results)
    assert passes == 2
    assert total == 2


def test_pass_rate_mixed() -> None:
    results = [
        PassFailResult(query_id="Q001", verdict="PASS"),
        PassFailResult(query_id="Q002", verdict="FAIL", failed_conditions=["D6_hallucination"]),
        PassFailResult(query_id="Q003", verdict="FAIL", failed_conditions=["D8_safety_flag"]),
    ]
    passes, total = pass_rate(results)
    assert passes == 1
    assert total == 3
