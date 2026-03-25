"""
Tree-sitter based parser for multi-language snippet extraction.

Supports Python, JavaScript, TypeScript, JSX, TSX, and Ruby. Falls back
gracefully when tree-sitter grammars are not installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from waverider.indexer import CodeSnippetInfo


# ---------------------------------------------------------------------------
# Language loading
# ---------------------------------------------------------------------------

def _get_ts_language(lang_name: str):
    """Return a ``tree_sitter.Language`` for *lang_name*, or ``None``."""
    from tree_sitter import Language

    if lang_name == "python":
        import tree_sitter_python
        return Language(tree_sitter_python.language())
    elif lang_name == "javascript":
        import tree_sitter_javascript
        return Language(tree_sitter_javascript.language())
    elif lang_name == "typescript":
        import tree_sitter_typescript
        return Language(tree_sitter_typescript.language_typescript())
    elif lang_name == "tsx":
        import tree_sitter_typescript
        return Language(tree_sitter_typescript.language_tsx())
    elif lang_name == "jsx":
        # tree-sitter-javascript natively supports JSX syntax
        import tree_sitter_javascript
        return Language(tree_sitter_javascript.language())
    elif lang_name == "ruby":
        import tree_sitter_ruby
        return Language(tree_sitter_ruby.language())
    return None


def is_supported(language: str) -> bool:
    """Return ``True`` if a tree-sitter grammar is available for *language*."""
    try:
        return _get_ts_language(language) is not None
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_snippets(
    content: str, language: str, file_path: Path
) -> List[CodeSnippetInfo]:
    """Extract code snippets from *content* using tree-sitter."""
    from tree_sitter import Parser

    ts_lang = _get_ts_language(language)
    if ts_lang is None:
        raise ValueError(f"No tree-sitter grammar for language: {language}")

    parser = Parser(ts_lang)
    tree = parser.parse(content.encode("utf-8"))
    lines = content.split("\n")

    if language == "python":
        snippets = _extract_python(tree, lines, file_path)
    elif language in ("javascript", "typescript", "tsx", "jsx"):
        snippets = _extract_js_ts(tree, lines, language, file_path)
    elif language == "ruby":
        snippets = _extract_ruby(tree, lines, file_path)
    else:
        snippets = []

    if not snippets:
        snippets.append(
            CodeSnippetInfo(
                snippet_type="file",
                name=file_path.stem,
                content=content,
                start_line=1,
                end_line=len(lines),
                language=language,
            )
        )
    return snippets


# ---------------------------------------------------------------------------
# Python extraction
# ---------------------------------------------------------------------------

def _extract_python(tree, lines: list[str], file_path: Path) -> List[CodeSnippetInfo]:
    snippets: List[CodeSnippetInfo] = []
    root = tree.root_node

    for node in root.children:
        if node.type == "function_definition":
            _add_python_function(node, lines, snippets)

        elif node.type == "decorated_definition":
            inner = _decorated_inner(node)
            if inner is not None and inner.type == "function_definition":
                _add_python_function(node, lines, snippets)  # use decorator node for range
            elif inner is not None and inner.type == "class_definition":
                _add_python_class(node, inner, lines, file_path, snippets)

        elif node.type == "class_definition":
            _add_python_class(None, node, lines, file_path, snippets)

        elif node.type in ("import_statement", "import_from_statement"):
            start = node.start_point[0]
            end = node.end_point[0]
            mod_name = ""
            if node.type == "import_from_statement":
                mn = node.child_by_field_name("module_name")
                if mn:
                    mod_name = mn.text.decode("utf-8")
            snippets.append(
                CodeSnippetInfo(
                    snippet_type="import",
                    name=mod_name,
                    content="\n".join(lines[start : end + 1]),
                    start_line=start + 1,
                    end_line=end + 1,
                    language="python",
                )
            )

        elif node.type == "expression_statement":
            _try_python_assignment(node, lines, file_path, snippets)

    return snippets


def _add_python_function(node, lines, snippets):
    start = node.start_point[0]
    end = node.end_point[0]
    inner = node if node.type == "function_definition" else _decorated_inner(node)
    name_node = inner.child_by_field_name("name") if inner else None
    name = name_node.text.decode("utf-8") if name_node else "unknown"
    snippets.append(
        CodeSnippetInfo(
            snippet_type="function",
            name=name,
            content="\n".join(lines[start : end + 1]),
            start_line=start + 1,
            end_line=end + 1,
            language="python",
        )
    )


def _add_python_class(decorator_node, class_node, lines, file_path, snippets):
    start = (decorator_node or class_node).start_point[0]
    name_node = class_node.child_by_field_name("name")
    class_name = name_node.text.decode("utf-8") if name_node else file_path.stem

    body = class_node.child_by_field_name("body")
    header_end = body.start_point[0] if body else class_node.end_point[0]
    header_lines = lines[start:header_end]

    # Docstring
    docstring_lines: list[str] = []
    if body:
        for child in body.children:
            if child.type == "expression_statement":
                for expr in child.children:
                    if expr.type == "string":
                        docstring_lines = lines[child.start_point[0] : child.end_point[0] + 1]
                break
            elif child.type not in ("comment", "newline"):
                break

    # Method signatures + individual function snippets
    method_sigs: list[str] = []
    if body:
        for child in body.children:
            func_node: Optional[object] = None
            outer = child
            if child.type == "function_definition":
                func_node = child
            elif child.type == "decorated_definition":
                func_node = _decorated_inner(child)
                if func_node is None or func_node.type != "function_definition":
                    func_node = None

            if func_node is not None:
                sig_line = lines[func_node.start_point[0]].rstrip()
                method_sigs.append(f"    {sig_line.strip()}")
                # Full method as a separate snippet
                m_start = outer.start_point[0]
                m_end = outer.end_point[0]
                m_name = func_node.child_by_field_name("name")
                snippets.append(
                    CodeSnippetInfo(
                        snippet_type="function",
                        name=m_name.text.decode("utf-8") if m_name else "method",
                        content="\n".join(lines[m_start : m_end + 1]),
                        start_line=m_start + 1,
                        end_line=m_end + 1,
                        language="python",
                    )
                )

    snippet_end = header_end
    parts = ["\n".join(header_lines)]
    if docstring_lines:
        parts.append("\n".join(docstring_lines))
        snippet_end = max(snippet_end, docstring_lines[0] if isinstance(docstring_lines[0], int) else header_end)
    if method_sigs:
        parts.append("    # Methods:\n" + "\n".join(method_sigs))

    snippets.append(
        CodeSnippetInfo(
            snippet_type="class",
            name=class_name,
            content="\n\n".join(p for p in parts if p),
            start_line=start + 1,
            end_line=snippet_end,
            language="python",
        )
    )


def _try_python_assignment(node, lines, file_path, snippets):
    """Extract module-level assignment from an expression_statement."""
    for child in node.children:
        if child.type == "assignment":
            start = node.start_point[0]
            end = node.end_point[0]
            left = child.children[0] if child.children else None
            var_name = left.text.decode("utf-8") if left else file_path.stem
            snippets.append(
                CodeSnippetInfo(
                    snippet_type="module_constant",
                    name=var_name,
                    content="\n".join(lines[start : end + 1]),
                    start_line=start + 1,
                    end_line=end + 1,
                    language="python",
                )
            )
            return


# ---------------------------------------------------------------------------
# JavaScript / TypeScript extraction
# ---------------------------------------------------------------------------

def _extract_js_ts(
    tree, lines: list[str], language: str, file_path: Path
) -> List[CodeSnippetInfo]:
    snippets: List[CodeSnippetInfo] = []
    root = tree.root_node

    for node in root.children:
        if node.type == "function_declaration":
            _add_js_function(node, node, lines, language, snippets)

        elif node.type == "class_declaration":
            _add_js_class(node, lines, language, file_path, snippets)

        elif node.type in ("import_statement",):
            start = node.start_point[0]
            end = node.end_point[0]
            snippets.append(
                CodeSnippetInfo(
                    snippet_type="import",
                    name="",
                    content="\n".join(lines[start : end + 1]),
                    start_line=start + 1,
                    end_line=end + 1,
                    language=language,
                )
            )

        elif node.type == "export_statement":
            _extract_js_export(node, lines, language, file_path, snippets)

        elif node.type in ("lexical_declaration", "variable_declaration"):
            _extract_js_variable(node, lines, language, file_path, snippets)

    return snippets


def _add_js_function(outer_node, func_node, lines, language, snippets):
    start = outer_node.start_point[0]
    end = outer_node.end_point[0]
    name_node = func_node.child_by_field_name("name")
    name = name_node.text.decode("utf-8") if name_node else "anonymous"
    snippets.append(
        CodeSnippetInfo(
            snippet_type="function",
            name=name,
            content="\n".join(lines[start : end + 1]),
            start_line=start + 1,
            end_line=end + 1,
            language=language,
        )
    )


def _add_js_class(node, lines, language, file_path, snippets):
    start = node.start_point[0]
    end = node.end_point[0]
    name_node = node.child_by_field_name("name")
    class_name = name_node.text.decode("utf-8") if name_node else file_path.stem

    body = node.child_by_field_name("body")
    header_end = body.start_point[0] + 1 if body else end + 1
    header = "\n".join(lines[start:header_end])

    method_sigs: list[str] = []
    if body:
        for child in body.children:
            if child.type == "method_definition":
                m_start = child.start_point[0]
                m_end = child.end_point[0]
                m_name = child.child_by_field_name("name")
                method_line = lines[m_start].rstrip()
                method_sigs.append(f"  {method_line.strip()}")
                snippets.append(
                    CodeSnippetInfo(
                        snippet_type="function",
                        name=m_name.text.decode("utf-8") if m_name else "method",
                        content="\n".join(lines[m_start : m_end + 1]),
                        start_line=m_start + 1,
                        end_line=m_end + 1,
                        language=language,
                    )
                )

    parts = [header]
    if method_sigs:
        parts.append("  // Methods:\n" + "\n".join(method_sigs))

    snippets.append(
        CodeSnippetInfo(
            snippet_type="class",
            name=class_name,
            content="\n\n".join(p for p in parts if p),
            start_line=start + 1,
            end_line=end + 1,
            language=language,
        )
    )


def _extract_js_export(node, lines, language, file_path, snippets):
    for child in node.children:
        if child.type == "function_declaration":
            _add_js_function(node, child, lines, language, snippets)
        elif child.type == "class_declaration":
            _add_js_class(child, lines, language, file_path, snippets)
        elif child.type in ("lexical_declaration", "variable_declaration"):
            _extract_js_variable(child, lines, language, file_path, snippets, export_node=node)


def _extract_js_variable(node, lines, language, file_path, snippets, export_node=None):
    outer = export_node or node
    for child in node.children:
        if child.type == "variable_declarator":
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            var_name = name_node.text.decode("utf-8") if name_node else file_path.stem

            start = outer.start_point[0]
            end = outer.end_point[0]

            if value_node and value_node.type == "arrow_function":
                stype = "function"
            else:
                stype = "module_constant"

            snippets.append(
                CodeSnippetInfo(
                    snippet_type=stype,
                    name=var_name,
                    content="\n".join(lines[start : end + 1]),
                    start_line=start + 1,
                    end_line=end + 1,
                    language=language,
                )
            )


# ---------------------------------------------------------------------------
# Ruby extraction
# ---------------------------------------------------------------------------

def _extract_ruby(tree, lines: list[str], file_path: Path) -> List[CodeSnippetInfo]:
    snippets: List[CodeSnippetInfo] = []
    root = tree.root_node

    for node in root.children:
        if node.type == "method":
            _add_ruby_method(node, lines, snippets)

        elif node.type == "class":
            _add_ruby_class(node, lines, file_path, snippets)

        elif node.type == "module":
            _add_ruby_module(node, lines, file_path, snippets)

        elif node.type == "call":
            # require / require_relative / include
            method_node = node.child_by_field_name("method")
            if method_node and method_node.text.decode("utf-8") in (
                "require",
                "require_relative",
                "include",
            ):
                start = node.start_point[0]
                end = node.end_point[0]
                snippets.append(
                    CodeSnippetInfo(
                        snippet_type="import",
                        name=method_node.text.decode("utf-8"),
                        content="\n".join(lines[start : end + 1]),
                        start_line=start + 1,
                        end_line=end + 1,
                        language="ruby",
                    )
                )

        elif node.type == "assignment":
            start = node.start_point[0]
            end = node.end_point[0]
            left = node.child_by_field_name("left")
            var_name = left.text.decode("utf-8") if left else file_path.stem
            # Only top-level SCREAMING_CASE constants
            if var_name.isupper() or var_name[0].isupper():
                snippets.append(
                    CodeSnippetInfo(
                        snippet_type="module_constant",
                        name=var_name,
                        content="\n".join(lines[start : end + 1]),
                        start_line=start + 1,
                        end_line=end + 1,
                        language="ruby",
                    )
                )

    return snippets


def _add_ruby_method(node, lines, snippets):
    start = node.start_point[0]
    end = node.end_point[0]
    name_node = node.child_by_field_name("name")
    name = name_node.text.decode("utf-8") if name_node else "unknown"
    snippets.append(
        CodeSnippetInfo(
            snippet_type="function",
            name=name,
            content="\n".join(lines[start : end + 1]),
            start_line=start + 1,
            end_line=end + 1,
            language="ruby",
        )
    )


def _add_ruby_class(node, lines, file_path, snippets):
    start = node.start_point[0]
    end = node.end_point[0]
    name_node = node.child_by_field_name("name")
    class_name = name_node.text.decode("utf-8") if name_node else file_path.stem

    body = node.child_by_field_name("body")
    header_end = body.start_point[0] + 1 if body else start + 1
    header = "\n".join(lines[start:header_end])

    method_sigs: list[str] = []
    if body:
        for child in body.children:
            if child.type == "method":
                m_start = child.start_point[0]
                m_end = child.end_point[0]
                m_name = child.child_by_field_name("name")
                method_line = lines[m_start].rstrip()
                method_sigs.append(f"  {method_line.strip()}")
                snippets.append(
                    CodeSnippetInfo(
                        snippet_type="function",
                        name=m_name.text.decode("utf-8") if m_name else "method",
                        content="\n".join(lines[m_start : m_end + 1]),
                        start_line=m_start + 1,
                        end_line=m_end + 1,
                        language="ruby",
                    )
                )

    parts = [header]
    if method_sigs:
        parts.append("  # Methods:\n" + "\n".join(method_sigs))

    snippets.append(
        CodeSnippetInfo(
            snippet_type="class",
            name=class_name,
            content="\n\n".join(p for p in parts if p),
            start_line=start + 1,
            end_line=end + 1,
            language="ruby",
        )
    )


def _add_ruby_module(node, lines, file_path, snippets):
    """Extract a Ruby module similarly to a class."""
    start = node.start_point[0]
    end = node.end_point[0]
    name_node = node.child_by_field_name("name")
    mod_name = name_node.text.decode("utf-8") if name_node else file_path.stem

    body = node.child_by_field_name("body")
    method_sigs: list[str] = []
    if body:
        for child in body.children:
            if child.type == "method":
                m_start = child.start_point[0]
                m_end = child.end_point[0]
                m_name = child.child_by_field_name("name")
                method_line = lines[m_start].rstrip()
                method_sigs.append(f"  {method_line.strip()}")
                snippets.append(
                    CodeSnippetInfo(
                        snippet_type="function",
                        name=m_name.text.decode("utf-8") if m_name else "method",
                        content="\n".join(lines[m_start : m_end + 1]),
                        start_line=m_start + 1,
                        end_line=m_end + 1,
                        language="ruby",
                    )
                )
            elif child.type == "class":
                _add_ruby_class(child, lines, file_path, snippets)

    header_end = body.start_point[0] + 1 if body else start + 1
    header = "\n".join(lines[start:header_end])
    parts = [header]
    if method_sigs:
        parts.append("  # Methods:\n" + "\n".join(method_sigs))

    snippets.append(
        CodeSnippetInfo(
            snippet_type="class",
            name=mod_name,
            content="\n\n".join(p for p in parts if p),
            start_line=start + 1,
            end_line=end + 1,
            language="ruby",
        )
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decorated_inner(node):
    """Return the function_definition or class_definition inside a decorated_definition."""
    for child in node.children:
        if child.type in ("function_definition", "class_definition"):
            return child
    return None
