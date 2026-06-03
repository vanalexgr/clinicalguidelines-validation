from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from tenacity import wait_none

from src.common.llm import AnthropicClient, OpenAIClient, make_judge_client


class FakeOpenAIResponse:
    def __init__(self) -> None:
        self.choices = [SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))]
        self.usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7)
        self._request_id = "req-openai-1"

    def model_dump(self, mode: str = "json") -> dict:
        return {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"prompt": 11}}


class FakeAnthropicTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeAnthropicResponse:
    def __init__(self) -> None:
        self.content = [FakeAnthropicTextBlock("done")]
        self.usage = SimpleNamespace(input_tokens=5, output_tokens=3)
        self._request_id = "req-anthropic-1"

    def model_dump(self, mode: str = "json") -> dict:
        return {"content": [{"type": "text", "text": "done"}], "usage": {"input_tokens": 5}}


class FakeStatusError(Exception):
    def __init__(self, status_code: int, request_id: str | None = None) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
        self.request_id = request_id


def test_openai_client_retries_once_and_logs_metrics(tmp_path: Path) -> None:
    metrics_path = tmp_path / "llm_calls.jsonl"
    client = OpenAIClient(
        api_key=None,
        client=object(),
        metrics_path=metrics_path,
        retry_wait=wait_none(),
    )

    attempts = {"count": 0}

    def fake_request(**kwargs):  # type: ignore[no-untyped-def]
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise FakeStatusError(429, request_id="req-retry")
        return FakeOpenAIResponse()

    client._request = fake_request  # type: ignore[method-assign]

    result = client.complete(
        system="system",
        user="user",
        model="gpt-5",
        json_mode=True,
    )

    assert attempts["count"] == 2
    assert result.text == '{"ok": true}'
    assert result.request_id == "req-openai-1"
    assert result.tokens_in == 11
    assert result.tokens_out == 7

    rows = [json.loads(line) for line in metrics_path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["provider"] == "openai"
    assert rows[0]["model"] == "gpt-5"
    assert rows[0]["request_id"] == "req-openai-1"
    assert rows[0]["tokens_in"] == 11
    assert rows[0]["tokens_out"] == 7
    assert rows[0]["est_cost_usd"] > 0


def test_anthropic_client_does_not_retry_401(tmp_path: Path) -> None:
    client = AnthropicClient(
        api_key=None,
        client=object(),
        metrics_path=tmp_path / "llm_calls.jsonl",
        retry_wait=wait_none(),
    )

    attempts = {"count": 0}

    def fake_request(**kwargs):  # type: ignore[no-untyped-def]
        attempts["count"] += 1
        raise FakeStatusError(401, request_id="req-401")

    client._request = fake_request  # type: ignore[method-assign]

    with pytest.raises(FakeStatusError):
        client.complete(system="system", user="user", model="claude-opus-4-8")

    assert attempts["count"] == 1


def test_make_judge_client_dispatches_on_provider() -> None:
    assert isinstance(make_judge_client({"provider": "anthropic"}), AnthropicClient)
    assert isinstance(make_judge_client({"provider": "openai"}), OpenAIClient)
