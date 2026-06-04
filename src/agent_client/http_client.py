"""HTTP client for the ClinicalGuidelines.io agent.

API shape (confirmed from Laravel-RAGFLOW-router, 2026-06):
  POST /api/v1/vascular-consult
  Auth: Authorization: Bearer <CGIO_API_KEY>
  Request:  { question, history?, pre_retrieval_mode?, guidelines? }
  Response (standard): { result, citation_chunks, narrative_chunks,
                         llm_citation_chunks, llm_narrative_chunks,
                         selected_guidelines, gap_assessment, assets, ... }
  Response (pre_retrieval_mode=true, gate fires):
                       { phase:"awaiting_confirmation", confirmation_message,
                         clarification_questions, pre_retrieval_result,
                         retrieval_payload:{...standard fields...} }
  Response (pre_retrieval_mode=true, gate suppressed):
                       { result, ... } — same as standard response

Run with --dry-run to dump one raw request/response for manual inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from src.agent_client.base import AgentClient, DryRunResult
from src.benchmark.validate_benchmark import load_benchmark_validated
from src.common.io import load_config
from src.common.schemas import AgentAnswer, BenchmarkItem, Citation, RetrievedPassage

# --------------------------------------------------------------------------- #
# Guideline key → canonical benchmark ID mapping
# (API key on left, benchmark expected_guidelines value on right)
# --------------------------------------------------------------------------- #
_GUIDELINE_KEY_TO_CANONICAL: dict[str, str] = {
    "carotid_vertebral": "ESVS_Carotid_2023",
    "abdominal_aortic_aneurysm": "ESVS_AAA_2024",
    "acute_limb_ischaemia": "ESVS_ALI_2020",
    "antithrombotic_therapy": "ESVS_Antithrombotic_2023",
    "asymptomatic_pad": "ESVS_Asymptomatic_PAD_IC_2024",
    "clti": "GVG_CLTI_2019",
    "chronic_venous_disease": "ESVS_Chronic_Venous_Disease_2022",
    "venous_thrombosis": "ESVS_Venous_Thrombosis_2021",
    "descending_thoracic_aorta": "ESVS_ThoracoAbdominal_2026",
    "aortic_arch": "ESVS_AorticArch_2024",
    "mesenteric_renal": "ESVS_MesentericRenal_2017",
    "vascular_trauma": "ESVS_VascularTrauma_2023",
    "vascular_graft_infections": "ESVS_GraftInfections_2020",
    "vascular_access": "ESVS_VascularAccess_2023",
}


class HttpAgentClient(AgentClient):
    """HTTP transport normalising ClinicalGuidelines.io responses into AgentAnswer."""

    def __init__(
        self,
        agent_cfg: dict,
        *,
        transport: Any | None = None,
    ) -> None:
        self.agent_cfg = agent_cfg
        self.base_url = str(agent_cfg.get("base_url") or "")
        self.api_key = agent_cfg.get("api_key")
        self.endpoint_path = str(agent_cfg.get("endpoint_path") or "")
        self.pre_retrieval_mode = bool(agent_cfg.get("pre_retrieval_mode", True))
        self.timeout_seconds = float(agent_cfg.get("timeout_seconds", 120))
        self._transport = transport or self._default_transport

    @property
    def cache_model_id(self) -> str:
        model = self.agent_cfg.get("model") or os.environ.get("CGIO_AGENT_MODEL") or "agent"
        return str(model)

    def generate_answer(self, item: BenchmarkItem, *, run_index: int = 0) -> AgentAnswer:
        call = self._invoke(item)
        return self._normalize(
            item=item,
            raw_response=call.raw_response,
            latency_s=call.latency_s,
            run_index=run_index,
        )

    def dry_run(self, item: BenchmarkItem) -> DryRunResult:
        return self._invoke(item)

    def _invoke(self, item: BenchmarkItem) -> DryRunResult:
        url = urllib.parse.urljoin(
            self.base_url.rstrip("/") + "/",
            self.endpoint_path.lstrip("/"),
        )
        payload = self._build_request_payload(item)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        started_at = time.monotonic()
        raw_response = self._transport(url, payload, headers, self.timeout_seconds)
        latency_s = time.monotonic() - started_at
        return DryRunResult(
            request_payload=payload,
            raw_response=raw_response,
            latency_s=latency_s,
        )

    def _build_request_payload(self, item: BenchmarkItem) -> dict[str, Any]:
        """Build POST body for /api/v1/vascular-consult."""
        # The question is always the last user turn.
        question = item.turns[-1].content

        # Prior turns become the history array (as plain strings, alternating user/assistant).
        history = [t.content for t in item.turns[:-1]]

        payload: dict[str, Any] = {"question": question}
        if history:
            payload["history"] = history
        if self.pre_retrieval_mode:
            payload["pre_retrieval_mode"] = True
        return payload

    def _default_transport(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> Any:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError:
            raise
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"_raw_text": body}

    def _normalize(
        self,
        *,
        item: BenchmarkItem,
        raw_response: Any,
        latency_s: float,
        run_index: int,
    ) -> AgentAnswer:
        """Map the CGIO API response to a normalised AgentAnswer.

        Two response shapes are handled:
          1. Standard / pre_retrieval_mode gate-suppressed:
             top-level ``result``, ``citation_chunks``, ``narrative_chunks``,
             ``selected_guidelines``, ``gap_assessment``.
          2. pre_retrieval_mode gate-fired (``phase == "awaiting_confirmation"``):
             top-level ``confirmation_message``, ``clarification_questions``,
             ``pre_retrieval_result``; clinical chunks live inside ``retrieval_payload``.
        """
        if not isinstance(raw_response, dict):
            raw_text = str(raw_response)
            return AgentAnswer(
                id=item.id,
                run_index=run_index,
                raw_response=raw_text,
                gate_fired=False,
                latency_seconds=latency_s,
                model_meta={"synthesis_model": self.cache_model_id, "endpoint": self.endpoint_path},
            )

        phase = raw_response.get("phase")
        gate_fired = phase == "awaiting_confirmation"

        # Clarification questions (only meaningful when gate fires)
        clarification_requested: list[str] = []
        if gate_fired:
            qs = raw_response.get("clarification_questions") or []
            clarification_requested = [str(q).strip() for q in qs if str(q).strip()]

        # Answer text
        if gate_fired:
            raw_text = str(raw_response.get("confirmation_message") or "")
        else:
            raw_text = self._extract_result_text(raw_response)

        # The clinical data (chunks, guidelines) may be nested under retrieval_payload
        # (phase 1 pre-fetches in parallel) or at the top level (standard mode).
        payload: dict = raw_response.get("retrieval_payload") or raw_response

        # Routed guidelines: selected_guidelines[].key → canonical benchmark ID
        routed_guidelines = self._extract_routed_guidelines(payload)

        # Citations: citation_chunks (prefer llm_citation_chunks for judge context)
        citation_chunks = (
            payload.get("llm_citation_chunks")
            or payload.get("citation_chunks")
            or []
        )
        citations = [_normalize_citation(c) for c in citation_chunks if isinstance(c, dict)]

        # Retrieved passages: narrative_chunks (prefer llm_ variant)
        narrative_chunks = (
            payload.get("llm_narrative_chunks")
            or payload.get("narrative_chunks")
            or []
        )
        retrieved_passages = [
            _normalize_passage(p, i)
            for i, p in enumerate(narrative_chunks)
            if isinstance(p, dict)
        ]

        # Uncertainty: from gap_assessment
        uncertainty = _extract_uncertainty(payload)

        # Recommendation text (same as raw_text for this API)
        recommendation = raw_text

        model_meta = {
            "synthesis_model": self.cache_model_id,
            "endpoint": self.endpoint_path,
            "phase": phase or "complete",
        }

        return AgentAnswer(
            id=item.id,
            run_index=run_index,
            raw_response=raw_text,
            gate_fired=gate_fired,
            clarification_requested=clarification_requested,
            routed_guidelines=routed_guidelines,
            recommendation=recommendation,
            citations=citations,
            retrieved_passages=retrieved_passages,
            uncertainty_statements=uncertainty,
            latency_seconds=latency_s,
            model_meta=model_meta,
        )

    def _extract_result_text(self, raw_response: dict) -> str:
        """Extract the main answer text from a standard (non-gate) response."""
        if "_raw_text" in raw_response:
            return str(raw_response["_raw_text"])

        # CGIO standard field
        for key in ("result", "response", "answer", "recommendation", "text", "message", "output"):
            if key in raw_response:
                val = raw_response[key]
                if isinstance(val, str):
                    return val

        # OpenWebUI choices wrapper
        choices = raw_response.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")
            if isinstance(content, str):
                return content

        return json.dumps(raw_response, ensure_ascii=False)

    def _extract_routed_guidelines(self, payload: dict) -> list[str]:
        """Map selected_guidelines[].key → canonical IDs, with fallbacks."""
        result: list[str] = []
        seen: set[str] = set()

        # Primary: CGIO selected_guidelines array (preferred)
        for entry in (payload.get("selected_guidelines") or []):
            if not isinstance(entry, dict):
                continue
            key = entry.get("key", "")
            canonical = _GUIDELINE_KEY_TO_CANONICAL.get(key, key)
            if canonical and canonical not in seen:
                result.append(canonical)
                seen.add(canonical)

        # Fallback A: pre-mapped list already in canonical form
        if not result:
            for gid in (payload.get("routed_guidelines") or payload.get("guidelines") or []):
                gid = str(gid)
                if gid and gid not in seen:
                    result.append(gid)
                    seen.add(gid)

        # Fallback B: derive from citation chunk source_guideline names
        if not result:
            for chunk in (payload.get("citation_chunks") or payload.get("citations") or []):
                if not isinstance(chunk, dict):
                    continue
                sg = chunk.get("source_guideline") or chunk.get("guideline") or ""
                if sg and sg not in seen:
                    result.append(sg)
                    seen.add(sg)
        return result


def _normalize_citation(chunk: dict) -> Citation:
    """Map a CGIO citation_chunk dict to a Citation schema object."""
    rec_id = (
        chunk.get("rec_id")
        or chunk.get("recommendation_id")
        or chunk.get("id")
    )
    # recommendation_id in CGIO is "Rec 1.2.3" — strip prefix for matching
    if isinstance(rec_id, str) and rec_id.startswith("Rec "):
        rec_id = rec_id[4:].strip()

    return Citation.model_validate({
        "rec_id": rec_id,
        "class": chunk.get("class"),
        "level": chunk.get("level"),
        "guideline": chunk.get("source_guideline") or chunk.get("guideline"),
        "passage": chunk.get("text") or chunk.get("content") or chunk.get("passage") or "",
    })


def _normalize_passage(chunk: dict, index: int) -> RetrievedPassage:
    """Map a CGIO narrative_chunk dict to a RetrievedPassage schema object."""
    text = chunk.get("content") or chunk.get("text") or chunk.get("passage") or ""
    guideline = (
        chunk.get("source_guideline")
        or chunk.get("guideline")
        or chunk.get("guideline_key")
        or "unknown"
    )
    chunk_id = chunk.get("chunk_id") or chunk.get("id") or f"chunk-{index}"
    return RetrievedPassage.model_validate({
        "guideline": str(guideline),
        "chunk_id": str(chunk_id),
        "text": str(text),
    })


def _extract_uncertainty(payload: dict) -> list[str]:
    """Extract uncertainty signals from gap_assessment."""
    gap = payload.get("gap_assessment") or {}
    if not isinstance(gap, dict):
        return []
    statements: list[str] = []
    if gap.get("hasGuidelineGap"):
        summary = gap.get("gapSummary")
        if summary:
            statements.append(str(summary))
        for facet in gap.get("uncoveredFacets") or []:
            statements.append(f"Uncovered facet: {facet}")
    return statements


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to config.yaml.")
    parser.add_argument("--benchmark-path", help="Optional benchmark JSONL override.")
    parser.add_argument("--item-id", help="Optional benchmark item id to dry-run/normalize.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dump one raw request/response pair instead of only the normalized answer.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    benchmark_path = args.benchmark_path or config["paths"]["benchmark"]
    items = load_benchmark_validated(benchmark_path)
    item = _select_item(items, args.item_id)
    client = HttpAgentClient(config["agent"])

    if args.dry_run:
        result = client.dry_run(item)
        print(
            json.dumps(
                {
                    "request_payload": result.request_payload,
                    "raw_response": result.raw_response,
                    "latency_s": result.latency_s,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    answer = client.generate_answer(item)
    print(json.dumps(answer.model_dump(by_alias=True), indent=2, ensure_ascii=False))
    return 0


def _select_item(items: list[BenchmarkItem], item_id: str | None) -> BenchmarkItem:
    if item_id is None:
        return items[0]
    for item in items:
        if item.id == item_id:
            return item
    raise ValueError(f"Benchmark item not found: {item_id}")


if __name__ == "__main__":
    raise SystemExit(main())
