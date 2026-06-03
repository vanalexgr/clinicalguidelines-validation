"""Shared agent-client interfaces and blinding helpers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from src.common.schemas import AgentAnswer, BenchmarkItem


@dataclass(slots=True)
class DryRunResult:
    request_payload: dict[str, Any]
    raw_response: Any
    latency_s: float


class AgentClient(ABC):
    """Normalized interface for obtaining AgentAnswer records."""

    @property
    @abstractmethod
    def cache_model_id(self) -> str:
        """Stable cache key component for this agent configuration."""

    @abstractmethod
    def generate_answer(self, item: BenchmarkItem, *, run_index: int = 0) -> AgentAnswer:
        """Call the backing agent and normalize its response."""

    @abstractmethod
    def dry_run(self, item: BenchmarkItem) -> DryRunResult:
        """Emit one raw request/response pair for mapping the live API shape."""


_BLINDING_PATTERNS = [
    re.compile(r"\bas an ai language model\b", re.IGNORECASE),
    re.compile(r"\bas an ai\b", re.IGNORECASE),
    re.compile(r"\bi am an ai(?: assistant| model)?\b", re.IGNORECASE),
    re.compile(r"\bi'm an ai(?: assistant| model)?\b", re.IGNORECASE),
    re.compile(r"\bopenai\b", re.IGNORECASE),
    re.compile(r"\banthropic\b", re.IGNORECASE),
    re.compile(r"\bchatgpt\b", re.IGNORECASE),
    re.compile(r"\bgpt[- ]?\d+(?:\.\d+)?\b", re.IGNORECASE),
    re.compile(r"\bclaude(?:[- ][A-Za-z0-9.]+)?\b", re.IGNORECASE),
    re.compile(r"\bgemini\b", re.IGNORECASE),
]


def blind_text(text: str) -> str:
    """Strip common self-identifying model and vendor strings before judging."""
    blinded = text
    for pattern in _BLINDING_PATTERNS:
        blinded = pattern.sub("[redacted]", blinded)
    blinded = re.sub(r"\[redacted\](?:\s*\[redacted\])+", "[redacted]", blinded)
    blinded = re.sub(r"[ \t]+", " ", blinded)
    blinded = re.sub(r" ?\n ?", "\n", blinded)
    return blinded.strip()
