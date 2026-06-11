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
