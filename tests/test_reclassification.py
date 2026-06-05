from __future__ import annotations

from src.metrics.citation_breakdown import CitationBreakdownResult
from src.metrics.reclassification import compute_reclassification_report


def test_reclassification_report() -> None:
    breakdown = CitationBreakdownResult(
        tiers={"A_VERIFIED": 1},
        tier_totals=1,
        hal_type_counts={"FALSE_POSITIVE": 1, "WRONG_APPLICATION": 1},
        hal_total=2,
        artefact_count=1,
        real_error_count=1,
        items_clean=[],
        items_artefact_only=["Q001"],
        items_real_error=["Q002"],
        reported_hal_rate=1.0,
        adjusted_hal_rate=0.5,
        clinical_risk_counts={"none": 1, "high": 1},
        per_item={
            "Q001": {
                "citations": [],
                "hallucination_flags": [
                    {
                        "auto_type": "OTHER",
                        "corrected_type": "FALSE_POSITIVE",
                        "clinical_risk": "none",
                    }
                ],
            },
            "Q002": {
                "citations": [],
                "hallucination_flags": [
                    {
                        "auto_type": "WRONG_APPLICATION",
                        "corrected_type": "WRONG_APPLICATION",
                        "clinical_risk": "high",
                    }
                ],
            },
        },
    )

    report = compute_reclassification_report(breakdown)

    assert report.total_flags == 2
    assert report.auto_type_counts["OTHER"] == 1
    assert report.corrected_type_counts["FALSE_POSITIVE"] == 1
    assert report.flags_reclassified == 1
    assert report.reclassification_rate == 0.5
    assert report.reported_hal_rate == 1.0
    assert report.auto_classified_hal_rate == 1.0
    assert report.clinical_review_rate == 0.5
    assert report.high_risk_rate == 0.5
