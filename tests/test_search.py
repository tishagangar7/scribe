"""DA-207/208/209 -- BM25, dense, and RRF-fused retrieval."""

from __future__ import annotations

from agent.retrieval import Chunk
from agent.search import (
    BM25Index,
    DenseIndex,
    ScoredChunk,
    reciprocal_rank_fusion,
    retrieve,
)


def _chunk(name: str, text: str, file_path: str = "m.py", line: int = 1) -> Chunk:
    # Chunk identity for RRF is (file_path, start_line, end_line), same as
    # for real chunks -- so synthetic chunks in one test must use distinct
    # lines, or they collide and look like the same chunk.
    return Chunk(
        file_path=file_path,
        name=name,
        kind="function",
        start_line=line,
        end_line=line,
        text=text,
    )


CHUNKS = [
    _chunk(
        "parse_config", "def parse_config(path): read yaml file and return dict", line=1
    ),
    _chunk(
        "connect_db", "def connect_db(url): open a postgres connection pool", line=2
    ),
    _chunk(
        "retry_request",
        "def retry_request(fn): retry an http request on failure",
        line=3,
    ),
]


def test_bm25_ranks_exact_lexical_match_highest():
    index = BM25Index(CHUNKS)
    results = index.search("postgres connection pool", k=3)

    assert results[0].chunk.name == "connect_db"
    assert results[0].score > 0


def test_bm25_on_empty_chunk_list_returns_empty():
    assert BM25Index([]).search("anything", k=5) == []


def test_bm25_search_is_capped_at_k():
    index = BM25Index(CHUNKS)
    assert len(index.search("request", k=1)) == 1


def test_dense_index_finds_semantic_match_without_shared_words():
    """The defining property of dense retrieval: no lexical overlap at all."""
    chunks = [
        _chunk("compute_tax", "figure out how much money the government takes", line=1),
        _chunk("render_button", "draw a clickable rectangle on the screen", line=2),
    ]
    index = DenseIndex(chunks)
    results = index.search("calculate income tax owed", k=2)

    assert results[0].chunk.name == "compute_tax"


def test_reciprocal_rank_fusion_rewards_agreement_between_lists():
    a = _chunk("a", "a", line=1)
    b = _chunk("b", "b", line=2)
    c = _chunk("c", "c", line=3)

    # "a" ranks 1st in both lists; "b" and "c" only appear in one each.
    list_1 = [ScoredChunk(a, 9.0), ScoredChunk(b, 5.0)]
    list_2 = [ScoredChunk(a, 0.9), ScoredChunk(c, 0.1)]

    fused = reciprocal_rank_fusion([list_1, list_2])

    assert fused[0].chunk.name == "a"
    assert {sc.chunk.name for sc in fused} == {"a", "b", "c"}


def test_reciprocal_rank_fusion_score_matches_manual_formula():
    a = _chunk("a", "a", line=1)
    b = _chunk("b", "b", line=2)
    list_1 = [ScoredChunk(a, 1.0), ScoredChunk(b, 1.0)]

    fused = reciprocal_rank_fusion([list_1], k=60)

    assert fused[0].score == 1.0 / 61
    assert fused[1].score == 1.0 / 62


def test_reciprocal_rank_fusion_of_empty_lists_is_empty():
    assert reciprocal_rank_fusion([[], []]) == []


def test_retrieve_hybrid_end_to_end(tmp_path):
    (tmp_path / "db.py").write_text(
        "def connect_db(url):\n    'open a postgres connection pool'\n    pass\n"
    )
    (tmp_path / "http.py").write_text(
        "def retry_request(fn):\n    'retry an http request on failure'\n    pass\n"
    )

    results = retrieve("postgres connection pool", tmp_path, k=1)

    assert len(results) == 1
    assert results[0].name == "connect_db"


def test_retrieve_bm25_only_and_dense_only_are_independently_selectable(tmp_path):
    (tmp_path / "db.py").write_text("def connect_db(url):\n    pass\n")
    (tmp_path / "http.py").write_text("def retry_request(fn):\n    pass\n")

    bm25_only = retrieve("connect_db", tmp_path, k=2, strategy="bm25")
    dense_only = retrieve("connect_db", tmp_path, k=2, strategy="dense")
    hybrid = retrieve("connect_db", tmp_path, k=2, strategy="hybrid")

    assert {c.name for c in bm25_only} == {"connect_db", "retry_request"}
    assert {c.name for c in dense_only} == {"connect_db", "retry_request"}
    assert {c.name for c in hybrid} == {"connect_db", "retry_request"}
