"""
Code indexing and analysis for building vector indices.
"""

import os
import ast
import hashlib
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime

from waverider.database import DatabaseManager
from waverider.embeddings import EmbeddingProvider


@dataclass
class CodeSnippetInfo:
    """Information about a code snippet."""

    snippet_type: str  # function, class, import, etc.
    name: str
    content: str
    start_line: int
    end_line: int
    language: str = "python"


class CodebaseIndexer:
    """Indexes a codebase and builds embeddings."""

    SUPPORTED_EXTENSIONS = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".java": "java",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
        ".go": "go",
        ".rs": "rust",
    }

    DEFAULT_EXCLUSIONS = {
        "__pycache__",
        ".git",
        ".gitignore",
        "node_modules",
        ".venv",
        "venv",
        ".env",
        "dist",
        "build",
        "*.egg-info",
    }

    def __init__(
        self,
        db_manager: DatabaseManager,
        embedding_provider: EmbeddingProvider,
        exclude_patterns: Optional[List[str]] = None,
    ):
        """Initialize the indexer.

        Args:
            db_manager: DatabaseManager instance
            embedding_provider: EmbeddingProvider instance
            exclude_patterns: Additional patterns to exclude
        """
        self.db = db_manager
        self.embeddings = embedding_provider

        self.exclude_patterns = set(self.DEFAULT_EXCLUSIONS)
        if exclude_patterns:
            self.exclude_patterns.update(exclude_patterns)

    def should_exclude(self, path: Path) -> bool:
        """Check if path should be excluded from indexing."""
        path_str = str(path)

        for pattern in self.exclude_patterns:
            if pattern in path_str:
                return True

        return False

    def get_files_to_index(self, codebase_path: str) -> List[Path]:
        """Get all source files to index.

        Args:
            codebase_path: Path to codebase root

        Returns:
            List of file paths to index
        """
        files = []
        base_path = Path(codebase_path)

        for file_path in base_path.rglob("*"):
            if file_path.is_file() and not self.should_exclude(file_path):
                if file_path.suffix in self.SUPPORTED_EXTENSIONS:
                    files.append(file_path)

        return files

    def extract_python_snippets(self, file_path: Path, content: str) -> List[CodeSnippetInfo]:
        """Extract Python code snippets (functions, classes, imports).

        Args:
            file_path: Path to the file
            content: File content

        Returns:
            List of CodeSnippetInfo objects
        """
        snippets = []

        try:
            tree = ast.parse(content)
        except SyntaxError:
            # If parsing fails, return the whole file as a snippet
            return [
                CodeSnippetInfo(
                    snippet_type="file",
                    name=file_path.stem,
                    content=content,
                    start_line=1,
                    end_line=len(content.split("\n")),
                    language="python",
                )
            ]

        lines = content.split("\n")

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Extract function
                start_line = node.lineno - 1
                end_line = node.end_lineno or node.lineno
                func_content = "\n".join(lines[start_line:end_line])

                snippets.append(
                    CodeSnippetInfo(
                        snippet_type="function",
                        name=node.name,
                        content=func_content,
                        start_line=node.lineno,
                        end_line=end_line,
                        language="python",
                    )
                )

            elif isinstance(node, ast.ClassDef):
                # Extract a compact class snippet: declaration (+ decorators) and optional docstring.
                # Method bodies are embedded separately from FunctionDef nodes.
                decorator_start_line = min((d.lineno for d in node.decorator_list), default=node.lineno)
                first_body_line = node.body[0].lineno if node.body else node.lineno + 1

                header_start_idx = decorator_start_line - 1
                header_end_exclusive = max(header_start_idx + 1, first_body_line - 1)
                header_lines = lines[header_start_idx:header_end_exclusive]

                snippet_start_line = decorator_start_line
                snippet_end_line = header_end_exclusive

                docstring_lines: List[str] = []
                if node.body and isinstance(node.body[0], ast.Expr):
                    doc_expr = node.body[0].value
                    if isinstance(doc_expr, ast.Constant) and isinstance(doc_expr.value, str):
                        doc_start = node.body[0].lineno
                        doc_end = node.body[0].end_lineno or doc_start
                        docstring_lines = lines[doc_start - 1 : doc_end]
                        snippet_end_line = doc_end

                class_parts = ["\n".join(header_lines)]
                if docstring_lines:
                    class_parts.append("\n".join(docstring_lines))
                class_content = "\n\n".join(part for part in class_parts if part)

                snippets.append(
                    CodeSnippetInfo(
                        snippet_type="class",
                        name=node.name,
                        content=class_content,
                        start_line=snippet_start_line,
                        end_line=snippet_end_line,
                        language="python",
                    )
                )

            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                # Extract imports
                start_line = node.lineno - 1
                end_line = node.end_lineno or node.lineno
                import_content = "\n".join(lines[start_line:end_line])

                import_name = getattr(node, "module", "")
                snippets.append(
                    CodeSnippetInfo(
                        snippet_type="import",
                        name=import_name,
                        content=import_content,
                        start_line=node.lineno,
                        end_line=end_line,
                        language="python",
                    )
                )

        # If no snippets found, use whole file
        if not snippets:
            snippets.append(
                CodeSnippetInfo(
                    snippet_type="file",
                    name=file_path.stem,
                    content=content,
                    start_line=1,
                    end_line=len(lines),
                    language="python",
                )
            )

        return snippets

    def extract_snippets(self, file_path: Path, content: str) -> List[CodeSnippetInfo]:
        """Extract code snippets from a file.

        Args:
            file_path: Path to the file
            content: File content

        Returns:
            List of CodeSnippetInfo objects
        """
        language = self.SUPPORTED_EXTENSIONS.get(file_path.suffix, "unknown")

        if language == "python":
            return self.extract_python_snippets(file_path, content)

        # For non-Python files, return file as single snippet
        # TODO: we want to support at least Javascript/TypeScript, and Ruby. 
        # Possibly HTML and CSS as well.
        lines = content.split("\n")
        return [
            CodeSnippetInfo(
                snippet_type="file",
                name=file_path.stem,
                content=content,
                start_line=1,
                end_line=len(lines),
                language=language,
            )
        ]

    def index_codebase(
        self,
        codebase_name: str,
        codebase_path: str,
        description: str = "",
        batch_size: int = 10,
    ) -> Dict[str, Any]:
        """Index a codebase.

        Args:
            codebase_name: Unique identifier for this codebase
            codebase_path: Path to codebase root
            description: Optional description
            batch_size: Number of snippets to embed in each batch

        Returns:
            Index statistics
        """
        # Initialize database schema
        self.db.init_schema()

        # Add codebase to database
        codebase_id = self.db.add_codebase(
            name=codebase_name, path=codebase_path, description=description
        )

        # Full rebuild semantics: clear previous index rows for this codebase.
        self.db.reset_codebase_contents(codebase_id)

        print(f"Indexing codebase: {codebase_name}")
        print(f"Path: {codebase_path}")

        # Get files to index
        files = self.get_files_to_index(codebase_path)
        print(f"Found {len(files)} files to index")

        total_snippets = 0
        total_embeddings = 0

        # Process each file
        for file_path in files:
            try:
                # Calculate file hash
                with open(file_path, "rb") as f:
                    content_hash = hashlib.sha256(f.read()).hexdigest()

                # Read file content
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                # Add file to database
                relative_path = str(file_path.relative_to(codebase_path))
                file_id = self.db.add_source_file(
                    codebase_id=codebase_id,
                    file_path=str(file_path),
                    relative_path=relative_path,
                    content_hash=content_hash,
                )

                # Extract snippets
                snippets = self.extract_snippets(file_path, content)

                # Collect snippets for batch embedding
                snippet_ids = []
                snippet_texts = []

                for snippet in snippets:
                    snippet_id = self.db.add_code_snippet(
                        file_id=file_id,
                        snippet_type=snippet.snippet_type,
                        name=snippet.name,
                        content=snippet.content,
                        start_line=snippet.start_line,
                        end_line=snippet.end_line,
                        language=snippet.language,
                    )
                    snippet_ids.append(snippet_id)
                    snippet_texts.append(snippet.content)
                    total_snippets += 1

                # Generate embeddings in batches
                if snippet_texts:
                    embeddings = self.embeddings.embed_batch(snippet_texts)

                    for snippet_id, embedding in zip(snippet_ids, embeddings):
                        self.db.add_embedding(snippet_id, embedding)
                        total_embeddings += 1

                    print(f"  {relative_path}: {len(snippets)} snippets, {len(embeddings)} embeddings")

            except Exception as e:
                print(f"  ERROR processing {file_path}: {e}")
                continue

        # Get final statistics
        stats = self.db.get_statistics(codebase_id)

        return {
            "codebase_id": codebase_id,
            "codebase_name": codebase_name,
            "total_files_indexed": len(files),
            "total_snippets": stats["total_snippets"],
            "total_embeddings": stats["total_embeddings"],
            "indexed_at": datetime.now().isoformat(),
        }
