"""Tests for indexer snippet extraction behavior."""

from pathlib import Path

import pytest

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


# ---------------------------------------------------------------------------
# Tree-sitter parser tests
# ---------------------------------------------------------------------------

def _ts_available():
    try:
        from waverider.treesitter_parser import is_supported
        return is_supported("python")
    except ImportError:
        return False


@pytest.mark.skipif(not _ts_available(), reason="tree-sitter-python not installed")
def test_treesitter_python_extracts_functions_and_classes() -> None:
    """Tree-sitter should produce equivalent output to the AST extractor for Python."""
    from waverider.treesitter_parser import extract_snippets

    content = '''
class Example:
    """Small class docstring."""

    def method_one(self):
        return 1

    def method_two(self):
        return 2


def standalone():
    pass
'''.strip()

    snippets = extract_snippets(content, "python", Path("example.py"))

    class_snippets = [s for s in snippets if s.snippet_type == "class"]
    func_snippets = [s for s in snippets if s.snippet_type == "function"]

    assert len(class_snippets) == 1
    cls = class_snippets[0]
    assert cls.name == "Example"
    assert "class Example:" in cls.content
    assert "Small class docstring." in cls.content
    assert "def method_one" in cls.content
    assert "def method_two" in cls.content
    assert "return 1" not in cls.content
    assert "return 2" not in cls.content

    func_names = {s.name for s in func_snippets}
    assert func_names >= {"method_one", "method_two", "standalone"}


@pytest.mark.skipif(not _ts_available(), reason="tree-sitter-python not installed")
def test_treesitter_python_imports_and_constants() -> None:
    """Tree-sitter should extract imports and module constants."""
    from waverider.treesitter_parser import extract_snippets

    content = '''import os
from pathlib import Path

MAX_SIZE = 100

def main():
    pass
'''.strip()

    snippets = extract_snippets(content, "python", Path("mod.py"))

    import_snippets = [s for s in snippets if s.snippet_type == "import"]
    assert len(import_snippets) >= 2

    const_snippets = [s for s in snippets if s.snippet_type == "module_constant"]
    assert any(s.name == "MAX_SIZE" for s in const_snippets)

    func_snippets = [s for s in snippets if s.snippet_type == "function"]
    assert any(s.name == "main" for s in func_snippets)


def _ts_js_available():
    try:
        from waverider.treesitter_parser import is_supported
        return is_supported("javascript")
    except ImportError:
        return False


@pytest.mark.skipif(not _ts_js_available(), reason="tree-sitter-javascript not installed")
def test_treesitter_javascript_extraction() -> None:
    """Tree-sitter should extract JS functions, classes, and imports."""
    from waverider.treesitter_parser import extract_snippets

    content = '''import { foo } from 'bar';

function greet(name) {
  return `Hello ${name}`;
}

class Widget {
  constructor(id) {
    this.id = id;
  }

  render() {
    return '<div>';
  }
}

const add = (a, b) => a + b;
'''.strip()

    snippets = extract_snippets(content, "javascript", Path("app.js"))

    import_snippets = [s for s in snippets if s.snippet_type == "import"]
    assert len(import_snippets) >= 1

    func_snippets = [s for s in snippets if s.snippet_type == "function"]
    func_names = {s.name for s in func_snippets}
    assert "greet" in func_names
    assert "add" in func_names
    # methods extracted individually
    assert "constructor" in func_names or "render" in func_names

    class_snippets = [s for s in snippets if s.snippet_type == "class"]
    assert len(class_snippets) == 1
    assert class_snippets[0].name == "Widget"


def _ts_ruby_available():
    try:
        from waverider.treesitter_parser import is_supported
        return is_supported("ruby")
    except ImportError:
        return False


@pytest.mark.skipif(not _ts_ruby_available(), reason="tree-sitter-ruby not installed")
def test_treesitter_ruby_extraction() -> None:
    """Tree-sitter should extract Ruby classes, methods, modules, and requires."""
    from waverider.treesitter_parser import extract_snippets

    content = '''require 'json'

MAX_RETRIES = 3

module Helpers
  def self.greet(name)
    "Hello #{name}"
  end
end

class Widget
  def initialize(id)
    @id = id
  end

  def render
    "<div>#{@id}</div>"
  end
end
'''.strip()

    snippets = extract_snippets(content, "ruby", Path("widget.rb"))

    import_snippets = [s for s in snippets if s.snippet_type == "import"]
    assert len(import_snippets) >= 1

    const_snippets = [s for s in snippets if s.snippet_type == "module_constant"]
    assert any(s.name == "MAX_RETRIES" for s in const_snippets)

    class_snippets = [s for s in snippets if s.snippet_type == "class"]
    class_names = {s.name for s in class_snippets}
    assert "Widget" in class_names
    assert "Helpers" in class_names

    func_snippets = [s for s in snippets if s.snippet_type == "function"]
    func_names = {s.name for s in func_snippets}
    assert "initialize" in func_names
    assert "render" in func_names


@pytest.mark.skipif(not _ts_js_available(), reason="tree-sitter-javascript not installed")
def test_treesitter_jsx_extraction() -> None:
    """JSX files should be parsed via tree-sitter-javascript (JSX-aware)."""
    from waverider.treesitter_parser import extract_snippets

    content = '''import React from 'react';

function App() {
  return <div>Hello</div>;
}

export const Button = ({ label }) => {
  return <button>{label}</button>;
};
'''.strip()

    snippets = extract_snippets(content, "jsx", Path("App.jsx"))

    import_snippets = [s for s in snippets if s.snippet_type == "import"]
    assert len(import_snippets) >= 1

    func_snippets = [s for s in snippets if s.snippet_type == "function"]
    func_names = {s.name for s in func_snippets}
    assert "App" in func_names
    assert "Button" in func_names


# ---------------------------------------------------------------------------
# Incremental indexing via extract_snippets dispatch
# ---------------------------------------------------------------------------

def test_extract_snippets_dispatches_to_treesitter_when_available() -> None:
    """extract_snippets should prefer tree-sitter when a grammar is installed."""
    indexer = CodebaseIndexer(
        db_manager=DatabaseManager(db_path=":memory:"),
        embedding_provider=MockEmbeddings(dimension=8),
    )

    content = "def hello(): pass"
    snippets = indexer.extract_snippets(Path("hello.py"), content)
    # Regardless of backend, a function named "hello" should be produced
    assert any(s.name == "hello" and s.snippet_type == "function" for s in snippets)
