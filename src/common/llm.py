"""Thin judge-only Anthropic/OpenAI clients with retry and call metrics."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from anthropic import Anthropic
from anthropic import APITimeoutError as AnthropicTimeoutError
from openai import APITimeoutError as OpenAITimeoutError
from openai import OpenAI
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from src.common.io import write_jsonl

PRICES: dict[str, dict[str, dict[str, float]]] = {
    "anthropic": {
        "default": {"input_per_million": 15.0, "output_per_million": 75.0},
        "claude-opus-4-8": {"input_per_million": 15.0, "output_per_million": 75.0},
    },
    "openai": {
        "default": {"input_per_million": 1.25, "output_per_million": 10.0},
        "gpt-5": {"input_per_million": 1.25, "output_per_million": 10.0},
    },
}

DEFAULT_METRICS_PATH = Path("outputs/metrics/llm_calls.jsonl")
UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017


@dataclass(slots=True)
class LLMResult:
    text: str
    request_id: str | None
    latency_s: float
    tokens_in: int
    tokens_out: int
    raw: dict


class LLMClient(Protocol):
    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 1500,
        json_mode: bool = False,
    ) -> LLMResult: ...


def _exception_status_code(exc: Exception) -> int | None:
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    response_code = getattr(response, "status_code", None)
    return response_code if isinstance(response_code, int) else None


def _exception_request_id(exc: Exception) -> str | None:
    request_id = getattr(exc, "request_id", None)
    return request_id if isinstance(request_id, str) else None


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError | AnthropicTimeoutError | OpenAITimeoutError):
        return True
    status_code = _exception_status_code(exc)
    return status_code == 429 or (status_code is not None and 500 <= status_code < 600)


def _estimate_cost_usd(provider: str, model: str, tokens_in: int, tokens_out: int) -> float:
    provider_prices = PRICES.get(provider, {})
    price_row = provider_prices.get(model) or provider_prices.get("default")
    if not price_row:
        return 0.0
    return round(
        ((tokens_in / 1_000_000) * price_row["input_per_million"])
        + ((tokens_out / 1_000_000) * price_row["output_per_million"]),
        6,
    )


class _BaseLLMClient:
    provider: str

    def __init__(
        self,
        *,
        api_key: str | None,
        metrics_path: str | os.PathLike[str] | None = None,
        client: Any | None = None,
        timeout: float | None = None,
        retry_wait: Any | None = None,
        retry_stop: Any | None = None,
    ) -> None:
        self.metrics_path = Path(metrics_path or DEFAULT_METRICS_PATH)
        self.timeout = timeout
        self._sdk_client = client
        self._api_key = api_key
        self._retry_wait = retry_wait if retry_wait is not None else wait_exponential(
            multiplier=2,
            min=2,
            max=60,
        )
        self._retry_stop = retry_stop if retry_stop is not None else stop_after_attempt(6)

    def complete(
        self,
        system: str,
        user: str,
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 1500,
        json_mode: bool = False,
    ) -> LLMResult:
        started_at = time.monotonic()
        try:
            response = self._request_with_retry(
                system=system,
                user=user,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=json_mode,
            )
        except Exception as exc:
            latency_s = time.monotonic() - started_at
            self._append_metrics_row(
                model=model,
                request_id=_exception_request_id(exc),
                latency_s=latency_s,
                tokens_in=0,
                tokens_out=0,
            )
            raise

        latency_s = time.monotonic() - started_at
        result = self._build_result(response=response, latency_s=latency_s, json_mode=json_mode)
        self._append_metrics_row(
            model=model,
            request_id=result.request_id,
            latency_s=result.latency_s,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
        )
        return result

    def _request_with_retry(self, **kwargs: Any) -> Any:
        retryer = Retrying(
            reraise=True,
            retry=retry_if_exception(_is_retryable_exception),
            wait=self._retry_wait,
            stop=self._retry_stop,
        )
        return retryer(self._request, **kwargs)

    def _append_metrics_row(
        self,
        *,
        model: str,
        request_id: str | None,
        latency_s: float,
        tokens_in: int,
        tokens_out: int,
    ) -> None:
        row = {
            "ts": datetime.now(UTC).isoformat(),
            "provider": self.provider,
            "model": model,
            "request_id": request_id,
            "latency_s": round(latency_s, 6),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "est_cost_usd": _estimate_cost_usd(
                provider=self.provider,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            ),
        }
        write_jsonl(self.metrics_path, [row], mode="a")

    def _request(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    def _build_result(self, *, response: Any, latency_s: float, json_mode: bool) -> LLMResult:
        raise NotImplementedError


class AnthropicClient(_BaseLLMClient):
    provider = "anthropic"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self._sdk_client is None and self._api_key:
            self._sdk_client = Anthropic(api_key=self._api_key)

    def _request(
        self,
        *,
        system: str,
        user: str,
        model: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> Any:
        if self._sdk_client is None:
            raise ValueError("AnthropicClient requires ANTHROPIC_API_KEY or an injected client.")

        # claude-opus-4+ and newer extended-thinking models deprecated temperature
        _temperature_deprecated = model.startswith(("claude-opus-4", "claude-sonnet-4"))
        request: dict[str, Any] = {
            "model": model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "max_tokens": max_tokens,
        }
        if not _temperature_deprecated:
            request["temperature"] = temperature
        if self.timeout is not None:
            request["timeout"] = self.timeout
        if json_mode:
            request["tools"] = [
                {
                    "name": "json_output",
                    "description": "Return the response as a single JSON object.",
                    "input_schema": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                }
            ]
            request["tool_choice"] = {"type": "tool", "name": "json_output"}
        return self._sdk_client.messages.create(**request)

    def _build_result(self, *, response: Any, latency_s: float, json_mode: bool) -> LLMResult:
        text_blocks: list[str] = []
        for block in getattr(response, "content", []):
            block_type = getattr(block, "type", None)
            if block_type == "tool_use" and json_mode:
                text_blocks.append(json.dumps(getattr(block, "input", {}), ensure_ascii=False))
            elif block_type == "text":
                text_blocks.append(getattr(block, "text", ""))

        usage = getattr(response, "usage", None)
        return LLMResult(
            text="\n".join(part for part in text_blocks if part).strip(),
            request_id=getattr(response, "_request_id", None),
            latency_s=latency_s,
            tokens_in=int(getattr(usage, "input_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "output_tokens", 0) or 0),
            raw=response.model_dump(mode="json"),
        )


class OpenAIClient(_BaseLLMClient):
    provider = "openai"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        if self._sdk_client is None and self._api_key:
            self._sdk_client = OpenAI(api_key=self._api_key)

    def _request(
        self,
        *,
        system: str,
        user: str,
        model: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> Any:
        if self._sdk_client is None:
            raise ValueError("OpenAIClient requires OPENAI_API_KEY or an injected client.")

        # o-series reasoning models (o1, o3, o4-mini…) reject temperature
        # and require max_completion_tokens instead of max_tokens
        _reasoning_model = model.startswith(("o1", "o3", "o4"))
        tokens_key = "max_completion_tokens" if _reasoning_model else "max_tokens"
        request: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tokens_key: max_tokens,
        }
        if not _reasoning_model:
            request["temperature"] = temperature
        if self.timeout is not None:
            request["timeout"] = self.timeout
        if json_mode:
            request["response_format"] = {"type": "json_object"}
        return self._sdk_client.chat.completions.create(**request)

    def _build_result(self, *, response: Any, latency_s: float, json_mode: bool) -> LLMResult:
        choice = response.choices[0]
        message = getattr(choice, "message", None)
        text = getattr(message, "content", "") if message is not None else ""
        usage = getattr(response, "usage", None)
        return LLMResult(
            text=(text or "").strip(),
            request_id=getattr(response, "_request_id", None),
            latency_s=latency_s,
            tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
            raw=response.model_dump(mode="json"),
        )


def make_judge_client(judge_cfg: dict) -> LLMClient:
    provider = judge_cfg["provider"].lower()
    api_key = judge_cfg.get("api_key")
    if api_key is None and judge_cfg.get("api_key_env"):
        api_key = os.environ.get(judge_cfg["api_key_env"])

    common_kwargs = {
        "api_key": api_key,
        "metrics_path": judge_cfg.get("metrics_path", DEFAULT_METRICS_PATH),
        "timeout": judge_cfg.get("timeout_seconds"),
    }
    if provider == "anthropic":
        return AnthropicClient(**common_kwargs)
    if provider == "openai":
        return OpenAIClient(**common_kwargs)
    raise ValueError(f"Unsupported judge provider: {judge_cfg['provider']}")
