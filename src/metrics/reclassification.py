"""Helpers for comparing raw hallucination flags with clinically reviewed labels."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from src.metrics.citation_breakdown import CitationBreakdownResult, REAL_ERROR_TYPES


@dataclass(slots=True)
class ReclassificationReport:
    total_flags: int
    auto_type_counts: dict[str, int]
    corrected_type_counts: dict[str, int]
    flags_reclassified: int
    reclassification_rate: float
    clinical_risk_counts: dict[str, int]
    reported_hal_rate: float
    auto_classified_hal_rate: float
    adjusted_hal_rate: float
    clinical_review_rate: float
    high_risk_rate: float


def compute_reclassification_report(
    breakdown: CitationBreakdownResult,
) -> ReclassificationReport:
    auto_type_counts: Counter[str] = Counter()
    corrected_type_counts: Counter[str] = Counter()
    clinical_risk_counts: Counter[str] = Counter()
    flags_reclassified = 0
    total_items = len(breakdown.per_item)
    items_with_auto_real_error: set[str] = set()
    items_with_corrected_real_error: set[str] = set()
    items_with_high_risk: set[str] = set()

    for item_id, payload in breakdown.per_item.items():
        for flag in payload["hallucination_flags"]:
            auto_type = str(flag["auto_type"])
            corrected_type = str(flag["corrected_type"])
            risk = str(flag["clinical_risk"])
            auto_type_counts[auto_type] += 1
            corrected_type_counts[corrected_type] += 1
            clinical_risk_counts[risk] += 1
            if auto_type != corrected_type:
                flags_reclassified += 1
            if auto_type in REAL_ERROR_TYPES:
                items_with_auto_real_error.add(item_id)
            if corrected_type in REAL_ERROR_TYPES:
                items_with_corrected_real_error.add(item_id)
            if risk == "high":
                items_with_high_risk.add(item_id)

    total_flags = sum(auto_type_counts.values())
    return ReclassificationReport(
        total_flags=total_flags,
        auto_type_counts=dict(sorted(auto_type_counts.items())),
        corrected_type_counts=dict(sorted(corrected_type_counts.items())),
        flags_reclassified=flags_reclassified,
        reclassification_rate=(flags_reclassified / total_flags) if total_flags else 0.0,
        clinical_risk_counts=dict(sorted(clinical_risk_counts.items())),
        reported_hal_rate=breakdown.reported_hal_rate,
        auto_classified_hal_rate=(
            len(items_with_auto_real_error) / total_items if total_items else 0.0
        ),
        adjusted_hal_rate=breakdown.adjusted_hal_rate,
        clinical_review_rate=(
            len(items_with_corrected_real_error) / total_items if total_items else 0.0
        ),
        high_risk_rate=(len(items_with_high_risk) / total_items if total_items else 0.0),
    )


__all__ = ["ReclassificationReport", "compute_reclassification_report"]
