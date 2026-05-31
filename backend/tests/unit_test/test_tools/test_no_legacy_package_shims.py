"""Static guard against package-as-implementation compatibility shims."""

from __future__ import annotations

from pathlib import Path


_TOOLS_ROOT = Path(__file__).resolve().parents[3] / "src" / "tools"
_REMOVED_SNIPPETS = (
    "sys.modules[__name__] = _impl",
    "keeps monkeypatching",
    "resolve to the same module",
)


def test_tool_packages_do_not_masquerade_as_implementation_modules() -> None:
    offenders: list[str] = []
    for path in _TOOLS_ROOT.rglob("__init__.py"):
        text = path.read_text()
        for snippet in _REMOVED_SNIPPETS:
            if snippet in text:
                offenders.append(f"{path.relative_to(_TOOLS_ROOT)}: {snippet}")

    assert offenders == []
