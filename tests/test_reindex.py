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
