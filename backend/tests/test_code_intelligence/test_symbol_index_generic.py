"""Tests for Python-only symbol indexing."""

from __future__ import annotations

from code_intelligence.analysis.symbol_index import SymbolIndex


def test_symbol_index_ignores_non_python_symbols(tmp_path) -> None:
    content = "export class Example {}\n"
    file_path = tmp_path / "sample.ts"
    file_path.write_text(content, encoding="utf-8")

    index = SymbolIndex(str(tmp_path))

    generation = index.refresh(str(file_path), content)
    assert generation > 0

    symbols = index.file_symbols(str(file_path))

    assert symbols == []
