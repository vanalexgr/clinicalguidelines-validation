"""HTTP client for the ClinicalGuidelines.io agent.

Two API modes are supported — select via config ``agent.api_type``:

  api_type: laravel  (default)
  ─────────────────────────────
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

  api_type: openwebui
  ─────────────────────────────
  HTTP polling transport for chat.clinicalguidelines.io (OpenWebUI 0.9.x).
  The vascular_expert tool uses a two-phase gate; tasks are dispatched async
  and results retrieved by polling /api/v1/chats/.

  Phase 1 — initial question:
    POST /api/chat/completions  { model, messages, stream:false,
                                   chat_id (hint), id, session_id }
    Server returns {"status":true,"task_ids":[...],"chat_id":"..."}
    Poll GET /api/v1/chats/?limit=20 until a chat appears with 1 assistant
    message (the pre-retrieval gate confirmation).

  Phase 2 — confirmation:
    POST /api/chat/completions  { model, messages (with history+confirm),
                                   chat_id (real, from phase-1 chat) }
    No id/session_id → SSE streaming mode; response streamed synchronously.
    OR fall back to async mode + polling for 2nd assistant message.

  Tool output (sources[0].document[0]) contains a parseable RECOMMENDATIONS
  section with structured citation data (rec_id, class, level).

Run with --dry-run to dump one raw request/response for manual inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from src.agent_client.base import AgentClient, DryRunResult
from src.benchmark.validate_benchmark import load_benchmark_validated
from src.common.io import load_config
from src.common.schemas import AgentAnswer, BenchmarkItem, Citation, RetrievedPassage

# --------------------------------------------------------------------------- #
# Guideline key → canonical benchmark ID mapping
# (API key on left, benchmark expected_guidelines value on right)
# --------------------------------------------------------------------------- #
# Maps full guideline names returned by the API → canonical benchmark IDs
_GUIDELINE_NAME_TO_CANONICAL: dict[str, str] = {
    "ESVS 2024 Clinical Practice Guidelines on the Management of Abdominal Aorto-Iliac Artery Aneurysms": "ESVS_AAA_2024",
    "Management of Acute Limb Ischaemia": "ESVS_ALI_2020",
    "Management of Atherosclerotic Carotid and Vertebral Artery Disease": "ESVS_Carotid_2023",
    "Antithrombotic Therapy for Vascular Diseases": "ESVS_Antithrombotic_2023",
    "Management of Asymptomatic Lower Limb Peripheral Arterial Disease and Intermittent Claudication": "ESVS_Asymptomatic_PAD_IC_2024",
    "Global Vascular Guidelines on CLTI Management": "GVG_CLTI_2019",
    "Chronic Venous Disease of the Lower Limbs": "ESVS_Chronic_Venous_Disease_2022",
    "Management of Venous Thrombosis": "ESVS_Venous_Thrombosis_2021",
    "European Thoraco-Abdominal Aortic Diseases": "ESVS_ThoracoAbdominal_2026",
    "Thoracic Aortic Pathologies Involving the Aortic Arch": "ESVS_AorticArch_2024",
    "Management of Diseases of the Mesenteric and Renal Arteries and Veins": "ESVS_MesentericRenal_2017",
    "Management of Vascular Trauma": "ESVS_VascularTrauma_2023",
    "Management of Vascular Graft and Endograft Infections": "ESVS_GraftInfections_2020",
    "Vascular Access": "ESVS_VascularAccess_2023",
}

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


class _OpenWebUISynthesisTransport:
    """Three-phase synthesis transport for chat.clinicalguidelines.io.

    Phase 1 — SSE tool-call dispatch:
      POST /api/chat/completions  { model, messages, tools=[consult_vascular_guidelines],
                                    tool_choice:"required", stream:true }
      OpenWebUI streams tool_call deltas; parse guideline selection from args.

    Phase 2 — Direct Laravel retrieval:
      POST /api/v1/vascular-consult  { question, guidelines, pre_retrieval_mode:false }
      Returns llm_citation_chunks, llm_narrative_chunks, selected_guidelines.

    Phase 3 — SSE synthesis:
      POST /api/chat/completions  { model, messages=[user+tool_call+tool_result], stream:true }
      OpenWebUI streams the LLM synthesis; collect into final answer text.
    """

    _GUIDELINE_ENUM = [
        "aortic_arch", "descending_thoracic_aorta", "abdominal_aortic_aneurysm",
        "mesenteric_renal", "asymptomatic_pad", "clti", "acute_limb_ischaemia",
        "carotid_vertebral", "venous_thrombosis", "chronic_venous_disease",
        "antithrombotic_therapy", "vascular_trauma", "vascular_graft_infections",
        "vascular_access",
    ]
    _TOOL_SPEC: list[dict] = [
        {
            "type": "function",
            "function": {
                "name": "consult_vascular_guidelines",
                "description": (
                    "Consult ESVS Vascular Guidelines. Select 1-3 guidelines based on the "
                    "clinical question. Call this tool for any vascular surgery clinical or "
                    "guideline question, including follow-up in an ongoing vascular case."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "guideline_1": {"type": "string", "enum": _GUIDELINE_ENUM},
                        "guideline_2": {"type": "string", "enum": _GUIDELINE_ENUM},
                        "guideline_3": {"type": "string", "enum": _GUIDELINE_ENUM},
                    },
                    "required": ["question", "guideline_1"],
                },
            },
        }
    ]

    def __init__(
        self,
        ow_base_url: str,
        ow_api_key: str,
        model: str,
        laravel_base_url: str,
        laravel_api_key: str,
        pre_retrieval_mode: bool = False,
    ) -> None:
        self._ow_base = ow_base_url.rstrip("/")
        self._laravel_base = laravel_base_url.rstrip("/")
        self._ow_hdrs = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ow_api_key}",
        }
        self._laravel_hdrs = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {laravel_api_key}",
        }
        self._model = model
        self._pre_retrieval_mode = pre_retrieval_mode

    def __call__(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        return self._run(payload, timeout_seconds)

    # ------------------------------------------------------------------ #
    # Main entry point
    # ------------------------------------------------------------------ #

    def _run(self, cgio_payload: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        question = cgio_payload.get("question", "")
        history = cgio_payload.get("history") or []
        messages = _cgio_to_openai_messages(history, question)
        t0 = time.monotonic()

        # ── Phase 1: SSE call to get tool call args ────────────────────
        p1_timeout = min(30.0, timeout_seconds * 0.25)
        tool_calls = self._get_tool_call(messages, timeout=p1_timeout)
        if not tool_calls:
            return {"result": "", "_phase": "no_tool_call"}

        tc = tool_calls[0]
        try:
            args = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, KeyError):
            return {"result": "", "_phase": "bad_tool_args"}

        guidelines = [
            args[k] for k in ("guideline_1", "guideline_2", "guideline_3")
            if args.get(k)
        ]
        tool_question = args.get("question") or question

        # ── Phase 2: direct Laravel retrieval (gate-aware) ────────────
        p2_timeout = min(90.0, timeout_seconds * 0.65)
        laravel_resp = self._call_laravel(
            tool_question, guidelines,
            timeout=p2_timeout,
            pre_retrieval_mode=self._pre_retrieval_mode,
        )
        if not laravel_resp:
            return {"result": "", "_phase": "laravel_failed"}

        # ── Gate: if Laravel returned phase-1, auto-confirm ───────────
        gate_fired = False
        clarification_questions: list[str] = []
        if laravel_resp.get("phase") == "awaiting_confirmation":
            gate_fired = True
            clarification_questions = [
                str(q).strip()
                for q in (laravel_resp.get("clarification_questions") or [])
                if str(q).strip()
            ]
            confirmation_msg = laravel_resp.get("confirmation_message") or ""
            # Send phase-2: confirm without supplying missing parameters so the
            # benchmark captures the system's behaviour under incomplete input.
            p2b_timeout = min(p2_timeout, max(30.0, timeout_seconds - (time.monotonic() - t0)))
            laravel_resp2 = self._call_laravel(
                "Confirmed. Please proceed.",
                guidelines,
                timeout=p2b_timeout,
                pre_retrieval_mode=True,
                history=[tool_question, confirmation_msg],
            )
            if laravel_resp2:
                laravel_resp = laravel_resp2

        llm_out = _format_tool_output(laravel_resp)

        # ── Phase 3: SSE synthesis with tool result ────────────────────
        messages2 = messages + [
            {"role": "assistant", "content": None, "tool_calls": tool_calls},
            {"role": "tool", "tool_call_id": tc["id"], "content": llm_out},
        ]
        remaining = timeout_seconds - (time.monotonic() - t0)
        final_text = self._get_synthesis(messages2, timeout=max(30.0, remaining))

        return {
            "result": final_text,
            "citation_chunks": laravel_resp.get("llm_citation_chunks") or [],
            "narrative_chunks": laravel_resp.get("llm_narrative_chunks") or [],
            "selected_guidelines": laravel_resp.get("selected_guidelines") or {},
            "gap_assessment": laravel_resp.get("gap_assessment") or {},
            "_gate_fired": gate_fired,
            "_clarification_questions": clarification_questions,
        }

    # ------------------------------------------------------------------ #
    # Phase helpers
    # ------------------------------------------------------------------ #

    def _get_tool_call(self, messages: list[dict], timeout: float) -> list[dict]:
        """SSE call with tool_choice:required; return assembled tool_call objects."""
        hdrs = dict(self._ow_hdrs)
        hdrs["Accept"] = "text/event-stream"
        body = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "chat_id": str(uuid.uuid4()),
            "tools": self._TOOL_SPEC,
            "tool_choice": "required",
        }
        req = urllib.request.Request(
            f"{self._ow_base}/api/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=hdrs,
            method="POST",
        )
        raw_tcs: dict[int, dict] = {}
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                for line_bytes in r:
                    line = line_bytes.decode("utf-8", errors="replace").rstrip()
                    if not line.startswith("data: "):
                        continue
                    ds = line[6:]
                    if ds == "[DONE]":
                        break
                    try:
                        ev = json.loads(ds)
                        for ch in (ev.get("choices") or []):
                            for tc in (ch.get("delta") or {}).get("tool_calls") or []:
                                idx = tc.get("index", 0)
                                if idx not in raw_tcs:
                                    raw_tcs[idx] = {"id": "", "type": "function",
                                                    "function": {"name": "", "arguments": ""}}
                                if tc.get("id"):
                                    raw_tcs[idx]["id"] = tc["id"]
                                fn = tc.get("function") or {}
                                if fn.get("name"):
                                    raw_tcs[idx]["function"]["name"] = fn["name"]
                                raw_tcs[idx]["function"]["arguments"] += fn.get("arguments") or ""
                    except (json.JSONDecodeError, KeyError):
                        pass
        except (urllib.error.URLError, urllib.error.HTTPError):
            return []
        return list(raw_tcs.values())

    def _call_laravel(
        self,
        question: str,
        guidelines: list[str],
        timeout: float,
        pre_retrieval_mode: bool = False,
        history: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Call the Laravel /api/v1/vascular-consult endpoint directly."""
        body: dict[str, Any] = {
            "question": question,
            "guidelines": guidelines,
            "pre_retrieval_mode": pre_retrieval_mode,
        }
        if history:
            body["history"] = history
        req = urllib.request.Request(
            f"{self._laravel_base}/api/v1/vascular-consult",
            data=json.dumps(body).encode("utf-8"),
            headers=self._laravel_hdrs,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def _get_synthesis(self, messages: list[dict], timeout: float) -> str:
        """SSE call with tool result message; return the synthesized answer text."""
        hdrs = dict(self._ow_hdrs)
        hdrs["Accept"] = "text/event-stream"
        body = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            "chat_id": str(uuid.uuid4()),
        }
        req = urllib.request.Request(
            f"{self._ow_base}/api/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=hdrs,
            method="POST",
        )
        text_parts: list[str] = []
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                for line_bytes in r:
                    line = line_bytes.decode("utf-8", errors="replace").rstrip()
                    if not line.startswith("data: "):
                        continue
                    ds = line[6:]
                    if ds == "[DONE]":
                        break
                    try:
                        ev = json.loads(ds)
                        for ch in (ev.get("choices") or []):
                            text = (ch.get("delta") or {}).get("content") or ""
                            if text:
                                text_parts.append(str(text))
                    except (json.JSONDecodeError, KeyError):
                        pass
        except (urllib.error.URLError, urllib.error.HTTPError):
            pass
        return "".join(text_parts)


# ────────────────────────────────────────────────────────────────────────────
# Module-level helpers shared by _OpenWebUIPollingTransport and tests
# ────────────────────────────────────────────────────────────────────────────

def _cgio_to_openai_messages(
    history: list[str], question: str
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for i, turn in enumerate(history):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": str(turn)})
    messages.append({"role": "user", "content": question})
    return messages


def _sorted_assistant_msgs(chat_data: dict) -> list[dict]:
    history = chat_data.get("chat", {}).get("history", {})
    msgs = history.get("messages") or {}
    asst = [m for m in msgs.values() if m.get("role") == "assistant"]
    return sorted(asst, key=lambda m: m.get("timestamp", 0))


def _is_final_answer(msg: dict) -> bool:
    content = msg.get("content") or ""
    return len(content) > 150 and ("##" in content or "ESVS" in content or "recommend" in content.lower())


_REC_PATTERN = re.compile(
    r"\[(?P<index>\d+)\]\s+Rec\s+(?P<rec_id>[\w.]+)\s*"
    r"\(Class\s+(?P<class>[^,)]+),\s*Level\s+(?P<level>[^)]+)\)[^\n]*\n"
    r">\s+rec_id:[^;]+;\s*[^\n]*guideline_name:(?P<guideline>[^;]+);[^\n]*",
    re.IGNORECASE,
)


def _parse_citations_from_tool_doc(doc: str) -> list[dict[str, Any]]:
    if not doc or "=== RECOMMENDATIONS ===" not in doc:
        return []
    section_start = doc.index("=== RECOMMENDATIONS ===")
    section_end = doc.find("\n===", section_start + 10)
    section = doc[section_start: section_end if section_end > 0 else len(doc)]

    citations = []
    for m in _REC_PATTERN.finditer(section):
        guideline_name = m.group("guideline").strip()
        citations.append({
            "recommendation_id": f"Rec {m.group('rec_id').strip()}",
            "class": f"Class {m.group('class').strip()}",
            "level": f"Level {m.group('level').strip()}",
            "source_guideline": guideline_name,
            "text": "",
        })
    return citations


def _parse_guidelines_from_metadata(metadata: list[dict]) -> list[dict[str, Any]]:
    if not metadata:
        return []
    params = metadata[0].get("parameters") or {}
    keys = [params.get(f"guideline_{i}") for i in range(1, 4)]
    return [{"key": k} for k in keys if k]


def _format_tool_output(laravel_resp: dict[str, Any]) -> str:
    """Format Laravel /api/v1/vascular-consult response as the tool's llm_out string."""
    llm_cit = laravel_resp.get("llm_citation_chunks") or []
    llm_nar = laravel_resp.get("llm_narrative_chunks") or []
    out: list[str] = []
    n = 1
    if llm_cit:
        out.append("=== RECOMMENDATIONS ===\n")
        for c in llm_cit:
            rec_id = c.get("recommendation_id", "N/A")
            cls = c.get("class", "N/A")
            lvl = c.get("level", "N/A")
            gl = c.get("guideline", "ESVS")
            text = str(c.get("text", ""))[:1200]
            out.append(f"[{n}] Rec {rec_id} (Class {cls}, Level {lvl}) — {gl}\n> {text}\n\n")
            n += 1
    else:
        out.append("=== RECOMMENDATIONS ===\nNo recommendation chunks retrieved.\n\n")
    if llm_nar:
        out.append("=== NARRATIVE CONTEXT ===\n")
        ni = 1
        for c in llm_nar:
            src = c.get("source_guideline", "ESVS")
            content = str(c.get("content", ""))[:1500]
            out.append(f"[{n}] {src} — Narrative {ni}\n{content}\n\n")
            n += 1
            ni += 1
    return "".join(out)


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

        if transport is not None:
            self._transport = transport
        elif agent_cfg.get("api_type") == "openwebui_synthesis":
            model = str(agent_cfg.get("model") or "gpt-5-chat")
            laravel_base = str(agent_cfg.get("laravel_base_url") or self.base_url)
            laravel_key = str(agent_cfg.get("laravel_api_key") or "")
            self._transport = _OpenWebUISynthesisTransport(
                ow_base_url=self.base_url,
                ow_api_key=str(self.api_key or ""),
                model=model,
                laravel_base_url=laravel_base,
                laravel_api_key=laravel_key,
                pre_retrieval_mode=self.pre_retrieval_mode,
            )
        else:
            self._transport = self._default_transport

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

        # ── Two-phase gate interaction ────────────────────────────────────────
        # When pre_retrieval_mode=True the gate runs first.  If it needs more
        # information it returns phase="awaiting_confirmation" before retrieval.
        # We automatically confirm so the benchmark always receives a full answer.
        # Phase-1 gate metadata (gate_fired, clarification_questions) is stashed
        # into the phase-2 response dict so _normalize() can surface it.
        if (
            isinstance(raw_response, dict)
            and raw_response.get("phase") == "awaiting_confirmation"
        ):
            phase1 = raw_response
            original_question = item.turns[-1].content
            confirmation_msg = phase1.get("confirmation_message") or ""
            phase2_payload: dict[str, Any] = {
                "question": "Confirmed. Please proceed.",
                "history": [original_question, confirmation_msg],
                "pre_retrieval_mode": True,
            }
            raw_response = self._transport(url, phase2_payload, headers, self.timeout_seconds)
            # Stash phase-1 gate data so _normalize() can read it even though
            # the phase-2 response no longer carries phase="awaiting_confirmation".
            if isinstance(raw_response, dict):
                raw_response["_phase1_gate_fired"] = True
                raw_response["_phase1_clarification_questions"] = (
                    phase1.get("clarification_questions") or []
                )
                raw_response["_phase1_confirmation_message"] = confirmation_msg

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

        # Two-phase interaction completed inside the transport: gate fired and
        # auto-confirmed; gate metadata surfaced as _gate_fired / _clarification_questions.
        # Also handle legacy _phase1_gate_fired stash from _default_transport path.
        if not gate_fired and (
            raw_response.get("_gate_fired") or raw_response.get("_phase1_gate_fired")
        ):
            gate_fired = True

        # Clarification questions (only meaningful when gate fires)
        clarification_requested: list[str] = []
        if gate_fired:
            qs = (
                raw_response.get("_clarification_questions")
                or raw_response.get("_phase1_clarification_questions")
                or raw_response.get("clarification_questions")
                or []
            )
            clarification_requested = [str(q).strip() for q in qs if str(q).strip()]

        # Answer text — always from phase-2 (or direct response if no gate fired)
        if phase == "awaiting_confirmation":
            # Single-phase gate response with no follow-up (should not occur
            # when pre_retrieval_mode is enabled, but handle gracefully).
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

        # Primary: CGIO selected_guidelines — two possible shapes:
        #   list shape (laravel gate):   [{key: "abdominal_aortic_aneurysm", ...}]
        #   dict shape (synthesis transport): {"abdominal_aortic_aneurysm": {...}}
        sg_raw = payload.get("selected_guidelines") or []
        if isinstance(sg_raw, dict):
            sg_raw = [{"key": k} for k in sg_raw]
        for entry in sg_raw:
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
    # recommendation_id arrives as "Rec 1.2.3" — strip prefix for index matching
    if isinstance(rec_id, str) and rec_id.upper().startswith("REC"):
        rec_id = rec_id.split(None, 1)[-1].strip()

    # class/level arrive as "Class I" / "Level A" — strip prefixes for index matching
    raw_class = chunk.get("class") or ""
    if isinstance(raw_class, str) and raw_class.lower().startswith("class "):
        raw_class = raw_class[6:].strip()

    raw_level = chunk.get("level") or ""
    if isinstance(raw_level, str) and raw_level.lower().startswith("level "):
        raw_level = raw_level[6:].strip()

    raw_guideline = chunk.get("source_guideline") or chunk.get("guideline") or ""
    canonical_guideline = _GUIDELINE_NAME_TO_CANONICAL.get(raw_guideline, raw_guideline) or None

    return Citation.model_validate({
        "rec_id": rec_id,
        "class": raw_class or None,
        "level": raw_level or None,
        "guideline": canonical_guideline,
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
