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
    matched: bool          # existence only (used for D3a pass/fail)
    metadata_matched: bool  # existence AND class AND level (separate accuracy metric)
    expected_guideline: str | None
    expected_class: str | None
    expected_level: str | None


@dataclass(slots=True)
class CitationExistenceResult:
    total_citations: int
    matched_citations: int         # existence-only count (used for D3a)
    existence_accuracy: float      # existence-only rate (used for D3a pass/fail)
    metadata_matched_citations: int       # existence + class + level count
    metadata_accuracy: float              # full metadata accuracy (separate metric)
    details: list[CitationExistenceDetail]


_CANONICAL_CLASS_MAP: dict[str, str] = {
    "1": "I",
    "2": "IIb",
    "3": "III",
    "i": "I",
    "iia": "IIa",
    "iib": "IIb",
    "iii": "III",
    "gps": "GPS",
    "good": "GPS",
    "good practice statement": "GPS",
}


def _normalise_class(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = " ".join(raw.strip().split())
    if not cleaned:
        return None
    return _CANONICAL_CLASS_MAP.get(cleaned.lower(), cleaned)


def _normalise_level(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = " ".join(raw.strip().split())
    if not cleaned or cleaned == "?":
        return None
    canonical = {"a": "A", "b": "B", "c": "C"}.get(cleaned.lower())
    return canonical or cleaned


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
    matched_citations = 0          # existence only
    metadata_matched_citations = 0  # existence + class + level

    for citation in citations:
        lookup_key = (
            f"{citation.guideline}:{citation.rec_id}"
            if citation.guideline and citation.rec_id
            else citation.rec_id or ""
        )
        entry = index.get(lookup_key) or index.get(citation.rec_id or "")
        exists = entry is not None
        citation_class = _normalise_class(citation.class_)
        entry_class = _normalise_class(entry.class_ if entry else None)
        citation_level = _normalise_level(citation.level)
        entry_level = _normalise_level(entry.level if entry else None)
        class_match = exists and citation_class is not None and citation_class == entry_class
        level_match = exists and citation_level == entry_level
        # D3a: existence only — did the system cite a recommendation that actually exists?
        matched = exists
        # Separate full-metadata accuracy — class and level also correct
        metadata_matched = bool(exists and class_match and level_match)
        matched_citations += int(matched)
        metadata_matched_citations += int(metadata_matched)
        details.append(
            CitationExistenceDetail(
                rec_id=citation.rec_id,
                exists=exists,
                class_match=bool(class_match),
                level_match=bool(level_match),
                matched=matched,
                metadata_matched=metadata_matched,
                expected_guideline=entry.guideline if entry else None,
                expected_class=entry.class_ if entry else None,
                expected_level=entry.level if entry else None,
            )
        )

    total_citations = len(citations)
    existence_accuracy = 1.0 if total_citations == 0 else matched_citations / total_citations
    metadata_accuracy = 1.0 if total_citations == 0 else metadata_matched_citations / total_citations
    return CitationExistenceResult(
        total_citations=total_citations,
        matched_citations=matched_citations,
        existence_accuracy=existence_accuracy,
        metadata_matched_citations=metadata_matched_citations,
        metadata_accuracy=metadata_accuracy,
        details=details,
    )
