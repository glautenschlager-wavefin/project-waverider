# tests/test_mcp_admin.py
"""
Tests for the four admin MCP tools: register_codebase, list_codebases,
set_codebase_enabled, deregister_codebase.

These tests call the tool functions directly (not via MCP transport).
Requires a live DB; skipped otherwise.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.database import DatabaseManager

# ---------------------------------------------------------------------------
# DB availability check (reuse pattern from test_database.py)
# ---------------------------------------------------------------------------

_TEST_DSN = os.environ.get(
    "WAVERIDER_TEST_DSN",
    os.environ.get("DATABASE_URL", "postgresql://waverider:changeme@localhost:5432/waverider"),
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


@pytest.fixture(autouse=True)
def clean_db():
    """Wipe codebase_metadata before each test so tests don't interfere."""
    if not _db_available():
        yield
        return
    db = DatabaseManager(dsn=_TEST_DSN)
    db.init_schema()
    with db._conn() as conn:
        conn.execute("DELETE FROM codebase_metadata")
    yield
    db.close()


# ---------------------------------------------------------------------------
# Import tool functions from mcp_server
# ---------------------------------------------------------------------------

import waverider.mcp_server as _srv


# ---------------------------------------------------------------------------
# register_codebase
# ---------------------------------------------------------------------------

@requires_db
class TestRegisterCodebase:
    def test_registers_new_codebase(self, tmp_path):
        result = _srv.register_codebase(
            name="my-repo",
            path=str(tmp_path),
            description="Test",
            language="python",
            github_repo="waveapps/my-repo",
            main_branch_name="main",
        )
        assert "my-repo" in result
        assert "Error" not in result

        db = DatabaseManager(dsn=_TEST_DSN)
        db.init_schema()
        row = db.get_codebase("my-repo")
        db.close()
        assert row is not None
        assert row["github_repo"] == "waveapps/my-repo"
        assert row["enabled"] is True
        assert row["last_indexed_commit"] is None

    def test_returns_error_for_missing_path(self):
        result = _srv.register_codebase(
            name="bad-repo",
            path="/nonexistent/path/that/does/not/exist",
        )
        assert "Error" in result
        assert "path" in result.lower()

    def test_idempotent_registration(self, tmp_path):
        _srv.register_codebase(name="my-repo", path=str(tmp_path))
        result = _srv.register_codebase(name="my-repo", path=str(tmp_path), description="updated")
        assert "Error" not in result


# ---------------------------------------------------------------------------
# list_codebases
# ---------------------------------------------------------------------------

@requires_db
class TestListCodebases:
    def test_shows_all_rows(self, tmp_path):
        db = DatabaseManager(dsn=_TEST_DSN)
        db.init_schema()
        db.upsert_codebase_registration("alpha", str(tmp_path), "A", "python", None, "main")
        db.upsert_codebase_registration("beta", str(tmp_path), "B", "typescript", None, "main")
        db.close()

        result = _srv.list_codebases()
        assert "alpha" in result
        assert "beta" in result

    def test_shows_empty_message_when_no_rows(self):
        result = _srv.list_codebases()
        assert "No codebases" in result


# ---------------------------------------------------------------------------
# set_codebase_enabled
# ---------------------------------------------------------------------------

@requires_db
class TestSetCodebaseEnabled:
    def test_disables_existing_codebase(self, tmp_path):
        db = DatabaseManager(dsn=_TEST_DSN)
        db.init_schema()
        db.upsert_codebase_registration("my-repo", str(tmp_path), "", "python", None, "main")
        db.close()

        result = _srv.set_codebase_enabled("my-repo", False)
        assert "disabled" in result

        db2 = DatabaseManager(dsn=_TEST_DSN)
        db2.init_schema()
        row = db2.get_codebase("my-repo")
        db2.close()
        assert row["enabled"] is False

    def test_returns_not_found_for_unknown(self):
        result = _srv.set_codebase_enabled("no-such-repo", True)
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# deregister_codebase
# ---------------------------------------------------------------------------

@requires_db
class TestDeregisterCodebase:
    def test_removes_row(self, tmp_path):
        db = DatabaseManager(dsn=_TEST_DSN)
        db.init_schema()
        db.upsert_codebase_registration("to-delete", str(tmp_path), "", "python", None, "main")
        db.close()

        result = _srv.deregister_codebase("to-delete")
        assert "to-delete" in result
        assert "Error" not in result

        db2 = DatabaseManager(dsn=_TEST_DSN)
        db2.init_schema()
        assert db2.get_codebase("to-delete") is None
        db2.close()

    def test_returns_not_found_for_unknown(self):
        result = _srv.deregister_codebase("no-such-repo")
        assert "not found" in result.lower()
