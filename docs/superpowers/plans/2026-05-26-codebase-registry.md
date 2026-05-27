# Implementation Plan: DB-Backed Codebase Registry + Git SHA Polling

**Date**: 2026-05-26
**Feature**: Replace hardcoded `index_wave_repos.sh` with a DB-backed registry and add auto-reindexing via local git SHA polling.

---

## Header

**Goal**: Codebase discovery moves from a hard-coded shell script to a `codebase_metadata` Postgres table. A poller script checks each enabled codebase's remote HEAD SHA and runs `build_index.py` on drift. Four MCP admin tools let agents manage the registry.

**Architecture**:
1. `database.py` — new schema columns + new CRUD methods
2. `scripts/seed_registry.py` — one-time migration of the 7 hardcoded repos
3. `scripts/reindex_if_changed.py` — polling script (git fetch → SHA compare → reindex)
4. `mcp_server.py` — 4 new admin tools exposed via FastMCP
5. `scripts/index_wave_repos.sh` — deprecation notice added (not deleted)

**Tech Stack**:
- Python 3.11 + psycopg3 (`psycopg` / `psycopg_pool`)
- Postgres with `codebase_metadata` table (project-managed schema)
- FastMCP (`mcp` library)
- Git CLI (local, no GitHub token required)

**Constraints**:
- Local-first: no AWS, no webhooks, no GitHub token in v1
- `deregister_codebase` deletes the registry row only; CocoIndex-managed tables are untouched
- `register_codebase` does NOT trigger an index run; first poll detects `last_indexed_commit = NULL`
- All 7 Wave repos confirmed on `main` branch

---

## Task 1: Schema Migration + New DatabaseManager Methods

**Files**:
- Modify: `src/waverider/database.py`
- Modify: `tests/test_database.py`

### Step 1.1 — Write failing tests

Append to `tests/test_database.py`:

```python
# ---------------------------------------------------------------------------
# Registry methods — new in codebase-registry feature
# ---------------------------------------------------------------------------

@requires_db
class TestRegistryMethods:
    def test_upsert_codebase_registration_creates_row(self, db, tmp_path):
        cid = db.upsert_codebase_registration(
            name="test-repo",
            path=str(tmp_path),
            description="Test repo",
            language="python",
            github_repo="waveapps/test-repo",
            main_branch_name="main",
        )
        assert cid > 0
        row = db.get_codebase("test-repo")
        assert row["github_repo"] == "waveapps/test-repo"
        assert row["main_branch_name"] == "main"
        assert row["enabled"] is True
        assert row["last_indexed_commit"] is None

    def test_upsert_codebase_registration_idempotent(self, db, tmp_path):
        cid1 = db.upsert_codebase_registration(
            name="test-repo", path=str(tmp_path), description="v1",
            language="python", github_repo="org/repo", main_branch_name="main",
        )
        cid2 = db.upsert_codebase_registration(
            name="test-repo", path=str(tmp_path), description="v2",
            language="typescript", github_repo="org/repo-new", main_branch_name="main",
        )
        assert cid1 == cid2
        row = db.get_codebase("test-repo")
        assert row["description"] == "v2"
        assert row["github_repo"] == "org/repo-new"

    def test_upsert_does_not_overwrite_last_indexed_commit(self, db, tmp_path):
        db.upsert_codebase_registration(
            name="test-repo", path=str(tmp_path), description="",
            language="python", github_repo=None, main_branch_name="main",
        )
        db.update_last_indexed_commit("test-repo", "abc1234")
        # Re-upsert must not clear the commit SHA
        db.upsert_codebase_registration(
            name="test-repo", path=str(tmp_path), description="updated",
            language="python", github_repo=None, main_branch_name="main",
        )
        row = db.get_codebase("test-repo")
        assert row["last_indexed_commit"] == "abc1234"

    def test_get_enabled_codebases_filters_disabled(self, db, tmp_path):
        db.upsert_codebase_registration(
            name="enabled-repo", path=str(tmp_path), description="",
            language="python", github_repo=None, main_branch_name="main",
        )
        db.upsert_codebase_registration(
            name="disabled-repo", path=str(tmp_path), description="",
            language="python", github_repo=None, main_branch_name="main",
        )
        db.set_codebase_enabled("disabled-repo", False)
        enabled = db.get_enabled_codebases()
        names = [r["name"] for r in enabled]
        assert "enabled-repo" in names
        assert "disabled-repo" not in names

    def test_update_last_indexed_commit(self, db, tmp_path):
        db.upsert_codebase_registration(
            name="test-repo", path=str(tmp_path), description="",
            language="python", github_repo=None, main_branch_name="main",
        )
        db.update_last_indexed_commit("test-repo", "deadbeef123")
        row = db.get_codebase("test-repo")
        assert row["last_indexed_commit"] == "deadbeef123"

    def test_set_codebase_enabled_returns_false_for_unknown(self, db):
        result = db.set_codebase_enabled("no-such-repo", False)
        assert result is False

    def test_delete_codebase(self, db, tmp_path):
        db.upsert_codebase_registration(
            name="to-delete", path=str(tmp_path), description="",
            language="python", github_repo=None, main_branch_name="main",
        )
        assert db.get_codebase("to-delete") is not None
        result = db.delete_codebase("to-delete")
        assert result is True
        assert db.get_codebase("to-delete") is None

    def test_delete_codebase_returns_false_for_unknown(self, db):
        result = db.delete_codebase("no-such-repo")
        assert result is False
```

### Step 1.2 — Run tests to confirm failure

```bash
cd "/Users/glautenschlager/dev/project waverider"
poetry run pytest tests/test_database.py::TestRegistryMethods -v 2>&1 | head -40
```

Expected: `AttributeError: 'DatabaseManager' object has no attribute 'upsert_codebase_registration'`

### Step 1.3 — Add `_MIGRATION_SQL` constant to `database.py`

After the existing `_SCHEMA_SQL` / `_BM25_INDEX_SQL` / `_TSVECTOR_INDEX_SQL` constants, add:

```python
# ---------------------------------------------------------------------------
# Schema migration — add registry columns (idempotent via IF NOT EXISTS)
# ---------------------------------------------------------------------------

_MIGRATION_SQL = """
ALTER TABLE codebase_metadata
    ADD COLUMN IF NOT EXISTS enabled             BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS github_repo         TEXT,
    ADD COLUMN IF NOT EXISTS main_branch_name    TEXT    NOT NULL DEFAULT 'main',
    ADD COLUMN IF NOT EXISTS last_indexed_commit TEXT
"""
```

### Step 1.4 — Call migration in `init_schema()`

Locate the end of `init_schema()` (just before or after the BM25/tsvector fallback block) and add:

```python
        # Apply registry column migration (idempotent)
        conn.execute(_MIGRATION_SQL)
        conn.commit()
```

The full tail of `init_schema()` after this change should look like:

```python
        # Apply registry column migration (idempotent)
        conn.execute(_MIGRATION_SQL)
        conn.commit()

        # BM25 full-text index (ParadeDB) with tsvector fallback
        try:
            conn.execute(_BM25_INDEX_SQL)
            conn.commit()
        except Exception:
            conn.rollback()
            conn.execute(_TSVECTOR_INDEX_SQL)
            conn.commit()
```

### Step 1.5 — Add new methods to `DatabaseManager`

Add all five methods below `close()` (or at the end of the class), before the CocoIndex methods block:

```python
    # ------------------------------------------------------------------
    # Codebase registry — added for DB-backed registry feature
    # ------------------------------------------------------------------

    def upsert_codebase_registration(
        self,
        name: str,
        path: str,
        description: str = "",
        language: str = "mixed",
        github_repo: Optional[str] = None,
        main_branch_name: str = "main",
    ) -> int:
        """Insert or update a codebase registry row.

        Does NOT overwrite ``enabled`` or ``last_indexed_commit`` on conflict.
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO codebase_metadata
                    (name, path, description, language, github_repo, main_branch_name, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (name) DO UPDATE SET
                    path             = EXCLUDED.path,
                    description      = EXCLUDED.description,
                    language         = EXCLUDED.language,
                    github_repo      = EXCLUDED.github_repo,
                    main_branch_name = EXCLUDED.main_branch_name,
                    updated_at       = NOW()
                RETURNING id
                """,
                (name, path, description, language, github_repo, main_branch_name),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"upsert_codebase_registration failed for '{name}'")
            return int(row["id"])

    def get_enabled_codebases(self) -> List[Dict[str, Any]]:
        """Return all codebases where ``enabled = true``, ordered by name."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM codebase_metadata WHERE enabled = true ORDER BY name"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_last_indexed_commit(self, name: str, sha: str) -> None:
        """Record the commit SHA that was last successfully indexed."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE codebase_metadata
                SET last_indexed_commit = %s, updated_at = NOW()
                WHERE name = %s
                """,
                (sha, name),
            )

    def set_codebase_enabled(self, name: str, enabled: bool) -> bool:
        """Flip the ``enabled`` flag for a codebase. Returns False if not found."""
        with self._conn() as conn:
            result = conn.execute(
                """
                UPDATE codebase_metadata
                SET enabled = %s, updated_at = NOW()
                WHERE name = %s
                """,
                (enabled, name),
            )
        return result.rowcount == 1

    def delete_codebase(self, name: str) -> bool:
        """Delete a codebase registry row. Returns False if not found.

        Does NOT remove CocoIndex-managed data (coco_snippets, etc.).
        """
        with self._conn() as conn:
            result = conn.execute(
                "DELETE FROM codebase_metadata WHERE name = %s", (name,)
            )
        return result.rowcount == 1
```

> **Note**: `Optional` and `List`, `Dict`, `Any` are already imported at the top of `database.py` via `from typing import ...`. Confirm before adding new imports.

### Step 1.6 — Run tests to confirm pass

```bash
poetry run pytest tests/test_database.py::TestRegistryMethods -v
```

Expected: all 8 tests green.

### Step 1.7 — Run full test suite to catch regressions

```bash
poetry run pytest tests/test_database.py -v
```

Expected: all pre-existing tests still pass.

---

## Task 2: `scripts/seed_registry.py`

**Files**:
- Create: `scripts/seed_registry.py`

### Step 2.1 — Create the script

```python
#!/usr/bin/env python3
"""One-time seed of the codebase registry from the seven repos in index_wave_repos.sh.

Idempotent — safe to re-run. Auto-detects the GitHub remote URL from each repo's
git config; falls back to None if the path doesn't exist or git fails.

Usage:
    poetry run python scripts/seed_registry.py
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.database import DatabaseManager

WAVE_SRC = Path("/Users/glautenschlager/wave/src")

REPOS = [
    {
        "name": "identity",
        "description": "Wave identity Python service",
        "language": "python",
        "main_branch_name": "main",
    },
    {
        "name": "reef",
        "description": "Wave reef TypeScript service",
        "language": "typescript",
        "main_branch_name": "main",
    },
    {
        "name": "payroll",
        "description": "Wave payroll Ruby service",
        "language": "ruby",
        "main_branch_name": "main",
    },
    {
        "name": "next-wave",
        "description": "Wave main web application (React/TypeScript)",
        "language": "typescript",
        "main_branch_name": "main",
    },
    {
        "name": "central-risk",
        "description": "Wave central risk service",
        "language": "python",
        "main_branch_name": "main",
    },
    {
        "name": "next-accounting",
        "description": "Wave next-accounting service",
        "language": "python",
        "main_branch_name": "main",
    },
    {
        "name": "accounting",
        "description": "Wave accounting service",
        "language": "python",
        "main_branch_name": "main",
    },
]


def _detect_github_repo(path: str) -> str | None:
    """Return 'org/repo' by parsing the git origin remote URL, or None on failure."""
    try:
        result = subprocess.run(
            ["git", "-C", path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        url = result.stdout.strip()
        # Handles both SSH (git@github.com:org/repo.git) and HTTPS (https://github.com/org/repo.git)
        m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", url)
        return m.group(1) if m else None
    except Exception:
        return None


def seed(db: DatabaseManager) -> None:
    for repo in REPOS:
        path = str(WAVE_SRC / repo["name"])
        if not Path(path).exists():
            print(f"  SKIP (path not found): {path}")
            continue

        github_repo = _detect_github_repo(path)
        rid = db.upsert_codebase_registration(
            name=repo["name"],
            path=path,
            description=repo["description"],
            language=repo["language"],
            github_repo=github_repo,
            main_branch_name=repo["main_branch_name"],
        )
        github_str = github_repo or "(no remote detected)"
        print(f"  OK: {repo['name']} (id={rid}, github={github_str})")


def main() -> None:
    db = DatabaseManager()
    db.init_schema()
    print("Seeding codebase registry ...")
    seed(db)
    db.close()
    print("Done.")


if __name__ == "__main__":
    main()
```

### Step 2.2 — Run and verify

```bash
poetry run python scripts/seed_registry.py
```

Expected output (paths that exist will show OK, others SKIP):
```
Seeding codebase registry ...
  OK: identity (id=1, github=waveapps/identity)
  OK: reef (id=2, github=waveapps/reef)
  ...
Done.
```

Then confirm rows in the DB:
```bash
poetry run python -c "
import sys; sys.path.insert(0,'src')
from waverider.database import DatabaseManager
db = DatabaseManager(); db.init_schema()
for r in db.list_codebases():
    print(r['name'], '|', r['enabled'], '|', r['main_branch_name'], '|', r['github_repo'])
db.close()
"
```

---

## Task 3: `scripts/reindex_if_changed.py`

**Files**:
- Create: `scripts/reindex_if_changed.py`
- Create: `tests/test_reindex.py`

### Step 3.1 — Write failing tests

```python
# tests/test_reindex.py
"""
Unit tests for scripts/reindex_if_changed.py.
Uses mocks — no live DB or git required.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Load the script as a module without executing main()
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location(
    "reindex_if_changed",
    _PROJECT_ROOT / "scripts" / "reindex_if_changed.py",
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

get_remote_sha = _mod.get_remote_sha
run_reindex = _mod.run_reindex
poll_once = _mod.poll_once

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FAKE_CB = {
    "name": "test-repo",
    "path": "/fake/path",
    "main_branch_name": "main",
    "description": "Test repo",
    "language": "python",
    "enabled": True,
    "last_indexed_commit": None,
    "github_repo": None,
}


# ---------------------------------------------------------------------------
# get_remote_sha
# ---------------------------------------------------------------------------

class TestGetRemoteSha:
    def test_returns_sha_on_success(self):
        fetch_mock = MagicMock(returncode=0)
        rev_mock = MagicMock(returncode=0, stdout="abc1234def5678\n")
        with patch("subprocess.run", side_effect=[fetch_mock, rev_mock]):
            sha = get_remote_sha("/fake/path", "main")
        assert sha == "abc1234def5678"

    def test_returns_none_on_git_failure(self):
        with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
            sha = get_remote_sha("/fake/path", "main")
        assert sha is None

    def test_strips_newline_from_rev_parse(self):
        fetch_mock = MagicMock(returncode=0)
        rev_mock = MagicMock(returncode=0, stdout="  deadbeef  \n")
        with patch("subprocess.run", side_effect=[fetch_mock, rev_mock]):
            sha = get_remote_sha("/fake/path", "main")
        assert sha == "deadbeef"


# ---------------------------------------------------------------------------
# poll_once
# ---------------------------------------------------------------------------

class TestPollOnce:
    def test_skips_when_sha_unchanged(self):
        db = MagicMock()
        cb = {**_FAKE_CB, "last_indexed_commit": "abc1234"}
        db.get_enabled_codebases.return_value = [cb]

        with patch.object(_mod, "get_remote_sha", return_value="abc1234"):
            summary = poll_once(db, _PROJECT_ROOT, dry_run=False)

        assert summary == {"checked": 1, "reindexed": 0, "skipped": 1, "failed": 0}
        db.update_last_indexed_commit.assert_not_called()

    def test_reindexes_when_sha_changed(self):
        db = MagicMock()
        cb = {**_FAKE_CB, "last_indexed_commit": "old1234"}
        db.get_enabled_codebases.return_value = [cb]

        with patch.object(_mod, "get_remote_sha", return_value="new5678"):
            with patch.object(_mod, "run_reindex", return_value=True):
                summary = poll_once(db, _PROJECT_ROOT, dry_run=False)

        assert summary["reindexed"] == 1
        db.update_last_indexed_commit.assert_called_once_with("test-repo", "new5678")

    def test_reindexes_when_last_commit_null(self):
        db = MagicMock()
        cb = {**_FAKE_CB, "last_indexed_commit": None}
        db.get_enabled_codebases.return_value = [cb]

        with patch.object(_mod, "get_remote_sha", return_value="abc1234"):
            with patch.object(_mod, "run_reindex", return_value=True):
                summary = poll_once(db, _PROJECT_ROOT, dry_run=False)

        assert summary["reindexed"] == 1
        db.update_last_indexed_commit.assert_called_once_with("test-repo", "abc1234")

    def test_dry_run_does_not_update_commit(self):
        db = MagicMock()
        cb = {**_FAKE_CB, "last_indexed_commit": None}
        db.get_enabled_codebases.return_value = [cb]

        with patch.object(_mod, "get_remote_sha", return_value="abc1234"):
            with patch.object(_mod, "run_reindex", return_value=True) as mock_reindex:
                summary = poll_once(db, _PROJECT_ROOT, dry_run=True)

        assert summary["reindexed"] == 1
        mock_reindex.assert_called_once_with(cb, _PROJECT_ROOT, True)
        db.update_last_indexed_commit.assert_not_called()

    def test_counts_failed_when_sha_unavailable(self):
        db = MagicMock()
        db.get_enabled_codebases.return_value = [_FAKE_CB]

        with patch.object(_mod, "get_remote_sha", return_value=None):
            summary = poll_once(db, _PROJECT_ROOT, dry_run=False)

        assert summary == {"checked": 1, "reindexed": 0, "skipped": 0, "failed": 1}

    def test_counts_failed_when_reindex_fails(self):
        db = MagicMock()
        cb = {**_FAKE_CB, "last_indexed_commit": None}
        db.get_enabled_codebases.return_value = [cb]

        with patch.object(_mod, "get_remote_sha", return_value="abc1234"):
            with patch.object(_mod, "run_reindex", return_value=False):
                summary = poll_once(db, _PROJECT_ROOT, dry_run=False)

        assert summary["failed"] == 1
        db.update_last_indexed_commit.assert_not_called()
```

### Step 3.2 — Run tests to confirm failure

```bash
poetry run pytest tests/test_reindex.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError` or `FileNotFoundError` because the script doesn't exist yet.

### Step 3.3 — Create the script

```python
#!/usr/bin/env python3
"""Poll enabled codebases for new commits and reindex on change.

Usage:
    # Check once and exit:
    poetry run python scripts/reindex_if_changed.py --once

    # Run forever, checking every 5 minutes:
    poetry run python scripts/reindex_if_changed.py --interval 300

    # Preview what would be reindexed without running anything:
    poetry run python scripts/reindex_if_changed.py --once --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.database import DatabaseManager

log = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_remote_sha(path: str, branch: str) -> Optional[str]:
    """Fetch origin and return the remote HEAD SHA, or None on failure."""
    try:
        subprocess.run(
            ["git", "-C", path, "fetch", "origin", branch],
            check=True,
            capture_output=True,
            timeout=30,
        )
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", f"origin/{branch}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except Exception as exc:
        log.warning("git failed for %s: %s", path, exc)
        return None


def run_reindex(codebase: Dict[str, Any], project_root: Path, dry_run: bool) -> bool:
    """Run build_index.py for the given codebase. Returns True on success."""
    script = project_root / "scripts" / "build_index.py"
    cmd = [
        sys.executable,
        str(script),
        "--codebase-path", codebase["path"],
        "--index-name", codebase["name"],
        "--description", codebase.get("description") or "",
        "--language", codebase.get("language") or "mixed",
    ]
    if dry_run:
        log.info("[DRY-RUN] Would run: %s", " ".join(cmd))
        return True
    log.info("Reindexing %s ...", codebase["name"])
    result = subprocess.run(cmd)
    return result.returncode == 0


def poll_once(
    db: DatabaseManager,
    project_root: Path,
    dry_run: bool,
) -> Dict[str, int]:
    """Check all enabled codebases and reindex those with new commits.

    Returns a summary dict: {checked, reindexed, skipped, failed}.
    """
    codebases = db.get_enabled_codebases()
    summary: Dict[str, int] = {"checked": 0, "reindexed": 0, "skipped": 0, "failed": 0}

    for cb in codebases:
        summary["checked"] += 1
        sha = get_remote_sha(cb["path"], cb["main_branch_name"])

        if sha is None:
            log.warning("Could not get remote SHA for %s — skipping", cb["name"])
            summary["failed"] += 1
            continue

        if sha == cb.get("last_indexed_commit"):
            log.debug("%s is up to date (%s)", cb["name"], sha[:8])
            summary["skipped"] += 1
            continue

        prev = (cb.get("last_indexed_commit") or "null")[:8]
        log.info("%s: new commit %s (was %s)", cb["name"], sha[:8], prev)

        success = run_reindex(cb, project_root, dry_run)
        if success:
            if not dry_run:
                db.update_last_indexed_commit(cb["name"], sha)
            summary["reindexed"] += 1
        else:
            log.error("Reindex failed for %s", cb["name"])
            summary["failed"] += 1

    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reindex codebases with new commits")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Check once then exit")
    mode.add_argument(
        "--interval",
        type=int,
        metavar="SECONDS",
        help="Poll continuously every N seconds",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be reindexed without running build_index.py",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    db = DatabaseManager()
    db.init_schema()

    try:
        if args.once:
            summary = poll_once(db, _PROJECT_ROOT, dry_run=args.dry_run)
            log.info("Done: %s", summary)
        else:
            while True:
                summary = poll_once(db, _PROJECT_ROOT, dry_run=args.dry_run)
                log.info("Poll complete: %s — sleeping %ds", summary, args.interval)
                time.sleep(args.interval)
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

### Step 3.4 — Run tests to confirm pass

```bash
poetry run pytest tests/test_reindex.py -v
```

Expected: all 7 tests green.

### Step 3.5 — Smoke-test dry-run

```bash
poetry run python scripts/reindex_if_changed.py --once --dry-run
```

Expected: logs for each enabled codebase showing either `[DRY-RUN] Would run: ...` or `up to date`.

---

## Task 4: Admin MCP Tools

**Files**:
- Modify: `src/waverider/mcp_server.py`
- Create: `tests/test_mcp_admin.py`

### Step 4.1 — Write failing tests

```python
# tests/test_mcp_admin.py
"""
Tests for the four admin MCP tools: register_codebase, list_codebases,
set_codebase_enabled, deregister_codebase.

These tests call the tool functions directly (not via MCP transport).
Requires a live DB; skipped otherwise.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

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
```

### Step 4.2 — Run tests to confirm failure

```bash
poetry run pytest tests/test_mcp_admin.py -v 2>&1 | head -30
```

Expected: `AttributeError: module 'waverider.mcp_server' has no attribute 'register_codebase'`

### Step 4.3 — Add the four admin tools to `mcp_server.py`

Append after the `retrieve_code` tool definition (before the final `if __name__ == "__main__"` block):

```python
# ---------------------------------------------------------------------------
# Codebase Registry Admin Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def register_codebase(
    name: str,
    path: str,
    description: str = "",
    language: str = "mixed",
    github_repo: str = "",
    main_branch_name: str = "main",
) -> str:
    """Register a codebase in the registry.

    Does not trigger an index run — the next poll cycle detects
    last_indexed_commit = NULL and reindexes automatically.

    Args:
        name: Unique identifier (e.g. "identity")
        path: Absolute path to the codebase on disk
        description: Human-readable description
        language: Primary language (python, typescript, ruby, mixed)
        github_repo: GitHub slug (e.g. "waveapps/identity") — optional, for future auto-reindex
        main_branch_name: Branch to track (default: main)
    """
    from pathlib import Path as _Path

    from waverider.database import DatabaseManager

    if not _Path(path).exists():
        return f"Error: path does not exist: {path}"

    db = DatabaseManager()
    try:
        cid = db.upsert_codebase_registration(
            name=name,
            path=path,
            description=description,
            language=language,
            github_repo=github_repo or None,
            main_branch_name=main_branch_name,
        )
        return (
            f"Registered '{name}' (id={cid}). "
            "Run `poetry run python scripts/reindex_if_changed.py --once` to index it."
        )
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        db.close()


@mcp.tool()
def list_codebases() -> str:
    """List all registered codebases and their reindex status.

    Returns name, enabled flag, language, last indexed commit SHA, and path.
    """
    from waverider.database import DatabaseManager

    db = DatabaseManager()
    try:
        rows = db.list_codebases()
        if not rows:
            return "No codebases registered. Run: poetry run python scripts/seed_registry.py"

        header = f"{'NAME':<20} {'ENABLED':<8} {'LANG':<12} {'LAST COMMIT':<12} PATH"
        sep = "-" * 90
        lines = [header, sep]
        for r in rows:
            sha = (r.get("last_indexed_commit") or "never")[:8]
            enabled = "yes" if r["enabled"] else "no"
            lines.append(
                f"{r['name']:<20} {enabled:<8} {r['language']:<12} {sha:<12} {r['path']}"
            )
        return "\n".join(lines)
    finally:
        db.close()


@mcp.tool()
def set_codebase_enabled(name: str, enabled: bool) -> str:
    """Enable or disable automatic reindexing for a codebase.

    Disabled codebases are skipped by the reindex poller but remain in the registry.

    Args:
        name: Codebase name (as returned by list_codebases)
        enabled: True to enable, False to disable
    """
    from waverider.database import DatabaseManager

    db = DatabaseManager()
    try:
        ok = db.set_codebase_enabled(name, enabled)
        if not ok:
            return f"Codebase '{name}' not found."
        state = "enabled" if enabled else "disabled"
        return f"'{name}' is now {state}."
    finally:
        db.close()


@mcp.tool()
def deregister_codebase(name: str) -> str:
    """Remove a codebase from the registry.

    Does not delete indexed data — CocoIndex-managed tables (coco_snippets, etc.)
    are left intact and must be cleared separately if desired.

    Args:
        name: Codebase name to remove (as returned by list_codebases)
    """
    from waverider.database import DatabaseManager

    db = DatabaseManager()
    try:
        ok = db.delete_codebase(name)
        if not ok:
            return f"Codebase '{name}' not found."
        return f"'{name}' removed from registry. Indexed data was not deleted."
    finally:
        db.close()
```

### Step 4.4 — Run tests to confirm pass

```bash
poetry run pytest tests/test_mcp_admin.py -v
```

Expected: all tests green (DB-dependent tests skipped if DB unavailable).

---

## Task 5: Deprecation Notice on `index_wave_repos.sh`

**Files**:
- Modify: `scripts/index_wave_repos.sh`

### Step 5.1 — Add notice at top of file

Replace the existing shebang line + leading comment block with:

```bash
#!/usr/bin/env bash
# DEPRECATED: This script is superseded by the DB-backed codebase registry.
#
# To manage codebases, use the MCP admin tools:
#   register_codebase, list_codebases, set_codebase_enabled, deregister_codebase
#
# To seed the registry from this list (one-time migration):
#   poetry run python scripts/seed_registry.py
#
# To trigger reindexing:
#   poetry run python scripts/reindex_if_changed.py --once
#
# This file is kept for historical reference and manual emergency use only.
# -------------------------------------------------------------------------
```

---

## Task 6: Verification

### Step 6.1 — Full test suite

```bash
poetry run pytest tests/ -v
```

Expected: all tests pass; DB-dependent tests are green when ParadeDB is running.

### Step 6.2 — Schema migration is idempotent

```bash
# Run init_schema() twice — should not error
poetry run python -c "
import sys; sys.path.insert(0,'src')
from waverider.database import DatabaseManager
db = DatabaseManager(); db.init_schema(); db.init_schema()
print('OK — schema init is idempotent')
db.close()
"
```

### Step 6.3 — Seed then inspect

```bash
poetry run python scripts/seed_registry.py
poetry run python scripts/reindex_if_changed.py --once --dry-run
```

Expected dry-run output for each enabled codebase:
```
[DRY-RUN] Would run: /path/to/python scripts/build_index.py --codebase-path ...
```

Or `up to date` for repos already indexed.

### Step 6.4 — Verify MCP tools are discoverable

```bash
poetry run python -c "
import sys; sys.path.insert(0,'src')
import waverider.mcp_server as srv
tools = [t for t in dir(srv) if not t.startswith('_')]
for t in ['register_codebase','list_codebases','set_codebase_enabled','deregister_codebase']:
    assert t in tools, f'Missing: {t}'
print('All 4 admin tools found in mcp_server module')
"
```

---

## Self-Review Checklist

- [x] No placeholders — all code blocks are complete and runnable
- [x] Test steps are before implementation steps within each task
- [x] Every verification step has an expected output
- [x] `add_codebase()` ON CONFLICT clause unchanged — does not touch new columns
- [x] `upsert_codebase_registration()` ON CONFLICT does not overwrite `enabled` or `last_indexed_commit`
- [x] Migration SQL uses `ADD COLUMN IF NOT EXISTS` — safe to run multiple times
- [x] `deregister_codebase` deletes registry row only
- [x] `register_codebase` does NOT trigger index run
- [x] All 7 repos default to `main_branch_name = 'main'`
- [x] `_MIGRATION_SQL` is called from `init_schema()` so it runs automatically on startup
- [x] Poller correctly treats `last_indexed_commit = NULL` as "needs indexing"
- [x] Dry-run path calls `run_reindex(..., dry_run=True)` but does NOT call `update_last_indexed_commit`

---

## Execution Handoff

All tasks are independent within-session work. Recommended order: 1 → 2 → 3 → 4 → 5 → 6.

Tasks 1, 3, and 4 have TDD gates (write failing test → implement → confirm green).

Run with: **Subagent-Driven Development** for parallelism across tasks 2, 3, 4, and 5 after Task 1 is complete (they all depend on the new DB methods from Task 1).
