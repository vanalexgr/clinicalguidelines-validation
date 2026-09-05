"""Pre-classify flagged claims by checking them against the evidence actually retrieved.

Most judge flags assert that a claim is "not present in the retrieved text". That
is a mechanically checkable statement: the retrieved passages and the cited
recommendations are recorded per item. Where the claim's distinctive content does
appear, the flag is a FALSE_POSITIVE and the reviewer confirms rather than derives
it. Where it does not, the reviewer still has to make the clinical call.
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
    "all", "listed", "definition", "definitions", "present", "retrieved", "text",
    "passages", "passage", "recommendation", "rec", "specific", "general",
}
# Numbers, grades, classes and percentages carry the clinical content of a claim.
SALIENT = re.compile(r"\b(?:\d+(?:\.\d+)?%?|c\d[a-z]?r?|grade\s*\d|class\s*[iv]+|stage\s*\d)\b", re.I)


def _content_tokens(text: str) -> set[str]:
    cleaned = "".join(ch.lower() if ch.isalnum() or ch in ".%" else " " for ch in text)
    return {tok for tok in cleaned.split() if tok not in STOPWORDS and len(tok) > 2}


def _salient_tokens(text: str) -> set[str]:
    return {m.group(0).lower().replace(" ", "") for m in SALIENT.finditer(text)}


def _best_window(claim: str, corpus: str, *, width: int = 400) -> tuple[float, str]:
    """Slide a window over the evidence and return the best-scoring excerpt."""
    claim_tokens = _content_tokens(claim)
    if not claim_tokens or not corpus:
        return 0.0, ""
    claim_salient = _salient_tokens(claim)

    best_score, best_text = 0.0, ""
    step = width // 2
    for start in range(0, max(len(corpus) - width, 0) + 1, step):
        window = corpus[start : start + width]
        window_tokens = _content_tokens(window)
        overlap = len(claim_tokens & window_tokens) / len(claim_tokens)
        if claim_salient:
            salient_hit = len(claim_salient & _salient_tokens(window)) / len(claim_salient)
            score = 0.6 * overlap + 0.4 * salient_hit
        else:
            score = overlap
        if score > best_score:
            best_score, best_text = score, window
    return best_score, best_text.strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worksheet", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.55)
    args = parser.parse_args()

    answers = {
        json.loads(line)["id"]: json.loads(line)
        for line in (args.repo / "outputs/answers/answers.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    }

    rows = list(csv.DictReader(args.worksheet.open(encoding="utf-8", newline="")))
    evidence_cache: dict[str, str] = {}
    suggested = 0

    for row in rows:
        query_id = row["query_id"]
        if query_id not in evidence_cache:
            answer = answers.get(query_id, {})
            parts = [
                str(passage.get("text", ""))
                for passage in (answer.get("retrieved_passages") or [])
            ]
            parts += [
                str(citation.get("passage", ""))
                for citation in (answer.get("citations") or [])
            ]
            evidence_cache[query_id] = "\n".join(parts)

        score, excerpt = _best_window(row["claim"], evidence_cache[query_id])
        row["support_score"] = f"{score:.2f}"
        row["supporting_evidence"] = excerpt
        row["suggested_type"] = "FALSE_POSITIVE" if score >= args.threshold else ""
        if row["suggested_type"]:
            suggested += 1

        # Clear the previous blanket pass so the reviewer re-decides deliberately.
        row["corrected_type"] = ""
        row["clinical_risk"] = ""
        row["clinical_note"] = ""

    columns = list(rows[0].keys())
    for extra in ("support_score", "supporting_evidence", "suggested_type"):
        if extra in columns:
            columns.remove(extra)
    insert_at = columns.index("corrected_type")
    for offset, extra in enumerate(("suggested_type", "support_score", "supporting_evidence")):
        columns.insert(insert_at + offset, extra)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)

    print(f"claims: {len(rows)}  suggested FALSE_POSITIVE: {suggested}  residue: {len(rows) - suggested}")
    from collections import Counter
    residue = Counter(r["query_id"] for r in rows if not r["suggested_type"])
    for query_id in sorted(residue):
        print(f"  {query_id}: {residue[query_id]} to review")
    print(f"written: {args.out}")


if __name__ == "__main__":
    main()
