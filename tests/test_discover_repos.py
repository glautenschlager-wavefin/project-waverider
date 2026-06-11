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
