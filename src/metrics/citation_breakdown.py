"""Citation-tier and hallucination reclassification breakdown helpers."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from src.common.io import read_jsonl
from src.common.schemas import AgentAnswer, BenchmarkItem, Citation, Judgment
from src.corpus.index import (
    RecommendationIndexEntry,
    _normalise_class,
    _normalise_guideline,
    _normalise_level,
    load_recommendation_index,
)

ModelT = TypeVar("ModelT", bound=BaseModel)

ARTEFACT_TYPES = {
    "FALSE_POSITIVE",
    "VALID_INFERENCE",
    "REFUSAL_OVERCLAIM",
    "CITATION_MISMATCH",
    "UNGROUNDED_CORRECT",
}
# Clinically accurate content that the locked corpus does not actually support.
# Not a clinical error, so excluded from the adjusted hallucination rate, but it
# breaches the provenance guarantee and is reported separately.
PROVENANCE_TYPES = {"UNGROUNDED_CORRECT"}
REAL_ERROR_TYPES = {
    "WRONG_THRESHOLD",
    "SEVERITY_INFLATION",
    "WRONG_APPLICATION",
    "SCOPE_EXPANSION",
    "GENUINE_ERROR",
    "METADATA_INFLATION",
    "OTHER",
}
TIER_LEGEND = {
    "A_VERIFIED": "rec_id found · guideline + class + level all match",
    "B_METADATA_ERR": "rec_id found · class or level reported differently from index",
    "C_GUIDELINE_MISMATCH": "rec_id exists in index but under a different guideline",
    "D_NOT_IN_INDEX": "rec_id not found anywhere in the recommendation index",
    "E_NO_REC_ID": "citation returned with no rec_id field",
}
HALLUCINATION_LEGEND = {
    "FALSE_POSITIVE": "Claim matches a real indexed rec — judge flagged incorrectly",
    "METADATA_INFLATION": "Class/level stated higher or differently than the actual rec",
    "WRONG_APPLICATION": "Correct rec misapplied to wrong patient subgroup or scenario",
    "SCOPE_EXPANSION": "Broadens a rec's applicability beyond its stated scope",
    "CITATION_MISMATCH": "Correct content but wrong citation bracket number in text",
    "VALID_INFERENCE": "Clinically valid inference from cited recs, not verbatim",
    "REFUSAL_OVERCLAIM": "System refused when passages do address the topic",
    "GENUINE_ERROR": "Explicit factual claim with no retrievable rec basis",
    "UNGROUNDED_CORRECT": (
        "Clinically accurate, but no retrievable basis in the locked corpus "
        "(e.g. guideline table content that was never indexed as a chunk)"
    ),
    "WRONG_THRESHOLD": (
        "States a numeric decision threshold that does not match the guideline "
        "(diameter, stenosis, grade cut-off)"
    ),
    "OTHER": "Does not fit any of the above patterns",
    "SEVERITY_INFLATION": "Overstates urgency or mandate beyond what the cited guidance supports",
}


@dataclass(slots=True)
class CitationBreakdownResult:
    tiers: dict[str, int]
    tier_totals: int
    hal_type_counts: dict[str, int]
    hal_total: int
    artefact_count: int
    real_error_count: int
    items_clean: list[str]
    items_artefact_only: list[str]
    items_real_error: list[str]
    reported_hal_rate: float
    adjusted_hal_rate: float
    clinical_risk_counts: dict[str, int]
    per_item: dict[str, dict[str, Any]]
    provenance_gap_count: int = 0
    items_provenance_gap: list[str] = field(default_factory=list)
    provenance_gap_rate: float = 0.0


def _load_models(path: Path, model_type: type[ModelT]) -> list[ModelT]:
    models: list[ModelT] = []
    for line_number, row in enumerate(read_jsonl(path), start=1):
        try:
            models.append(model_type.model_validate(row))
        except ValidationError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return models


def _claim_prefix(text: str, *, length: int = 60) -> str:
    return str(text)[:length]


def _load_overrides(path: Path) -> dict[tuple[str, str | None, str], dict[str, Any]]:
    """Return {(query_id, judge_or_none, claim_prefix_60): override_record}."""
    overrides: dict[tuple[str, str | None, str], dict[str, Any]] = {}
    if not path.exists():
        return overrides

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
        key = (
            str(record["query_id"]),
            record.get("judge"),
            _claim_prefix(str(record["claim_prefix"])),
        )
        overrides[key] = record
    return overrides


def _override_for_claim(
    overrides: dict[tuple[str, str | None, str], dict[str, Any]],
    *,
    query_id: str,
    judge: str,
    claim: str,
) -> dict[str, Any] | None:
    prefix = _claim_prefix(claim)
    return overrides.get((query_id, judge, prefix)) or overrides.get((query_id, None, prefix))


def _auto_classify_claim(claim: str) -> str:
    low = claim.lower()
    if re.search(r"does not (?:explicitly )?address|not explicitly address", low):
        return "REFUSAL_OVERCLAIM"
    if re.search(r"mandatory|must\b(?! be considered)|urgent.*mandatory", low):
        return "SEVERITY_INFLATION"
    if re.search(
        r"misappl|wrong (?:patient|population|subgroup)|not applicable"
        r"|applied to.*despite|for patients without|below.*threshold",
        low,
    ):
        return "WRONG_APPLICATION"
    if re.search(r"regardless of whether|all patients\b|broadens", low):
        return "SCOPE_EXPANSION"
    if re.search(
        r"citation\s*\[\d+\].*not (?:found|provid|in citation)"
        r"|without corresponding|not.*citation list|not clearly mapped",
        low,
    ):
        return "CITATION_MISMATCH"
    if re.search(
        r"class.*(?:i{1,3}[ab]?|iii)\b|level\s*[abc]\b.*wrong"
        r"|wrong class|wrong level|labeled.*class|specified.*level",
        low,
    ):
        return "METADATA_INFLATION"
    if re.search(
        r"not verbatim|valid inference|logically follows|implied\b"
        r"|can be inferred|does not explicitly state|not directly stated",
        low,
    ):
        return "VALID_INFERENCE"
    if re.search(r"fabricat|invent|no citation|no basis|incorrect\b|no rec basis", low):
        return "GENUINE_ERROR"
    return "OTHER"


def _tier_citation(
    citation: Citation,
    index: dict[str, RecommendationIndexEntry],
) -> tuple[str, RecommendationIndexEntry | None]:
    rec_id = (citation.rec_id or "").strip()
    guideline = (_normalise_guideline(citation.guideline) or "").strip()
    if not rec_id:
        return "E_NO_REC_ID", None

    compound_key = f"{guideline}:{rec_id}" if guideline else ""
    entry = index.get(compound_key) or index.get(rec_id)
    if entry is None:
        return "D_NOT_IN_INDEX", None
    if guideline and entry.guideline != guideline:
        return "C_GUIDELINE_MISMATCH", entry

    if (
        _normalise_class(citation.class_) == _normalise_class(entry.class_)
        and _normalise_level(citation.level) == _normalise_level(entry.level)
    ):
        return "A_VERIFIED", entry
    return "B_METADATA_ERR", entry


def compute_citation_breakdown(
    *,
    answers_path: Path,
    judgments_path: Path,
    index_path: Path,
    bench_path: Path,
    overrides_path: Path,
) -> CitationBreakdownResult:
    answers = _load_models(answers_path, AgentAnswer)
    judgments = _load_models(judgments_path, Judgment)
    benchmark_items = _load_models(bench_path, BenchmarkItem)
    index = load_recommendation_index(index_path)
    overrides = _load_overrides(overrides_path)

    item_ids = [item.id for item in benchmark_items]
    per_item: dict[str, dict[str, Any]] = {
        item_id: {"citations": [], "hallucination_flags": []}
        for item_id in item_ids
    }

    tier_counts: Counter[str] = Counter()
    for answer in answers:
        per_item.setdefault(answer.id, {"citations": [], "hallucination_flags": []})
        for citation in answer.citations:
            tier, entry = _tier_citation(citation, index)
            tier_counts[tier] += 1
            per_item[answer.id]["citations"].append(
                {
                    "rec_id": citation.rec_id,
                    "guideline": citation.guideline,
                    "class": citation.class_,
                    "level": citation.level,
                    "tier": tier,
                    "index_class": entry.class_ if entry else None,
                    "index_level": entry.level if entry else None,
                    "index_text": (entry.text[:130] if entry else None),
                }
            )

    corrected_type_counts: Counter[str] = Counter()
    clinical_risk_counts: Counter[str] = Counter()
    for judgment in judgments:
        if not judgment.hallucination.present:
            continue
        per_item.setdefault(judgment.query_id, {"citations": [], "hallucination_flags": []})
        for claim in judgment.hallucination.unsupported_claims:
            auto_type = _auto_classify_claim(claim)
            override = _override_for_claim(
                overrides,
                query_id=judgment.query_id,
                judge=judgment.judge,
                claim=claim,
            )
            corrected_type = str(override["corrected_type"]) if override else auto_type
            clinical_risk = str(override.get("clinical_risk", "unknown")) if override else "unknown"
            clinical_note = str(override.get("clinical_note", "")) if override else ""
            corrected_type_counts[corrected_type] += 1
            clinical_risk_counts[clinical_risk] += 1
            per_item[judgment.query_id]["hallucination_flags"].append(
                {
                    "judge": judgment.judge,
                    "run": judgment.run_index,
                    "type": auto_type,
                    "auto_type": auto_type,
                    "corrected_type": corrected_type,
                    "clinical_risk": clinical_risk,
                    "clinical_note": clinical_note,
                    "claim": claim,
                }
            )

    reported_flag_items = {
        judgment.query_id for judgment in judgments if judgment.hallucination.present
    } | {item_id for item_id, payload in per_item.items() if payload["hallucination_flags"]}
    items_real_error: list[str] = []
    items_artefact_only: list[str] = []
    items_provenance_gap: list[str] = []
    for item_id in item_ids:
        flags = per_item.get(item_id, {}).get("hallucination_flags", [])
        if not flags:
            continue
        corrected_types = {flag["corrected_type"] for flag in flags}
        if corrected_types & PROVENANCE_TYPES:
            items_provenance_gap.append(item_id)
        if corrected_types & REAL_ERROR_TYPES:
            items_real_error.append(item_id)
        else:
            items_artefact_only.append(item_id)

    items_clean = [item_id for item_id in item_ids if item_id not in reported_flag_items]
    total_items = len(item_ids)
    total_flags = sum(len(payload["hallucination_flags"]) for payload in per_item.values())
    reported_hal_rate = len(reported_flag_items) / total_items if total_items else 0.0
    adjusted_hal_rate = len(items_real_error) / total_items if total_items else 0.0

    return CitationBreakdownResult(
        tiers=dict(sorted(tier_counts.items())),
        tier_totals=sum(tier_counts.values()),
        hal_type_counts=dict(sorted(corrected_type_counts.items())),
        hal_total=total_flags,
        artefact_count=sum(
            count for label, count in corrected_type_counts.items() if label in ARTEFACT_TYPES
        ),
        real_error_count=sum(
            count for label, count in corrected_type_counts.items() if label in REAL_ERROR_TYPES
        ),
        provenance_gap_count=sum(
            count for label, count in corrected_type_counts.items() if label in PROVENANCE_TYPES
        ),
        items_provenance_gap=sorted(items_provenance_gap),
        provenance_gap_rate=(
            len(items_provenance_gap) / total_items if total_items else 0.0
        ),
        items_clean=sorted(items_clean),
        items_artefact_only=sorted(items_artefact_only),
        items_real_error=sorted(items_real_error),
        reported_hal_rate=reported_hal_rate,
        adjusted_hal_rate=adjusted_hal_rate,
        clinical_risk_counts=dict(sorted(clinical_risk_counts.items())),
        per_item={item_id: per_item[item_id] for item_id in sorted(per_item)},
    )


def breakdown_to_dict(result: CitationBreakdownResult) -> dict[str, Any]:
    return {
        "citation_tiers": {
            "total": result.tier_totals,
            "counts": result.tiers,
            "pct": {
                label: round(count / result.tier_totals, 3) if result.tier_totals else 0.0
                for label, count in result.tiers.items()
            },
            "legend": TIER_LEGEND,
        },
        "hallucination_types": {
            "total_flagged_claims": result.hal_total,
            "counts": result.hal_type_counts,
            "legend": HALLUCINATION_LEGEND,
        },
        "hallucination_rate_summary": {
            "reported_rate": result.reported_hal_rate,
            "adjusted_rate": result.adjusted_hal_rate,
            "artefact_claims": result.artefact_count,
            "real_error_claims": result.real_error_count,
            "items_clean": result.items_clean,
            "items_only_artefact": result.items_artefact_only,
            "items_real_error": result.items_real_error,
        },
        "provenance_gap": {
            "rate": result.provenance_gap_rate,
            "claims": result.provenance_gap_count,
            "items": result.items_provenance_gap,
            "definition": (
                "Items containing at least one clinically correct claim with no "
                "retrievable basis in the locked corpus."
            ),
        },
        "clinical_risk_distribution": result.clinical_risk_counts,
        "per_item": result.per_item,
    }


def render_citation_breakdown_markdown(result: CitationBreakdownResult) -> str:
    citation_rows = [
        {
            "Tier": f"`{tier}`",
            "Description": TIER_LEGEND.get(tier, ""),
            "n": count,
            "%": _fmt_pct_fraction(count, result.tier_totals),
        }
        for tier, count in result.tiers.items()
    ]
    hall_rows = [
        {
            "Type": f"`{label}`",
            "Category": "Artefact" if label in ARTEFACT_TYPES else "Real error",
            "Description": HALLUCINATION_LEGEND.get(label, ""),
            "n": count,
        }
        for label, count in result.hal_type_counts.items()
    ]
    rate_rows = [
        {
            "Category": "Reported (any flag, any judge)",
            "Items": f"{len(result.items_artefact_only) + len(result.items_real_error)}",
            "Rate": _fmt_pct(result.reported_hal_rate),
        },
        {
            "Category": "Items with only artefact flags",
            "Items": str(len(result.items_artefact_only)),
            "Rate": "—",
        },
        {
            "Category": "Items with ≥1 real error",
            "Items": str(len(result.items_real_error)),
            "Rate": _fmt_pct(result.adjusted_hal_rate),
        },
        {
            "Category": "No flags at all (clean)",
            "Items": str(len(result.items_clean)),
            "Rate": "—",
        },
    ]
    risk_rows = [
        {"Risk": risk, "Claim count": count}
        for risk, count in result.clinical_risk_counts.items()
    ]
    lines = [
        "# Citation Correctness & Hallucination Error Breakdown",
        "",
        "## 1. Citation Correctness Tiers",
        "",
        _markdown_table(citation_rows),
        "",
        "## 2. Hallucination Claim Types",
        "",
        _markdown_table(hall_rows),
        "",
        "## 3. Adjusted Hallucination Rate",
        "",
        _markdown_table(rate_rows),
        "",
        f"**Clean items:** {', '.join(result.items_clean) or '(none)'}  ",
        f"**Artefact-only items:** {', '.join(result.items_artefact_only) or '(none)'}  ",
        f"**Items with real errors:** {', '.join(result.items_real_error) or '(none)'}",
        "",
        "## 4. Clinical Risk Distribution",
        "",
        _markdown_table(risk_rows),
        "",
        "## 5. Hallucination Flags Detail",
        "",
        _markdown_table(
            [
                {
                    "Item": item_id,
                    "Judge": flag["judge"],
                    "Auto": f"`{flag['auto_type']}`",
                    "Corrected": f"`{flag['corrected_type']}`",
                    "Risk": flag["clinical_risk"],
                    "Claim": flag["claim"],
                }
                for item_id, payload in result.per_item.items()
                for flag in payload["hallucination_flags"]
            ]
        ),
    ]
    return "\n".join(lines).rstrip() + "\n"


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _fmt_pct_fraction(count: int, total: int) -> str:
    return _fmt_pct(count / total) if total else "0.0%"


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "_No data._"
    headers = list(rows[0].keys())
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(header, "")) for header in headers) + " |")
    return "\n".join(lines)


__all__ = [
    "ARTEFACT_TYPES",
    "CitationBreakdownResult",
    "HALLUCINATION_LEGEND",
    "REAL_ERROR_TYPES",
    "TIER_LEGEND",
    "PROVENANCE_TYPES",
    "_load_overrides",
    "breakdown_to_dict",
    "compute_citation_breakdown",
    "render_citation_breakdown_markdown",
]
