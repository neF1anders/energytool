"""LexicalIndex на bm25s."""

from __future__ import annotations

import re
from collections.abc import Sequence

import bm25s
import numpy as np

from energytool.domain.knowledge import Chunk

STEM_LENGTH = 6
_WORD = re.compile(r"[0-9a-zа-я]+")
_STOPWORDS = frozenset(
    [
        "а",
        "без",
        "бы",
        "был",
        "в",
        "вам",
        "вас",
        "ваш",
        "ваша",
        "ваши",
        "во",
        "вот",
        "вы",
        "да",
        "для",
        "до",
        "если",
        "есть",
        "ещё",
        "еще",
        "же",
        "за",
        "и",
        "из",
        "или",
        "им",
        "их",
        "как",
        "ко",
        "когда",
        "кто",
        "ли",
        "либо",
        "мне",
        "мы",
        "на",
        "над",
        "не",
        "нет",
        "ни",
        "но",
        "нас",
        "наш",
        "наша",
        "наши",
        "о",
        "об",
        "он",
        "она",
        "они",
        "от",
        "по",
        "под",
        "при",
        "про",
        "с",
        "со",
        "так",
        "также",
        "то",
        "тоже",
        "только",
        "у",
        "уже",
        "чем",
        "что",
        "чтобы",
        "это",
        "эти",
        "этот",
        "я",
        "можно",
        "нужно",
        "ли",
    ]
)


def tokenize(text: str) -> list[str]:
    words = _WORD.findall(text.lower().replace("ё", "е"))
    return [w[:STEM_LENGTH] for w in words if w not in _STOPWORDS]


class Bm25Index:
    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self._ids = [chunk.id for chunk in chunks]
        self._position = {chunk_id: i for i, chunk_id in enumerate(self._ids)}
        self._retriever = bm25s.BM25()
        self._retriever.index([tokenize(chunk.text) for chunk in chunks], show_progress=False)

    def search(
        self, text: str, k: int, allowed: frozenset[str] | None = None
    ) -> list[tuple[str, float]]:
        known = self._retriever.get_tokens_ids(tokenize(text))
        if not known:
            return []
        scores = np.asarray(self._retriever.get_scores(known), dtype=float)
        if allowed is not None:
            mask = np.full(len(self._ids), -np.inf)
            positions = [self._position[i] for i in allowed if i in self._position]
            mask[positions] = 0.0
            scores = scores + mask
        order = np.argsort(-scores)[:k]
        return [(self._ids[i], float(scores[i])) for i in order if scores[i] > 0]
