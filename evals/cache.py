"""Disk cache for LLM outputs and embeddings, so reruns measure code changes, not sampling noise."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

CACHE_DIR = Path(__file__).resolve().parent / ".cache"


def cache_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    tmp.replace(path)


def cached_json(namespace: str, key: str, compute: Callable[[], Any], cache_dir: Path = CACHE_DIR) -> Any:
    path = cache_dir / namespace / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    value = compute()
    _write(path, value)
    return value


def cached_batch(
    namespace: str, keys: list[str], compute: Callable[[list[int]], list[Any]], cache_dir: Path = CACHE_DIR
) -> list[Any]:
    """Like cached_json for a list. compute() gets the positions that aren't cached yet, in one call."""
    paths = [cache_dir / namespace / f"{key}.json" for key in keys]
    values = [json.loads(p.read_text(encoding="utf-8")) if p.exists() else None for p in paths]
    missing = [i for i, p in enumerate(paths) if not p.exists()]
    if missing:
        for i, value in zip(missing, compute(missing)):
            _write(paths[i], value)
            values[i] = value
    return values
