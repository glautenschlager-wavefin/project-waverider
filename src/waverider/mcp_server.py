"""MCP server for Waverider - exposes codebase knowledge graph as MCP tools."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from waverider.config import get_config


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
    """Search the codebase for functions, classes, and files matching a query.

    Uses the configured search backend (Postgres or Neo4j) to find symbols.
    Supports side-by-side validation during migration via WAVERIDER_SEARCH_BACKEND.

    Args:
        query: Keyword or name to search for (e.g. function name, class name, file name)
        codebase_name: Name of the indexed codebase (default: waverider)
        limit: Maximum number of results to return

    Environment:
        WAVERIDER_SEARCH_BACKEND: 'postgres' (default) or 'neo4j'
        WAVERIDER_FALLBACK_ENABLED: 'true' (default) or 'false'
    """
    config = get_config()
    
    try:
        if config.is_postgres():
            return _search_codebase_postgres(query, codebase_name, limit, config)
        else:  # neo4j
            return _search_codebase_neo4j(query, codebase_name, limit, config)
    except Exception as e:
        return f"Search error: {e}"


def _search_codebase_postgres(query: str, codebase_name: str, limit: int, config) -> str:
    """Search using Postgres backend."""
    from waverider.database import DatabaseManager

    db = DatabaseManager()
    
    # Get codebase metadata from Postgres
    codebase_meta = db.get_codebase(codebase_name)
    if not codebase_meta:
        db.close()
        return (
            f"Codebase '{codebase_name}' not found in Postgres index. "
            "Run: poetry run python scripts/build_index.py "
            f"--codebase-path /path/to/{codebase_name} --index-name {codebase_name}"
        )
    
    # Try Postgres-backed symbol search (prioritizes file/function/class name matches)
    results = db.search_symbols_by_name(query=query, codebase_id=codebase_meta["id"], limit=limit)
    db.close()
    
    # If Postgres search succeeds, format and return
    if results:
        lines = [f"Found {len(results)} symbol(s) for '{query}' in {codebase_name}:"]
        for r in results:
            match_type = r.get("match_type", "?")
            lines.append(
                f"\n  [{match_type.upper()}] {r['file_path']} ({r['snippet_type']}: {r['name']})"
            )
            lines.append(f"      Lines {r['start_line']}–{r['end_line']}")
            snippet_preview = r["content"][:150].replace("\n", "\n      ")
            if len(r["content"]) > 150:
                snippet_preview += "..."
            lines.append(f"      {snippet_preview}")
        return "\n".join(lines)
    
    # Fallback to Neo4j if enabled
    if config.fallback_enabled:
        return _search_codebase_neo4j(query, codebase_name, limit, config)
    
    return f"No results found for '{query}' in {codebase_name} (Postgres backend, fallback disabled)."


def _search_codebase_neo4j(query: str, codebase_name: str, limit: int, config) -> str:
    """Search using Neo4j backend."""
    from waverider.neo4j_graph import Neo4jGraphManager

    neo4j = Neo4jGraphManager()
    
    try:
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
    finally:
        neo4j.close()
        
    if not results:
        backend_label = "Neo4j" if config.is_neo4j() else "Neo4j (fallback)"
        return f"No results found for '{query}' in codebase '{codebase_name}' ({backend_label})."
    
    lines = [f"Found {len(results)} file(s) matching '{query}' (Neo4j):"]
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


@mcp.tool()
def retrieve_code(query: str, codebase_name: str = "waverider", limit: int = 5) -> str:
    """Semantically retrieve the most relevant code snippets for a natural-language query.

    Uses hybrid search (vector embeddings + keyword/BM25 matching) with Reciprocal Rank Fusion.
    This preserves lexical search quality while adding semantic understanding.

    Requires the codebase to be indexed with `scripts/build_index.py`.

    Args:
        query: Natural-language description of what you're looking for
        codebase_name: Name of the indexed codebase (default: waverider)
        limit: Number of snippets to return (default: 5)

    Environment:
        WAVERIDER_SEARCH_BACKEND: 'postgres' (default) or 'neo4j' (Neo4j does not support semantic search)
        WAVERIDER_SEARCH_HYBRID: 'true' (default) or 'false' (disables keyword fusion)
    """
    config = get_config()
    
    if config.is_neo4j():
        return (
            "Semantic search (retrieve_code) requires Postgres backend with pgvector. "
            "Set WAVERIDER_SEARCH_BACKEND=postgres or use search_codebase for Neo4j-only mode."
        )
    
    try:
        import httpx
        from waverider.database import DatabaseManager
        from waverider.fusion import rrf_fuse

        db = DatabaseManager()

        # Get codebase and check that it has been indexed.
        codebase = db.get_codebase(codebase_name)
        if not codebase:
            db.close()
            return (
                f"Codebase '{codebase_name}' not found. "
                "Run: poetry run python scripts/build_index.py "
                f"--codebase-path /path/to/{codebase_name} --index-name {codebase_name}"
            )
        
        codebase_id = codebase["id"]
        stats = db.get_statistics(codebase_id)
        if stats.get("coco_row_count", 0) == 0:
            db.close()
            return (
                f"Codebase '{codebase_name}' has not been indexed yet. "
                "Run: poetry run python scripts/build_index.py "
                f"--codebase-path /path/to/{codebase_name} --index-name {codebase_name}"
            )

        # Embed the query using Ollama.
        ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        embed_model = os.getenv("OLLAMA_MODEL", "nomic-embed-text")
        query_vec: list[float] | None = None
        try:
            resp = httpx.post(
                f"{ollama_url}/api/embeddings",
                json={"model": embed_model, "prompt": query},
                timeout=30.0,
            )
            resp.raise_for_status()
            query_vec = resp.json()["embedding"]
        except Exception as emb_err:
            db.close()
            return (
                f"Could not generate query embedding (is Ollama running?): {emb_err}\n"
                "Start Ollama with: ollama serve"
            )

        # Hybrid search on coco_snippets (CocoIndex schema).
        vector_results: list = []
        keyword_results: list = []

        vector_results = db.search_coco_embeddings(
            query_embedding=query_vec,
            codebase_name=codebase_name,
            limit=limit * 2,
        )
        if config.hybrid_search:
            keyword_results = db.search_coco_bm25(
                query=query,
                codebase_name=codebase_name,
                limit=limit * 2,
            )

        # Fuse results if both vector and keyword results exist
        if config.hybrid_search and keyword_results:
            fused = rrf_fuse(
                {"vector": vector_results, "keyword": keyword_results},
                id_key="id",
                limit=limit,
            )
            results = fused if fused else vector_results[:limit]
        else:
            results = vector_results[:limit]

        db.close()

        if not results:
            return f"No snippets found for '{query}'. The index may be empty — run build_index.py."

        lines = [f"Top {len(results)} snippet(s) for '{query}':"]
        for r in results:
            score = r.get("rrf_score") or r.get("similarity", "—")
            lines.append(
                f"\n--- {r['file_path']} ({r['snippet_type']}: {r['name']}) score={score} ---"
            )
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


@mcp.tool()
def get_config() -> str:
    """Return the current Waverider search configuration.
    
    Returns information about:
    - Active search backend (postgres or neo4j)
    - Hybrid search mode (vector + keyword fusion)
    - Fallback behavior
    """
    from waverider.config import get_config as get_waverider_config
    
    config = get_waverider_config()
    return (
        f"Waverider Configuration:\n"
        f"  Backend: {config.backend.value}\n"
        f"  Hybrid Search: {'enabled (vector + keyword)' if config.hybrid_search else 'disabled (vector only)'}\n"
        f"  Fallback Enabled: {config.fallback_enabled}\n"
        f"\n"
        f"Environment Variables:\n"
        f"  WAVERIDER_SEARCH_BACKEND={os.getenv('WAVERIDER_SEARCH_BACKEND', 'not set (default: postgres)')}\n"
        f"  WAVERIDER_SEARCH_HYBRID={os.getenv('WAVERIDER_SEARCH_HYBRID', 'not set (default: true)')}\n"
        f"  WAVERIDER_FALLBACK_ENABLED={os.getenv('WAVERIDER_FALLBACK_ENABLED', 'not set (default: true)')}"
    )


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mcp.run(transport=transport)
