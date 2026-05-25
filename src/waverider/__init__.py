"""
Waverider: MCP server for building vector indices and knowledge graphs over codebases.
"""

__version__ = "0.1.0"
__author__ = "Wave"

from waverider.config import SearchBackend, SearchConfig, get_config
from waverider.database import DatabaseManager
from waverider.neo4j_graph import Neo4jGraphManager

__all__ = [
    "DatabaseManager",
    "Neo4jGraphManager",
    "SearchBackend",
    "SearchConfig",
    "get_config",
]
