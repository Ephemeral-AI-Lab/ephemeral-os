"""Tests for generic non-Python symbol indexing."""

from __future__ import annotations

from code_intelligence.analysis.symbol_index import SymbolIndex
from code_intelligence.types import SymbolKind


def test_symbol_index_extracts_generic_typescript_symbols(tmp_path) -> None:
    content = (
        "export class Example {}\n"
        "export interface Props {}\n"
        "export async function render() {}\n"
        "export const answer = 42;\n"
    )
    file_path = tmp_path / "sample.ts"
    file_path.write_text(content, encoding="utf-8")

    index = SymbolIndex(str(tmp_path))

    generation = index.refresh(str(file_path), content)
    assert generation > 0

    symbols = index.file_symbols(str(file_path))
    names_to_kinds = {symbol.name: symbol.kind for symbol in symbols}

    assert names_to_kinds["Example"] == SymbolKind.CLASS
    assert names_to_kinds["Props"] == SymbolKind.INTERFACE
    assert names_to_kinds["render"] == SymbolKind.FUNCTION
    assert names_to_kinds["answer"] == SymbolKind.CONSTANT
