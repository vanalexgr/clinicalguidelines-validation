"""Authoritative data contracts for the validation pipeline.

These pydantic models are the single source of truth. JSON on disk must validate
against them. Codex: extend with validators as needed but do NOT change field names
or semantics without orchestrator sign-off (see CODEX.md §2).
"""
from __future__ import annotations

from enum import Enum
from typing import Literal
from warnings import warn

from pydantic import BaseModel, Field, model_validator


# --------------------------------------------------------------------------- #
# Benchmark
# --------------------------------------------------------------------------- #
class QueryType(str, Enum):
    knowledge = "A_knowledge"
    complete_case = "B_complete_case"
    underspecified = "C_underspecified"
    followup = "D_followup"
    multiguideline = "E_multiguideline"
    should_refuse = "G_should_refuse"


class GateExpected(str, Enum):
    fire = "fire"
    suppress = "suppress"
    na = "na"


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class KeyRecommendation(BaseModel):
    rec_id: str | None = None
    class_: str | None = Field(default=None, alias="class")
    level: str | None = None
    summary: str

    model_config = {"populate_by_name": True}


class Gold(BaseModel):
    expected_guidelines: list[str] = Field(default_factory=list)
    gate_expected: GateExpected
    required_parameters: list[str] = Field(default_factory=list)
    acceptable_refusal: bool = False
    answer_key: str
    key_recommendations: list[KeyRecommendation] = Field(default_factory=list)
    notes: str = ""

    @model_validator(mode="after")
    def validate_gate_requirements(self) -> Gold:
        if self.gate_expected == GateExpected.fire and not self.required_parameters:
            raise ValueError(
                "gold.required_parameters must be non-empty when gold.gate_expected is 'fire'."
            )
        if self.acceptable_refusal and self.gate_expected != GateExpected.na:
            warn(
                "gold.acceptable_refusal is true and gold.gate_expected should be 'na'.",
                stacklevel=2,
            )
        return self


class BenchmarkItem(BaseModel):
    id: str = Field(pattern=r"^Q[0-9]{3,}$")
    query_type: QueryType
    safety_critical: bool
    turns: list[Turn] = Field(min_length=1)
    gold: Gold
    verified: bool = False

    @model_validator(mode="after")
    def validate_query_type_consistency(self) -> BenchmarkItem:
        if (
            self.query_type == QueryType.underspecified
            and self.gold.gate_expected != GateExpected.fire
        ):
            raise ValueError(
                "gold.gate_expected must be 'fire' when query_type is 'C_underspecified'."
            )
        if self.query_type == QueryType.should_refuse and not self.gold.acceptable_refusal:
            raise ValueError(
                "gold.acceptable_refusal must be true when query_type is 'G_should_refuse'."
            )
        return self


# --------------------------------------------------------------------------- #
# Agent answer
# --------------------------------------------------------------------------- #
class Citation(BaseModel):
    rec_id: str | None = None
    class_: str | None = Field(default=None, alias="class")
    level: str | None = None
    guideline: str | None = None
    passage: str = ""

    model_config = {"populate_by_name": True}


class RetrievedPassage(BaseModel):
    guideline: str
    chunk_id: str
    text: str


class AgentAnswer(BaseModel):
    id: str
    run_index: int = 0
    raw_response: str
    blinded_response: str = ""           # filled by the blinding step
    gate_fired: bool
    clarification_requested: list[str] = Field(default_factory=list)
    routed_guidelines: list[str] = Field(default_factory=list)
    recommendation: str = ""
    citations: list[Citation] = Field(default_factory=list)
    retrieved_passages: list[RetrievedPassage] = Field(default_factory=list)
    uncertainty_statements: list[str] = Field(default_factory=list)
    latency_seconds: float = 0.0
    model_meta: dict = Field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Judgment
# --------------------------------------------------------------------------- #
class LikertScore(BaseModel):
    score: int = Field(ge=0, le=3)
    rationale: str = ""


class CitationSupportScore(LikertScore):
    unsupported_citations: list[str] = Field(default_factory=list)


class Hallucination(BaseModel):
    present: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    count: int = 0


class SafetyCriticalError(BaseModel):
    flag: bool
    reason: str | None = None


class JudgmentDimensions(BaseModel):
    clinical_correctness: LikertScore
    citation_support: CitationSupportScore
    completeness: LikertScore
    uncertainty_handling: LikertScore


class Judgment(BaseModel):
    query_id: str
    judge: str
    run_index: int = 0
    dimensions: JudgmentDimensions
    hallucination: Hallucination
    safety_critical_error: SafetyCriticalError
    overall_comment: str = ""
    meta: dict = Field(default_factory=dict, alias="_meta")

    model_config = {"populate_by_name": True}


# --------------------------------------------------------------------------- #
# Deterministic metric outputs
# --------------------------------------------------------------------------- #
RoutingLabel = Literal["CORRECT", "PARTIAL", "WRONG"]


class GateOutcome(BaseModel):
    item_id: str
    expected: GateExpected
    fired: bool
    correct: bool
    parameter_recall: float | None = None   # only for correctly-fired underspecified items


__all__ = [
    "AgentAnswer",
    "BenchmarkItem",
    "Citation",
    "CitationSupportScore",
    "GateExpected",
    "GateOutcome",
    "Gold",
    "Hallucination",
    "Judgment",
    "JudgmentDimensions",
    "KeyRecommendation",
    "LikertScore",
    "QueryType",
    "RetrievedPassage",
    "RoutingLabel",
    "SafetyCriticalError",
    "Turn",
]
