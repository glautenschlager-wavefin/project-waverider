"""Tests for indexer snippet extraction behavior."""

from pathlib import Path

from waverider.database import DatabaseManager
from waverider.embeddings import MockEmbeddings
from waverider.indexer import CodebaseIndexer


def test_extract_python_snippets_class_is_compact() -> None:
    """Class snippets should include declaration/docstring and method signatures but not full method bodies."""
    indexer = CodebaseIndexer(
        db_manager=DatabaseManager(db_path=":memory:"),
        embedding_provider=MockEmbeddings(dimension=8),
    )

    content = '''
class Example:
    """Small class docstring."""

    def method_one(self):
        return 1

    def method_two(self):
        return 2
'''.strip()

    snippets = indexer.extract_python_snippets(file_path=Path("example.py"), content=content)

    class_snippets = [s for s in snippets if s.snippet_type == "class" and s.name == "Example"]
    function_snippets = [s for s in snippets if s.snippet_type == "function"]

    assert len(class_snippets) == 1
    class_content = class_snippets[0].content

    assert "class Example:" in class_content
    assert "Small class docstring." in class_content
    # Class snippet should include method signatures (one-liners)
    assert "def method_one" in class_content
    assert "def method_two" in class_content
    # But should NOT contain method bodies
    assert "return 1" not in class_content
    assert "return 2" not in class_content

    assert {s.name for s in function_snippets} >= {"method_one", "method_two"}
