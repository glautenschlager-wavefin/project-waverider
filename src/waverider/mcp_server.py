"""MCP server for Waverider - exposes codebase knowledge graph as MCP tools."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP


def _load_project_env() -> None:
    current_file = Path(__file__).resolve()
    project_root = current_file.parents[2]
    load_dotenv(project_root / ".env", override=False)
    load_dotenv(override=False)


_load_project_env()

mcp = FastMCP(
    "waverider",
    host=os.getenv("MCP_HOST", "127.0.0.1"),
    port=int(os.getenv("MCP_PORT", "8000")),
)


@mcp.tool()
def search_codebase(query: str, codebase_name: str = "waverider", limit: int = 10) -> str:
    """Search the Waverider codebase knowledge graph for functions, classes, and files matching a query.

    Args:
        query: Keyword or name to search for (e.g. function name, class name, file name)
        codebase_name: Name of the indexed codebase (default: waverider)
        limit: Maximum number of results to return
    """
    try:
        from waverider.neo4j_graph import Neo4jGraphManager

        neo4j = Neo4jGraphManager()
        results = neo4j.query(
            """
            MATCH (cb:Codebase {name: $codebase_name})-[:CONTAINS_FILE]->(f:CodeFile)
            WITH f
            OPTIONAL MATCH (f)-[:CONTAINS_FUNCTION]->(fn:Function)
            WITH f, collect(DISTINCT fn) AS funcs
            OPTIONAL MATCH (f)-[:CONTAINS_CLASS]->(cl:Class)
            WITH f, funcs, collect(DISTINCT cl) AS classes
            WHERE toLower(f.path) CONTAINS toLower($term)
               OR any(fn IN funcs WHERE toLower(fn.name) CONTAINS toLower($term))
               OR any(cl IN classes WHERE toLower(cl.name) CONTAINS toLower($term))
            RETURN
              f.path AS file,
              [fn IN funcs WHERE toLower(fn.name) CONTAINS toLower($term) | {name: fn.name, signature: fn.signature, docstring: fn.docstring}] AS matched_functions,
              [cl IN classes WHERE toLower(cl.name) CONTAINS toLower($term) | {name: cl.name, docstring: cl.docstring}] AS matched_classes,
              [fn IN funcs | fn.name] AS all_functions,
              [cl IN classes | cl.name] AS all_classes
            LIMIT $limit
            """,
            term=query,
            limit=limit,
            codebase_name=codebase_name,
        )
        neo4j.close()
        if not results:
            return f"No results found for '{query}' in codebase '{codebase_name}'."
        lines = [f"Found {len(results)} file(s) matching '{query}':"]
        for r in results:
            lines.append(f"\n  File: {r['file']}")
            if r["matched_functions"]:
                lines.append("    Matching functions:")
                for fn in r["matched_functions"]:
                    sig = fn.get("signature") or fn["name"]
                    lines.append(f"      - {sig}")
                    doc = fn.get("docstring")
                    if doc:
                        lines.append(f"        {doc}")
            if r["matched_classes"]:
                lines.append("    Matching classes:")
                for cl in r["matched_classes"]:
                    lines.append(f"      - {cl['name']}")
                    doc = cl.get("docstring")
                    if doc:
                        lines.append(f"        {doc}")
            lines.append(f"    All functions: {', '.join(r['all_functions']) or '(none)'}")
            lines.append(f"    All classes:   {', '.join(r['all_classes']) or '(none)'}")
        return "\n".join(lines)
    except Exception as e:
        return f"Search error: {e}"


@mcp.tool()
def retrieve_code(query: str, codebase_name: str = "waverider", limit: int = 5) -> str:
    """Semantically retrieve the most relevant code snippets for a natural-language query.

    Uses vector embeddings to find snippets by meaning, not just keyword matching.
    Requires the codebase to be indexed with `scripts/build_index.py`.

    Args:
        query: Natural-language description of what you're looking for
        codebase_name: Name of the indexed codebase (default: waverider)
        limit: Number of snippets to return (default: 5)
    """
    try:
        from waverider.database import DatabaseManager
        from waverider.embeddings import get_embedding_provider

        db = DatabaseManager(db_path="data/waverider.db")
        codebase = db.get_codebase(codebase_name)
        if not codebase:
            return (
                f"Codebase '{codebase_name}' not found in vector index. "
                "Run: poetry run python scripts/build_index.py "
                "--codebase-path ./src --index-name waverider"
            )

        # Auto-detect embedding provider from index metadata to match what was used at index time.
        metadata_path = Path("indices") / f"{codebase_name}_metadata.json"
        provider_name = "ollama"
        model_name = "nomic-embed-text"
        if metadata_path.exists():
            with open(metadata_path) as mf:
                meta = json.load(mf)
            provider_name = meta.get("embedding_provider", "ollama")
            model_name = meta.get("embedding_model", "nomic-embed-text")

        provider_note = ""
        try:
            embeddings = get_embedding_provider(provider=provider_name, model=model_name)
            query_vec = embeddings.embed(query)
        except Exception:
            embeddings = get_embedding_provider(provider="mock")
            query_vec = embeddings.embed(query)
            provider_note = " [mock embeddings — install and start Ollama for real semantic search]"

        results = db.search_embeddings(
            query_embedding=query_vec,
            codebase_id=codebase["id"],
            limit=limit,
        )

        if not results:
            return f"No snippets found for '{query}'. The index may be empty — run build_index.py."

        provider_note = " [mock embeddings — install and start Ollama for real semantic search]" if provider_note else ""
        lines = [f"Top {len(results)} snippet(s) for '{query}'{provider_note}:"]
        for r in results:
            lines.append(f"\n--- {r['file_path']} ({r['snippet_type']}: {r['name']}) similarity={r['similarity']} ---")
            lines.append(r["content"])
        return "\n".join(lines)

    except Exception as e:
        return f"Retrieval error: {e}"


@mcp.tool()
def neo4j_status() -> str:
    """Check Neo4j connectivity and return basic graph statistics."""
    try:
        from waverider.neo4j_graph import Neo4jGraphManager

        neo4j = Neo4jGraphManager()
        result = neo4j.query("MATCH (n) RETURN COUNT(n) AS node_count")
        count = result[0]["node_count"] if result else 0
        neo4j.close()
        return f"Neo4j connected. Total nodes in graph: {count}"
    except Exception as e:
        return f"Neo4j unavailable: {e}"


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
