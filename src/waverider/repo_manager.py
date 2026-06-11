"""WaveRider-managed git clone lifecycle.

Owns cloning, fetching, and resetting the local working copies that the indexer
reads from. The GitHub token is supplied per-invocation via an ephemeral
credential helper and is never written to .git/config or to the command line.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from waverider import config

log = logging.getLogger(__name__)

_FETCH_TIMEOUT = 300


class RepoSyncError(RuntimeError):
    """Raised when a clone/fetch/reset operation fails."""


def _remote_url(github_repo: str) -> str:
    """Return the clean HTTPS remote URL (no token) for a repo slug."""
    return f"https://github.com/{github_repo}.git"


def local_path(name: str, repo_root: Optional[Path] = None) -> Path:
    """Return the managed clone directory for a codebase name."""
    root = repo_root or config.get_repo_root()
    return root / name


def _git(args: List[str], *, cwd: Optional[Path], token: str) -> subprocess.CompletedProcess:
    """Run a git command with the token injected via an ephemeral credential helper.

    The helper reads the password from the GITHUB_TOKEN env var, so the token
    never appears on the command line (visible via `ps`) and is never persisted
    to .git/config (it is passed with `-c`, which is per-invocation only).
    """
    helper = '!f() { echo "username=x-access-token"; echo "password=$GITHUB_TOKEN"; }; f'
    cmd = [
        "git",
        "-c", f"credential.helper={helper}",
        "-c", "credential.useHttpPath=false",
        *args,
    ]
    env = {"GIT_TERMINAL_PROMPT": "0", "GITHUB_TOKEN": token}
    full_env = {**os.environ, **env}
    return subprocess.run(
        cmd, cwd=str(cwd) if cwd else None, env=full_env,
        check=True, capture_output=True, text=True, timeout=_FETCH_TIMEOUT,
    )


def ensure_current(
    github_repo: str,
    name: str,
    branch: str = "main",
    *,
    token: Optional[str] = None,
    repo_root: Optional[Path] = None,
) -> str:
    """Ensure the managed clone of ``github_repo`` matches origin/<branch>.

    Clones if missing, otherwise fetches and hard-resets. Returns the HEAD SHA.
    Raises RepoSyncError on any git failure.
    """
    token = token or config.get_github_token()
    dest = local_path(name, repo_root)
    url = _remote_url(github_repo)
    try:
        if not (dest / ".git").exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            _git(["clone", "--branch", branch, url, str(dest)], cwd=None, token=token)
        else:
            _git(["fetch", "origin", branch], cwd=dest, token=token)
            _git(["reset", "--hard", f"origin/{branch}"], cwd=dest, token=token)
            _git(["clean", "-fdx"], cwd=dest, token=token)
        head = _git(["rev-parse", "HEAD"], cwd=dest, token=token)
        return head.stdout.strip()
    except subprocess.CalledProcessError as exc:
        # exc.stderr may contain the remote URL but never the token.
        raise RepoSyncError(
            f"git sync failed for {github_repo} ({name}): {exc.stderr.strip()}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RepoSyncError(f"git sync timed out for {github_repo} ({name})") from exc
