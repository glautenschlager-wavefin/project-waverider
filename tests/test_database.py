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
