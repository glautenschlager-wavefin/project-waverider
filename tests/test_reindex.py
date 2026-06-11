# tests/test_reindex.py
"""Unit tests for scripts/reindex_if_changed.py — fake repo_manager, no DB/git."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location(
    "reindex_if_changed", _PROJECT_ROOT / "scripts" / "reindex_if_changed.py",
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

run_reindex = _mod.run_reindex
poll_once = _mod.poll_once

_FAKE_CB = {
    "name": "test-repo",
    "path": None,
    "main_branch_name": "main",
    "description": "Test repo",
    "language": "python",
    "enabled": True,
    "last_indexed_commit": None,
    "github_repo": "waveaccounting/test-repo",
}


class TestPollOnce:
    def _db_with(self, cb):
        db = MagicMock()
        db.get_enabled_codebases.return_value = [cb]
        return db

    def test_skips_when_sha_unchanged(self):
        db = self._db_with({**_FAKE_CB, "last_indexed_commit": "abc1234"})
        with patch.object(_mod.repo_manager, "ensure_current", return_value="abc1234"):
            with patch.object(_mod, "run_reindex") as reindex:
                summary = poll_once(db, _PROJECT_ROOT, dry_run=False)
        assert summary == {"checked": 1, "reindexed": 0, "skipped": 1, "failed": 0}
        reindex.assert_not_called()
        db.update_last_indexed_commit.assert_not_called()

    def test_reindexes_and_writes_path_when_changed(self):
        db = self._db_with({**_FAKE_CB, "last_indexed_commit": "old1234"})
        with patch.object(_mod.repo_manager, "ensure_current", return_value="new5678"):
            with patch.object(_mod.repo_manager, "local_path",
                              return_value=Path("/clones/test-repo")):
                with patch.object(_mod, "run_reindex", return_value=True):
                    summary = poll_once(db, _PROJECT_ROOT, dry_run=False)
        assert summary["reindexed"] == 1
        db.update_codebase_path.assert_called_once_with("test-repo", "/clones/test-repo")
        db.update_last_indexed_commit.assert_called_once_with("test-repo", "new5678")

    def test_reindexes_when_last_commit_null(self):
        db = self._db_with({**_FAKE_CB, "last_indexed_commit": None})
        with patch.object(_mod.repo_manager, "ensure_current", return_value="abc1234"):
            with patch.object(_mod.repo_manager, "local_path",
                              return_value=Path("/clones/test-repo")):
                with patch.object(_mod, "run_reindex", return_value=True):
                    summary = poll_once(db, _PROJECT_ROOT, dry_run=False)
        assert summary["reindexed"] == 1
        db.update_last_indexed_commit.assert_called_once_with("test-repo", "abc1234")

    def test_records_sync_error_and_skips_on_repo_sync_error(self):
        from waverider.repo_manager import RepoSyncError
        db = self._db_with({**_FAKE_CB, "last_indexed_commit": "abc1234"})
        with patch.object(_mod.repo_manager, "ensure_current",
                          side_effect=RepoSyncError("fetch failed")):
            summary = poll_once(db, _PROJECT_ROOT, dry_run=False)
        assert summary == {"checked": 1, "reindexed": 0, "skipped": 0, "failed": 1}
        db.record_sync_error.assert_called_once()
        assert db.record_sync_error.call_args.args[0] == "test-repo"
        db.update_last_indexed_commit.assert_not_called()

    def test_does_not_advance_commit_when_reindex_fails(self):
        db = self._db_with({**_FAKE_CB, "last_indexed_commit": "old1234"})
        with patch.object(_mod.repo_manager, "ensure_current", return_value="new5678"):
            with patch.object(_mod.repo_manager, "local_path",
                              return_value=Path("/clones/test-repo")):
                with patch.object(_mod, "run_reindex", return_value=False):
                    summary = poll_once(db, _PROJECT_ROOT, dry_run=False)
        assert summary["failed"] == 1
        db.update_last_indexed_commit.assert_not_called()

    def test_dry_run_does_not_update_commit(self):
        db = self._db_with({**_FAKE_CB, "last_indexed_commit": None})
        with patch.object(_mod.repo_manager, "ensure_current", return_value="abc1234"):
            with patch.object(_mod.repo_manager, "local_path",
                              return_value=Path("/clones/test-repo")):
                with patch.object(_mod, "run_reindex", return_value=True):
                    summary = poll_once(db, _PROJECT_ROOT, dry_run=True)
        assert summary["reindexed"] == 1
        db.update_last_indexed_commit.assert_not_called()


class TestRunReindex:
    def test_dry_run_returns_true_without_subprocess(self):
        with patch("subprocess.run") as run:
            ok = run_reindex(_FAKE_CB, Path("/clones/test-repo"), _PROJECT_ROOT, dry_run=True)
        assert ok is True
        run.assert_not_called()

    def test_invokes_build_index_with_local_path(self):
        completed = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=completed) as run:
            ok = run_reindex(_FAKE_CB, Path("/clones/test-repo"), _PROJECT_ROOT, dry_run=False)
        assert ok is True
        cmd = run.call_args.args[0]
        assert "--codebase-path" in cmd
        assert "/clones/test-repo" in cmd
