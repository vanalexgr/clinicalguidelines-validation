"""No-gate baseline: does an unguarded LLM ask for the missing parameters by itself?

Reviewer 2 objects that framing the prior tool as "passive" overstates the Context
Gate's novelty, because current models will request missing detail on their own.
That is testable. This runs the underspecified benchmark vignettes through the
synthesis model with no gate, no retrieval and no clarification instruction, and
measures the same two quantities reported for the gate:

  sensitivity      -- share of underspecified queries that draw any clarification
  parameter recall -- share of the gold mandatory parameters actually requested

The system prompt deliberately neither asks for nor discourages clarification;
instructing either way would decide the result in advance.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

from src.common.llm import OpenAIClient

SYSTEM_PROMPT = (
    "You are a clinical decision support assistant for vascular surgery. "
    "A clinician has asked you the following question. Respond as you judge best."
)

CLARIFY_PATTERNS = [
    r"\?\s*$",
    r"\bcould you\b|\bcan you\b|\bplease (?:provide|specify|confirm|clarify|share)\b",
    r"\bwhat (?:is|are|was|were)\b|\bdo you know\b|\bis the\b.*\?",
    r"\bmore information\b|\badditional information\b|\bneed to know\b",
    r"\bin order to advise\b|\bto answer (?:this|that)\b.*\bneed\b",
]


def _asks_for_clarification(text: str) -> bool:
    body = text.strip()
    if not body:
        return False
    return any(re.search(p, body, re.I | re.M) for p in CLARIFY_PATTERNS)


def _parameter_recall(text: str, required: list[str]) -> tuple[float, list[str]]:
    """Share of gold mandatory parameters whose key terms appear in the reply.

    Mirrors the keyword-in-text rule the deterministic gate metric uses, so the
    two numbers are comparable rather than merely adjacent.
    """
    if not required:
        return float("nan"), []
    low = text.lower()
    hits = []
    for param in required:
        terms = [t for t in re.split(r"[^a-z0-9.]+", param.lower()) if len(t) > 3]
        if terms and any(t in low for t in terms):
            hits.append(param)
    return len(hits) / len(required), hits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/config.yaml"))
    parser.add_argument("--model", default="gpt-5-chat-latest")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("outputs/metrics/nogate_baseline.jsonl"))
    parser.add_argument("--env", type=Path, default=Path(".env"))
    args = parser.parse_args()

    load_dotenv(args.env)
    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    bench = [
        json.loads(line)
        for line in Path(cfg["paths"]["benchmark"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    items = [i for i in bench if i.get("query_type") == "C_underspecified"]
    print(f"{len(items)} underspecified items x {args.runs} runs, model={args.model}")

    client = OpenAIClient(
        api_key=os.environ.get("OPENAI_API_KEY"),
        metrics_path=Path(cfg["paths"]["metrics_dir"]) / "llm_calls.jsonl",
        timeout=120,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for item in items:
        question = item["turns"][0]["content"]
        required = item["gold"].get("required_parameters") or []
        for run in range(args.runs):
            result = client.complete(
                system=SYSTEM_PROMPT,
                user=question,
                model=args.model,
                temperature=0.0,
                max_tokens=1200,
            )
            text = result.text if hasattr(result, "text") else str(result)
            asked = _asks_for_clarification(text)
            recall, hits = _parameter_recall(text, required)
            records.append(
                {
                    "query_id": item["id"],
                    "run_index": run,
                    "model": args.model,
                    "asked_for_clarification": asked,
                    "parameter_recall": recall,
                    "parameters_requested": hits,
                    "required_parameters": required,
                    "response": text,
                }
            )
            print(f"  {item['id']} run{run}: clarify={asked} recall={recall:.2f}")

    with args.out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(records)} records to {args.out}")


if __name__ == "__main__":
    main()
