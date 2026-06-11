"""Configuration management for Waverider search backends.

Supports switching between Postgres (pgVector + BM25) and Neo4j backends
for keyword and semantic search. Enables side-by-side validation during migration.
"""

import os
from enum import Enum
from pathlib import Path
from typing import Literal

_BACKEND_TYPE = Literal["postgres", "neo4j"]


class SearchBackend(str, Enum):
    """Supported search backends."""

    POSTGRES = "postgres"
    NEO4J = "neo4j"


class SearchConfig:
    """Configuration for search backend and behavior.

    Environment variables:
    - WAVERIDER_SEARCH_BACKEND: 'postgres' or 'neo4j' (default: 'postgres')
    - WAVERIDER_SEARCH_HYBRID: 'true' or 'false' (default: 'true')
      Enables hybrid search (vector + keyword fusion) when backend supports it.
    - WAVERIDER_FALLBACK_ENABLED: 'true' or 'false' (default: 'true')
      Allow fallback to secondary backend if primary search fails.
    """

    def __init__(self):
        """Initialize search configuration from environment."""
        backend_str = os.getenv("WAVERIDER_SEARCH_BACKEND", "postgres").lower()
        if backend_str not in [b.value for b in SearchBackend]:
            raise ValueError(
                f"Invalid WAVERIDER_SEARCH_BACKEND: {backend_str}. "
                f"Must be one of: {', '.join(b.value for b in SearchBackend)}"
            )
        self.backend: SearchBackend = SearchBackend(backend_str)

        self.hybrid_search = (
            os.getenv("WAVERIDER_SEARCH_HYBRID", "true").lower() == "true"
        )
        self.fallback_enabled = (
            os.getenv("WAVERIDER_FALLBACK_ENABLED", "true").lower() == "true"
        )

    def is_postgres(self) -> bool:
        """Check if Postgres backend is active."""
        return self.backend == SearchBackend.POSTGRES

    def is_neo4j(self) -> bool:
        """Check if Neo4j backend is active."""
        return self.backend == SearchBackend.NEO4J

    def get_backend(self) -> SearchBackend:
        """Return the configured backend."""
        return self.backend

    def __repr__(self) -> str:
        """Return string representation of config."""
        return (
            f"SearchConfig(backend={self.backend.value}, "
            f"hybrid={self.hybrid_search}, fallback={self.fallback_enabled})"
        )


# Global config instance
_CONFIG: SearchConfig | None = None


def get_config() -> SearchConfig:
    """Get or initialize the global search configuration.

    Returns:
        SearchConfig instance with current settings.
    """
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = SearchConfig()
    return _CONFIG


def reset_config() -> None:
    """Reset the global config instance. Mainly for testing."""
    global _CONFIG
    _CONFIG = None


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
