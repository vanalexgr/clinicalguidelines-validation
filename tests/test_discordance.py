from __future__ import annotations

import csv
import json
from pathlib import Path

from src.common.io import write_jsonl
from src.common.schemas import AgentAnswer, BenchmarkItem, Judgment, RetrievedPassage
from src.discordance.export_review import export_review

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = REPO_ROOT / "data/benchmark/benchmark_queries.seed.jsonl"


def _seed_item(item_id: str) -> BenchmarkItem:
    for line in SEED_PATH.read_text(encoding="utf-8").splitlines():
        item = BenchmarkItem.model_validate(json.loads(line))
        if item.id == item_id:
            return item
    raise AssertionError(f"Missing benchmark item {item_id}")


def _answer(
    item: BenchmarkItem,
    *,
    run_index: int = 0,
    raw_response: str,
    routed_guidelines: list[str] | None = None,
    gate_fired: bool | None = None,
    recommendation: str = "",
    passage_text: str = "",
) -> AgentAnswer:
    return AgentAnswer(
        id=item.id,
        run_index=run_index,
        raw_response=raw_response,
        blinded_response=raw_response,
        gate_fired=item.gold.gate_expected.value == "fire" if gate_fired is None else gate_fired,
        clarification_requested=[],
        routed_guidelines=routed_guidelines or list(item.gold.expected_guidelines),
        recommendation=recommendation,
        citations=[],
        retrieved_passages=[
            RetrievedPassage(
                guideline=(item.gold.expected_guidelines or ["OUT_OF_SCOPE"])[0],
                chunk_id=f"{item.id}-chunk",
                text=passage_text or f"Retrieved passage for {item.id}.",
            )
        ],
        uncertainty_statements=[],
        latency_seconds=1.0,
        model_meta={},
    )


def _judgment(
    query_id: str,
    judge: str,
    run_index: int,
    *,
    answer_run_index: int = 0,
    clinical_correctness: int = 3,
    citation_support: int = 3,
    completeness: int = 3,
    uncertainty_handling: int = 3,
    hallucination_present: bool = False,
    hallucination_count: int = 0,
    safety_flag: bool = False,
    safety_reason: str | None = None,
) -> Judgment:
    return Judgment.model_validate(
        {
            "query_id": query_id,
            "judge": judge,
            "run_index": run_index,
            "dimensions": {
                "clinical_correctness": {
                    "score": clinical_correctness,
                    "rationale": f"{judge} clinical rationale run {run_index}",
                },
                "citation_support": {
                    "score": citation_support,
                    "rationale": f"{judge} citation rationale run {run_index}",
                    "unsupported_citations": [],
                },
                "completeness": {
                    "score": completeness,
                    "rationale": f"{judge} completeness rationale run {run_index}",
                },
                "uncertainty_handling": {
                    "score": uncertainty_handling,
                    "rationale": f"{judge} uncertainty rationale run {run_index}",
                },
            },
            "hallucination": {
                "present": hallucination_present,
                "unsupported_claims": [],
                "count": hallucination_count,
            },
            "safety_critical_error": {
                "flag": safety_flag,
                "reason": safety_reason,
            },
            "overall_comment": f"{judge} overall comment run {run_index}",
            "_meta": {
                "request_id": f"{judge}-{query_id}-{run_index}",
                "latency_s": 0.1,
                "tokens": {"in": 10, "out": 20},
                "answer_run_index": answer_run_index,
            },
        }
    )


def _write_inputs(
    tmp_path: Path,
    *,
    items: list[BenchmarkItem],
    answers: list[AgentAnswer],
    judgments: list[Judgment],
    corpus_index: dict | None = None,
) -> tuple[Path, Path, Path, Path]:
    benchmark_path = tmp_path / "benchmark.jsonl"
    answers_path = tmp_path / "answers.jsonl"
    judgments_path = tmp_path / "judgments.jsonl"
    corpus_index_path = tmp_path / "recommendation_index.json"

    write_jsonl(benchmark_path, [item.model_dump(by_alias=True) for item in items])
    write_jsonl(answers_path, [answer.model_dump(by_alias=True) for answer in answers])
    write_jsonl(judgments_path, [judgment.model_dump(by_alias=True) for judgment in judgments])
    corpus_index_path.write_text(json.dumps(corpus_index or {}), encoding="utf-8")
    return benchmark_path, answers_path, judgments_path, corpus_index_path


def test_export_review_writes_three_rows_with_full_context(tmp_path: Path) -> None:
    routing_item = _seed_item("Q001")
    disagreement_item = _seed_item("Q002")
    safety_item = _seed_item("Q003")
    clean_item = _seed_item("Q010")

    answers = [
        _answer(
            routing_item,
            raw_response="Routing error answer.",
            routed_guidelines=["SVS_PAD_2022"],
            recommendation="Misrouted recommendation.",
            passage_text="Routing passage text.",
        ),
        _answer(
            disagreement_item,
            raw_response="Disagreement answer.",
            recommendation="AAA answer.",
            passage_text="AAA passage text.",
        ),
        _answer(
            safety_item,
            raw_response="Safety flagged answer.",
            recommendation="CEA within 14 days.",
            passage_text="Carotid passage text.",
        ),
        _answer(
            clean_item,
            raw_response="Clean answer.",
            recommendation="Endovenous thermal ablation.",
            passage_text="Varicose vein passage text.",
        ),
    ]

    judgments = [
        *[
            _judgment("Q001", "judge_a", run_index)
            for run_index in range(3)
        ],
        *[
            _judgment("Q001", "judge_b", run_index)
            for run_index in range(3)
        ],
        *[
            _judgment("Q002", "judge_a", run_index, citation_support=0)
            for run_index in range(3)
        ],
        *[
            _judgment("Q002", "judge_b", run_index, citation_support=3)
            for run_index in range(3)
        ],
        *[
            _judgment(
                "Q003",
                "judge_a",
                run_index,
                safety_flag=True,
                safety_reason="missed urgent revascularization",
            )
            for run_index in range(3)
        ],
        *[
            _judgment("Q003", "judge_b", run_index)
            for run_index in range(3)
        ],
        *[
            _judgment("Q010", "judge_a", run_index)
            for run_index in range(3)
        ],
        *[
            _judgment("Q010", "judge_b", run_index)
            for run_index in range(3)
        ],
    ]

    benchmark_path, answers_path, judgments_path, corpus_index_path = _write_inputs(
        tmp_path,
        items=[routing_item, disagreement_item, safety_item, clean_item],
        answers=answers,
        judgments=judgments,
    )

    csv_path = tmp_path / "discordance_review.csv"
    markdown_path = tmp_path / "discordance_review.md"
    review_items = export_review(
        benchmark_path=benchmark_path,
        answers_path=answers_path,
        judgments_path=judgments_path,
        corpus_index_path=corpus_index_path,
        csv_path=csv_path,
        markdown_path=markdown_path,
    )

    assert [review_item.item.id for review_item in review_items] == ["Q001", "Q002", "Q003"]

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 3
    rows_by_id = {row["query_id"]: row for row in rows}
    assert set(rows_by_id) == {"Q001", "Q002", "Q003"}

    assert rows_by_id["Q001"]["routing_label"] == "WRONG"
    assert rows_by_id["Q001"]["trigger_routing_wrong"] == "True"
    assert rows_by_id["Q002"]["trigger_likert_disagreement"] == "True"
    assert rows_by_id["Q002"]["likert_disagreement_dimensions"] == '["citation_support"]'
    assert rows_by_id["Q003"]["trigger_any_safety_flag"] == "True"
    # clinical_correctness is no longer exported on the discordance review surface.
    assert "ensemble_clinical_correctness" not in rows_by_id["Q003"]
    assert "human_score_clinical_correctness" not in rows_by_id["Q003"]
    assert rows_by_id["Q003"]["human_notes"] == ""
    assert "Routing error answer." in rows_by_id["Q001"]["agent_answer"]
    assert "Carotid passage text." in rows_by_id["Q003"]["retrieved_passages_json"]
    assert '"role": "user"' in rows_by_id["Q003"]["question_turns_json"]

    markdown = markdown_path.read_text(encoding="utf-8")
    assert "# Discordance Review" in markdown
    assert "## Q003" in markdown
    assert "Safety flagged answer." in markdown
    assert safety_item.gold.answer_key in markdown
    assert "Carotid passage text." in markdown
    assert "judge_a" in markdown
    assert "judge_b" in markdown
    assert "### Human Review" in markdown


def test_export_review_prefers_answer_run_with_judgments(tmp_path: Path) -> None:
    item = _seed_item("Q001")
    answer_run_zero = _answer(
        item,
        run_index=0,
        raw_response="Unjudged answer.",
        recommendation="Unused answer.",
    )
    answer_run_one = _answer(
        item,
        run_index=1,
        raw_response="Judged answer.",
        routed_guidelines=["SVS_PAD_2022"],
        recommendation="Used answer.",
    )

    judgments = [
        *[
            _judgment("Q001", "judge_a", run_index, answer_run_index=1)
            for run_index in range(3)
        ],
        *[
            _judgment("Q001", "judge_b", run_index, answer_run_index=1)
            for run_index in range(3)
        ],
    ]

    benchmark_path, answers_path, judgments_path, corpus_index_path = _write_inputs(
        tmp_path,
        items=[item],
        answers=[answer_run_zero, answer_run_one],
        judgments=judgments,
    )

    csv_path = tmp_path / "discordance_review.csv"
    markdown_path = tmp_path / "discordance_review.md"
    export_review(
        benchmark_path=benchmark_path,
        answers_path=answers_path,
        judgments_path=judgments_path,
        corpus_index_path=corpus_index_path,
        csv_path=csv_path,
        markdown_path=markdown_path,
    )

    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == 1
    assert rows[0]["answer_run_index"] == "1"
    assert rows[0]["agent_answer"] == "Judged answer."
