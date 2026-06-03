"""Generate normalized AgentAnswer rows from the benchmark set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.agent_client import blind_text
from src.agent_client.base import AgentClient
from src.agent_client.http_client import HttpAgentClient
from src.agent_client.mcp_client import MCPAgentClient
from src.benchmark.validate_benchmark import load_benchmark_validated
from src.common.io import Cache, get_logger, load_config, write_jsonl
from src.common.schemas import AgentAnswer, BenchmarkItem


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Path to config.yaml.")
    parser.add_argument(
        "--benchmark-path",
        help="Optional benchmark JSONL override.",
    )
    parser.add_argument(
        "--output-path",
        help="Optional answers JSONL override.",
    )
    parser.add_argument(
        "--item-id",
        help="Optional single benchmark item id to run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dump one raw request/response pair instead of generating answers.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    benchmark_path = args.benchmark_path or config["paths"]["benchmark"]
    output_path = Path(args.output_path or config["paths"]["answers"])
    items = load_benchmark_validated(benchmark_path)
    selected_items = _filter_items(items, args.item_id)
    client = build_agent_client(config["agent"])

    if args.dry_run:
        sample = selected_items[0]
        result = client.dry_run(sample)
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

    rows = generate_answers(config=config, items=selected_items, client=client)
    write_jsonl(output_path, rows, mode="w")

    logger = get_logger(__name__)
    logger.info("wrote %s answers to %s", len(rows), output_path)
    return 0


def build_agent_client(agent_cfg: dict) -> AgentClient:
    client_type = agent_cfg.get("client", "http")
    if client_type == "http":
        return HttpAgentClient(agent_cfg)
    if client_type == "mcp":
        return MCPAgentClient()
    raise ValueError(f"Unsupported agent client type: {client_type}")


def generate_answers(
    *,
    config: dict,
    items: list[BenchmarkItem],
    client: AgentClient,
) -> list[dict]:
    run_cfg = config.get("run", {})
    use_cache = bool(run_cfg.get("cache", True))
    blind_answers = bool(run_cfg.get("blind_answers", True))
    runs_per_agent = int(run_cfg.get("runs_per_agent", 1))
    cache = Cache() if use_cache else None

    rows: list[dict] = []
    for item in items:
        for run_index in range(runs_per_agent):
            answer = _load_or_generate(
                item=item,
                run_index=run_index,
                client=client,
                cache=cache,
                blind_answers=blind_answers,
            )
            rows.append(answer.model_dump(by_alias=True))
    return rows


def _filter_items(items: list[BenchmarkItem], item_id: str | None) -> list[BenchmarkItem]:
    if item_id is None:
        return items
    for item in items:
        if item.id == item_id:
            return [item]
    raise ValueError(f"Benchmark item not found: {item_id}")


def _load_or_generate(
    *,
    item: BenchmarkItem,
    run_index: int,
    client: AgentClient,
    cache: Cache | None,
    blind_answers: bool,
) -> AgentAnswer:
    cache_key = None
    if cache is not None:
        cache_key = cache.key("agent_answer", item.id, client.cache_model_id, run_index)
        cached = cache.get(cache_key)
        if cached is not None:
            return AgentAnswer.model_validate(cached)

    answer = client.generate_answer(item, run_index=run_index)
    blinded_response = blind_text(answer.raw_response) if blind_answers else answer.raw_response
    answer = answer.model_copy(update={"blinded_response": blinded_response})

    if cache is not None and cache_key is not None:
        cache.set(cache_key, answer.model_dump(by_alias=True))

    return answer


if __name__ == "__main__":
    raise SystemExit(main())
