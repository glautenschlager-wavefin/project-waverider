"""
SQLite database management for Waverider.
"""

import sqlite3
from pathlib import Path
from typing import Any, List, Optional, Tuple, Dict
from datetime import datetime
import json


class DatabaseManager:
    """Manages SQLite database for storing codebases, embeddings, and indices."""

    def __init__(self, db_path: str = "data/waverider.db"):
        """Initialize database manager.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        """Create and return database connection."""
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        """Initialize database schema with all required tables."""
        conn = self.connect()
        cursor = conn.cursor()

        # Codebase metadata table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS codebase_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                path TEXT NOT NULL,
                description TEXT,
                language TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Source files table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS source_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codebase_id INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                content_hash TEXT,
                file_size INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (codebase_id) REFERENCES codebase_metadata(id),
                UNIQUE(codebase_id, file_path)
            )
        """)

        # Code snippets table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS code_snippets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER NOT NULL,
                snippet_type TEXT NOT NULL,
                name TEXT,
                start_line INTEGER,
                end_line INTEGER,
                content TEXT NOT NULL,
                language TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (file_id) REFERENCES source_files(id)
            )
        """)

        # Embeddings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snippet_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                embedding_vector TEXT NOT NULL,
                embedding_dimensions INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (snippet_id) REFERENCES code_snippets(id),
                UNIQUE(snippet_id, model)
            )
        """)

        # Indices table (tracks index build history)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS indices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                index_name TEXT UNIQUE NOT NULL,
                codebase_id INTEGER NOT NULL,
                model TEXT NOT NULL,
                total_snippets INTEGER,
                total_embeddings INTEGER,
                status TEXT,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                metadata TEXT,
                FOREIGN KEY (codebase_id) REFERENCES codebase_metadata(id)
            )
        """)

        # Create indices for faster queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_files_codebase ON source_files(codebase_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snippets_file ON code_snippets(file_id)")
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_embeddings_snippet ON embeddings(snippet_id)"
        )

        conn.commit()
        conn.close()

    def add_codebase(
        self, name: str, path: str, description: str = "", language: str = "mixed"
    ) -> int:
        """Add a new codebase to track.

        Args:
            name: Unique codebase identifier
            path: Full path to codebase
            description: Optional description
            language: Programming language(s)

        Returns:
            Codebase ID
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO codebase_metadata (name, path, description, language)
            VALUES (?, ?, ?, ?)
        """,
            (name, path, description, language),
        )

        codebase_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return codebase_id

    def add_source_file(
        self, codebase_id: int, file_path: str, relative_path: str, content_hash: str
    ) -> int:
        """Add a source file to the database.

        Args:
            codebase_id: ID of the parent codebase
            file_path: Full file path
            relative_path: Path relative to codebase root
            content_hash: Hash of file content

        Returns:
            File ID
        """
        conn = self.connect()
        cursor = conn.cursor()

        file_size = Path(file_path).stat().st_size if Path(file_path).exists() else 0

        cursor.execute(
            """
            INSERT INTO source_files (codebase_id, file_path, relative_path, content_hash, file_size)
            VALUES (?, ?, ?, ?, ?)
        """,
            (codebase_id, file_path, relative_path, content_hash, file_size),
        )

        file_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return file_id

    def add_code_snippet(
        self,
        file_id: int,
        snippet_type: str,
        content: str,
        name: str = "",
        start_line: int = 0,
        end_line: int = 0,
        language: str = "python",
    ) -> int:
        """Add a code snippet.

        Args:
            file_id: ID of parent source file
            snippet_type: Type of snippet (function, class, import, etc.)
            content: The actual code content
            name: Optional name/identifier
            start_line: Starting line number
            end_line: Ending line number
            language: Programming language

        Returns:
            Snippet ID
        """
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO code_snippets (file_id, snippet_type, name, start_line, end_line, content, language)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (file_id, snippet_type, name, start_line, end_line, content, language),
        )

        snippet_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return snippet_id

    def add_embedding(
        self, snippet_id: int, embedding: List[float], model: str = "text-embedding-3-small"
    ) -> int:
        """Store an embedding vector for a code snippet.

        Args:
            snippet_id: ID of the code snippet
            embedding: List of floats representing the embedding
            model: Model used to generate the embedding

        Returns:
            Embedding ID
        """
        conn = self.connect()
        cursor = conn.cursor()

        # Store embedding as JSON string
        embedding_str = json.dumps(embedding)
        dimensions = len(embedding)

        cursor.execute(
            """
            INSERT INTO embeddings (snippet_id, model, embedding_vector, embedding_dimensions)
            VALUES (?, ?, ?, ?)
        """,
            (snippet_id, model, embedding_str, dimensions),
        )

        embedding_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return embedding_id

    def get_codebase(self, name: str) -> Optional[Dict[str, Any]]:
        """Get codebase by name."""
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM codebase_metadata WHERE name = ?", (name,))
        row = cursor.fetchone()
        conn.close()

        return dict(row) if row else None

    def list_codebases(self) -> List[Dict[str, Any]]:
        """List all tracked codebases."""
        conn = self.connect()
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM codebase_metadata ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]

    def search_embeddings(
        self,
        query_embedding: List[float],
        codebase_id: int,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> List[Dict[str, Any]]:
        """Search for similar code snippets using cosine similarity.

        Note: This is a placeholder. For production, use specialized vector DB or
        implement approximate nearest neighbors.

        Args:
            query_embedding: Query vector
            codebase_id: Filter by codebase
            limit: Number of results
            threshold: Similarity threshold

        Returns:
            List of similar snippets
        """
        conn = self.connect()
        cursor = conn.cursor()

        # This is a simplified search - in production, use FAISS, Pinecone, etc.
        cursor.execute(
            """
            SELECT cs.id, cs.name, cs.snippet_type, cs.content, e.embedding_vector
            FROM code_snippets cs
            JOIN source_files sf ON cs.file_id = sf.id
            JOIN embeddings e ON cs.id = e.snippet_id
            WHERE sf.codebase_id = ?
            LIMIT ?
        """,
            (codebase_id, limit),
        )

        results = cursor.fetchall()
        conn.close()

        return [dict(row) for row in results]

    def get_statistics(self, codebase_id: int) -> Dict[str, Any]:
        """Get statistics for a codebase."""
        conn = self.connect()
        cursor = conn.cursor()

        # Total files
        cursor.execute("SELECT COUNT(*) FROM source_files WHERE codebase_id = ?", (codebase_id,))
        total_files = cursor.fetchone()[0]

        # Total snippets
        cursor.execute(
            """
            SELECT COUNT(*) FROM code_snippets cs
            JOIN source_files sf ON cs.file_id = sf.id
            WHERE sf.codebase_id = ?
        """,
            (codebase_id,),
        )
        total_snippets = cursor.fetchone()[0]

        # Total embeddings
        cursor.execute(
            """
            SELECT COUNT(*) FROM embeddings e
            JOIN code_snippets cs ON e.snippet_id = cs.id
            JOIN source_files sf ON cs.file_id = sf.id
            WHERE sf.codebase_id = ?
        """,
            (codebase_id,),
        )
        total_embeddings = cursor.fetchone()[0]

        conn.close()

        return {
            "total_files": total_files,
            "total_snippets": total_snippets,
            "total_embeddings": total_embeddings,
        }
