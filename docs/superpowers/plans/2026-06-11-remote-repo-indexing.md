# Remote Repo Indexing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make WaveRider index remote Wave repos by cloning them into a WaveRider-managed location, with org auto-discovery, instead of relying on engineer-owned local clones.

**Architecture:** Introduce a clone-management boundary (`repo_manager.py`) and a discovery path (`github_discovery.py`) between the codebase registry and the unchanged CocoIndex indexer. `github_repo` becomes the source of truth; `path` becomes a managed clone location. The reindex poller becomes a thin orchestrator: sync the clone (`ensure_current`), then index only when the HEAD SHA changed.

**Tech Stack:** Python 3.14 / Poetry, PostgreSQL (psycopg), `requests` (GitHub REST), git CLI (subprocess), FastMCP, pytest.

**Spec:** `docs/superpowers/specs/2026-06-10-remote-repo-indexing-design.md`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/waverider/config.py` (modify) | Surface `GITHUB_TOKEN`, `WAVERIDER_REPO_ROOT`, `WAVERIDER_GITHUB_ORG` |
| `src/waverider/database.py` (modify) | New columns + `update_codebase_path`, `record_sync_error`, clear-error-on-commit, `enabled` param on upsert, relax `path` NOT NULL |
| `src/waverider/repo_manager.py` (create) | Managed clone lifecycle: `local_path`, `ensure_current`, `RepoSyncError` |
| `src/waverider/github_discovery.py` (create) | Org listing: `RepoInfo`, `list_org_repos`, `DiscoveryError` |
| `scripts/discover_repos.py` (create) | CLI wrapper over shared `run_discovery` |
| `scripts/reindex_if_changed.py` (modify) | Thin orchestrator over `repo_manager` |
| `scripts/seed_default_repos.py` (create) | Enable the curated `DEFAULT_REPOS` set |
| `src/waverider/mcp_server.py` (modify) | `discover_codebases` tool; update `register_codebase`, `list_codebases` |
| `tests/test_repo_manager.py` (create) | Local bare-repo fixture, no network |
| `tests/test_github_discovery.py` (create) | Mocked HTTP |
| `tests/test_reindex.py` (modify) | Fake `repo_manager`, new flow |
| `docs/CODEBASE_REGISTRY.md`, `AGENTS.md` (modify) | Document remote-clone model |
| `scripts/seed_registry.py`, `scripts/index_wave_repos.sh` (delete) | Replaced by discovery + seed |

---

## Task 1: Config — surface new environment variables

**Files:**
- Modify: `src/waverider/config.py`
- Test: `tests/test_config_env.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_config_env.py`:

```python
"""Tests for remote-indexing env-var helpers in waverider.config."""
import os
from pathlib import Path

import pytest

from waverider import config


def test_github_org_default(monkeypatch):
    monkeypatch.delenv("WAVERIDER_GITHUB_ORG", raising=False)
    assert config.get_github_org() == "waveaccounting"


def test_github_org_override(monkeypatch):
    monkeypatch.setenv("WAVERIDER_GITHUB_ORG", "acme")
    assert config.get_github_org() == "acme"


def test_repo_root_default(monkeypatch):
    monkeypatch.delenv("WAVERIDER_REPO_ROOT", raising=False)
    assert config.get_repo_root() == Path.home() / ".waverider" / "repos"


def test_repo_root_override(monkeypatch, tmp_path):
    monkeypatch.setenv("WAVERIDER_REPO_ROOT", str(tmp_path))
    assert config.get_repo_root() == tmp_path


def test_github_token_returns_value(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok123")
    assert config.get_github_token() == "tok123"


def test_github_token_missing_raises(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        config.get_github_token()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/test_config_env.py -v`
Expected: FAIL with `AttributeError: module 'waverider.config' has no attribute 'get_github_org'`

- [ ] **Step 3: Add the helpers**

Append to `src/waverider/config.py` (after `get_config`):

```python
# ---------------------------------------------------------------------------
# Remote indexing configuration
# ---------------------------------------------------------------------------

_DEFAULT_GITHUB_ORG = "waveaccounting"


def get_github_org() -> str:
    """Return the GitHub org to discover (env WAVERIDER_GITHUB_ORG)."""
    return os.getenv("WAVERIDER_GITHUB_ORG", _DEFAULT_GITHUB_ORG)


def get_repo_root() -> Path:
    """Return the root dir for WaveRider-managed clones (env WAVERIDER_REPO_ROOT)."""
    override = os.getenv("WAVERIDER_REPO_ROOT")
    if override:
        return Path(override)
    return Path.home() / ".waverider" / "repos"


def get_github_token() -> str:
    """Return the GitHub PAT (env GITHUB_TOKEN). Raises if unset."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError(
            "GITHUB_TOKEN is not set. Export a GitHub PAT with repo read access "
            "to clone and discover Wave repositories."
        )
    return token
```

Add `from pathlib import Path` to the imports at the top of the file if not already present.

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/test_config_env.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/waverider/config.py tests/test_config_env.py
git commit -m "feat: add remote-indexing config helpers (GITHUB_TOKEN, repo root, org)"
```

---

## Task 2: Database — sync-error columns, nullable path, new methods

**Files:**
- Modify: `src/waverider/database.py:148-156` (`_MIGRATION_SQL`)
- Modify: `src/waverider/database.py:528-587` (`upsert_codebase_registration`, `update_last_indexed_commit`)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_database.py`:

```python
# ---------------------------------------------------------------------------
# Remote indexing — path write-back & sync-error tracking
# ---------------------------------------------------------------------------


@requires_db
class TestRemoteIndexingColumns:
    def test_upsert_with_null_path(self, db):
        cid = db.upsert_codebase_registration(
            name="identity", path=None, github_repo="waveaccounting/identity",
            enabled=False,
        )
        assert cid > 0
        row = db.get_codebase("identity")
        assert row["path"] is None
        assert row["enabled"] is False
        assert row["github_repo"] == "waveaccounting/identity"

    def test_upsert_preserves_enabled_on_conflict(self, db):
        db.upsert_codebase_registration(name="reef", path=None,
                                        github_repo="waveaccounting/reef", enabled=False)
        db.set_codebase_enabled("reef", True)
        # Re-discovery upsert with enabled=False must NOT flip it back off
        db.upsert_codebase_registration(name="reef", path=None,
                                        github_repo="waveaccounting/reef", enabled=False)
        assert db.get_codebase("reef")["enabled"] is True

    def test_update_codebase_path(self, db):
        db.upsert_codebase_registration(name="api", path=None,
                                        github_repo="waveaccounting/api", enabled=True)
        db.update_codebase_path("api", "/tmp/clones/api")
        assert db.get_codebase("api")["path"] == "/tmp/clones/api"

    def test_record_and_clear_sync_error(self, db):
        db.upsert_codebase_registration(name="nav", path=None,
                                        github_repo="waveaccounting/nav", enabled=True)
        db.record_sync_error("nav", "fetch failed: timeout")
        row = db.get_codebase("nav")
        assert row["last_sync_error"] == "fetch failed: timeout"
        assert row["last_sync_error_at"] is not None
        # A successful commit clears the error
        db.update_last_indexed_commit("nav", "abcdef123456")
        row = db.get_codebase("nav")
        assert row["last_sync_error"] is None
        assert row["last_sync_error_at"] is None
        assert row["last_indexed_commit"] == "abcdef123456"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_database.py::TestRemoteIndexingColumns -v`
Expected: FAIL (e.g. `TypeError: upsert_codebase_registration() got an unexpected keyword argument 'enabled'`), or all skipped if no DB — if skipped, start the DB first: `docker compose up -d`.

- [ ] **Step 3: Migration — add columns and relax path NOT NULL**

In `src/waverider/database.py`, replace `_MIGRATION_SQL` (currently at lines 148-156):

```python
_MIGRATION_SQL = """
ALTER TABLE codebase_metadata
    ADD COLUMN IF NOT EXISTS enabled             BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS github_repo         TEXT,
    ADD COLUMN IF NOT EXISTS main_branch_name    TEXT    NOT NULL DEFAULT 'main',
    ADD COLUMN IF NOT EXISTS last_indexed_commit TEXT,
    ADD COLUMN IF NOT EXISTS last_sync_error     TEXT,
    ADD COLUMN IF NOT EXISTS last_sync_error_at  TIMESTAMPTZ;
ALTER TABLE codebase_metadata ALTER COLUMN path DROP NOT NULL;
"""
```

`init_schema` runs `_MIGRATION_SQL` via `conn.execute(_MIGRATION_SQL)` (multi-statement execute is fine for psycopg). Verify that call still exists in `init_schema`; no change needed there.

- [ ] **Step 4: Add `enabled` param + nullable path to `upsert_codebase_registration`**

Replace `upsert_codebase_registration` (lines ~528-560):

```python
    def upsert_codebase_registration(
        self,
        name: str,
        path: Optional[str] = None,
        description: str = "",
        language: str = "mixed",
        github_repo: Optional[str] = None,
        main_branch_name: str = "main",
        enabled: bool = True,
    ) -> int:
        """Insert or update a codebase registry row.

        On INSERT, ``enabled`` is set from the argument. On CONFLICT, ``enabled``
        and ``last_indexed_commit`` are preserved (not overwritten) so that
        re-discovery never re-disables an admin-enabled codebase.
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO codebase_metadata
                    (name, path, description, language, github_repo,
                     main_branch_name, enabled, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (name) DO UPDATE SET
                    path             = EXCLUDED.path,
                    description      = EXCLUDED.description,
                    language         = EXCLUDED.language,
                    github_repo      = EXCLUDED.github_repo,
                    main_branch_name = EXCLUDED.main_branch_name,
                    updated_at       = NOW()
                RETURNING id
                """,
                (name, path, description, language, github_repo,
                 main_branch_name, enabled),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"upsert_codebase_registration failed for '{name}'")
            return int(row["id"])
```

- [ ] **Step 5: Add `update_codebase_path` and `record_sync_error`; clear error on commit**

Replace `update_last_indexed_commit` (lines ~570-580) and add two methods after it:

```python
    def update_last_indexed_commit(self, name: str, sha: str) -> None:
        """Record the commit SHA that was last successfully indexed.

        Also clears any recorded sync error, since a successful index implies a
        successful sync.
        """
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE codebase_metadata
                SET last_indexed_commit = %s,
                    last_sync_error     = NULL,
                    last_sync_error_at  = NULL,
                    updated_at          = NOW()
                WHERE name = %s
                """,
                (sha, name),
            )

    def update_codebase_path(self, name: str, path: str) -> None:
        """Write back the WaveRider-managed clone path for a codebase."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE codebase_metadata
                SET path = %s, updated_at = NOW()
                WHERE name = %s
                """,
                (path, name),
            )

    def record_sync_error(self, name: str, message: str) -> None:
        """Record a RepoSyncError message + timestamp on the codebase record."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE codebase_metadata
                SET last_sync_error = %s, last_sync_error_at = NOW(), updated_at = NOW()
                WHERE name = %s
                """,
                (message, name),
            )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `poetry run pytest tests/test_database.py::TestRemoteIndexingColumns -v`
Expected: PASS (4 passed). Run the full DB suite to confirm no regressions: `poetry run pytest tests/test_database.py -v`

- [ ] **Step 7: Commit**

```bash
git add src/waverider/database.py tests/test_database.py
git commit -m "feat: nullable path, sync-error columns, path write-back in registry"
```

---

## Task 3: repo_manager — managed clone lifecycle

**Files:**
- Create: `src/waverider/repo_manager.py`
- Test: `tests/test_repo_manager.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_repo_manager.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_repo_manager.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'waverider.repo_manager'`

- [ ] **Step 3: Implement `repo_manager.py`**

Create `src/waverider/repo_manager.py`:

```python
"""WaveRider-managed git clone lifecycle.

Owns cloning, fetching, and resetting the local working copies that the indexer
reads from. The GitHub token is supplied per-invocation via an ephemeral
credential helper and is never written to .git/config or to the command line.
"""
from __future__ import annotations

import logging
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
    import os
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_repo_manager.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/waverider/repo_manager.py tests/test_repo_manager.py
git commit -m "feat: repo_manager for WaveRider-managed clones with token-safe git"
```

---

## Task 4: github_discovery — list org repos

**Files:**
- Create: `src/waverider/github_discovery.py`
- Test: `tests/test_github_discovery.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_github_discovery.py`:

```python
"""Tests for github_discovery — GitHub org listing with mocked HTTP."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from waverider import github_discovery
from waverider.github_discovery import DiscoveryError, RepoInfo


def _repo(name, *, archived=False, fork=False, lang="Python", branch="main"):
    return {
        "name": name,
        "full_name": f"waveaccounting/{name}",
        "default_branch": branch,
        "description": f"{name} service",
        "language": lang,
        "archived": archived,
        "fork": fork,
    }


def _resp(json_data, status=200, link=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.headers = {"Link": link} if link else {}
    return r


class TestListOrgRepos:
    def test_maps_fields(self):
        with patch("requests.get", return_value=_resp([_repo("identity")])):
            repos = github_discovery.list_org_repos("waveaccounting", token="t")
        assert repos == [
            RepoInfo(
                name="identity",
                github_repo="waveaccounting/identity",
                default_branch="main",
                description="identity service",
                language="python",
            )
        ]

    def test_filters_archived_and_forks(self):
        payload = [_repo("keep"), _repo("old", archived=True), _repo("forked", fork=True)]
        with patch("requests.get", return_value=_resp(payload)):
            repos = github_discovery.list_org_repos("waveaccounting", token="t")
        assert [r.name for r in repos] == ["keep"]

    def test_language_defaults_to_mixed_when_null(self):
        repo = _repo("nolang")
        repo["language"] = None
        with patch("requests.get", return_value=_resp([repo])):
            repos = github_discovery.list_org_repos("waveaccounting", token="t")
        assert repos[0].language == "mixed"

    def test_follows_pagination(self):
        page1 = _resp([_repo("a")], link='<https://api.github.com/x?page=2>; rel="next"')
        page2 = _resp([_repo("b")])
        with patch("requests.get", side_effect=[page1, page2]) as g:
            repos = github_discovery.list_org_repos("waveaccounting", token="t")
        assert [r.name for r in repos] == ["a", "b"]
        assert g.call_count == 2

    def test_raises_discovery_error_on_non_200(self):
        with patch("requests.get", return_value=_resp({"message": "Bad creds"}, status=401)):
            with pytest.raises(DiscoveryError, match="401"):
                github_discovery.list_org_repos("waveaccounting", token="t")

    def test_raises_discovery_error_on_network_failure(self):
        import requests
        with patch("requests.get", side_effect=requests.RequestException("boom")):
            with pytest.raises(DiscoveryError):
                github_discovery.list_org_repos("waveaccounting", token="t")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_github_discovery.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'waverider.github_discovery'`

- [ ] **Step 3: Implement `github_discovery.py`**

Create `src/waverider/github_discovery.py`:

```python
"""GitHub org repository discovery via the REST API.

Pure HTTP + filtering — no DB access, so it is fully unit-testable with mocked
responses. Returns RepoInfo records for non-archived, non-fork repos.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import requests

log = logging.getLogger(__name__)

_API = "https://api.github.com"
_TIMEOUT = 30
_PER_PAGE = 100


class DiscoveryError(RuntimeError):
    """Raised when the GitHub API cannot be queried successfully."""


@dataclass(frozen=True)
class RepoInfo:
    """A discovered repository, mapped to registry fields."""

    name: str
    github_repo: str
    default_branch: str
    description: str
    language: str


def _next_url(resp: requests.Response) -> Optional[str]:
    """Extract the rel=next URL from a GitHub Link header, if present."""
    link = resp.headers.get("Link", "")
    for part in link.split(","):
        section = part.split(";")
        if len(section) < 2:
            continue
        if 'rel="next"' in section[1]:
            return section[0].strip().strip("<>")
    return None


def list_org_repos(org: str, token: str) -> List[RepoInfo]:
    """List non-archived, non-fork repos for a GitHub org.

    Raises DiscoveryError on any non-200 response or network failure.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    url: Optional[str] = f"{_API}/orgs/{org}/repos?per_page={_PER_PAGE}"
    results: List[RepoInfo] = []

    while url:
        try:
            resp = requests.get(url, headers=headers, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            raise DiscoveryError(f"GitHub request failed: {exc}") from exc

        if resp.status_code != 200:
            raise DiscoveryError(
                f"GitHub API returned {resp.status_code} for org '{org}'"
            )

        for repo in resp.json():
            if repo.get("archived") or repo.get("fork"):
                continue
            language = (repo.get("language") or "mixed").lower()
            results.append(
                RepoInfo(
                    name=repo["name"],
                    github_repo=repo["full_name"],
                    default_branch=repo.get("default_branch") or "main",
                    description=repo.get("description") or "",
                    language=language,
                )
            )

        url = _next_url(resp)

    log.info("Discovered %d active repos in org '%s'", len(results), org)
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_github_discovery.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add src/waverider/github_discovery.py tests/test_github_discovery.py
git commit -m "feat: github_discovery to list org repos via REST API"
```

---

## Task 5: Discovery core + CLI script

**Files:**
- Create: `scripts/discover_repos.py`
- Test: `tests/test_discover_repos.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_discover_repos.py`:

```python
"""Tests for the discovery orchestrator run_discovery (mocked discovery + fake DB)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

from waverider.github_discovery import RepoInfo

_PROJECT_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location(
    "discover_repos", _PROJECT_ROOT / "scripts" / "discover_repos.py"
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

run_discovery = _mod.run_discovery


def _info(name):
    return RepoInfo(name=name, github_repo=f"waveaccounting/{name}",
                    default_branch="main", description=f"{name} svc", language="python")


def test_upserts_new_repos_as_disabled():
    db = MagicMock()
    db.list_codebases.return_value = []
    repos = [_info("identity"), _info("reef")]
    with patch.object(_mod.github_discovery, "list_org_repos", return_value=repos):
        summary = run_discovery(db, org="waveaccounting", token="t", dry_run=False)

    assert summary == {"discovered": 2, "new": 2, "existing": 0}
    # New repos registered with enabled=False
    for call in db.upsert_codebase_registration.call_args_list:
        assert call.kwargs["enabled"] is False
        assert call.kwargs["path"] is None


def test_counts_existing_and_preserves_them():
    db = MagicMock()
    db.list_codebases.return_value = [{"name": "identity"}]
    repos = [_info("identity"), _info("reef")]
    with patch.object(_mod.github_discovery, "list_org_repos", return_value=repos):
        summary = run_discovery(db, org="waveaccounting", token="t", dry_run=False)

    assert summary == {"discovered": 2, "new": 1, "existing": 1}


def test_dry_run_does_not_write():
    db = MagicMock()
    db.list_codebases.return_value = []
    with patch.object(_mod.github_discovery, "list_org_repos", return_value=[_info("api")]):
        summary = run_discovery(db, org="waveaccounting", token="t", dry_run=True)

    assert summary == {"discovered": 1, "new": 1, "existing": 0}
    db.upsert_codebase_registration.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_discover_repos.py -v`
Expected: FAIL with `FileNotFoundError` / cannot load spec (script doesn't exist yet)

- [ ] **Step 3: Implement `scripts/discover_repos.py`**

Create `scripts/discover_repos.py`:

```python
#!/usr/bin/env python3
"""Discover repos in a GitHub org and register them (disabled) in the registry.

Usage:
    poetry run python scripts/discover_repos.py
    poetry run python scripts/discover_repos.py --org waveaccounting --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider import config, github_discovery
from waverider.database import DatabaseManager

log = logging.getLogger(__name__)


def run_discovery(
    db: DatabaseManager,
    org: str,
    token: str,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Discover org repos and upsert new ones as disabled.

    Existing codebases are left untouched (their enabled flag is preserved).
    Returns {discovered, new, existing}.
    """
    repos = github_discovery.list_org_repos(org, token)
    existing_names = {cb["name"] for cb in db.list_codebases()}

    new = 0
    for repo in repos:
        if repo.name in existing_names:
            continue
        new += 1
        if dry_run:
            log.info("[DRY-RUN] Would register '%s' (%s, disabled)",
                     repo.name, repo.github_repo)
            continue
        db.upsert_codebase_registration(
            name=repo.name,
            path=None,
            description=repo.description,
            language=repo.language,
            github_repo=repo.github_repo,
            main_branch_name=repo.default_branch,
            enabled=False,
        )

    return {
        "discovered": len(repos),
        "new": new,
        "existing": len(repos) - new,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover GitHub org repos")
    parser.add_argument("--org", default=None, help="GitHub org (default: WAVERIDER_GITHUB_ORG)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    org = args.org or config.get_github_org()
    token = config.get_github_token()

    db = DatabaseManager()
    db.init_schema()
    try:
        summary = run_discovery(db, org=org, token=token, dry_run=args.dry_run)
        log.info("Discovery complete: %s", summary)
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_discover_repos.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/discover_repos.py tests/test_discover_repos.py
git commit -m "feat: discover_repos script registers org repos as disabled"
```

---

## Task 6: Rewrite reindex_if_changed as a thin orchestrator

**Files:**
- Modify: `scripts/reindex_if_changed.py` (replace `get_remote_sha`, `run_reindex`, `poll_once`)
- Modify: `tests/test_reindex.py` (replace the `get_remote_sha` and `poll_once` test classes)

- [ ] **Step 1: Rewrite the tests first**

Replace the entire contents of `tests/test_reindex.py` with:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_reindex.py -v`
Expected: FAIL (`run_reindex` still has old signature; `poll_once` still calls `get_remote_sha`; `_mod.repo_manager` missing)

- [ ] **Step 3: Rewrite the script**

In `scripts/reindex_if_changed.py`, update the imports block and replace `get_remote_sha`, `run_reindex`, and `poll_once`.

Replace the import section:

```python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider import repo_manager
from waverider.database import DatabaseManager
from waverider.repo_manager import RepoSyncError
```

Delete the `get_remote_sha` function entirely. Replace `run_reindex` and `poll_once` with:

```python
def run_reindex(
    codebase: Dict[str, Any],
    local_path: Path,
    project_root: Path,
    dry_run: bool,
) -> bool:
    """Run build_index.py for the given codebase clone. Returns True on success."""
    script = project_root / "scripts" / "build_index.py"
    cmd = [
        sys.executable,
        str(script),
        "--codebase-path", str(local_path),
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
    """Sync each enabled codebase's clone, then reindex those whose HEAD changed.

    Returns a summary dict: {checked, reindexed, skipped, failed}.
    """
    codebases = db.get_enabled_codebases()
    summary: Dict[str, int] = {"checked": 0, "reindexed": 0, "skipped": 0, "failed": 0}

    for cb in codebases:
        summary["checked"] += 1
        try:
            sha = repo_manager.ensure_current(
                cb["github_repo"], cb["name"], cb["main_branch_name"]
            )
        except RepoSyncError as exc:
            log.warning("Sync failed for %s: %s", cb["name"], exc)
            if not dry_run:
                db.record_sync_error(cb["name"], str(exc))
            summary["failed"] += 1
            continue

        if sha == cb.get("last_indexed_commit"):
            log.debug("%s is up to date (%s)", cb["name"], sha[:8])
            summary["skipped"] += 1
            continue

        prev = (cb.get("last_indexed_commit") or "null")[:8]
        log.info("%s: new commit %s (was %s)", cb["name"], sha[:8], prev)

        local = repo_manager.local_path(cb["name"])
        if not dry_run:
            db.update_codebase_path(cb["name"], str(local))

        success = run_reindex(cb, local, project_root, dry_run)
        if success:
            if not dry_run:
                db.update_last_indexed_commit(cb["name"], sha)
            summary["reindexed"] += 1
        else:
            log.error("Reindex failed for %s", cb["name"])
            summary["failed"] += 1

    return summary
```

Also update the module docstring's usage examples to mention that clones are managed automatically (replace the top docstring's first paragraph with a one-line note: "Syncs each enabled codebase's managed clone, then reindexes those whose HEAD SHA changed.").

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_reindex.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/reindex_if_changed.py tests/test_reindex.py
git commit -m "refactor: reindex poller orchestrates managed clones (sync-then-compare)"
```

---

## Task 7: MCP server — discover_codebases tool + updated register/list

**Files:**
- Modify: `src/waverider/mcp_server.py:320-395` (`register_codebase`, `list_codebases`) and add `discover_codebases`
- Test: `tests/test_mcp_admin.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_admin.py` (after the existing import of tool functions; add the new imports to the existing import line from `mcp_server`):

```python
# Add discover_codebases / register_codebase / list_codebases to the existing
# `from waverider.mcp_server import (...)` block.


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


class TestDiscoverCodebases:
    def test_returns_summary_text(self):
        from waverider import mcp_server
        from waverider.github_discovery import RepoInfo
        from unittest.mock import patch
        repos = [RepoInfo("identity", "waveaccounting/identity", "main", "id", "python")]
        with patch.object(mcp_server, "_run_discovery", return_value={
            "discovered": 1, "new": 1, "existing": 0
        }) as run:
            out = mcp_server.discover_codebases(org="waveaccounting")
        run.assert_called_once()
        assert "discovered" in out.lower()
        assert "1" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_mcp_admin.py -k "Remote or SyncError or Discover" -v`
Expected: FAIL (`register_codebase` still requires a `path` arg; `discover_codebases` / `_run_discovery` not defined)

- [ ] **Step 3: Update `register_codebase`**

Replace `register_codebase` (lines ~320-367) in `src/waverider/mcp_server.py`:

```python
@mcp.tool()
def register_codebase(
    name: str,
    github_repo: str,
    description: str = "",
    language: str = "mixed",
    main_branch_name: str = "main",
    enabled: bool = True,
) -> str:
    """Register a remote codebase in the registry.

    The repo is cloned and indexed automatically by the next poll cycle
    (last_indexed_commit = NULL triggers a reindex). The local clone path is
    managed by WaveRider — do not pass a local path.

    Args:
        name: Unique identifier (e.g. "identity")
        github_repo: GitHub slug, e.g. "waveaccounting/identity" (required)
        description: Human-readable description
        language: Primary language (python, typescript, ruby, mixed)
        main_branch_name: Branch to track (default: main)
        enabled: Whether the poller should index it (default: True)
    """
    from waverider.database import DatabaseManager

    if not github_repo.strip():
        return "Error: github_repo is required (e.g. 'waveaccounting/identity')."

    db = DatabaseManager()
    try:
        cid = db.upsert_codebase_registration(
            name=name,
            path=None,
            description=description,
            language=language,
            github_repo=github_repo.strip(),
            main_branch_name=main_branch_name,
            enabled=enabled,
        )
        return (
            f"Registered '{name}' (id={cid}). "
            "Run `poetry run python scripts/reindex_if_changed.py --once` to clone & index it."
        )
    except Exception as exc:
        return f"Error: {exc}"
    finally:
        db.close()
```

- [ ] **Step 4: Update `list_codebases` to show github_repo + sync error**

Replace `list_codebases` (lines ~369-395):

```python
@mcp.tool()
def list_codebases() -> str:
    """List all registered codebases and their reindex status.

    Returns name, enabled flag, language, last indexed commit, GitHub repo,
    and any recorded sync error.
    """
    from waverider.database import DatabaseManager

    db = DatabaseManager()
    try:
        rows = db.list_codebases()
        if not rows:
            return "No codebases registered. Run: poetry run python scripts/discover_repos.py"

        header = (
            f"{'NAME':<22} {'ENABLED':<8} {'LANG':<10} "
            f"{'LAST COMMIT':<12} {'GITHUB REPO':<28} SYNC ERROR"
        )
        sep = "-" * 110
        lines = [header, sep]
        for r in rows:
            sha = (r.get("last_indexed_commit") or "never")[:8]
            enabled = "yes" if r["enabled"] else "no"
            repo = r.get("github_repo") or "-"
            err = (r.get("last_sync_error") or "").splitlines()
            err_text = err[0][:40] if err else ""
            lines.append(
                f"{r['name']:<22} {enabled:<8} {r['language']:<10} "
                f"{sha:<12} {repo:<28} {err_text}"
            )
        return "\n".join(lines)
    finally:
        db.close()
```

- [ ] **Step 5: Add `discover_codebases` tool + `_run_discovery` helper**

Add to `src/waverider/mcp_server.py`, immediately before `register_codebase`:

```python
def _run_discovery(org: str, dry_run: bool = False) -> dict:
    """Shared discovery runner — wraps scripts/discover_repos.run_discovery."""
    import importlib.util
    from pathlib import Path as _Path

    from waverider import config
    from waverider.database import DatabaseManager

    script = _Path(__file__).resolve().parent.parent.parent / "scripts" / "discover_repos.py"
    spec = importlib.util.spec_from_file_location("discover_repos", script)
    dmod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(dmod)  # type: ignore[union-attr]

    token = config.get_github_token()
    db = DatabaseManager()
    db.init_schema()
    try:
        return dmod.run_discovery(db, org=org, token=token, dry_run=dry_run)
    finally:
        db.close()


@mcp.tool()
def discover_codebases(org: str = "") -> str:
    """Discover repos in a GitHub org and register new ones (disabled).

    Newly discovered repos are added to the registry with enabled=False so an
    admin can choose which to index. Existing codebases are left untouched.
    Requires GITHUB_TOKEN to be set.

    Args:
        org: GitHub org to scan (default: WAVERIDER_GITHUB_ORG / "waveaccounting")
    """
    from waverider import config

    target_org = org.strip() or config.get_github_org()
    try:
        summary = _run_discovery(target_org, dry_run=False)
    except Exception as exc:
        return f"Error: {exc}"
    return (
        f"Discovery of '{target_org}' complete: "
        f"discovered={summary['discovered']}, new={summary['new']} (disabled), "
        f"existing={summary['existing']}. "
        "Enable repos with set_codebase_enabled(name, True)."
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `poetry run pytest tests/test_mcp_admin.py -v`
Expected: PASS (existing tests still pass; new Remote/SyncError/Discover tests pass)

- [ ] **Step 7: Commit**

```bash
git add src/waverider/mcp_server.py tests/test_mcp_admin.py
git commit -m "feat: discover_codebases MCP tool; remote-first register/list_codebases"
```

---

## Task 8: seed_default_repos script

**Files:**
- Create: `scripts/seed_default_repos.py`
- Test: `tests/test_seed_default_repos.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_seed_default_repos.py`:

```python
"""Tests for scripts/seed_default_repos.py (fake DB)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

_PROJECT_ROOT = Path(__file__).parent.parent
_spec = importlib.util.spec_from_file_location(
    "seed_default_repos", _PROJECT_ROOT / "scripts" / "seed_default_repos.py"
)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

DEFAULT_REPOS = _mod.DEFAULT_REPOS
seed_defaults = _mod.seed_defaults


def test_default_repos_list_is_the_common_services():
    assert DEFAULT_REPOS == [
        "identity", "reef", "api", "javascript-wave-api-client", "next-wave",
        "wave-messages", "lighthouse", "chunnelx", "nav", "tuktuk", "buoyant",
    ]


def test_enables_only_repos_present_in_registry():
    db = MagicMock()
    # Only identity and reef are registered (set_codebase_enabled returns True for those)
    db.set_codebase_enabled.side_effect = lambda name, enabled: name in {"identity", "reef"}
    enabled = seed_defaults(db)
    assert set(enabled) == {"identity", "reef"}
    # It attempted every default
    assert db.set_codebase_enabled.call_count == len(DEFAULT_REPOS)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_seed_default_repos.py -v`
Expected: FAIL (script doesn't exist)

- [ ] **Step 3: Implement `scripts/seed_default_repos.py`**

Create `scripts/seed_default_repos.py`:

```python
#!/usr/bin/env python3
"""Enable a curated set of common Wave services in the registry.

Run after discovery to turn on the everyday services. Users can enable their own
product repos afterward with set_codebase_enabled / the MCP tool.

Usage:
    poetry run python scripts/seed_default_repos.py
"""
from __future__ import annotations

import logging
import os
import sys
from typing import List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from waverider.database import DatabaseManager

log = logging.getLogger(__name__)

DEFAULT_REPOS: List[str] = [
    "identity",
    "reef",
    "api",
    "javascript-wave-api-client",
    "next-wave",
    "wave-messages",
    "lighthouse",
    "chunnelx",
    "nav",
    "tuktuk",
    "buoyant",
]


def seed_defaults(db: DatabaseManager) -> List[str]:
    """Enable each DEFAULT_REPOS entry that exists in the registry.

    Returns the list of names actually enabled (those present in the registry).
    """
    enabled: List[str] = []
    for name in DEFAULT_REPOS:
        if db.set_codebase_enabled(name, True):
            enabled.append(name)
            log.info("Enabled '%s'", name)
        else:
            log.warning("Skipped '%s' — not in registry (run discover_repos first)", name)
    return enabled


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    db = DatabaseManager()
    db.init_schema()
    try:
        enabled = seed_defaults(db)
        print(f"Enabled {len(enabled)} default repos: {', '.join(enabled) or '(none)'}")
        missing = [r for r in DEFAULT_REPOS if r not in enabled]
        if missing:
            print(f"Not found in registry (run discover_repos.py first): {', '.join(missing)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_seed_default_repos.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/seed_default_repos.py tests/test_seed_default_repos.py
git commit -m "feat: seed_default_repos to enable common Wave services"
```

---

## Task 9: Cleanup + docs

**Files:**
- Delete: `scripts/seed_registry.py`, `scripts/index_wave_repos.sh`
- Modify: `docs/CODEBASE_REGISTRY.md`, `AGENTS.md`

- [ ] **Step 1: Confirm nothing imports the deleted scripts**

Run: `grep -rn "seed_registry\|index_wave_repos" --include="*.py" --include="*.md" --include="*.sh" .`
Expected: only references inside the two files being deleted and in docs (which Step 3 updates). If a test references `seed_registry`, update or remove that reference.

- [ ] **Step 2: Delete the obsolete scripts**

```bash
git rm scripts/seed_registry.py scripts/index_wave_repos.sh
```

- [ ] **Step 3: Update docs**

In `docs/CODEBASE_REGISTRY.md`, replace any "seed the registry" / local-path workflow section with the remote-clone workflow:

```markdown
## Remote-clone model

Codebases are identified by their GitHub repo (`github_repo`, e.g.
`waveaccounting/identity`). WaveRider clones each enabled repo into a managed
location (`WAVERIDER_REPO_ROOT`, default `~/.waverider/repos/<name>`); the `path`
column is managed automatically and is `NULL` until the first successful clone.

### Workflow

1. **Discover** org repos (registers them disabled):
   `poetry run python scripts/discover_repos.py`
2. **Enable** the common services:
   `poetry run python scripts/seed_default_repos.py`
   (or enable individually via the `set_codebase_enabled` MCP tool / your own product repos)
3. **Index**: `poetry run python scripts/reindex_if_changed.py --once`
   (clones/fetches each enabled repo, reindexes those whose HEAD changed)

### Environment

| Var | Default | Purpose |
|-----|---------|---------|
| `GITHUB_TOKEN` | (required) | PAT for clone + discovery |
| `WAVERIDER_REPO_ROOT` | `~/.waverider/repos` | Managed clone root |
| `WAVERIDER_GITHUB_ORG` | `waveaccounting` | Org to discover |

Sync failures are recorded on the codebase row (`last_sync_error`,
`last_sync_error_at`) and shown by `list_codebases`; they auto-retry next cycle.
```

In `AGENTS.md`, add `discover_codebases` to the admin-tools listing:

```markdown
### `discover_codebases`
**Purpose**: Discover repos in a GitHub org and register new ones (disabled).
**Parameters**: `org` (default: `waveaccounting`). Requires `GITHUB_TOKEN`.
```

- [ ] **Step 4: Run the full test suite**

Run: `poetry run pytest -v`
Expected: PASS (DB-dependent tests pass when `docker compose up -d` is running; otherwise skipped). No failures.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove local-path seeding scripts; document remote-clone model"
```

---

## Self-Review

**Spec coverage:**
- Remote clone source → Task 3 (`repo_manager`). ✓
- Org discovery (disabled default) → Tasks 4–5. ✓
- Unchanged CocoIndex path → Task 6 keeps `build_index.py` invocation, only swaps the path source. ✓
- Clean module boundary → `repo_manager` + `github_discovery`. ✓
- `github_repo` source of truth, `path` managed/nullable → Tasks 2, 7. ✓
- Sync-error DB logging (user note 1) → Task 2 columns + `record_sync_error`/clear-on-commit, Task 6 wiring, Task 7 surfacing. ✓
- `DEFAULT_REPOS` exact list (user note 2) → Task 8. ✓
- PAT auth, token never on disk/CLI → Task 3 `_git` credential helper. ✓
- Sync-then-compare ordering → Task 6 `poll_once`. ✓
- Config env vars → Task 1. ✓
- MCP `discover_codebases` + updated `register`/`list` → Task 7. ✓
- Remove `seed_registry.py` / `index_wave_repos.sh`; docs → Task 9. ✓
- YAGNI cuts (GitHub App, webhooks, concurrent/sparse clones, delete-on-deregister) → not implemented, by design. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases" — every code step has full code. ✓

**Type consistency:**
- `ensure_current(github_repo, name, branch, *, token, repo_root) -> str` and `local_path(name, repo_root)` used identically in Task 3 tests and Task 6 orchestrator. ✓
- `run_discovery(db, org, token, dry_run)` signature matches Task 5 tests, Task 7 `_run_discovery`. ✓
- `RepoInfo(name, github_repo, default_branch, description, language)` consistent across Tasks 4, 5, 7. ✓
- `upsert_codebase_registration(..., enabled=...)` with `path` optional used consistently in Tasks 2, 5, 7. ✓
- `record_sync_error(name, message)` / `update_codebase_path(name, path)` consistent in Tasks 2, 6. ✓
- `run_reindex(codebase, local_path, project_root, dry_run)` consistent in Task 6 impl + tests. ✓
