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
