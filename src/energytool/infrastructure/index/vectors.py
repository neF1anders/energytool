"""VectorIndex: косинусная близость в numpy по нормированной матрице (~235 × 1024)."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt


def normalize(matrix: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    norms = np.linalg.norm(matrix, axis=-1, keepdims=True)
    return np.asarray(matrix / np.where(norms == 0, 1.0, norms), dtype=np.float32)


class NumpyVectorIndex:
    def __init__(self, ids: Sequence[str], vectors: Sequence[Sequence[float]]) -> None:
        if len(ids) != len(vectors):
            raise ValueError("ids and vectors must have the same length")
        self._ids = list(ids)
        self._position = {chunk_id: i for i, chunk_id in enumerate(self._ids)}
        self._matrix = normalize(np.asarray(vectors, dtype=np.float32))

    def search(
        self, vector: Sequence[float], k: int, allowed: frozenset[str] | None = None
    ) -> list[tuple[str, float]]:
        if not self._ids:
            return []
        query = normalize(np.asarray(vector, dtype=np.float32))
        scores = self._matrix @ query
        if allowed is not None:
            positions = np.fromiter(
                (self._position[i] for i in allowed if i in self._position), dtype=np.int64
            )
            candidates = positions[np.argsort(-scores[positions])][:k]
        else:
            candidates = np.argsort(-scores)[:k]
        return [(self._ids[i], float(scores[i])) for i in candidates]
