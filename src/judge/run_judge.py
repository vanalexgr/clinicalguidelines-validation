"""Run blinded LLM-as-judge evaluation over normalized agent answers."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import StrictUndefined, Template
from pydantic import ValidationError

from src.agent_client import blind_text
from src.common.io import Cache, get_logger, load_benchmark, load_config, read_jsonl, write_jsonl
from src.common.llm import LLMClient, LLMResult, make_judge_client
from src.common.schemas import AgentAnswer, BenchmarkItem, Judgment

UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SYSTEM_PROMPT_PATH = PROMPTS_DIR / "judge_system.txt"
USER_PROMPT_PATH = PROMPTS_DIR / "judge_user.j2"


@dataclass(slots=True)
class JudgePrompts:
    system: str
    user_template: Template


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to config.yaml.")
    parser.add_argument(
        "--benchmark-path",
        help="Optional benchmark JSONL override.",
    )
    parser.add_argument(
        "--answers-path",
        help="Optional answers JSONL override.",
    )
    parser.add_argument(
        "--output-path",
        help="Optional judgments JSONL override.",
    )
    parser.add_argument(
        "--item-id",
        help="Optional single benchmark item id to run.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    benchmark_path = args.benchmark_path or config["paths"]["benchmark"]
    answers_path = args.answers_path or config["paths"]["answers"]
    output_path = Path(args.output_path or config["paths"]["judgments"])

    items = _filter_items(load_benchmark(benchmark_path), args.item_id)
    answers = _filter_answers(load_answers(answers_path), args.item_id)
    prompts = load_judge_prompts()
    failures_path = _failure_log_path(config)
    clients = build_judge_clients(config)

    rows = run_judges(
        config=config,
        items=items,
        answers=answers,
        prompts=prompts,
        clients=clients,
        failures_path=failures_path,
    )
    write_jsonl(output_path, rows, mode="w")

    logger = get_logger(__name__)
    logger.info("wrote %s judgments to %s", len(rows), output_path)
    return 0


def build_judge_clients(config: dict) -> dict[str, LLMClient]:
    metrics_path = Path(config["paths"]["metrics_dir"]) / "llm_calls.jsonl"
    clients: dict[str, LLMClient] = {}
    for judge_cfg in config["judges"]:
        cfg = dict(judge_cfg)
        cfg.setdefault("metrics_path", metrics_path)
        clients[cfg["name"]] = make_judge_client(cfg)
    return clients


def load_judge_prompts(
    system_path: str | Path = SYSTEM_PROMPT_PATH,
    user_template_path: str | Path = USER_PROMPT_PATH,
) -> JudgePrompts:
    system = Path(system_path).read_text(encoding="utf-8")
    user_template = Template(
        Path(user_template_path).read_text(encoding="utf-8"),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return JudgePrompts(system=system, user_template=user_template)


def load_answers(path: str | Path) -> list[AgentAnswer]:
    answers: list[AgentAnswer] = []
    for line_number, row in enumerate(read_jsonl(path), start=1):
        try:
            answers.append(AgentAnswer.model_validate(row))
        except ValidationError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return answers


def render_judge_user_prompt(
    *,
    item: BenchmarkItem,
    answer: AgentAnswer,
    user_template: Template,
) -> str:
    blinded_response = answer.blinded_response or blind_text(answer.raw_response)
    return user_template.render(
        turns=[turn.model_dump() for turn in item.turns],
        blinded_response=blinded_response,
        routed_guidelines=answer.routed_guidelines,
        citations=[citation.model_dump(by_alias=True) for citation in answer.citations],
        retrieved_passages=[passage.model_dump() for passage in answer.retrieved_passages],
        gold=item.gold.model_dump(by_alias=True),
    ).strip()


def run_judges(
    *,
    config: dict,
    items: list[BenchmarkItem],
    answers: list[AgentAnswer],
    prompts: JudgePrompts,
    clients: dict[str, LLMClient],
    failures_path: str | Path,
) -> list[dict]:
    item_by_id = {item.id: item for item in items}
    run_cfg = config.get("run", {})
    runs_per_judge = int(run_cfg.get("runs_per_judge", 1))
    use_cache = bool(run_cfg.get("cache", True))
    cache = Cache() if use_cache else None
    logger = get_logger(__name__)
    judgments: list[dict] = []

    for answer in answers:
        item = item_by_id.get(answer.id)
        if item is None:
            raise ValueError(f"Answer {answer.id} does not match any benchmark item.")

        for judge_cfg in config["judges"]:
            judge_name = judge_cfg["name"]
            client = clients[judge_name]
            for run_index in range(runs_per_judge):
                judgment = _load_or_judge(
                    item=item,
                    answer=answer,
                    judge_cfg=judge_cfg,
                    client=client,
                    run_index=run_index,
                    prompts=prompts,
                    cache=cache,
                    failures_path=failures_path,
                )
                if judgment is None:
                    logger.warning(
                        "judge failed for item=%s answer_run=%s judge=%s run=%s",
                        item.id,
                        answer.run_index,
                        judge_name,
                        run_index,
                    )
                    continue
                judgments.append(judgment.model_dump(by_alias=True))
    return judgments


def _load_or_judge(
    *,
    item: BenchmarkItem,
    answer: AgentAnswer,
    judge_cfg: dict,
    client: LLMClient,
    run_index: int,
    prompts: JudgePrompts,
    cache: Cache | None,
    failures_path: str | Path,
) -> Judgment | None:
    cache_key = None
    cache_item_id = f"{item.id}:answer-{answer.run_index}"
    if cache is not None:
        cache_key = cache.key("judge", cache_item_id, judge_cfg["name"], run_index)
        cached = cache.get(cache_key)
        if cached is not None:
            return Judgment.model_validate(cached)

    user_prompt = render_judge_user_prompt(
        item=item,
        answer=answer,
        user_template=prompts.user_template,
    )
    result = client.complete(
        prompts.system,
        user_prompt,
        model=judge_cfg["name"],
        temperature=float(judge_cfg.get("temperature", 0.0)),
        max_tokens=int(judge_cfg.get("max_tokens", 1500)),
        json_mode=True,
    )

    parse_error: Exception | None = None
    try:
        judgment = _parse_judgment(
            result=result,
            query_id=item.id,
            answer_run_index=answer.run_index,
            judge_name=judge_cfg["name"],
            run_index=run_index,
        )
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        parse_error = exc
    else:
        if cache is not None and cache_key is not None:
            cache.set(cache_key, judgment.model_dump(by_alias=True))
        return judgment

    repair_result = client.complete(
        prompts.system,
        build_repair_user_prompt(
            original_user_prompt=user_prompt,
            invalid_output=result.text,
            error=parse_error,
        ),
        model=judge_cfg["name"],
        temperature=float(judge_cfg.get("temperature", 0.0)),
        max_tokens=int(judge_cfg.get("max_tokens", 1500)),
        json_mode=True,
    )
    try:
        judgment = _parse_judgment(
            result=repair_result,
            query_id=item.id,
            answer_run_index=answer.run_index,
            judge_name=judge_cfg["name"],
            run_index=run_index,
        )
    except (json.JSONDecodeError, ValidationError, ValueError) as repair_error:
        _log_failure(
            failures_path=failures_path,
            item=item,
            answer=answer,
            judge_cfg=judge_cfg,
            run_index=run_index,
            initial_result=result,
            initial_error=parse_error,
            repair_result=repair_result,
            repair_error=repair_error,
        )
        return None

    if cache is not None and cache_key is not None:
        cache.set(cache_key, judgment.model_dump(by_alias=True))
    return judgment


def _parse_judgment(
    *,
    result: LLMResult,
    query_id: str,
    answer_run_index: int,
    judge_name: str,
    run_index: int,
) -> Judgment:
    payload = json.loads(result.text)
    if not isinstance(payload, dict):
        raise ValueError("judge output must decode to a JSON object")

    payload["query_id"] = query_id
    payload["judge"] = judge_name
    payload["run_index"] = run_index
    payload["_meta"] = {
        "request_id": result.request_id,
        "latency_s": result.latency_s,
        "tokens": {
            "in": result.tokens_in,
            "out": result.tokens_out,
        },
        "answer_run_index": answer_run_index,
    }
    return Judgment.model_validate(payload)


def build_repair_user_prompt(
    *,
    original_user_prompt: str,
    invalid_output: str,
    error: Exception | None,
) -> str:
    message = str(error) if error is not None else "unknown error"
    return (
        "Your previous response was not valid strict JSON for this evaluation task.\n"
        "Return ONLY one valid JSON object matching the required output schema.\n"
        "Do not include markdown, code fences, or any commentary.\n\n"
        f"Validation/parsing error: {message}\n\n"
        "Original evaluation prompt:\n"
        f"{original_user_prompt}\n\n"
        "Previous invalid response:\n"
        f"{invalid_output}"
    )


def _log_failure(
    *,
    failures_path: str | Path,
    item: BenchmarkItem,
    answer: AgentAnswer,
    judge_cfg: dict,
    run_index: int,
    initial_result: LLMResult,
    initial_error: Exception | None,
    repair_result: LLMResult,
    repair_error: Exception,
) -> None:
    row = {
        "ts": datetime.now(UTC).isoformat(),
        "query_id": item.id,
        "answer_run_index": answer.run_index,
        "judge": judge_cfg["name"],
        "run_index": run_index,
        "initial_request_id": initial_result.request_id,
        "initial_latency_s": round(initial_result.latency_s, 6),
        "initial_tokens_in": initial_result.tokens_in,
        "initial_tokens_out": initial_result.tokens_out,
        "initial_error": str(initial_error) if initial_error is not None else None,
        "initial_raw_output": initial_result.text,
        "repair_request_id": repair_result.request_id,
        "repair_latency_s": round(repair_result.latency_s, 6),
        "repair_tokens_in": repair_result.tokens_in,
        "repair_tokens_out": repair_result.tokens_out,
        "repair_error": str(repair_error),
        "repair_raw_output": repair_result.text,
    }
    write_jsonl(failures_path, [row], mode="a")


def _failure_log_path(config: dict) -> Path:
    return Path(config["paths"]["metrics_dir"]) / "judge_failures.jsonl"


def _filter_items(items: list[BenchmarkItem], item_id: str | None) -> list[BenchmarkItem]:
    if item_id is None:
        return items
    for item in items:
        if item.id == item_id:
            return [item]
    raise ValueError(f"Benchmark item not found: {item_id}")


def _filter_answers(answers: list[AgentAnswer], item_id: str | None) -> list[AgentAnswer]:
    if item_id is None:
        return answers
    filtered = [answer for answer in answers if answer.id == item_id]
    if not filtered:
        raise ValueError(f"Answer item not found: {item_id}")
    return filtered


if __name__ == "__main__":
    raise SystemExit(main())
