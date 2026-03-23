"""
Neo4j knowledge graph management for codebases.
"""

import os
from typing import Dict, List, Any, Optional
from abc import ABC


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
        """Initialize Neo4j driver."""
        try:
            from neo4j import GraphDatabase

            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            # Test connection
            self.driver.verify_connectivity()
            print("Connected to Neo4j")
        except ImportError:
            raise ImportError("neo4j package not found. Install with: pip install neo4j")
        except Exception as e:
            raise ConnectionError(f"Failed to connect to Neo4j: {e}")

    def close(self):
        """Close Neo4j connection."""
        if self.driver:
            self.driver.close()

    def init_schema(self) -> None:
        """Initialize Neo4j schema with constraints."""
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
    ) -> None:
        """Add a function node.

        Args:
            file_path: Path to parent file
            function_name: Function name
            language: Programming language
            signature: Function signature
        """
        with self.driver.session() as session:
            session.run(
                """
                MATCH (f:CodeFile {path: $file_path})
                MERGE (func:Function {name: $function_name, file_id: $file_path})
                SET func.language = $language, func.signature = $signature
                MERGE (f)-[:CONTAINS_FUNCTION]->(func)
            """,
                file_path=file_path,
                function_name=function_name,
                language=language,
                signature=signature,
            )

    def add_class(
        self,
        file_path: str,
        class_name: str,
        language: str = "python",
        parent_class: Optional[str] = None,
    ) -> None:
        """Add a class node.

        Args:
            file_path: Path to parent file
            class_name: Class name
            language: Programming language
            parent_class: Parent class if applicable
        """
        with self.driver.session() as session:
            session.run(
                """
                MATCH (f:CodeFile {path: $file_path})
                MERGE (cls:Class {name: $class_name, file_id: $file_path})
                SET cls.language = $language, cls.parent_class = $parent_class
                MERGE (f)-[:CONTAINS_CLASS]->(cls)
            """,
                file_path=file_path,
                class_name=class_name,
                language=language,
                parent_class=parent_class,
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
            List of result records
        """
        with self.driver.session() as session:
            result = session.run(cypher, **params)
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
