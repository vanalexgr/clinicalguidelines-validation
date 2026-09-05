"""Build the reduced adjudication worksheet: grounding decided here, clinic decided by a human.

A judge's flagged "claim" is often shorthand ("C1 definition") that points into the
agent's answer rather than quoting it. Resolving that pointer to the sentences the
agent actually wrote, and only then testing those sentences against the retrieved
evidence, is what makes the grounding question mechanically answerable.

The reviewer is then asked one question per claim -- is it clinically correct? --
and the pair (grounded, clinically_correct) determines the taxonomy label.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

STOPWORDS = {
    "a", "an", "and", "the", "of", "for", "in", "to", "is", "are", "as", "not",
    "no", "with", "on", "or", "that", "this", "it", "be", "by", "from", "any",
    "all", "listed", "present", "retrieved", "text", "passages", "passage",
    "definition", "definitions", "specific", "general", "recommendation", "rec",
}


def _tokens(text: str) -> set[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return {tok for tok in cleaned.split() if tok not in STOPWORDS and len(tok) > 2}


def _key_terms(claim: str) -> list[str]:
    """Distinctive terms a claim turns on: codes, numbers, and long words."""
    terms = re.findall(r"\b(?:[CcTt]\d[a-z]?r?|\d+(?:\.\d+)?%?|[A-Za-z]{6,})\b", claim)
    return [t for t in terms if t.lower() not in STOPWORDS]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" -*#") for p in parts if len(p.strip()) > 15]


def _resolve_to_answer(claim: str, answer: str) -> str:
    """Find the sentences in the answer that the flagged claim refers to."""
    claim_tokens = _tokens(claim)
    terms = [t.lower() for t in _key_terms(claim)]
    scored = []
    for sentence in _sentences(answer):
        sentence_tokens = _tokens(sentence)
        overlap = len(claim_tokens & sentence_tokens) / len(claim_tokens) if claim_tokens else 0.0
        low = sentence.lower()
        term_hits = sum(1 for t in terms if t in low) / len(terms) if terms else 0.0
        score = max(overlap, term_hits)
        if score > 0:
            scored.append((score, sentence))
    scored.sort(key=lambda pair: -pair[0])
    return " ".join(sentence for _, sentence in scored[:2])


def _grounding(resolved: str, evidence: str) -> tuple[str, str]:
    """Report which distinctive terms of the resolved text appear in the evidence."""
    terms = _key_terms(resolved)
    if not terms:
        return "UNCLEAR", "no distinctive terms to test"
    low_evidence = evidence.lower()
    missing = [t for t in dict.fromkeys(terms) if t.lower() not in low_evidence]
    found = [t for t in dict.fromkeys(terms) if t.lower() in low_evidence]
    ratio = len(found) / (len(found) + len(missing))
    detail = f"{len(found)}/{len(found) + len(missing)} terms in evidence"
    if missing:
        detail += " · absent: " + ", ".join(missing[:6])
    if ratio >= 0.85:
        return "GROUNDED", detail
    if ratio <= 0.4:
        return "NOT_GROUNDED", detail
    return "PARTIAL", detail


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worksheet", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    answers = {
        json.loads(line)["id"]: json.loads(line)
        for line in (args.repo / "outputs/answers/answers.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    }

    rows = list(csv.DictReader(args.worksheet.open(encoding="utf-8", newline="")))
    out_rows = []
    for row in rows:
        answer = answers.get(row["query_id"], {})
        evidence = "\n".join(
            [str(p.get("text", "")) for p in (answer.get("retrieved_passages") or [])]
            + [str(c.get("passage", "")) for c in (answer.get("citations") or [])]
        )
        answer_text = answer.get("recommendation") or answer.get("raw_response", "")
        resolved = _resolve_to_answer(row["claim"], answer_text) or row["claim"]
        verdict, detail = _grounding(resolved, evidence)

        out_rows.append(
            {
                "query_id": row["query_id"],
                "group_id": row["group_id"],
                "claim": row["claim"],
                "what_the_agent_actually_said": resolved,
                "grounded_in_corpus": verdict,
                "grounding_detail": detail,
                "clinically_correct": "",
                "error_type_if_incorrect": "",
                "clinical_risk_if_incorrect": "",
                "clinical_note": "",
                "question": row["question"],
                "claim_prefix": row["claim_prefix"],
                "auto_type": row["auto_type"],
            }
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(out_rows[0].keys()))
        writer.writeheader()
        writer.writerows(out_rows)

    from collections import Counter
    print(f"claims: {len(out_rows)}")
    print("grounding:", dict(Counter(r["grounded_in_corpus"] for r in out_rows)))
    print(f"written: {args.out}")


if __name__ == "__main__":
    main()
