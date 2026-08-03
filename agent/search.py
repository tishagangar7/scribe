"""DA-207/208/209 -- hybrid BM25 + dense retrieval over chunks, fused via RRF.

BM25 (lexical) and dense embeddings (semantic) catch different things: BM25
wins on exact identifier/error-message matches, dense embeddings win when
the issue text and the relevant code don't share vocabulary. Reciprocal
rank fusion combines their rankings without needing to calibrate BM25
scores (unbounded) against cosine similarities (bounded [-1, 1]) onto a
common scale -- it only looks at rank position in each list.

BM25-only and dense-only paths stay independently callable (`BM25Index`,
`DenseIndex`, and `retrieve(..., strategy=...)`) because Sprint 4's
ablations need to measure each retrieval strategy on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from agent.retrieval import Chunk, chunk_repo

_MODEL_NAME = "all-MiniLM-L6-v2"
Strategy = Literal["hybrid", "bm25", "dense"]


def _tokenize(text: str) -> list[str]:
    return text.lower().split()


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk
    score: float


def _chunk_key(chunk: Chunk) -> tuple[str, int, int]:
    return (chunk.file_path, chunk.start_line, chunk.end_line)


class BM25Index:
    """Sparse lexical retrieval over chunk text."""

    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks
        self._bm25 = BM25Okapi([_tokenize(c.text) for c in chunks]) if chunks else None

    def search(self, query: str, k: int) -> list[ScoredChunk]:
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(range(len(self._chunks)), key=lambda i: -scores[i])
        return [ScoredChunk(self._chunks[i], float(scores[i])) for i in ranked[:k]]


class DenseIndex:
    """Dense embedding retrieval via brute-force cosine similarity.

    The model is loaded once per process and shared across instances --
    it's a fixed local model (~80MB), not a call to any billed API, but
    loading it repeatedly would still be needlessly slow.
    """

    _shared_model: SentenceTransformer | None = None

    def __init__(
        self, chunks: list[Chunk], model: SentenceTransformer | None = None
    ) -> None:
        self._chunks = chunks
        self._model = model or self._get_model()
        if chunks:
            vectors = self._model.encode(
                [c.text for c in chunks], normalize_embeddings=True
            )
            self._embeddings = np.asarray(vectors)
        else:
            self._embeddings = np.zeros((0, 0))

    @classmethod
    def _get_model(cls) -> SentenceTransformer:
        if cls._shared_model is None:
            cls._shared_model = SentenceTransformer(_MODEL_NAME)
        return cls._shared_model

    def search(self, query: str, k: int) -> list[ScoredChunk]:
        if not self._chunks:
            return []
        query_vec = np.asarray(self._model.encode([query], normalize_embeddings=True))[
            0
        ]
        sims = self._embeddings @ query_vec
        ranked = np.argsort(-sims)[:k]
        return [ScoredChunk(self._chunks[i], float(sims[i])) for i in ranked]


def reciprocal_rank_fusion(
    result_lists: list[list[ScoredChunk]], *, k: int = 60
) -> list[ScoredChunk]:
    """Fuse ranked lists by rank position: score = sum(1 / (k + rank + 1)).

    Standard RRF (k=60 by convention). Deliberately ignores each list's raw
    scores -- BM25's unbounded scores and cosine similarity's [-1, 1] range
    aren't comparable, but rank position always is.
    """
    scores: dict[tuple[str, int, int], float] = {}
    chunk_by_key: dict[tuple[str, int, int], Chunk] = {}
    for results in result_lists:
        for rank, scored in enumerate(results):
            key = _chunk_key(scored.chunk)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            chunk_by_key[key] = scored.chunk

    ranked_keys = sorted(scores, key=lambda key: -scores[key])
    return [ScoredChunk(chunk_by_key[key], scores[key]) for key in ranked_keys]


def retrieve(
    issue: str,
    repo: Path,
    k: int = 10,
    *,
    strategy: Strategy = "hybrid",
) -> list[Chunk]:
    """Retrieve the top-`k` chunks from `repo` relevant to `issue`.

    `strategy` selects "hybrid" (BM25 + dense, fused via RRF), "bm25", or
    "dense" alone -- for one-off queries. Building `BM25Index`/`DenseIndex`
    directly and reusing them across many queries against the same repo
    avoids re-chunking and re-embedding on every call.
    """
    chunks = chunk_repo(repo)
    fan_out = max(k, 20)

    if strategy == "bm25":
        return [sc.chunk for sc in BM25Index(chunks).search(issue, k)]
    if strategy == "dense":
        return [sc.chunk for sc in DenseIndex(chunks).search(issue, k)]

    bm25_results = BM25Index(chunks).search(issue, fan_out)
    dense_results = DenseIndex(chunks).search(issue, fan_out)
    fused = reciprocal_rank_fusion([bm25_results, dense_results])
    return [sc.chunk for sc in fused[:k]]
