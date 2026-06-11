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
