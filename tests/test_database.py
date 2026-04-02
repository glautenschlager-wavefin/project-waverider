"""
Tests for database module.
"""

import pytest
import tempfile
from pathlib import Path

from waverider.database import DatabaseManager


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = DatabaseManager(db_path=str(db_path))
        yield db


def test_init_schema(temp_db):
    """Test database schema initialization."""
    temp_db.init_schema()
    conn = temp_db.connect()
    cursor = conn.cursor()

    # Check that tables exist
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='codebase_metadata'"
    )
    assert cursor.fetchone() is not None

    conn.close()


def test_add_codebase(temp_db):
    """Test adding a codebase."""
    temp_db.init_schema()

    codebase_id = temp_db.add_codebase(
        name="test-project",
        path="/path/to/test",
        description="Test codebase",
        language="python",
    )

    assert codebase_id > 0

    codebase = temp_db.get_codebase("test-project")
    assert codebase is not None
    assert codebase["name"] == "test-project"


def test_list_codebases(temp_db):
    """Test listing codebases."""
    temp_db.init_schema()

    temp_db.add_codebase(name="project1", path="/path/1")
    temp_db.add_codebase(name="project2", path="/path/2")

    codebases = temp_db.list_codebases()
    assert len(codebases) == 2


def test_add_codebase_is_idempotent(temp_db):
    """Repeated add_codebase calls for the same name should update and reuse the same ID."""
    temp_db.init_schema()

    first_id = temp_db.add_codebase(name="project", path="/path/old", description="old")
    second_id = temp_db.add_codebase(name="project", path="/path/new", description="new")

    assert first_id == second_id

    codebase = temp_db.get_codebase("project")
    assert codebase is not None
    assert codebase["path"] == "/path/new"
    assert codebase["description"] == "new"


def test_add_source_file_is_idempotent(temp_db):
    """Repeated add_source_file calls for the same codebase/file should update and reuse the same ID."""
    temp_db.init_schema()

    codebase_id = temp_db.add_codebase(name="project", path="/path/project")

    first_id = temp_db.add_source_file(
        codebase_id=codebase_id,
        file_path="/path/project/src/main.py",
        relative_path="src/main.py",
        content_hash="old-hash",
    )
    second_id = temp_db.add_source_file(
        codebase_id=codebase_id,
        file_path="/path/project/src/main.py",
        relative_path="src/main.py",
        content_hash="new-hash",
    )

    assert first_id == second_id


# ---------------------------------------------------------------------------
# tokenize_code_identifiers tests
# ---------------------------------------------------------------------------

from waverider.database import tokenize_code_identifiers


class TestTokenizeCodeIdentifiers:
    def test_snake_case(self):
        result = tokenize_code_identifiers("add_embedding")
        assert "add" in result
        assert "embedding" in result
        # Original preserved
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
        """Identifiers shorter than 3 chars should not be split."""
        result = tokenize_code_identifiers("ab")
        # No extra tokens appended — just the original
        assert result == "ab"

    def test_no_splitting_needed(self):
        """A single lowercase word should not produce extra tokens."""
        result = tokenize_code_identifiers("search")
        assert result == "search"

    def test_preserves_original(self):
        text = "def add_embedding(self, vec):"
        result = tokenize_code_identifiers(text)
        assert text in result


# ---------------------------------------------------------------------------
# FTS5 / BM25 search tests
# ---------------------------------------------------------------------------

class TestFTS5Search:
    @pytest.fixture
    def db_with_snippets(self, tmp_path):
        """Set up a DB with FTS5 index and some snippets."""
        db = DatabaseManager(db_path=str(tmp_path / "test.db"))
        db.init_schema()
        cid = db.add_codebase(name="proj", path="/proj")
        fid = db.add_source_file(
            codebase_id=cid,
            file_path=str(tmp_path / "a.py"),
            relative_path="src/database_manager.py",
            content_hash="abc",
        )
        # Create the source file so add_source_file doesn't fail
        (tmp_path / "a.py").write_text("")

        s1 = db.add_code_snippet(
            file_id=fid,
            snippet_type="class",
            name="DatabaseManager",
            content="class DatabaseManager:\n    pass",
            language="python",
        )
        db.add_to_fts(s1, "DatabaseManager", "class DatabaseManager:\n    pass", "src/database_manager.py")

        s2 = db.add_code_snippet(
            file_id=fid,
            snippet_type="function",
            name="add_embedding",
            content="def add_embedding(self, vec):\n    self.db.insert(vec)",
            language="python",
        )
        db.add_to_fts(s2, "add_embedding", "def add_embedding(self, vec):\n    self.db.insert(vec)", "src/database_manager.py")

        return db, cid

    def test_exact_name_match(self, db_with_snippets):
        db, cid = db_with_snippets
        results = db.search_bm25("DatabaseManager", cid)
        assert len(results) > 0
        assert any(r["name"] == "DatabaseManager" for r in results)

    def test_sub_token_match(self, db_with_snippets):
        """Searching 'database' should find 'DatabaseManager' via sub-token expansion."""
        db, cid = db_with_snippets
        results = db.search_bm25("database", cid)
        assert len(results) > 0
        names = [r["name"] for r in results]
        assert "DatabaseManager" in names

    def test_snake_case_partial(self, db_with_snippets):
        """Searching 'embedding' should find 'add_embedding'."""
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
        results = db.search_bm25("nonexistent_xyzzy", cid)
        assert results == []

    def test_rebuild_fts_index(self, db_with_snippets):
        db, cid = db_with_snippets
        count = db.rebuild_fts_index(cid)
        assert count == 2  # two snippets
        # Search should still work after rebuild
        results = db.search_bm25("DatabaseManager", cid)
        assert len(results) > 0


def test_add_source_file_verifies_hash(temp_db):
    """Verify add_source_file actually updates the content_hash."""
    temp_db.init_schema()
    codebase_id = temp_db.add_codebase(name="project", path="/path/project")

    first_id = temp_db.add_source_file(
        codebase_id=codebase_id,
        file_path="/path/project/src/main.py",
        relative_path="src/main.py",
        content_hash="old-hash",
    )
    temp_db.add_source_file(
        codebase_id=codebase_id,
        file_path="/path/project/src/main.py",
        relative_path="src/main.py",
        content_hash="new-hash",
    )

    conn = temp_db.connect()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT content_hash FROM source_files WHERE id = ?",
        (first_id,),
    )
    row = cursor.fetchone()
    conn.close()

    assert row is not None
    assert row["content_hash"] == "new-hash"


def test_reset_codebase_contents(temp_db):
    """reset_codebase_contents should remove files/snippets/embeddings for one codebase only."""
    temp_db.init_schema()

    codebase1 = temp_db.add_codebase(name="project1", path="/path/project1")
    codebase2 = temp_db.add_codebase(name="project2", path="/path/project2")

    file1 = temp_db.add_source_file(
        codebase_id=codebase1,
        file_path="/path/project1/main.py",
        relative_path="main.py",
        content_hash="hash1",
    )
    snippet1 = temp_db.add_code_snippet(
        file_id=file1,
        snippet_type="file",
        name="main",
        content="print('p1')",
        start_line=1,
        end_line=1,
        language="python",
    )
    temp_db.add_embedding(snippet_id=snippet1, embedding=[0.1, 0.2], model="mock")

    file2 = temp_db.add_source_file(
        codebase_id=codebase2,
        file_path="/path/project2/main.py",
        relative_path="main.py",
        content_hash="hash2",
    )
    snippet2 = temp_db.add_code_snippet(
        file_id=file2,
        snippet_type="file",
        name="main",
        content="print('p2')",
        start_line=1,
        end_line=1,
        language="python",
    )
    temp_db.add_embedding(snippet_id=snippet2, embedding=[0.3, 0.4], model="mock")

    temp_db.reset_codebase_contents(codebase1)

    stats1 = temp_db.get_statistics(codebase1)
    stats2 = temp_db.get_statistics(codebase2)

    assert stats1["total_files"] == 0
    assert stats1["total_snippets"] == 0
    assert stats1["total_embeddings"] == 0

    assert stats2["total_files"] == 1
    assert stats2["total_snippets"] == 1
    assert stats2["total_embeddings"] == 1


def test_delete_file_contents(temp_db):
    """delete_file_contents should remove snippets/embeddings but keep the source_file row."""
    temp_db.init_schema()

    codebase_id = temp_db.add_codebase(name="project", path="/path/project")
    file_id = temp_db.add_source_file(
        codebase_id=codebase_id,
        file_path="/path/project/a.py",
        relative_path="a.py",
        content_hash="aaa",
    )
    snippet_id = temp_db.add_code_snippet(
        file_id=file_id,
        snippet_type="function",
        name="foo",
        content="def foo(): pass",
        start_line=1,
        end_line=1,
        language="python",
    )
    temp_db.add_embedding(snippet_id=snippet_id, embedding=[0.1, 0.2], model="mock")

    temp_db.delete_file_contents(file_id)

    stats = temp_db.get_statistics(codebase_id)
    assert stats["total_files"] == 1  # source_file row still present
    assert stats["total_snippets"] == 0
    assert stats["total_embeddings"] == 0


def test_delete_source_file(temp_db):
    """delete_source_file should remove the file row and all its children."""
    temp_db.init_schema()

    codebase_id = temp_db.add_codebase(name="project", path="/path/project")
    file_id = temp_db.add_source_file(
        codebase_id=codebase_id,
        file_path="/path/project/b.py",
        relative_path="b.py",
        content_hash="bbb",
    )
    snippet_id = temp_db.add_code_snippet(
        file_id=file_id,
        snippet_type="function",
        name="bar",
        content="def bar(): pass",
        start_line=1,
        end_line=1,
        language="python",
    )
    temp_db.add_embedding(snippet_id=snippet_id, embedding=[0.5, 0.6], model="mock")

    temp_db.delete_source_file(file_id)

    stats = temp_db.get_statistics(codebase_id)
    assert stats["total_files"] == 0
    assert stats["total_snippets"] == 0
    assert stats["total_embeddings"] == 0


def test_get_file_hashes(temp_db):
    """get_file_hashes should return a mapping of relative_path -> (file_id, hash)."""
    temp_db.init_schema()

    codebase_id = temp_db.add_codebase(name="project", path="/path/project")
    fid1 = temp_db.add_source_file(
        codebase_id=codebase_id,
        file_path="/path/project/a.py",
        relative_path="a.py",
        content_hash="hash-a",
    )
    fid2 = temp_db.add_source_file(
        codebase_id=codebase_id,
        file_path="/path/project/b.py",
        relative_path="b.py",
        content_hash="hash-b",
    )

    hashes = temp_db.get_file_hashes(codebase_id)
    assert hashes == {
        "a.py": (fid1, "hash-a"),
        "b.py": (fid2, "hash-b"),
    }


def test_vector_index_build_and_search(temp_db):
    """Build a precomputed vector index and verify search returns correct snippets."""
    temp_db.init_schema()

    codebase_id = temp_db.add_codebase(name="project", path="/path/project")
    file_id = temp_db.add_source_file(
        codebase_id=codebase_id,
        file_path="/path/project/main.py",
        relative_path="main.py",
        content_hash="h",
    )

    # Add two snippets with simple known embeddings
    s1 = temp_db.add_code_snippet(
        file_id=file_id, snippet_type="function", name="alpha",
        content="def alpha(): ...", start_line=1, end_line=1, language="python",
    )
    temp_db.add_embedding(snippet_id=s1, embedding=[1.0, 0.0, 0.0], model="mock")

    s2 = temp_db.add_code_snippet(
        file_id=file_id, snippet_type="function", name="beta",
        content="def beta(): ...", start_line=2, end_line=2, language="python",
    )
    temp_db.add_embedding(snippet_id=s2, embedding=[0.0, 1.0, 0.0], model="mock")

    n = temp_db.build_vector_index(codebase_id)
    assert n == 2

    # Search for a vector close to alpha
    results = temp_db.search_embeddings(
        query_embedding=[1.0, 0.0, 0.0],
        codebase_id=codebase_id,
        limit=1,
    )
    assert len(results) == 1
    assert results[0]["name"] == "alpha"
