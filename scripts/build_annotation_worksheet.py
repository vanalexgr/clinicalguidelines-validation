"""Build a clinician annotation worksheet for flagged hallucination claims.

Emits one row per distinct (query_id, claim) pair with the full adjudication
context: the question, the agent's answer, the verbatim text of every cited
recommendation, and the gold answer key. The filled worksheet converts back to
``data/annotation/hallucination_overrides.jsonl`` via
``scripts/worksheet_to_overrides.py``.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import OrderedDict, defaultdict
from pathlib import Path

from src.metrics.citation_breakdown import (
    HALLUCINATION_LEGEND,
    _auto_classify_claim,
    _claim_prefix,
    _load_overrides,
)

RISK_VALUES = ("none", "low", "moderate", "high")

COLUMNS = [
    "group_id",
    "group_lead",
    "query_id",
    "query_type",
    "safety_critical",
    "judges",
    "n_flags",
    "auto_type",
    "corrected_type",
    "clinical_risk",
    "clinical_note",
    "claim",
    "question",
    "agent_answer",
    "cited_recommendations",
    "gold_answer_key",
    "gold_key_recommendations",
    "claim_prefix",
]


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


_STOPWORDS = {
    "a", "an", "and", "the", "of", "for", "in", "to", "is", "are", "as", "not",
    "no", "with", "on", "or", "that", "this", "it", "be", "by", "from",
}


def _tokens(claim: str) -> set[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in claim)
    return {tok for tok in cleaned.split() if tok not in _STOPWORDS}


def _group_claims(rows: list[dict], *, threshold: float = 0.55) -> None:
    """Cluster near-identical claims within an item so each is adjudicated once.

    Both judges, across three runs each, restate the same objection in different
    words. The overrides file still needs one record per exact claim prefix, so
    grouping only changes what the reviewer reads, not what is written back.
    """
    by_item: dict[str, list[dict]] = {}
    for row in rows:
        by_item.setdefault(row["query_id"], []).append(row)

    for query_id, item_rows in by_item.items():
        clusters: list[tuple[set[str], int]] = []
        for row in item_rows:
            tokens = _tokens(row["claim"])
            for cluster_tokens, group_index in clusters:
                overlap = tokens & cluster_tokens
                union = tokens | cluster_tokens
                if union and len(overlap) / len(union) >= threshold:
                    row["group_id"] = f"{query_id}-G{group_index}"
                    cluster_tokens |= tokens
                    break
            else:
                group_index = len(clusters) + 1
                clusters.append((set(tokens), group_index))
                row["group_id"] = f"{query_id}-G{group_index}"

        seen: set[str] = set()
        for row in item_rows:
            row["group_lead"] = "yes" if row["group_id"] not in seen else ""
            seen.add(row["group_id"])


def _render_citations(citations: list[dict], index: dict) -> str:
    lines = []
    for citation in citations:
        rec_id = str(citation.get("rec_id") or "?")
        guideline = str(citation.get("guideline") or "?")
        entry = index.get(f"{guideline}:{rec_id}") or index.get(rec_id)
        if entry is not None:
            lines.append(
                f"[{guideline} rec {rec_id}] Class {entry.class_} Level {entry.level}: {entry.text}"
            )
        else:
            lines.append(
                f"[{guideline} rec {rec_id}] Class {citation.get('class')} "
                f"Level {citation.get('level')}: NOT FOUND IN CORPUS INDEX"
            )
    return "\n".join(lines)


def _render_gold_recs(recs: list[dict]) -> str:
    return "\n".join(
        f"rec {r.get('rec_id')} (Class {r.get('class')} Level {r.get('level')}): {r.get('summary')}"
        for r in recs
    )


def build_rows(
    judgments: list[dict],
    answers: list[dict],
    benchmark: list[dict],
    index: dict,
    overrides: dict,
) -> list[dict]:
    items = {row["id"]: row for row in benchmark}
    answer_by_id = {row["id"]: row for row in answers}

    # Collapse repeat flags: the same claim text raised across runs or by both
    # judges is one clinical question, not three.
    claims: OrderedDict[tuple[str, str], dict] = OrderedDict()
    for judgment in judgments:
        hallucination = judgment.get("hallucination") or {}
        if not hallucination.get("present"):
            continue
        query_id = judgment["query_id"]
        for claim in hallucination.get("unsupported_claims") or []:
            key = (query_id, _claim_prefix(str(claim)))
            slot = claims.setdefault(
                key,
                {"claim": str(claim), "judges": set(), "n_flags": 0},
            )
            slot["judges"].add(judgment["judge"])
            slot["n_flags"] += 1
            if len(str(claim)) > len(slot["claim"]):
                slot["claim"] = str(claim)

    rows: list[dict] = []
    for (query_id, prefix), slot in claims.items():
        item = items.get(query_id, {})
        gold = item.get("gold", {})
        answer = answer_by_id.get(query_id, {})

        citations = answer.get("citations") or []
        if isinstance(citations, str):
            citations = []

        existing = overrides.get((query_id, None, prefix))
        rows.append(
            {
                "group_id": "",
                "group_lead": "",
                "query_id": query_id,
                "query_type": item.get("query_type", ""),
                "safety_critical": item.get("safety_critical", ""),
                "judges": " + ".join(sorted(slot["judges"])),
                "n_flags": slot["n_flags"],
                "auto_type": _auto_classify_claim(slot["claim"]),
                "corrected_type": (existing or {}).get("corrected_type", ""),
                "clinical_risk": (existing or {}).get("clinical_risk", ""),
                "clinical_note": (existing or {}).get("clinical_note", ""),
                "claim": slot["claim"],
                "question": " | ".join(
                    t.get("content", "") for t in item.get("turns", [])
                ),
                "agent_answer": answer.get("recommendation") or answer.get("raw_response", ""),
                "cited_recommendations": _render_citations(citations, index),
                "gold_answer_key": gold.get("answer_key", ""),
                "gold_key_recommendations": _render_gold_recs(
                    gold.get("key_recommendations") or []
                ),
                "claim_prefix": prefix,
            }
        )
    _group_claims(rows)
    return rows


def write_markdown(rows: list[dict], path: Path) -> None:
    """Group claims by item so a reviewer reads each case's context once."""
    by_item: OrderedDict[str, list[dict]] = OrderedDict()
    for row in rows:
        by_item.setdefault(row["query_id"], []).append(row)

    out = [
        "# Hallucination flag adjudication worksheet",
        "",
        f"{len(rows)} distinct claims across {len(by_item)} items. "
        "For each claim record `corrected_type`, `clinical_risk`, and a one-sentence "
        "`clinical_note`. See `annotation_legend.md` for permitted values.",
        "",
    ]
    for query_id, item_rows in by_item.items():
        head = item_rows[0]
        out += [
            f"## {query_id} — {head['query_type']}"
            + ("  ·  **safety-critical**" if str(head["safety_critical"]).lower() == "true" else ""),
            "",
            f"**Question.** {head['question']}",
            "",
            "<details><summary>Agent answer</summary>",
            "",
            "```",
            head["agent_answer"],
            "```",
            "",
            "</details>",
            "",
            "<details><summary>Cited recommendations (verbatim from corpus)</summary>",
            "",
            "```",
            head["cited_recommendations"],
            "```",
            "",
            "</details>",
            "",
            f"**Gold answer key.** {head['gold_answer_key']}",
            "",
        ]
        if head["gold_key_recommendations"]:
            out += ["```", head["gold_key_recommendations"], "```", ""]
        out += [f"### Flagged claims ({len(item_rows)})", ""]
        for n, row in enumerate(item_rows, start=1):
            out += [
                f"**{n}.** {row['claim']}",
                "",
                f"> flagged by {row['judges']} ({row['n_flags']}x) · auto_type `{row['auto_type']}`",
                "",
                "| corrected_type | clinical_risk | clinical_note |",
                "|---|---|---|",
                "|  |  |  |",
                "",
            ]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def write_legend(path: Path) -> None:
    lines = ["# Annotation legend", "", "## corrected_type", ""]
    lines += [f"- `{name}` — {desc}" for name, desc in sorted(HALLUCINATION_LEGEND.items())]
    lines += [
        "",
        "## clinical_risk",
        "",
        "- `none` — not an error, or an error with no clinical consequence",
        "- `low` — wrong but a clinician would not act differently",
        "- `moderate` — could change management in a non-critical way",
        "- `high` — could cause patient harm if acted on",
        "",
        "Leave `clinical_note` as one sentence saying *why*. It is the audit trail.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    from src.corpus.index import load_recommendation_index

    repo = args.repo
    rows = build_rows(
        judgments=_read_jsonl(repo / "outputs/judgments/judgments.jsonl"),
        answers=_read_jsonl(repo / "outputs/answers/answers.jsonl"),
        benchmark=_read_jsonl(repo / "data/benchmark/benchmark_queries.v2.jsonl"),
        index=load_recommendation_index(repo / "data/corpus/recommendation_index.json"),
        overrides=_load_overrides(repo / "data/annotation/hallucination_overrides.jsonl"),
    )

    args.out.mkdir(parents=True, exist_ok=True)
    csv_path = args.out / "annotation_worksheet.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    write_markdown(rows, args.out / "annotation_worksheet.md")
    write_legend(args.out / "annotation_legend.md")

    todo = sum(1 for r in rows if not r["corrected_type"])
    per_item = defaultdict(int)
    for row in rows:
        if not row["corrected_type"]:
            per_item[row["query_id"]] += 1
    print(f"rows: {len(rows)}  prefilled: {len(rows) - todo}  to annotate: {todo}")
    for query_id in sorted(per_item):
        print(f"  {query_id}: {per_item[query_id]}")
    print(f"written: {csv_path}")


if __name__ == "__main__":
    main()
