"""Tests for RRF fusion logic."""

import pytest

from waverider.fusion import rrf_fuse


def _make_results(ids):
    """Helper: create a list of result dicts from a sequence of IDs."""
    return [{"id": i, "name": f"item_{i}", "content": f"code for {i}"} for i in ids]


class TestRRFFuse:
    def test_single_source(self):
        """Single ranked list should preserve order."""
        results = _make_results([10, 20, 30])
        fused = rrf_fuse({"bm25": results}, limit=3)
        assert [r["id"] for r in fused] == [10, 20, 30]

    def test_two_sources_same_ranking(self):
        """Two sources with identical rankings should preserve that order."""
        a = _make_results([1, 2, 3])
        b = _make_results([1, 2, 3])
        fused = rrf_fuse({"bm25": a, "vector": b}, limit=3)
        assert [r["id"] for r in fused] == [1, 2, 3]

    def test_overlapping_documents_boosted(self):
        """A document appearing in both lists should rank higher than single-list docs."""
        # bm25 ranks: [A, B, C]
        # vector ranks: [C, D, A]
        # A appears in both at rank 1 and rank 3 -> should be boosted
        bm25 = _make_results([1, 2, 3])
        vec = _make_results([3, 4, 1])
        fused = rrf_fuse({"bm25": bm25, "vector": vec}, limit=5)
        ids = [r["id"] for r in fused]
        # Both 1 and 3 appear in both lists; 1 has rank 1+3, 3 has rank 3+1 -> tied
        # 2 and 4 each appear in only one list
        assert 1 in ids[:2]
        assert 3 in ids[:2]

    def test_rrf_score_formula(self):
        """Verify RRF score calculation: score = sum(weight / (k + rank))."""
        results = _make_results([42])
        fused = rrf_fuse({"src": results}, k=60, limit=1)
        # rank is 1-based, so score = 1.0 / (60 + 1) = 1/61
        expected = round(1.0 / 61, 6)
        assert fused[0]["rrf_score"] == expected

    def test_weighted_sources(self):
        """Weights should scale contributions proportionally."""
        a = _make_results([1, 2])
        b = _make_results([2, 1])
        # With bm25 weight=2.0, vector weight=1.0:
        # doc 1: bm25 rank 1 * 2.0 + vector rank 2 * 1.0
        # doc 2: bm25 rank 2 * 2.0 + vector rank 1 * 1.0
        fused = rrf_fuse(
            {"bm25": a, "vector": b},
            weights={"bm25": 2.0, "vector": 1.0},
            limit=2,
        )
        # doc 1: 2.0/61 + 1.0/62 ≈ 0.04893
        # doc 2: 2.0/62 + 1.0/61 ≈ 0.04865
        assert fused[0]["id"] == 1
        assert fused[1]["id"] == 2

    def test_limit_respected(self):
        """Should return at most `limit` results."""
        results = _make_results(range(20))
        fused = rrf_fuse({"src": results}, limit=5)
        assert len(fused) == 5

    def test_empty_inputs(self):
        """Empty ranked lists should return empty results."""
        fused = rrf_fuse({})
        assert fused == []
        fused = rrf_fuse({"src": []})
        assert fused == []

    def test_rrf_score_field_present(self):
        """Every result should have an rrf_score field."""
        fused = rrf_fuse({"src": _make_results([1, 2])}, limit=2)
        for r in fused:
            assert "rrf_score" in r
            assert isinstance(r["rrf_score"], float)
            assert r["rrf_score"] > 0

    def test_disjoint_lists(self):
        """Disjoint lists should interleave by position."""
        a = _make_results([1, 2, 3])
        b = _make_results([4, 5, 6])
        fused = rrf_fuse({"a": a, "b": b}, limit=6)
        # First item from each list should appear before second items
        ids = [r["id"] for r in fused]
        assert ids.index(1) < ids.index(2)  # a's ordering preserved
        assert ids.index(4) < ids.index(5)  # b's ordering preserved
