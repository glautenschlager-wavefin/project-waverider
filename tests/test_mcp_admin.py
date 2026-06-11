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
# register_codebase (remote-first)
# ---------------------------------------------------------------------------

@requires_db
class TestRegisterCodebaseRemote:
    def test_register_requires_github_repo(self):
        from waverider.mcp_server import register_codebase
        out = register_codebase(name="x", github_repo="")
        assert "github_repo" in out.lower()

    def test_register_with_null_path(self):
        from waverider.mcp_server import register_codebase
        from waverider.database import DatabaseManager
        out = register_codebase(name="identity", github_repo="waveaccounting/identity")
        assert "Registered" in out
        db = DatabaseManager(dsn=_TEST_DSN)
        try:
            row = db.get_codebase("identity")
        finally:
            db.close()
        assert row["github_repo"] == "waveaccounting/identity"
        assert row["path"] is None

    def test_idempotent_registration(self):
        from waverider.mcp_server import register_codebase
        from waverider.database import DatabaseManager
        first = register_codebase(name="reef", github_repo="waveaccounting/reef")
        second = register_codebase(name="reef", github_repo="waveaccounting/reef")
        assert "Registered" in first
        assert "Registered" in second
        db = DatabaseManager(dsn=_TEST_DSN)
        try:
            rows = [r for r in db.list_codebases() if r["name"] == "reef"]
        finally:
            db.close()
        assert len(rows) == 1


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


# ---------------------------------------------------------------------------
# list_codebases shows sync error
# ---------------------------------------------------------------------------

@requires_db
class TestListCodebasesShowsSyncError:
    def test_list_includes_sync_error(self):
        from waverider.mcp_server import list_codebases
        from waverider.database import DatabaseManager
        db = DatabaseManager(dsn=_TEST_DSN)
        try:
            db.upsert_codebase_registration(name="nav", path=None,
                                            github_repo="waveaccounting/nav", enabled=True)
            db.record_sync_error("nav", "boom")
        finally:
            db.close()
        out = list_codebases()
        assert "nav" in out
        assert "boom" in out


# ---------------------------------------------------------------------------
# discover_codebases
# ---------------------------------------------------------------------------

class TestDiscoverCodebases:
    def test_returns_summary_text(self):
        from waverider import mcp_server
        from unittest.mock import patch
        with patch.object(mcp_server, "_run_discovery", return_value={
            "discovered": 1, "new": 1, "existing": 0
        }) as run:
            out = mcp_server.discover_codebases(org="waveaccounting")
        run.assert_called_once()
        assert "discovered" in out.lower()
        assert "1" in out
