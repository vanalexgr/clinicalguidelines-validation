"""Recommendation index loading and deterministic citation-existence checks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from src.common.schemas import Citation


class RecommendationIndexEntry(BaseModel):
    guideline: str
    class_: str = Field(alias="class")
    level: str
    text: str

    model_config = {"populate_by_name": True, "extra": "forbid"}


@dataclass(slots=True)
class CitationExistenceDetail:
    rec_id: str | None
    exists: bool
    class_match: bool
    level_match: bool
    matched: bool
    expected_guideline: str | None
    expected_class: str | None
    expected_level: str | None


@dataclass(slots=True)
class CitationExistenceResult:
    total_citations: int
    matched_citations: int
    existence_accuracy: float
    details: list[CitationExistenceDetail]


def load_recommendation_index(
    path: str | Path,
) -> dict[str, RecommendationIndexEntry]:
    """Load a recommendation index JSON file."""
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{source}: recommendation index must be a JSON object.")

    index: dict[str, RecommendationIndexEntry] = {}
    for rec_id, raw_entry in payload.items():
        if rec_id.startswith("_"):
            continue
        try:
            index[rec_id] = RecommendationIndexEntry.model_validate(raw_entry)
        except ValidationError as exc:
            message = "; ".join(
                f"{'.'.join(str(part) for part in error['loc']) or '$'}: {error['msg']}"
                for error in exc.errors(include_url=False)
            )
            raise ValueError(f"{source}:{rec_id}: {message}") from exc

    return index


def evaluate_citation_existence(
    citations: list[Citation],
    index: dict[str, RecommendationIndexEntry],
) -> CitationExistenceResult:
    """Return citation-existence accuracy and per-citation match detail."""
    details: list[CitationExistenceDetail] = []
    matched_citations = 0

    for citation in citations:
        entry = index.get(citation.rec_id or "")
        exists = entry is not None
        class_match = exists and citation.class_ is not None and citation.class_ == entry.class_
        level_match = exists and citation.level is not None and citation.level == entry.level
        matched = bool(exists and class_match and level_match)
        matched_citations += int(matched)
        details.append(
            CitationExistenceDetail(
                rec_id=citation.rec_id,
                exists=exists,
                class_match=bool(class_match),
                level_match=bool(level_match),
                matched=matched,
                expected_guideline=entry.guideline if entry else None,
                expected_class=entry.class_ if entry else None,
                expected_level=entry.level if entry else None,
            )
        )

    total_citations = len(citations)
    existence_accuracy = 1.0 if total_citations == 0 else matched_citations / total_citations
    return CitationExistenceResult(
        total_citations=total_citations,
        matched_citations=matched_citations,
        existence_accuracy=existence_accuracy,
        details=details,
    )
