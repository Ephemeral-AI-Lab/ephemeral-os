"""Scenario definitions for the LSP live e2e suite.

Each scenario is a small, self-contained probe of one LSP tool's behavior.
The runner writes ``setup_files`` to the sandbox, executes ``tool_calls``
in order, and validates each call's response against ``assertions``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tools.core.results import ToolResult

__all__ = [
    "LSP_SCENARIOS",
    "LspScenario",
    "LspToolCall",
    "ScenarioFailure",
]


class ScenarioFailure(AssertionError):
    """Raised when an LSP scenario assertion fails."""


@dataclass(frozen=True)
class LspToolCall:
    tool_name: str
    args: dict[str, Any]
    assertions: tuple[Callable[[ToolResult], None], ...] = ()


@dataclass(frozen=True)
class LspEdit:
    """Optional file edit applied between tool calls."""

    file_path: str
    new_contents: str
    old_text: str | None = None


@dataclass(frozen=True)
class LspScenario:
    name: str
    description: str
    setup_files: dict[str, str]
    tool_calls: tuple[LspToolCall, ...]
    edits: tuple[tuple[int, LspEdit], ...] = field(default_factory=tuple)


def _decode_payload(result: ToolResult) -> dict[str, Any]:
    if result.is_error:
        raise ScenarioFailure(f"tool error: {result.output}")
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as exc:
        raise ScenarioFailure(
            f"tool output was not JSON: {result.output!r}"
        ) from exc


def _assert_hover_contains(*tokens: str) -> Callable[[ToolResult], None]:
    def check(result: ToolResult) -> None:
        payload = _decode_payload(result)
        hover = payload.get("hover") or {}
        contents = hover.get("contents") if isinstance(hover, dict) else None
        text = json.dumps(contents) if contents is not None else json.dumps(payload)
        for token in tokens:
            if token not in text:
                raise ScenarioFailure(
                    f"hover did not contain {token!r}; got {text!r}"
                )

    return check


def _assert_definitions_at(file_path_endswith: str, line: int) -> Callable[[ToolResult], None]:
    def check(result: ToolResult) -> None:
        payload = _decode_payload(result)
        defs = payload.get("definitions") or []
        if not isinstance(defs, list) or not defs:
            raise ScenarioFailure(f"no definitions returned: {payload}")
        for entry in defs:
            file_path = str(entry.get("file_path") or "")
            range_obj = entry.get("range") or {}
            start_line = (
                range_obj.get("start", {}).get("line")
                if isinstance(range_obj, dict)
                else None
            )
            if (
                file_path.endswith(file_path_endswith)
                and isinstance(start_line, int)
                and start_line == line
            ):
                return
        raise ScenarioFailure(
            f"no definition at {file_path_endswith}:line={line}; got {defs}"
        )

    return check


def _assert_references_count_at_least(min_count: int) -> Callable[[ToolResult], None]:
    def check(result: ToolResult) -> None:
        payload = _decode_payload(result)
        refs = payload.get("references") or []
        if not isinstance(refs, list) or len(refs) < min_count:
            raise ScenarioFailure(
                f"expected ≥{min_count} references, got {refs}"
            )

    return check


def _assert_diagnostics_mention(token: str) -> Callable[[ToolResult], None]:
    def check(result: ToolResult) -> None:
        payload = _decode_payload(result)
        diags = payload.get("diagnostics") or []
        if not isinstance(diags, list) or not diags:
            raise ScenarioFailure(f"expected ≥1 diagnostic, got {diags}")
        joined = json.dumps(diags)
        if token not in joined:
            raise ScenarioFailure(
                f"diagnostic did not mention {token!r}; got {joined}"
            )

    return check


def _assert_no_diagnostics() -> Callable[[ToolResult], None]:
    def check(result: ToolResult) -> None:
        payload = _decode_payload(result)
        diags = payload.get("diagnostics") or []
        if diags:
            raise ScenarioFailure(f"expected no diagnostics, got {diags}")

    return check


def _assert_symbols_include(*names: str) -> Callable[[ToolResult], None]:
    def check(result: ToolResult) -> None:
        payload = _decode_payload(result)
        symbols = payload.get("symbols") or []
        seen = {str(s.get("name", "")) for s in symbols if isinstance(s, dict)}
        for name in names:
            if name not in seen:
                raise ScenarioFailure(
                    f"symbol {name!r} not in result; got {seen}"
                )

    return check


_DIR = "lsp_test"


def _scratch(scenario: str, file_name: str) -> str:
    return f"{_DIR}/{scenario}/{file_name}"


HOVER_FILE = _scratch("hover_returns_signature", "mod.py")
HOVER_BODY = (
    "def add_two_numbers(a: int, b: int) -> int:\n"
    "    return a + b\n"
    "\n"
    "result = add_two_numbers(1, 2)\n"
)

DEFS_FILE = _scratch("find_definitions_resolves_local_def", "mod.py")
DEFS_BODY = (
    "def discover_target_value() -> int:\n"
    "    return 42\n"
    "\n"
    "def main() -> int:\n"
    "    return discover_target_value()\n"
)

REFS_FILE = _scratch("find_references_returns_call_sites", "mod.py")
REFS_BODY = (
    "def shared_helper() -> int:\n"
    "    return 1\n"
    "\n"
    "def caller_a() -> int:\n"
    "    return shared_helper()\n"
    "\n"
    "def caller_b() -> int:\n"
    "    return shared_helper() + 1\n"
)

DIAG_FILE = _scratch("diagnostics_flags_undefined_name", "mod.py")
DIAG_BODY = (
    "def example() -> int:\n"
    "    return undefined_local_symbol_xyz\n"
)

SYMBOLS_FILE = _scratch("query_symbols_lists_module_symbols", "mod.py")
SYMBOLS_BODY = (
    "class FirstClass:\n"
    "    pass\n"
    "\n"
    "def first_function() -> int:\n"
    "    return 1\n"
    "\n"
    "def second_function() -> int:\n"
    "    return 2\n"
)

EDIT_FILE = _scratch("hover_reflects_edit", "mod.py")
EDIT_BODY_V1 = (
    "def transform(x: int) -> int:\n"
    "    return x * 2\n"
)
EDIT_BODY_V2 = (
    "def transform(x: int, y: int = 0) -> str:\n"
    "    return str(x + y)\n"
)

COMPLEX_DIR = f"{_DIR}/complex_all_tools_layerstack"
COMPLEX_CONFIG = "pyrightconfig.json"
COMPLEX_INIT = f"{COMPLEX_DIR}/__init__.py"
COMPLEX_MODEL = f"{COMPLEX_DIR}/model.py"
COMPLEX_SERVICE = f"{COMPLEX_DIR}/service.py"
COMPLEX_CONSUMER = f"{COMPLEX_DIR}/consumer.py"
COMPLEX_CONFIG_BODY = (
    "{\n"
    "  \"include\": [\"lsp_test/complex_all_tools_layerstack\"],\n"
    "  \"typeCheckingMode\": \"strict\",\n"
    "  \"useLibraryCodeForTypes\": true\n"
    "}\n"
)
COMPLEX_MODEL_V1 = (
    "from dataclasses import dataclass\n"
    "\n"
    "@dataclass\n"
    "class UserProfile:\n"
    "    first_name: str\n"
    "    last_name: str\n"
    "\n"
    "def display_name(profile: UserProfile) -> str:\n"
    "    return f\"{profile.first_name} {profile.last_name}\"\n"
)
COMPLEX_MODEL_RETURN_EDIT = (
    "def display_name(profile: UserProfile) -> str:\n"
    "    return f\"{profile.first_name} {profile.last_name}\"\n"
)
COMPLEX_MODEL_V2_RETURN = (
    "def display_name(profile: UserProfile) -> int:\n"
    "    return len(profile.first_name) + len(profile.last_name)\n"
)
COMPLEX_SERVICE_V1 = (
    "from .model import UserProfile, display_name\n"
    "\n"
    "profile = UserProfile(first_name=\"Ada\", last_name=\"Lovelace\")\n"
    "name: str = display_name(profile)\n"
    "\n"
    "def render_one() -> str:\n"
    "    return str(display_name(profile))\n"
    "\n"
    "def render_two() -> str:\n"
    "    return f\"value={display_name(profile)}\"\n"
)
COMPLEX_SERVICE_NAME_EDIT = "name: str = display_name(profile)\n"
COMPLEX_SERVICE_NAME_V2 = "name: int = display_name(profile)\n"
COMPLEX_CONSUMER_V1 = (
    "from .service import name\n"
    "\n"
    "final: str = missing_value\n"
)
COMPLEX_CONSUMER_MISSING_EDIT = "final: str = missing_value\n"
COMPLEX_CONSUMER_FIXED = "final: str = name\n"


LSP_SCENARIOS: tuple[LspScenario, ...] = (
    LspScenario(
        name="hover_returns_signature",
        description="Hover on a function name returns parameter and return type info.",
        setup_files={HOVER_FILE: HOVER_BODY},
        tool_calls=(
            LspToolCall(
                tool_name="lsp.hover",
                args={"file_path": HOVER_FILE, "line": 0, "character": 4},
                assertions=(_assert_hover_contains("add_two_numbers", "int"),),
            ),
        ),
    ),
    LspScenario(
        name="find_definitions_resolves_local_def",
        description="find_definitions on a call site resolves to the def line.",
        setup_files={DEFS_FILE: DEFS_BODY},
        tool_calls=(
            LspToolCall(
                tool_name="lsp.find_definitions",
                args={"file_path": DEFS_FILE, "line": 4, "character": 11},
                assertions=(_assert_definitions_at("mod.py", 0),),
            ),
        ),
    ),
    LspScenario(
        name="find_references_returns_call_sites",
        description="find_references on a def returns ≥2 call sites.",
        setup_files={REFS_FILE: REFS_BODY},
        tool_calls=(
            LspToolCall(
                tool_name="lsp.find_references",
                args={
                    "file_path": REFS_FILE,
                    "line": 0,
                    "character": 4,
                    "include_declaration": False,
                },
                assertions=(_assert_references_count_at_least(2),),
            ),
        ),
    ),
    LspScenario(
        name="diagnostics_flags_undefined_name",
        description="diagnostics surfaces an undefined name.",
        setup_files={DIAG_FILE: DIAG_BODY},
        tool_calls=(
            LspToolCall(
                tool_name="lsp.diagnostics",
                args={"file_path": DIAG_FILE},
                assertions=(_assert_diagnostics_mention("undefined_local_symbol_xyz"),),
            ),
        ),
    ),
    LspScenario(
        name="query_symbols_lists_module_symbols",
        description="query_symbols returns the named functions and class.",
        setup_files={SYMBOLS_FILE: SYMBOLS_BODY},
        tool_calls=(
            LspToolCall(
                tool_name="lsp.query_symbols",
                args={"query": "first", "file_path": SYMBOLS_FILE},
                assertions=(_assert_symbols_include("FirstClass", "first_function"),),
            ),
        ),
    ),
    LspScenario(
        name="hover_reflects_edit",
        description="After edit_file, the next hover reflects the new signature.",
        setup_files={EDIT_FILE: EDIT_BODY_V1},
        tool_calls=(
            LspToolCall(
                tool_name="lsp.hover",
                args={"file_path": EDIT_FILE, "line": 0, "character": 4},
                assertions=(_assert_hover_contains("transform"),),
            ),
            LspToolCall(
                tool_name="lsp.hover",
                args={"file_path": EDIT_FILE, "line": 0, "character": 4},
                assertions=(_assert_hover_contains("transform", "str"),),
            ),
        ),
        edits=((1, LspEdit(file_path=EDIT_FILE, new_contents=EDIT_BODY_V2)),),
    ),
    LspScenario(
        name="complex_all_tools_layerstack_write_edit_cycle",
        description=(
            "Exercises every LSP tool after multiple layer-stack writes and "
            "search/replace edits."
        ),
        setup_files={
            COMPLEX_CONFIG: COMPLEX_CONFIG_BODY,
            COMPLEX_INIT: "",
            COMPLEX_MODEL: COMPLEX_MODEL_V1,
            COMPLEX_SERVICE: COMPLEX_SERVICE_V1,
            COMPLEX_CONSUMER: COMPLEX_CONSUMER_V1,
        },
        tool_calls=(
            LspToolCall(
                tool_name="lsp.hover",
                args={"file_path": COMPLEX_MODEL, "line": 7, "character": 4},
                assertions=(_assert_hover_contains("display_name", "str"),),
            ),
            LspToolCall(
                tool_name="lsp.find_definitions",
                args={"file_path": COMPLEX_SERVICE, "line": 3, "character": 13},
                assertions=(_assert_definitions_at("model.py", 7),),
            ),
            LspToolCall(
                tool_name="lsp.find_references",
                args={
                    "file_path": COMPLEX_MODEL,
                    "line": 7,
                    "character": 4,
                    "include_declaration": False,
                },
                assertions=(_assert_references_count_at_least(3),),
            ),
            LspToolCall(
                tool_name="lsp.query_symbols",
                args={"query": "", "file_path": COMPLEX_MODEL},
                assertions=(_assert_symbols_include("UserProfile", "display_name"),),
            ),
            LspToolCall(
                tool_name="lsp.diagnostics",
                args={"file_path": COMPLEX_CONSUMER},
                assertions=(_assert_diagnostics_mention("missing_value"),),
            ),
            LspToolCall(
                tool_name="lsp.diagnostics",
                args={"file_path": COMPLEX_CONSUMER},
                assertions=(_assert_no_diagnostics(),),
            ),
            LspToolCall(
                tool_name="lsp.hover",
                args={"file_path": COMPLEX_MODEL, "line": 7, "character": 4},
                assertions=(_assert_hover_contains("display_name", "int"),),
            ),
            LspToolCall(
                tool_name="lsp.find_definitions",
                args={"file_path": COMPLEX_SERVICE, "line": 3, "character": 13},
                assertions=(_assert_definitions_at("model.py", 7),),
            ),
            LspToolCall(
                tool_name="lsp.diagnostics",
                args={"file_path": COMPLEX_SERVICE},
                assertions=(_assert_no_diagnostics(),),
            ),
        ),
        edits=(
            (
                5,
                LspEdit(
                    file_path=COMPLEX_CONSUMER,
                    old_text=COMPLEX_CONSUMER_MISSING_EDIT,
                    new_contents=COMPLEX_CONSUMER_FIXED,
                ),
            ),
            (
                6,
                LspEdit(
                    file_path=COMPLEX_MODEL,
                    old_text=COMPLEX_MODEL_RETURN_EDIT,
                    new_contents=COMPLEX_MODEL_V2_RETURN,
                ),
            ),
            (
                6,
                LspEdit(
                    file_path=COMPLEX_SERVICE,
                    old_text=COMPLEX_SERVICE_NAME_EDIT,
                    new_contents=COMPLEX_SERVICE_NAME_V2,
                ),
            ),
        ),
    ),
)
