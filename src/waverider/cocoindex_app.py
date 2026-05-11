"""CocoIndex-based incremental indexer for Waverider — Phase 2.

This module defines the CocoIndex App that replaces the manual indexer.
CocoIndex handles all incremental logic: it detects which files changed since
the last run and only re-extracts snippets + re-embeds those files.

Key design:
- One App per codebase, named "waverider_<codebase_name>".
- Source: localfs.walk_dir over all supported file extensions.
- Memoised per-file function: tree-sitter extraction + Ollama embedding.
- Target: Postgres table "coco_snippets" (CocoIndex-managed) with HNSW and
  tsvector indexes declared at setup time.
- CocoIndex state (memoisation cache) is stored in the database pointed to
  by COCOINDEX_DB_URL (defaults to the same ParadeDB via DATABASE_URL).
"""

from __future__ import annotations

import logging
import os
import pathlib
from dataclasses import dataclass
from typing import Annotated, AsyncIterator

import asyncpg
import httpx
import numpy as np
from numpy.typing import NDArray

import cocoindex as coco
from cocoindex.connectors import localfs, postgres
from cocoindex.resources.file import PatternFilePathMatcher
from cocoindex.resources.id import IdGenerator

from waverider.indexer import CodeSnippetInfo, CodebaseIndexer
from waverider.treesitter_parser import extract_snippets

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL: str = os.getenv(
    "DATABASE_URL", "postgresql://waverider:changeme@localhost:5432/waverider"
)
OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL: str = os.getenv("OLLAMA_MODEL", "nomic-embed-text")
EMBED_DIMS: int = 768

# CocoIndex-managed table name for code snippets.
TABLE_NAME = "coco_snippets"

# ---------------------------------------------------------------------------
# Shared resource context keys
# ---------------------------------------------------------------------------

PG_DB = coco.ContextKey[asyncpg.Pool]("waverider_pg_db")
EMBEDDER = coco.ContextKey["OllamaEmbedder"]("waverider_ollama_embedder", detect_change=True)

# ---------------------------------------------------------------------------
# File discovery configuration
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS: dict[str, str] = CodebaseIndexer.SUPPORTED_EXTENSIONS
INCLUDED_PATTERNS = [f"**/*{ext}" for ext in SUPPORTED_EXTENSIONS]
EXCLUDED_PATTERNS = [
    ".*/**",
    "__pycache__/**",
    "node_modules/**",
    ".venv/**",
    "venv/**",
    "dist/**",
    "build/**",
    "*.egg-info/**",
]


# ---------------------------------------------------------------------------
# Ollama embedder (wraps Ollama REST API, compatible with CocoIndex)
# ---------------------------------------------------------------------------


class OllamaEmbedder:
    """Async embedder that calls Ollama's REST API.

    Implements ``get_sentence_embedding_dimension()`` so CocoIndex can infer
    the vector column size when building the Postgres table schema.
    """

    def __init__(self, model: str = EMBED_MODEL, base_url: str = OLLAMA_URL) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=60.0)

    def get_sentence_embedding_dimension(self) -> int:
        """Return the embedding dimension (768 for nomic-embed-text)."""
        return EMBED_DIMS

    async def embed(self, text: str) -> NDArray[np.float32]:
        """Generate a 768-dim embedding vector for *text* via Ollama."""
        response = await self._client.post(
            f"{self.base_url}/api/embeddings",
            json={"model": self.model, "prompt": text},
        )
        response.raise_for_status()
        return np.array(response.json()["embedding"], dtype=np.float32)

    async def aclose(self) -> None:
        await self._client.aclose()


# ---------------------------------------------------------------------------
# Target dataclass — one row per code snippet
# ---------------------------------------------------------------------------


@dataclass
class CodeSnippetRow:
    """One indexed code snippet stored in the CocoIndex-managed Postgres table."""

    id: int
    codebase_name: str
    file_path: str       # relative path within the codebase
    snippet_type: str    # "function", "class", "file", etc.
    name: str
    content: str
    start_line: int
    end_line: int
    language: str
    embedding: Annotated[NDArray, EMBEDDER]


# ---------------------------------------------------------------------------
# Lifespan: set up shared resources once per process
# ---------------------------------------------------------------------------


@coco.lifespan
async def coco_lifespan(builder: coco.EnvironmentBuilder) -> AsyncIterator[None]:
    embedder = OllamaEmbedder()
    async with await asyncpg.create_pool(DATABASE_URL) as pool:
        builder.provide(PG_DB, pool)
        builder.provide(EMBEDDER, embedder)
        yield
    await embedder.aclose()


# ---------------------------------------------------------------------------
# Processing function — memoised per file
# ---------------------------------------------------------------------------


@coco.fn(memo=True)
async def process_file(
    file: localfs.File,
    codebase_name: str,
    table: postgres.TableTarget[CodeSnippetRow],
) -> None:
    """Extract code snippets from a source file and write embeddings to Postgres.

    Memoised with ``memo=True``: if the file content and this function's code
    are both unchanged since the last run, CocoIndex skips execution entirely
    and the previous rows remain in the target table untouched.
    """
    suffix = file.file_path.path.suffix
    language = SUPPORTED_EXTENSIONS.get(suffix)
    if not language:
        return

    try:
        content = await file.read_text()
    except (UnicodeDecodeError, OSError) as exc:
        log.warning("Could not read %s: %s", file.file_path.path, exc)
        return

    # Extract snippets with tree-sitter; fall back to whole-file snippet on error.
    try:
        snippets = extract_snippets(content, language, file.file_path.path)
    except Exception as exc:
        log.warning("Snippet extraction failed for %s: %s", file.file_path.path, exc)
        snippets = [
            CodeSnippetInfo(
                snippet_type="file",
                name=file.file_path.path.stem,
                content=content,
                start_line=1,
                end_line=len(content.splitlines()),
                language=language,
            )
        ]

    embedder = coco.use_context(EMBEDDER)
    id_gen = IdGenerator()
    relative_path = str(file.file_path.path)

    for snippet in snippets:
        try:
            embedding = await embedder.embed(snippet.content)
        except Exception as exc:
            log.warning(
                "Embedding failed for snippet '%s' in %s: %s",
                snippet.name, relative_path, exc,
            )
            continue

        table.declare_row(
            row=CodeSnippetRow(
                id=await id_gen.next_id(snippet.content),
                codebase_name=codebase_name,
                file_path=relative_path,
                snippet_type=snippet.snippet_type,
                name=snippet.name,
                content=snippet.content,
                start_line=snippet.start_line,
                end_line=snippet.end_line,
                language=snippet.language,
                embedding=embedding,
            )
        )


# ---------------------------------------------------------------------------
# App main — declares target and walks source directory
# ---------------------------------------------------------------------------


@coco.fn
async def app_main(sourcedir: pathlib.Path, codebase_name: str) -> None:
    """CocoIndex main function: mount the Postgres target and walk the source directory."""
    target_table = await postgres.mount_table_target(
        PG_DB,
        table_name=TABLE_NAME,
        table_schema=await postgres.TableSchema.from_class(
            CodeSnippetRow, primary_key=["id"]
        ),
    )

    # HNSW index for O(log n) cosine similarity search.
    target_table.declare_vector_index(column="embedding")

    # GIN index on tsvector for keyword (BM25-style) search via the fallback path.
    target_table.declare_sql_command_attachment(
        name="content_gin_idx",
        setup_sql=(
            f"CREATE INDEX IF NOT EXISTS {TABLE_NAME}_content_gin "
            f"ON {TABLE_NAME} USING GIN (to_tsvector('english', content))"
        ),
        teardown_sql=f"DROP INDEX IF EXISTS {TABLE_NAME}_content_gin",
    )

    files = localfs.walk_dir(
        sourcedir,
        recursive=True,
        path_matcher=PatternFilePathMatcher(
            included_patterns=INCLUDED_PATTERNS,
            excluded_patterns=EXCLUDED_PATTERNS,
        ),
    )

    await coco.mount_each(process_file, files.items(), codebase_name, target_table)


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def make_app(codebase_name: str, sourcedir: pathlib.Path) -> coco.App:
    """Create a CocoIndex App for incremental indexing of *sourcedir*.

    Call ``app.update()`` inside a ``coco.runtime()`` context to run one
    incremental pass, or ``app.update_blocking(live=True)`` for continuous
    file-watching mode.

    Example::

        import asyncio, cocoindex as coco
        from waverider.cocoindex_app import make_app

        app = make_app("myproject", pathlib.Path("/path/to/myproject"))
        async def run():
            async with coco.runtime():
                await coco.show_progress(app.update())
        asyncio.run(run())
    """
    return coco.App(
        coco.AppConfig(name=f"waverider_{codebase_name}"),
        app_main,
        sourcedir=sourcedir,
        codebase_name=codebase_name,
    )
