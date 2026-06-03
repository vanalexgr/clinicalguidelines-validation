"""HTTP client for the ClinicalGuidelines.io agent."""

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


class HttpAgentClient(AgentClient):
    """HTTP transport with best-effort normalization into AgentAnswer."""

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
        self.request_template = agent_cfg.get("request_template", {})
        self.timeout_seconds = float(agent_cfg.get("timeout_seconds", 120))
        self._transport = transport or self._default_transport

    @property
    def cache_model_id(self) -> str:
        model = (
            self.request_template.get("model")
            or self.agent_cfg.get("model")
            or os.environ.get("CGIO_AGENT_MODEL")
            or "agent"
        )
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
        url = urllib.parse.urljoin(self.base_url.rstrip("/") + "/", self.endpoint_path.lstrip("/"))
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
        turns = [{"role": turn.role, "content": turn.content} for turn in item.turns]

        if self.endpoint_path == "/api/chat/completions":
            payload: dict[str, Any] = {
                "messages": turns,
                "stream": False,
            }
            model = (
                self.request_template.get("model")
                or self.agent_cfg.get("model")
                or os.environ.get("CGIO_AGENT_MODEL")
            )
            if model:
                payload["model"] = model
            for key, value in self.request_template.items():
                if key in {"model", "field_query", "field_session"} or value is None:
                    continue
                payload[key] = value
            return payload

        query_field = self.request_template.get("field_query", "message")
        session_field = self.request_template.get("field_session", "conversation_id")
        payload = {
            query_field: item.turns[-1].content,
            "turns": turns,
        }
        if session_field:
            payload[session_field] = item.id
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
        # TODO(author): Confirm the exact OpenWebUI/ClinicalGuidelines.io response shape from a live
        # `--dry-run`. Current assumptions:
        # 1. OpenWebUI-style responses return `choices[0].message.content` for the answer text.
        # 2. Optional grounding/citation data may appear in `sources`, `citations`, or
        #    `retrieved_passages`.
        # 3. Optional structured fields such as `gate_fired`, `clarification_requested`,
        #    `routed_guidelines`, and `uncertainty_statements` may be present directly and are used
        #    when available. Otherwise normalization falls back to best-effort heuristics.
        raw_text = self._extract_text(raw_response)
        citations = self._extract_citations(raw_response)
        retrieved_passages = self._extract_retrieved_passages(raw_response)
        clarification_requested = self._extract_string_list(
            raw_response,
            ["clarification_requested", "required_parameters", "follow_up_questions"],
        )
        gate_fired = self._extract_bool(raw_response, ["gate_fired", "needs_clarification"])
        if gate_fired is None:
            gate_fired = bool(clarification_requested)
        routed_guidelines = self._extract_routed_guidelines(
            raw_response,
            citations,
            retrieved_passages,
        )
        uncertainty = self._extract_uncertainty(raw_response, raw_text)
        recommendation = self._extract_recommendation(raw_response, raw_text)

        model_meta = {
            "synthesis_model": self._extract_model_name(raw_response) or self.cache_model_id,
            "endpoint": self.endpoint_path,
        }

        return AgentAnswer(
            id=item.id,
            run_index=run_index,
            raw_response=raw_text,
            gate_fired=bool(gate_fired),
            clarification_requested=clarification_requested,
            routed_guidelines=routed_guidelines,
            recommendation=recommendation,
            citations=citations,
            retrieved_passages=retrieved_passages,
            uncertainty_statements=uncertainty,
            latency_seconds=latency_s,
            model_meta=model_meta,
        )

    def _extract_text(self, raw_response: Any) -> str:
        if isinstance(raw_response, str):
            return raw_response
        if not isinstance(raw_response, dict):
            return json.dumps(raw_response, ensure_ascii=False)
        if "_raw_text" in raw_response:
            return str(raw_response["_raw_text"])

        choices = raw_response.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            content = message.get("content", "")
            return self._coerce_content_to_text(content)

        for key in (
            "raw_response",
            "response",
            "answer",
            "recommendation",
            "text",
            "message",
            "output",
        ):
            if key in raw_response:
                return self._coerce_content_to_text(raw_response[key])

        return json.dumps(raw_response, ensure_ascii=False)

    def _extract_model_name(self, raw_response: Any) -> str | None:
        if isinstance(raw_response, dict):
            value = raw_response.get("model") or raw_response.get("model_name")
            return str(value) if value else None
        return None

    def _extract_recommendation(self, raw_response: Any, raw_text: str) -> str:
        if isinstance(raw_response, dict):
            recommendation = raw_response.get("recommendation")
            if recommendation:
                return self._coerce_content_to_text(recommendation)
        return raw_text

    def _extract_uncertainty(self, raw_response: Any, raw_text: str) -> list[str]:
        explicit = self._extract_string_list(raw_response, ["uncertainty_statements"])
        if explicit:
            return explicit

        statements: list[str] = []
        for line in raw_text.splitlines():
            lowered = line.lower()
            if any(
                token in lowered
                for token in (
                    "uncertain",
                    "insufficient",
                    "not enough information",
                    "guidelines are silent",
                    "outside the covered guidelines",
                )
            ):
                statements.append(line.strip())
        return statements

    def _extract_routed_guidelines(
        self,
        raw_response: Any,
        citations: list[Citation],
        retrieved_passages: list[RetrievedPassage],
    ) -> list[str]:
        explicit = self._extract_string_list(raw_response, ["routed_guidelines", "guidelines"])
        if explicit:
            return explicit

        guidelines = [
            citation.guideline
            for citation in citations
            if citation.guideline
        ] + [passage.guideline for passage in retrieved_passages if passage.guideline]
        deduped = list(dict.fromkeys(guidelines))
        return deduped

    def _extract_citations(self, raw_response: Any) -> list[Citation]:
        items = self._extract_list(raw_response, ["citations", "sources"])
        citations: list[Citation] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            payload = {
                "rec_id": item.get("rec_id") or item.get("id"),
                "class": item.get("class"),
                "level": item.get("level"),
                "guideline": item.get("guideline") or item.get("document"),
                "passage": self._coerce_content_to_text(
                    item.get("passage") or item.get("text") or item.get("content") or ""
                ),
            }
            citations.append(Citation.model_validate(payload))
        return citations

    def _extract_retrieved_passages(self, raw_response: Any) -> list[RetrievedPassage]:
        items = self._extract_list(raw_response, ["retrieved_passages", "sources"])
        passages: list[RetrievedPassage] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            text = self._coerce_content_to_text(
                item.get("text") or item.get("passage") or item.get("content") or ""
            )
            if not text:
                continue
            payload = {
                "guideline": item.get("guideline") or item.get("document") or "unknown",
                "chunk_id": item.get("chunk_id") or item.get("id") or f"source-{index}",
                "text": text,
            }
            passages.append(RetrievedPassage.model_validate(payload))
        return passages

    def _extract_string_list(self, raw_response: Any, keys: list[str]) -> list[str]:
        items = self._extract_list(raw_response, keys)
        return [str(item).strip() for item in items if str(item).strip()]

    def _extract_list(self, raw_response: Any, keys: list[str]) -> list[Any]:
        if not isinstance(raw_response, dict):
            return []
        for key in keys:
            value = raw_response.get(key)
            if isinstance(value, list):
                return value
        return []

    def _extract_bool(self, raw_response: Any, keys: list[str]) -> bool | None:
        if not isinstance(raw_response, dict):
            return None
        for key in keys:
            value = raw_response.get(key)
            if isinstance(value, bool):
                return value
        return None

    def _coerce_content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    if "text" in item:
                        parts.append(str(item["text"]))
                    elif item.get("type") == "text" and "content" in item:
                        parts.append(str(item["content"]))
            return "\n".join(part for part in parts if part).strip()
        if isinstance(content, dict):
            if "text" in content:
                return str(content["text"])
            if "content" in content:
                return self._coerce_content_to_text(content["content"])
        return str(content)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to config.yaml.")
    parser.add_argument(
        "--benchmark-path",
        help="Optional benchmark JSONL override.",
    )
    parser.add_argument(
        "--item-id",
        help="Optional benchmark item id to dry-run/normalize.",
    )
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
