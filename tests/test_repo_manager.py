"""Tests for repo_manager — git mechanics via a local bare-repo fixture (no network)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from waverider import repo_manager
from waverider.repo_manager import RepoSyncError


def _run(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def origin(tmp_path) -> Path:
    """Create a bare 'origin' repo with one commit on main, return its path."""
    work = tmp_path / "work"
    work.mkdir()
    _run("git", "init", "-b", "main", cwd=work)
    _run("git", "config", "user.email", "t@t.com", cwd=work)
    _run("git", "config", "user.name", "t", cwd=work)
    (work / "README.md").write_text("hello\n")
    _run("git", "add", ".", cwd=work)
    _run("git", "commit", "-m", "init", cwd=work)
    bare = tmp_path / "origin.git"
    _run("git", "clone", "--bare", str(work), str(bare))
    return bare


@pytest.fixture
def patched_remote(origin, monkeypatch, tmp_path):
    """Point repo_manager at the local bare repo and an isolated clone root."""
    monkeypatch.setattr(repo_manager, "_remote_url", lambda github_repo: f"file://{origin}")
    monkeypatch.setenv("WAVERIDER_REPO_ROOT", str(tmp_path / "clones"))
    monkeypatch.setenv("GITHUB_TOKEN", "unused-for-file-transport")
    return origin


def _add_commit(origin: Path, tmp_path: Path) -> str:
    """Add a commit to origin via a scratch clone; return the new SHA."""
    scratch = tmp_path / "scratch"
    _run("git", "clone", str(origin), str(scratch))
    _run("git", "config", "user.email", "t@t.com", cwd=scratch)
    _run("git", "config", "user.name", "t", cwd=scratch)
    (scratch / "new.txt").write_text("more\n")
    _run("git", "add", ".", cwd=scratch)
    _run("git", "commit", "-m", "second", cwd=scratch)
    _run("git", "push", "origin", "main", cwd=scratch)
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=scratch,
                         check=True, capture_output=True, text=True)
    return out.stdout.strip()


class TestLocalPath:
    def test_local_path_under_repo_root(self, patched_remote, tmp_path):
        p = repo_manager.local_path("identity")
        assert p == tmp_path / "clones" / "identity"


class TestEnsureCurrent:
    def test_clones_when_missing(self, patched_remote):
        sha = repo_manager.ensure_current("waveaccounting/identity", "identity", "main")
        assert len(sha) == 40
        assert (repo_manager.local_path("identity") / "README.md").exists()

    def test_returns_updated_sha_after_remote_advances(self, patched_remote, origin, tmp_path):
        repo_manager.ensure_current("waveaccounting/identity", "identity", "main")
        new_sha = _add_commit(origin, tmp_path)
        sha = repo_manager.ensure_current("waveaccounting/identity", "identity", "main")
        assert sha == new_sha
        assert (repo_manager.local_path("identity") / "new.txt").exists()

    def test_resets_local_divergence(self, patched_remote):
        repo_manager.ensure_current("waveaccounting/identity", "identity", "main")
        # Dirty the working tree
        readme = repo_manager.local_path("identity") / "README.md"
        readme.write_text("LOCAL EDIT\n")
        repo_manager.ensure_current("waveaccounting/identity", "identity", "main")
        assert readme.read_text() == "hello\n"

    def test_raises_repo_sync_error_on_bad_remote(self, monkeypatch, tmp_path):
        monkeypatch.setattr(repo_manager, "_remote_url",
                            lambda github_repo: f"file://{tmp_path}/does-not-exist.git")
        monkeypatch.setenv("WAVERIDER_REPO_ROOT", str(tmp_path / "clones"))
        monkeypatch.setenv("GITHUB_TOKEN", "x")
        with pytest.raises(RepoSyncError):
            repo_manager.ensure_current("waveaccounting/ghost", "ghost", "main")
