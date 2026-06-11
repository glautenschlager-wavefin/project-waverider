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
