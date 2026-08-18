"""Embedding providers. HashingEmbedder is deterministic and dependency-free
(tests, offline dev); FastEmbedEmbedder is the production model (ONNX, local)."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


_TOKEN_RE = re.compile(r"\w+")


class HashingEmbedder:
    """Deterministic bag-of-trigrams hashing embedder. Overlapping character
    trigrams of word tokens hash into a fixed-dim unit vector — crude but
    stable, similar texts land near each other. Test/dev use only."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in _TOKEN_RE.findall(text.lower()):
            padded = f"^{token}$"
            grams = [padded[i:i + 3] for i in range(len(padded) - 2)] or [padded]
            for gram in grams:
                h = int.from_bytes(hashlib.md5(gram.encode()).digest()[:4], "big")
                vec[h % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class FastEmbedEmbedder:
    """Local ONNX embeddings via fastembed (BAAI/bge-small-en-v1.5)."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        from fastembed import TextEmbedding  # lazy: optional dependency

        self._model = TextEmbedding(model_name)
        self.dim = 384

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]
