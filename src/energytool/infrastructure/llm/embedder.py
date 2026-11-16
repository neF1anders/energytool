"""Порт Embedder поверх gateway: батчи, кэш по sha1(модель + текст)."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Sequence
from pathlib import Path

from energytool.infrastructure.llm.gateway import LlmGateway

logger = logging.getLogger(__name__)


class GatewayEmbedder:
    def __init__(
        self,
        gateway: LlmGateway,
        model: str,
        batch_size: int = 32,
        cache_dir: Path | None = None,
    ) -> None:
        self._gateway = gateway
        self._model = model
        self._batch_size = batch_size
        self._lock = threading.Lock()
        self._cache_file = cache_dir / f"{_slug(model)}.jsonl" if cache_dir else None
        self._cache: dict[str, list[float]] = self._read_disk_cache()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        keys = [self._key(text) for text in texts]
        with self._lock:
            missing = list(dict.fromkeys(k for k in keys if k not in self._cache))
        if missing:
            by_key = dict(zip(keys, texts, strict=True))
            for start in range(0, len(missing), self._batch_size):
                batch = missing[start : start + self._batch_size]
                vectors = self._gateway.embed([by_key[k] for k in batch])
                self._store(dict(zip(batch, vectors, strict=True)))
        logger.debug(
            "embed",
            extra={"data": {"texts": len(texts), "computed": len(missing)}},
        )
        with self._lock:
            return [self._cache[k] for k in keys]

    def _key(self, text: str) -> str:
        return hashlib.sha1(f"{self._model}\n{text}".encode()).hexdigest()

    def _store(self, computed: dict[str, list[float]]) -> None:
        with self._lock:
            self._cache.update(computed)
            if self._cache_file is not None:
                with self._cache_file.open("a", encoding="utf-8") as f:
                    for key, vector in computed.items():
                        f.write(json.dumps({"k": key, "v": vector}) + "\n")

    def _read_disk_cache(self) -> dict[str, list[float]]:
        if self._cache_file is None:
            return {}
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        if not self._cache_file.exists():
            return {}
        cache: dict[str, list[float]] = {}
        with self._cache_file.open(encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    cache[row["k"]] = row["v"]
                except (ValueError, KeyError):
                    logger.warning("embedding cache: skipped broken line")
        logger.info(
            "embedding cache loaded",
            extra={"data": {"file": str(self._cache_file), "vectors": len(cache)}},
        )
        return cache


def _slug(model: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in model)
