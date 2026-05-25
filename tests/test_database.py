"""
Tests for database module.

These tests require a running ParadeDB (PostgreSQL + pgvector + pg_bm25) instance.
Set DATABASE_URL or WAVERIDER_TEST_DSN to point at a test database.
Tests are skipped automatically when the database is unreachable.
"""

import os
import pytest

from waverider.database import DatabaseManager, tokenize_code_identifiers

# ---------------------------------------------------------------------------
# Test DSN resolution
# ---------------------------------------------------------------------------

_TEST_DSN = os.environ.get(
    "WAVERIDER_TEST_DSN",
    os.environ.get(
        "DATABASE_URL",
        "postgresql://waverider:changeme@localhost:5432/waverider",
    ),
)


def _db_available() -> bool:
    try:
        import psycopg
        with psycopg.connect(_TEST_DSN, connect_timeout=3):
            return True
    except Exception:
        return False


requires_db = pytest.mark.skipif(
    not _db_available(), reason="ParadeDB not reachable — set WAVERIDER_TEST_DSN"
)


@pytest.fixture(scope="function")
def db():
    """Provide a DatabaseManager connected to the test DB, reset between tests."""
    manager = DatabaseManager(dsn=_TEST_DSN)
    manager.init_schema()
    # Clean slate: remove all codebases (cascades to everything else)
    with manager._conn() as conn:
        conn.execute("DELETE FROM codebase_metadata")
    yield manager
    manager.close()


# ---------------------------------------------------------------------------
# tokenize_code_identifiers — pure Python, no DB needed
# ---------------------------------------------------------------------------


class TestTokenizeCodeIdentifiers:
    def test_snake_case(self):
        result = tokenize_code_identifiers("add_embedding")
        assert "add" in result
        assert "embedding" in result
        assert "add_embedding" in result

    def test_camel_case(self):
        result = tokenize_code_identifiers("DatabaseManager")
        assert "database" in result
        assert "manager" in result

    def test_pascal_case_multiple_caps(self):
        result = tokenize_code_identifiers("HTMLParser")
        assert "html" in result
        assert "parser" in result

    def test_dot_path(self):
        result = tokenize_code_identifiers("waverider.database")
        assert "waverider" in result
        assert "database" in result

    def test_combined(self):
        result = tokenize_code_identifiers("my_module.DatabaseManager")
        assert "my" in result
        assert "module" in result
        assert "database" in result
        assert "manager" in result

    def test_short_tokens_skipped(self):
        result = tokenize_code_identifiers("ab")
        assert result == "ab"

    def test_no_splitting_needed(self):
        result = tokenize_code_identifiers("search")
        assert result == "search"

    def test_preserves_original(self):
        text = "def add_embedding(self, vec):"
        result = tokenize_code_identifiers(text)
        assert text in result


# ---------------------------------------------------------------------------
# Schema / CRUD tests — require DB
# ---------------------------------------------------------------------------


@requires_db
def test_init_schema_is_idempotent(db):
    """Calling init_schema twice should not raise."""
    db.init_schema()


@requires_db
def test_add_codebase(db):
    codebase_id = db.add_codebase(
        name="test-project", path="/path/to/test", description="Test", language="python"
    )
    assert codebase_id > 0
    codebase = db.get_codebase("test-project")
    assert codebase is not None
    assert codebase["name"] == "test-project"


@requires_db
def test_list_codebases(db):
    db.add_codebase(name="project1", path="/path/1")
    db.add_codebase(name="project2", path="/path/2")
    codebases = db.list_codebases()
    names = [c["name"] for c in codebases]
    assert "project1" in names
    assert "project2" in names


@requires_db
def test_add_codebase_is_idempotent(db):
    first_id = db.add_codebase(name="project", path="/path/old", description="old")
    second_id = db.add_codebase(name="project", path="/path/new", description="new")
    assert first_id == second_id
    codebase = db.get_codebase("project")
    assert codebase["path"] == "/path/new"
    assert codebase["description"] == "new"


@requires_db
def test_add_source_file_is_idempotent(db):
    codebase_id = db.add_codebase(name="project", path="/path/project")
    first_id = db.add_source_file(
        codebase_id=codebase_id,
        file_path="/path/project/src/main.py",
        relative_path="src/main.py",
        content_hash="old-hash",
    )
    second_id = db.add_source_file(
        codebase_id=codebase_id,
        file_path="/path/project/src/main.py",
        relative_path="src/main.py",
        content_hash="new-hash",
    )
    assert first_id == second_id

    hashes = db.get_file_hashes(codebase_id)
    assert hashes["src/main.py"] == (first_id, "new-hash")


@requires_db
def test_reset_codebase_contents(db):
    cid1 = db.add_codebase(name="p1", path="/p1")
    cid2 = db.add_codebase(name="p2", path="/p2")

    def _add_snippet(cid, tag):
        fid = db.add_source_file(
            codebase_id=cid, file_path=f"/p/{tag}.py",
            relative_path=f"{tag}.py", content_hash=tag,
        )
        sid = db.add_code_snippet(
            file_id=fid, snippet_type="file", name=tag,
            content="pass", start_line=1, end_line=1, language="python",
        )
        db.add_embedding(snippet_id=sid, embedding=[0.1] * 768, model="mock")

    _add_snippet(cid1, "a")
    _add_snippet(cid2, "b")

    db.reset_codebase_contents(cid1)
    s1 = db.get_statistics(cid1)
    s2 = db.get_statistics(cid2)

    assert s1["total_files"] == 0
    assert s1["total_snippets"] == 0
    assert s1["total_embeddings"] == 0

    assert s2["total_files"] == 1
    assert s2["total_snippets"] == 1
    assert s2["total_embeddings"] == 1


@requires_db
def test_delete_file_contents(db):
    cid = db.add_codebase(name="project", path="/p")
    fid = db.add_source_file(
        codebase_id=cid, file_path="/p/a.py", relative_path="a.py", content_hash="h"
    )
    sid = db.add_code_snippet(
        file_id=fid, snippet_type="function", name="foo",
        content="def foo(): pass", start_line=1, end_line=1, language="python",
    )
    db.add_embedding(snippet_id=sid, embedding=[0.1] * 768, model="mock")

    db.delete_file_contents(fid)
    stats = db.get_statistics(cid)
    assert stats["total_files"] == 1      # file row kept
    assert stats["total_snippets"] == 0
    assert stats["total_embeddings"] == 0


@requires_db
def test_delete_source_file(db):
    cid = db.add_codebase(name="project", path="/p")
    fid = db.add_source_file(
        codebase_id=cid, file_path="/p/b.py", relative_path="b.py", content_hash="h"
    )
    sid = db.add_code_snippet(
        file_id=fid, snippet_type="function", name="bar",
        content="def bar(): pass", start_line=1, end_line=1, language="python",
    )
    db.add_embedding(snippet_id=sid, embedding=[0.5] * 768, model="mock")

    db.delete_source_file(fid)
    stats = db.get_statistics(cid)
    assert stats["total_files"] == 0
    assert stats["total_snippets"] == 0
    assert stats["total_embeddings"] == 0


@requires_db
def test_get_file_hashes(db):
    cid = db.add_codebase(name="project", path="/p")
    fid1 = db.add_source_file(
        codebase_id=cid, file_path="/p/a.py", relative_path="a.py", content_hash="hash-a"
    )
    fid2 = db.add_source_file(
        codebase_id=cid, file_path="/p/b.py", relative_path="b.py", content_hash="hash-b"
    )
    hashes = db.get_file_hashes(cid)
    assert hashes == {"a.py": (fid1, "hash-a"), "b.py": (fid2, "hash-b")}


# ---------------------------------------------------------------------------
# BM25 search tests — require DB with pg_bm25
# ---------------------------------------------------------------------------


@requires_db
class TestBM25Search:
    @pytest.fixture
    def db_with_snippets(self, db):
        cid = db.add_codebase(name="proj", path="/proj")
        fid = db.add_source_file(
            codebase_id=cid, file_path="/proj/db.py",
            relative_path="src/database_manager.py", content_hash="abc",
        )
        db.add_code_snippet(
            file_id=fid, snippet_type="class", name="DatabaseManager",
            content="class DatabaseManager:\n    pass", language="python",
        )
        db.add_code_snippet(
            file_id=fid, snippet_type="function", name="add_embedding",
            content="def add_embedding(self, vec):\n    self.db.insert(vec)",
            language="python",
        )
        return db, cid

    def test_exact_name_match(self, db_with_snippets):
        db, cid = db_with_snippets
        results = db.search_bm25("DatabaseManager", cid)
        assert len(results) > 0
        assert any(r["name"] == "DatabaseManager" for r in results)

    @pytest.mark.xfail(reason="camelCase sub-token splitting requires pg_bm25 code tokenizer; tsvector fallback does not split identifiers")
    def test_sub_token_match(self, db_with_snippets):
        """The 'code' tokenizer splits 'DatabaseManager' so 'database' matches."""
        db, cid = db_with_snippets
        results = db.search_bm25("database", cid)
        assert len(results) > 0
        assert any(r["name"] == "DatabaseManager" for r in results)

    def test_snake_case_partial(self, db_with_snippets):
        """'embedding' should find 'add_embedding' via code tokenizer."""
        db, cid = db_with_snippets
        results = db.search_bm25("embedding", cid)
        assert len(results) > 0
        assert any(r["name"] == "add_embedding" for r in results)

    def test_bm25_score_present(self, db_with_snippets):
        db, cid = db_with_snippets
        results = db.search_bm25("embedding", cid)
        for r in results:
            assert "bm25_score" in r
            assert isinstance(r["bm25_score"], float)

    def test_no_results(self, db_with_snippets):
        db, cid = db_with_snippets
        results = db.search_bm25("nonexistent_xyzzy_zzz", cid)
        assert results == []


# ---------------------------------------------------------------------------
# Vector search tests — require DB with pgvector
# ---------------------------------------------------------------------------


@requires_db
def test_vector_search(db):
    """pgvector HNSW index returns correct nearest neighbour."""
    cid = db.add_codebase(name="project", path="/p")
    fid = db.add_source_file(
        codebase_id=cid, file_path="/p/main.py", relative_path="main.py", content_hash="h"
    )

    vec_a = [1.0] + [0.0] * 767
    vec_b = [0.0, 1.0] + [0.0] * 766

    s1 = db.add_code_snippet(
        file_id=fid, snippet_type="function", name="alpha",
        content="def alpha(): ...", start_line=1, end_line=1, language="python",
    )
    db.add_embedding(snippet_id=s1, embedding=vec_a, model="mock")

    s2 = db.add_code_snippet(
        file_id=fid, snippet_type="function", name="beta",
        content="def beta(): ...", start_line=2, end_line=2, language="python",
    )
    db.add_embedding(snippet_id=s2, embedding=vec_b, model="mock")

    results = db.search_embeddings(query_embedding=vec_a, codebase_id=cid, limit=1)
    assert len(results) == 1
    assert results[0]["name"] == "alpha"



