"""Neo4j knowledge graph management for codebases."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv


def _load_project_env() -> None:
    """Load environment variables from common project `.env` locations."""
    current_file = Path(__file__).resolve()
    project_root = current_file.parents[2]

    load_dotenv(project_root / ".env", override=False)
    load_dotenv(override=False)


class Neo4jGraphManager:
    """Manages Neo4j knowledge graph for codebase analysis."""

    def __init__(
        self,
        uri: Optional[str] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
    ):
        """Initialize Neo4j graph manager.

        Args:
            uri: Neo4j connection URI (uses NEO4J_URI env var if not provided)
            user: Neo4j username (uses NEO4J_USER env var if not provided)
            password: Neo4j password (uses NEO4J_PASSWORD env var if not provided)
        """
        _load_project_env()

        self.uri = uri or os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.getenv("NEO4J_USER", "neo4j")
        self.password = password or os.getenv("NEO4J_PASSWORD")

        if not self.password:
            raise ValueError(
                "Neo4j password not found. "
                "Set NEO4J_PASSWORD environment variable or pass password parameter."
            )

        self.driver = None
        self._init_driver()

    def _init_driver(self):
        """Initialize Neo4j driver with graceful fallback for auth issues."""
        try:
            from neo4j import GraphDatabase

            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            print("✓ Neo4j Bolt driver created")
        except ImportError:
            raise ImportError("neo4j package not found. Install with: pip install neo4j")
        except Exception as e:
            import warnings
            warnings.warn(f"Neo4j driver initialization failed: {e}. "
                         f"Graph queries may be unavailable. "
                         f"This usually indicates a Bolt protocol or auth mismatch with the Neo4j server.")
            self.driver = None

    def close(self):
        """Close Neo4j connection."""
        if self.driver:
            self.driver.close()

    def init_schema(self) -> None:
        """Initialize Neo4j schema with constraints."""
        if not self.driver:
            print("⚠ Neo4j driver not available - skipping schema initialization")
            return
        
        with self.driver.session() as session:
            # Create node labels and constraints
            queries = [
                # Unique constraints
                "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Codebase) REQUIRE n.name IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (n:CodeFile) REQUIRE n.path IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Function) REQUIRE (n.file_id, n.name) IS UNIQUE",
                "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Class) REQUIRE (n.file_id, n.name) IS UNIQUE",
                # Indices for faster queries
                "CREATE INDEX IF NOT EXISTS FOR (n:CodeFile) ON (n.file_type)",
                "CREATE INDEX IF NOT EXISTS FOR (n:Function) ON (n.language)",
                "CREATE INDEX IF NOT EXISTS FOR (n:Class) ON (n.language)",
            ]

            for query in queries:
                try:
                    session.run(query)
                except Exception as e:
                    print(f"Note: {e}")

            print("Neo4j schema initialized")

    def add_codebase(self, name: str, path: str, description: str = "") -> None:
        """Add a codebase node.

        Args:
            name: Codebase identifier
            path: Path to codebase
            description: Optional description
        """
        with self.driver.session() as session:
            session.run(
                """
                MERGE (cb:Codebase {name: $name})
                SET cb.path = $path, cb.description = $description
            """,
                name=name,
                path=path,
                description=description,
            )

    def add_code_file(
        self, codebase_name: str, file_path: str, file_type: str, content_hash: str
    ) -> None:
        """Add a code file node.

        Args:
            codebase_name: Parent codebase name
            file_path: File path
            file_type: File type (e.g., 'python', 'javascript')
            content_hash: Hash of file content
        """
        with self.driver.session() as session:
            session.run(
                """
                MATCH (cb:Codebase {name: $codebase_name})
                MERGE (f:CodeFile {path: $file_path})
                SET f.file_type = $file_type, f.content_hash = $content_hash
                MERGE (cb)-[:CONTAINS_FILE]->(f)
            """,
                codebase_name=codebase_name,
                file_path=file_path,
                file_type=file_type,
                content_hash=content_hash,
            )

    def add_function(
        self,
        file_path: str,
        function_name: str,
        language: str = "python",
        signature: str = "",
        docstring: str = "",
    ) -> None:
        """Add a function node.

        Args:
            file_path: Path to parent file
            function_name: Function name
            language: Programming language
            signature: Function signature
            docstring: Function docstring
        """
        with self.driver.session() as session:
            session.run(
                """
                MATCH (f:CodeFile {path: $file_path})
                MERGE (func:Function {name: $function_name, file_id: $file_path})
                SET func.language = $language, func.signature = $signature, func.docstring = $docstring
                MERGE (f)-[:CONTAINS_FUNCTION]->(func)
            """,
                file_path=file_path,
                function_name=function_name,
                language=language,
                signature=signature,
                docstring=docstring,
            )

    def add_class(
        self,
        file_path: str,
        class_name: str,
        language: str = "python",
        parent_class: Optional[str] = None,
        docstring: str = "",
    ) -> None:
        """Add a class node.

        Args:
            file_path: Path to parent file
            class_name: Class name
            language: Programming language
            parent_class: Parent class if applicable
            docstring: Class docstring
        """
        with self.driver.session() as session:
            session.run(
                """
                MATCH (f:CodeFile {path: $file_path})
                MERGE (cls:Class {name: $class_name, file_id: $file_path})
                SET cls.language = $language, cls.parent_class = $parent_class, cls.docstring = $docstring
                MERGE (f)-[:CONTAINS_CLASS]->(cls)
            """,
                file_path=file_path,
                class_name=class_name,
                language=language,
                parent_class=parent_class,
                docstring=docstring,
            )

    def add_function_call(self, caller_name: str, callee_name: str) -> None:
        """Add a function call relationship.

        Args:
            caller_name: Name of calling function
            callee_name: Name of called function
        """
        with self.driver.session() as session:
            session.run(
                """
                MATCH (caller:Function {name: $caller_name})
                MATCH (callee:Function {name: $callee_name})
                MERGE (caller)-[:CALLS]->(callee)
            """,
                caller_name=caller_name,
                callee_name=callee_name,
            )

    def add_import_relationship(self, from_file: str, to_file: str, module_name: str = "") -> None:
        """Add an import relationship between files.

        Args:
            from_file: File doing the importing
            to_file: File being imported
            module_name: Name of imported module
        """
        with self.driver.session() as session:
            session.run(
                """
                MATCH (f1:CodeFile {path: $from_file})
                MATCH (f2:CodeFile {path: $to_file})
                MERGE (f1)-[:IMPORTS {module: $module_name}]->(f2)
            """,
                from_file=from_file,
                to_file=to_file,
                module_name=module_name,
            )

    def query(self, cypher: str, **params) -> List[Dict[str, Any]]:
        """Execute a Cypher query.

        Args:
            cypher: Cypher query string
            **params: Query parameters

        Returns:
            List of result records (empty if driver unavailable)
        """
        if not self.driver:
            import warnings
            warnings.warn("Neo4j driver not available - cannot execute query")
            return []
        
        with self.driver.session() as session:
            result = session.run(cypher, params)
            return [dict(record) for record in result]

    def get_function_dependency_graph(self, codebase_name: str) -> List[Dict[str, Any]]:
        """Get function call dependency graph.

        Args:
            codebase_name: Codebase to analyze

        Returns:
            List of call relationships
        """
        cypher = """
            MATCH (cb:Codebase {name: $codebase_name})-[:CONTAINS_FILE]->(f:CodeFile)-[:CONTAINS_FUNCTION]->(func1:Function)
            MATCH (func1)-[:CALLS]->(func2:Function)
            RETURN func1.name as caller, func2.name as callee
        """
        return self.query(cypher, codebase_name=codebase_name)

    def get_circular_dependencies(self, codebase_name: str) -> List[Dict[str, Any]]:
        """Find circular dependencies in codebase.

        Args:
            codebase_name: Codebase to analyze

        Returns:
            List of circular dependency cycles
        """
        cypher = """
            MATCH (cb:Codebase {name: $codebase_name})-[:CONTAINS_FILE]->(f1:CodeFile)
            MATCH (f1)-[:IMPORTS*]->(f2:CodeFile)-[:IMPORTS*]->(f1)
            RETURN DISTINCT f1.path as file1, f2.path as file2
        """
        return self.query(cypher, codebase_name=codebase_name)

    def get_statistics(self, codebase_name: str) -> Dict[str, int]:
        """Get graph statistics for a codebase.

        Args:
            codebase_name: Codebase to analyze

        Returns:
            Statistics dictionary
        """
        with self.driver.session() as session:
            files = session.run(
                "MATCH (cb:Codebase {name: $name})-[:CONTAINS_FILE]->(f) RETURN COUNT(f) as count",
                name=codebase_name,
            ).single()["count"]

            functions = session.run(
                """
                MATCH (cb:Codebase {name: $name})-[:CONTAINS_FILE]->(f)-[:CONTAINS_FUNCTION]->(fn)
                RETURN COUNT(fn) as count
            """,
                name=codebase_name,
            ).single()["count"]

            classes = session.run(
                """
                MATCH (cb:Codebase {name: $name})-[:CONTAINS_FILE]->(f)-[:CONTAINS_CLASS]->(c)
                RETURN COUNT(c) as count
            """,
                name=codebase_name,
            ).single()["count"]

            relationships = session.run(
                """
                MATCH (cb:Codebase {name: $name})-[:CONTAINS_FILE]->(f)
                MATCH (f)-[r]->()
                RETURN COUNT(r) as count
            """,
                name=codebase_name,
            ).single()["count"]

        return {
            "files": files,
            "functions": functions,
            "classes": classes,
            "relationships": relationships,
        }

    def populate_from_coco(self, codebase_name: str, db) -> Dict[str, int]:
        """Populate Neo4j from code snippets in the database.

        Works with either:
        - New CocoIndex table (coco_snippets) if it exists
        - Fallback to old schema tables (code_snippets, source_files) if coco table not found

        Args:
            codebase_name: Name of the codebase to populate
            db: DatabaseManager instance

        Returns:
            Dictionary with counts: files, functions, classes, imports
        """
        import re

        # Ensure codebase node exists
        self.add_codebase(name=codebase_name, path="", description="")

        stats = {"files": 0, "functions": 0, "classes": 0, "imports": 0}

        # Check which schema to use
        use_coco_table = db.coco_table_exists()

        if use_coco_table:
            return self._populate_from_coco_table(codebase_name, db, stats)
        else:
            return self._populate_from_old_schema(codebase_name, db, stats)

    def _populate_from_coco_table(self, codebase_name: str, db, stats: Dict) -> Dict[str, int]:
        """Populate Neo4j from the new coco_snippets table."""
        import re

        # Get all unique files for this codebase from coco_snippets
        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT DISTINCT file_path, language FROM coco_snippets
                    WHERE codebase_name = %s
                    ORDER BY file_path
                    """,
                    (codebase_name,),
                )
                files = cur.fetchall()

        # Group snippets by file
        files_by_path: Dict[str, List[Dict]] = {}
        for file_path, language in files:
            files_by_path[file_path] = []

            # Get all snippets for this file
            with db.pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, snippet_type, name, content, start_line, end_line, language
                        FROM coco_snippets
                        WHERE codebase_name = %s AND file_path = %s
                        ORDER BY start_line
                        """,
                        (codebase_name, file_path),
                    )
                    rows = cur.fetchall()

            files_by_path[file_path] = [
                {
                    "id": row[0],
                    "snippet_type": row[1],
                    "name": row[2],
                    "content": row[3],
                    "start_line": row[4],
                    "end_line": row[5],
                    "language": row[6],
                }
                for row in rows
            ]

        # Add file nodes
        for file_path, snippets_list in files_by_path.items():
            language = snippets_list[0]["language"] if snippets_list else "unknown"
            self.add_code_file(codebase_name, file_path, language, content_hash="")
            stats["files"] += 1

            # Add function/class nodes from snippets
            for snippet in snippets_list:
                if snippet["snippet_type"] == "function":
                    self.add_function(
                        file_path=file_path,
                        function_name=snippet["name"],
                        language=snippet["language"],
                        signature="",
                        docstring="",
                    )
                    stats["functions"] += 1
                elif snippet["snippet_type"] == "class":
                    self.add_class(
                        file_path=file_path,
                        class_name=snippet["name"],
                        language=snippet["language"],
                        parent_class=None,
                        docstring="",
                    )
                    stats["classes"] += 1

        # Extract and add relationships (imports, function calls)
        for file_path, snippets_list in files_by_path.items():
            for snippet in snippets_list:
                content = snippet["content"]
                language = snippet["language"]

                # Extract imports
                import_patterns = self._extract_imports(content, language)
                stats["imports"] += len(import_patterns)

                # Extract function calls
                if snippet["snippet_type"] == "function":
                    call_patterns = self._extract_function_calls(content, language)
                    for called_func in call_patterns:
                        try:
                            self.add_function_call(
                                caller_name=snippet["name"], callee_name=called_func
                            )
                        except Exception:
                            pass

        return stats

    def _populate_from_old_schema(self, codebase_name: str, db, stats: Dict) -> Dict[str, int]:
        """Populate Neo4j from the old schema (code_snippets + source_files tables)."""
        import re

        # Get codebase ID
        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM codebase_metadata WHERE name = %s", (codebase_name,))
                result = cur.fetchone()
                if not result:
                    return stats
                codebase_id = result[0]

        # Get all files in this codebase
        with db.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, file_path FROM source_files WHERE codebase_id = %s ORDER BY file_path",
                    (codebase_id,),
                )
                files = cur.fetchall()

        files_by_path: Dict[str, List[Dict]] = {}
        for file_id, file_path in files:
            # Get all snippets for this file
            with db.pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT snippet_type, name, content, start_line, end_line, language
                        FROM code_snippets
                        WHERE source_file_id = %s
                        ORDER BY start_line
                        """,
                        (file_id,),
                    )
                    rows = cur.fetchall()

            files_by_path[file_path] = [
                {
                    "snippet_type": row[0],
                    "name": row[1],
                    "content": row[2],
                    "start_line": row[3],
                    "end_line": row[4],
                    "language": row[5],
                }
                for row in rows
            ]

        # Add file nodes
        for file_path, snippets_list in files_by_path.items():
            language = snippets_list[0]["language"] if snippets_list else "unknown"
            self.add_code_file(codebase_name, file_path, language, content_hash="")
            stats["files"] += 1

            # Add function/class nodes
            for snippet in snippets_list:
                if snippet["snippet_type"] == "function":
                    self.add_function(
                        file_path=file_path,
                        function_name=snippet["name"],
                        language=snippet["language"],
                        signature="",
                        docstring="",
                    )
                    stats["functions"] += 1
                elif snippet["snippet_type"] == "class":
                    self.add_class(
                        file_path=file_path,
                        class_name=snippet["name"],
                        language=snippet["language"],
                        parent_class=None,
                        docstring="",
                    )
                    stats["classes"] += 1

        # Extract relationships
        for file_path, snippets_list in files_by_path.items():
            for snippet in snippets_list:
                content = snippet["content"]
                language = snippet["language"]

                # Extract imports
                import_patterns = self._extract_imports(content, language)
                stats["imports"] += len(import_patterns)

                # Extract function calls
                if snippet["snippet_type"] == "function":
                    call_patterns = self._extract_function_calls(content, language)
                    for called_func in call_patterns:
                        try:
                            self.add_function_call(
                                caller_name=snippet["name"], callee_name=called_func
                            )
                        except Exception:
                            pass

        return stats

    def _extract_imports(self, content: str, language: str) -> List[str]:
        """Extract import statements from code.

        Args:
            content: Code content
            language: Programming language

        Returns:
            List of imported module names
        """
        imports = []

        if language == "python":
            # Match: import X, from X import Y, from X import Y as Z
            patterns = [
                r"^\s*import\s+([\w.]+)",  # import module
                r"^\s*from\s+([\w.]+)\s+import",  # from module import
            ]
        elif language in ("javascript", "typescript", "jsx", "tsx"):
            # Match: import X from 'Y', import { X } from 'Y'
            patterns = [r"import\s+.*from\s+['\"]([^'\"]+)['\"]"]
        else:
            return []

        for line in content.split("\n"):
            for pattern in patterns:
                match = re.search(pattern, line)
                if match:
                    imports.append(match.group(1))

        return list(set(imports))  # Remove duplicates

    def _extract_function_calls(self, content: str, language: str) -> List[str]:
        """Extract function call names from code.

        Args:
            content: Code content
            language: Programming language

        Returns:
            List of called function names
        """
        calls = []

        if language == "python":
            # Match: function_name( or self.method_name( or obj.method(
            pattern = r"(?:^|\s|\.)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\("
        elif language in ("javascript", "typescript", "jsx", "tsx"):
            # Match: functionName( or obj.method(
            pattern = r"(?:^|\s|\.)\s*([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\("
        else:
            return []

        for line in content.split("\n"):
            # Skip comments
            if line.strip().startswith("#") or line.strip().startswith("//"):
                continue
            matches = re.findall(pattern, line)
            calls.extend(matches)

        # Filter out common keywords
        keywords = {
            "if",
            "for",
            "while",
            "return",
            "assert",
            "print",
            "len",
            "range",
            "int",
            "str",
            "list",
            "dict",
            "set",
        }
        return [c for c in set(calls) if c not in keywords]
