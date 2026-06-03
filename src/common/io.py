"""Shared I/O, config, cache, and logging helpers."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Iterator
from hashlib import sha1
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import yaml

from src.common.schemas import BenchmarkItem

PathLike = str | os.PathLike[str]


def read_jsonl(path: PathLike) -> Iterator[dict]:
    """Yield JSON objects from a JSONL file."""
    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{source}:{line_number}: expected a JSON object per line.")
            yield row


def write_jsonl(path: PathLike, rows: Iterable[dict], *, mode: str = "w") -> None:
    """Write JSON objects to a JSONL file."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open(mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def load_benchmark(path: PathLike) -> list[BenchmarkItem]:
    """Load and validate benchmark items from JSONL."""
    source = Path(path)
    items: list[BenchmarkItem] = []

    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{source}:{line_number}: invalid JSON: {exc.msg}") from exc
            try:
                items.append(BenchmarkItem.model_validate(payload))
            except Exception as exc:  # pydantic validation surface kept intact for the caller
                raise ValueError(f"{source}:{line_number}: {exc}") from exc

    return items


def load_config(path: PathLike) -> dict:
    """Load YAML config and resolve *_env keys from the environment."""
    source = Path(path)
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    return _resolve_env_keys(raw)


def _resolve_env_keys(value: Any) -> Any:
    if isinstance(value, dict):
        resolved: dict[str, Any] = {}
        for key, item in value.items():
            resolved_item = _resolve_env_keys(item)
            resolved[key] = resolved_item
            if key.endswith("_env"):
                target_key = key[: -len("_env")]
                resolved[target_key] = os.environ.get(resolved_item) if isinstance(
                    resolved_item, str
                ) else None
        return resolved
    if isinstance(value, list):
        return [_resolve_env_keys(item) for item in value]
    return value


def get_logger(name: str) -> logging.Logger:
    """Return a logger with a consistent stderr formatter."""
    logger = logging.getLogger(name)
    if getattr(logger, "_cgio_configured", False):
        return logger

    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )

    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    logger._cgio_configured = True  # type: ignore[attr-defined]
    return logger


class Cache:
    """Small file-backed JSON cache keyed by stage/item/model/run."""

    def __init__(self, dir: PathLike = ".cache") -> None:
        self.dir = Path(dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def key(self, stage: str, item_id: str, model: str, run_index: int) -> str:
        joined = "\x1f".join((stage, item_id, model, str(run_index)))
        return sha1(joined.encode("utf-8")).hexdigest()

    def get(self, key: str) -> dict | None:
        target = self.dir / f"{key}.json"
        if not target.exists():
            return None
        with target.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError(f"{target}: cached value must be a JSON object.")
        return value

    def set(self, key: str, value: dict) -> None:
        target = self.dir / f"{key}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            delete=False,
        ) as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
            tmp_name = handle.name
        os.replace(tmp_name, target)
