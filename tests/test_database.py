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
