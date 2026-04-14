"""Per-file symbol extraction for the workspace index.

Dispatches Python files through ``ast``, other languages through the
shared tree-sitter cache when available, and finally falls back to
regex patterns. The functions here are pure — they own no state and
accept a tree cache as a dependency when relevant.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from code_intelligence.types import SymbolInfo, SymbolKind

_GENERIC_SYMBOL_PATTERNS: tuple[tuple[re.Pattern[str], SymbolKind], ...] = (
    (re.compile(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)"), SymbolKind.FUNCTION),
    (re.compile(r"(?:export\s+)?class\s+(\w+)"), SymbolKind.CLASS),
    (re.compile(r"(?:export\s+)?interface\s+(\w+)"), SymbolKind.INTERFACE),
    (re.compile(r"(?:export\s+)?const\s+(\w+)\s*="), SymbolKind.CONSTANT),
    (re.compile(r"def\s+(\w+)\s*\("), SymbolKind.FUNCTION),
    (re.compile(r"func\s+(\w+)\s*\("), SymbolKind.FUNCTION),
    (re.compile(r"fn\s+(\w+)\s*[<(]"), SymbolKind.FUNCTION),
)

# Maps tree-sitter node type -> (SymbolKind | None, sets_container).
# ``kind=None`` means derive from context (variable_declarator looks for const).
_TREE_SITTER_KINDS: dict[str, tuple[SymbolKind | None, bool]] = {
    "class_declaration": (SymbolKind.CLASS, True),
    "class_definition": (SymbolKind.CLASS, True),
    "function_declaration": (SymbolKind.FUNCTION, False),
    "generator_function_declaration": (SymbolKind.FUNCTION, False),
    "method_definition": (SymbolKind.METHOD, False),
    "method_signature": (SymbolKind.METHOD, False),
    "interface_declaration": (SymbolKind.INTERFACE, True),
    "variable_declarator": (None, False),
    "public_field_definition": (SymbolKind.PROPERTY, False),
    "field_definition": (SymbolKind.PROPERTY, False),
}


class TreeSitterNode:
    """Friendly wrapper over a tree-sitter node."""

    __slots__ = ("_node", "_content")

    def __init__(self, node: Any, content: str) -> None:
        self._node = node
        self._content = content

    @property
    def type(self) -> str:
        return str(getattr(self._node, "type", "") or "")

    @property
    def children(self) -> list["TreeSitterNode"]:
        return [TreeSitterNode(c, self._content) for c in getattr(self._node, "children", None) or []]

    def child_by_field(self, name: str) -> "TreeSitterNode | None":
        fn = getattr(self._node, "child_by_field_name", None)
        if callable(fn):
            result = fn(name)
            return TreeSitterNode(result, self._content) if result else None
        return None

    def name_child(self) -> "TreeSitterNode | None":
        for child in self.children:
            if child.type in {
                "identifier",
                "type_identifier",
                "property_identifier",
                "shorthand_property_identifier",
            }:
                return child
        return None

    @property
    def text(self) -> str:
        start = getattr(self._node, "start_byte", None)
        end = getattr(self._node, "end_byte", None)
        if isinstance(start, int) and isinstance(end, int):
            return self._content[start:end]
        return str(getattr(self._node, "text", "") or "")

    @property
    def name(self) -> str:
        named = self.child_by_field("name")
        if named is not None:
            return named.text.strip()
        name_child = self.name_child()
        if name_child is not None:
            return name_child.text.strip()
        return ""

    def start_point(self) -> tuple[int, int]:
        return _point_to_position(getattr(self._node, "start_point", None))

    def end_point(self) -> tuple[int, int]:
        return _point_to_position(getattr(self._node, "end_point", None))

    def signature_text(self) -> str:
        text = self.text.strip()
        return text.splitlines()[0][:100] if text else ""

    def is_const_declaration(self) -> bool:
        current = getattr(self._node, "parent", None)
        while current is not None:
            if str(getattr(current, "type", "") or "") == "lexical_declaration":
                for child in getattr(current, "children", None) or []:
                    if str(getattr(child, "type", "") or "") == "const":
                        return True
                return False
            current = getattr(current, "parent", None)
        return False


def _point_to_position(point: Any) -> tuple[int, int]:
    if isinstance(point, tuple) and len(point) >= 2:
        return int(point[0]) + 1, int(point[1])
    row = getattr(point, "row", None)
    column = getattr(point, "column", None)
    if row is not None and column is not None:
        return int(row) + 1, int(column)
    return 0, 0


def extract_symbols(
    file_path: str,
    content: str,
    tree_cache: Any | None = None,
) -> list[SymbolInfo]:
    """Extract symbols from *content*, choosing the best strategy for the file."""
    ext = Path(file_path).suffix.lower()
    if ext == ".py":
        return _extract_python(file_path, content)
    tree_sitter_symbols = _extract_tree_sitter(file_path, content, tree_cache)
    if tree_sitter_symbols:
        return tree_sitter_symbols
    return _extract_generic(file_path, content)


# -- Tree-sitter --------------------------------------------------------------


def _extract_tree_sitter(
    file_path: str,
    content: str,
    tree_cache: Any | None,
) -> list[SymbolInfo]:
    if tree_cache is None:
        return []
    entry = tree_cache.get_tree(file_path, content=content)
    if entry is None:
        return []
    root = getattr(entry.tree, "root_node", None)
    if root is None:
        return []

    symbols: list[SymbolInfo] = []
    _walk_tree_sitter(TreeSitterNode(root, content), file_path, symbols, container="")
    return symbols


def _walk_tree_sitter(
    node: TreeSitterNode,
    file_path: str,
    bucket: list[SymbolInfo],
    container: str,
) -> None:
    kind_and_container = _TREE_SITTER_KINDS.get(node.type)
    current_container = container
    if kind_and_container is not None:
        kind, sets_container = kind_and_container
        if kind is None:
            kind = SymbolKind.CONSTANT if node.is_const_declaration() else SymbolKind.VARIABLE
        symbol = _tree_sitter_symbol(node, file_path, kind, container)
        if symbol is not None:
            bucket.append(symbol)
            if sets_container:
                current_container = symbol.name
    for child in node.children:
        _walk_tree_sitter(child, file_path, bucket, current_container)


def _tree_sitter_symbol(
    node: TreeSitterNode,
    file_path: str,
    kind: SymbolKind,
    container: str,
) -> SymbolInfo | None:
    name = node.name
    if not name:
        return None
    full_name = (
        f"{container}.{name}"
        if container and kind in {SymbolKind.METHOD, SymbolKind.PROPERTY}
        else name
    )
    start_line, start_char = node.start_point()
    end_line, _ = node.end_point()
    return SymbolInfo(
        name=full_name,
        kind=kind,
        file_path=file_path,
        line=start_line,
        end_line=end_line,
        character=start_char,
        signature=node.signature_text(),
        container=container,
    )


# -- Python ast ---------------------------------------------------------------


def _extract_python(file_path: str, content: str) -> list[SymbolInfo]:
    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError:
        return []
    symbols: list[SymbolInfo] = []
    _walk_python_ast(tree, file_path, symbols, container="")
    return symbols


def _walk_python_ast(
    node: ast.AST,
    file_path: str,
    bucket: list[SymbolInfo],
    container: str,
) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            full_name = f"{container}.{child.name}" if container else child.name
            args = [arg.arg for arg in child.args.args]
            bucket.append(
                _python_symbol(
                    file_path,
                    child,
                    name=full_name,
                    kind=SymbolKind.METHOD if container else SymbolKind.FUNCTION,
                    signature=f"def {child.name}({', '.join(args)})",
                    docstring=ast.get_docstring(child) or "",
                    container=container,
                )
            )
            _walk_python_ast(child, file_path, bucket, full_name)
        elif isinstance(child, ast.ClassDef):
            full_name = f"{container}.{child.name}" if container else child.name
            bucket.append(
                _python_symbol(
                    file_path,
                    child,
                    name=full_name,
                    kind=SymbolKind.CLASS,
                    signature=f"class {child.name}",
                    docstring=ast.get_docstring(child) or "",
                    container=container,
                )
            )
            _walk_python_ast(child, file_path, bucket, full_name)
        elif isinstance(child, ast.Assign):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    full_name = f"{container}.{target.id}" if container else target.id
                    bucket.append(
                        _python_symbol(
                            file_path,
                            target,
                            name=full_name,
                            kind=SymbolKind.VARIABLE,
                            signature=f"{target.id} = ...",
                            container=container,
                        )
                    )
        else:
            _walk_python_ast(child, file_path, bucket, container)


def _python_symbol(
    file_path: str,
    node: ast.AST,
    *,
    name: str,
    kind: SymbolKind,
    signature: str,
    docstring: str = "",
    container: str = "",
) -> SymbolInfo:
    return SymbolInfo(
        name=name,
        kind=kind,
        file_path=file_path,
        line=getattr(node, "lineno", 0),
        end_line=getattr(node, "end_lineno", getattr(node, "lineno", 0)),
        character=getattr(node, "col_offset", 0),
        signature=signature,
        docstring=docstring,
        container=container,
    )


# -- Regex fallback -----------------------------------------------------------


def _extract_generic(file_path: str, content: str) -> list[SymbolInfo]:
    symbols: list[SymbolInfo] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        for pattern, kind in _GENERIC_SYMBOL_PATTERNS:
            m = pattern.match(stripped)
            if m:
                symbols.append(
                    SymbolInfo(
                        name=m.group(1),
                        kind=kind,
                        file_path=file_path,
                        line=lineno,
                        end_line=lineno,
                        character=0,
                        signature=stripped[:100],
                    )
                )
                break
    return symbols
