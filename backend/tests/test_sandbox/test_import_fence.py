"""Import-fence tests for the sandbox public API cutover."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import re
from pathlib import Path

import pytest


SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
_TOOL_ALLOWED = {
    "sandbox.api",
    "sandbox.api.edit",
    "sandbox.api.read",
    "sandbox.api.shell",
    "sandbox.api.write",
}
_TOOL_FORBIDDEN_PREFIXES = (
    "sandbox.api.raw_exec",
    "sandbox.providers",
    "sandbox.occ",
    "sandbox.overlay",
    "sandbox.runtime",
    "sandbox.daytona",
    "sandbox.code_intelligence",
)


def test_agent_sandbox_tools_import_only_public_api_verbs() -> None:
    offenders: list[str] = []
    for module in _python_files(SRC_ROOT / "tools" / "sandbox_toolkit"):
        for imported in _imports(module):
            if not imported.startswith("sandbox."):
                continue
            if imported in _TOOL_ALLOWED:
                continue
            if any(
                imported == prefix or imported.startswith(f"{prefix}.")
                for prefix in _TOOL_FORBIDDEN_PREFIXES
            ):
                offenders.append(
                    f"{module.relative_to(SRC_ROOT)} imports {imported}"
                )
                continue
            offenders.append(f"{module.relative_to(SRC_ROOT)} imports {imported}")

    assert offenders == []


def test_non_api_production_code_does_not_import_private_api_utils() -> None:
    offenders: list[str] = []
    api_root = SRC_ROOT / "sandbox" / "api"
    for module in _python_files(SRC_ROOT):
        if module.is_relative_to(api_root):
            continue
        for imported in _imports(module):
            if imported == "sandbox.api.utils" or imported.startswith(
                "sandbox.api.utils."
            ):
                offenders.append(f"{module.relative_to(SRC_ROOT)} imports {imported}")

    assert offenders == []


def test_runtime_code_does_not_import_daytona_provider_modules() -> None:
    offenders: list[str] = []
    runtime_root = SRC_ROOT / "sandbox" / "runtime"
    for module in _python_files(runtime_root):
        for imported in _imports(module):
            if imported == "sandbox.providers.daytona" or imported.startswith(
                "sandbox.providers.daytona."
            ):
                offenders.append(f"{module.relative_to(SRC_ROOT)} imports {imported}")

    assert offenders == []


# ---------------------------------------------------------------------------
# Provider-agnostic lifecycle fence (locks the seam from the plan)
# ---------------------------------------------------------------------------


# Allowlisted importers of sandbox.providers.daytona.* outside the daytona
# package itself.
#
# AUTHORIZED EXCEPTION (per .omc/plans/sandbox-provider-agnostic-lifecycle.md
# §Success criteria, lines 21-26):
#   `server/app_factory.py` — the single startup bootstrap call to
#   `bootstrap_daytona_provider()`.
#
# DEVIATION FROM PLAN (scope reduction — needs follow-up):
#   `benchmarks/sweevo/sandbox.py` retains two daytona imports because it
#   uses raw SDK semantics that aren't on the ProviderAdapter primitive
#   surface today: `set_labels()` on the raw sandbox and direct
#   `sandbox.process.exec(...)` for streaming/binary upload paths. The
#   plan's §Step 6 expected sweevo to migrate fully; that's deferred to
#   a follow-up because the primitive gap (a) is not in the plan's
#   §Out of scope list and (b) requires either a new `set_labels`
#   primitive or rebuilding sweevo's exec helpers on top of `provider.exec`.
#   Architect verification should explicitly approve or reject this
#   carve-out.
_DAYTONA_IMPORT_ALLOWLIST = {
    Path("server/app_factory.py"),
    Path("benchmarks/sweevo/sandbox.py"),
}


def test_no_daytona_imports_outside_provider_package_or_bootstrap() -> None:
    """Daytona is exposed only through the adapter — the provider-agnostic seam."""
    offenders: list[str] = []
    daytona_root = SRC_ROOT / "sandbox" / "providers" / "daytona"
    for module in _python_files(SRC_ROOT):
        rel = module.relative_to(SRC_ROOT)
        if module.is_relative_to(daytona_root):
            continue
        if rel in _DAYTONA_IMPORT_ALLOWLIST:
            continue
        for imported in _imports(module):
            if imported == "sandbox.providers.daytona" or imported.startswith(
                "sandbox.providers.daytona."
            ):
                offenders.append(f"{rel} imports {imported}")

    assert offenders == [], (
        "Modules must not import sandbox.providers.daytona.* outside the "
        f"daytona package: {offenders}"
    )


def test_control_runtime_api_do_not_import_daytona_sdk() -> None:
    """control/, runtime/, api/ stay free of any direct daytona_sdk usage."""
    offenders: list[str] = []
    for sub in ("control", "runtime", "api"):
        root = SRC_ROOT / "sandbox" / sub
        for module in _python_files(root):
            for imported in _imports(module):
                if imported == "daytona_sdk" or imported.startswith("daytona_sdk."):
                    offenders.append(
                        f"{module.relative_to(SRC_ROOT)} imports {imported}"
                    )

    assert offenders == [], (
        "control/, runtime/, api/ must not import any daytona SDK module: "
        f"{offenders}"
    )


def test_control_runtime_api_lifecycle_do_not_import_daytona_provider() -> None:
    """The plan's locked seam: control/, runtime/, and api/lifecycle.py are
    provider-neutral — none of them imports sandbox.providers.daytona.*."""
    offenders: list[str] = []
    for path in (
        SRC_ROOT / "sandbox" / "control",
        SRC_ROOT / "sandbox" / "runtime",
        SRC_ROOT / "sandbox" / "api" / "lifecycle.py",
    ):
        if path.is_file():
            modules = [path]
        else:
            modules = _python_files(path)
        for module in modules:
            for imported in _imports(module):
                if imported == "sandbox.providers.daytona" or imported.startswith(
                    "sandbox.providers.daytona."
                ):
                    offenders.append(
                        f"{module.relative_to(SRC_ROOT)} imports {imported}"
                    )

    assert offenders == [], (
        "control/, runtime/, and api/lifecycle.py must not import "
        f"sandbox.providers.daytona.*: {offenders}"
    )


def test_no_sandbox_lifecycle_package_remains() -> None:
    """sandbox.lifecycle is gone — the name is reserved for sandbox.api.lifecycle."""
    assert _find_spec_or_none("sandbox.lifecycle") is None
    assert _find_spec_or_none("sandbox.lifecycle.factory") is None
    assert _find_spec_or_none("sandbox.lifecycle.workspace") is None
    assert _find_spec_or_none("sandbox.lifecycle.context") is None


def test_no_sandbox_service_symbol_in_src() -> None:
    """grep -r 'SandboxService' backend/src returns zero hits in non-daytona files."""
    offenders: list[str] = []
    for module in _python_files(SRC_ROOT):
        text = module.read_text(encoding="utf-8")
        if "SandboxService" in text:
            offenders.append(str(module.relative_to(SRC_ROOT)))
    assert offenders == [], f"SandboxService symbol still present: {offenders}"


def test_legacy_lifecycle_classes_are_unimportable() -> None:
    """SandboxProxy and DaytonaSandboxLifecycle are deleted."""
    assert _find_spec_or_none("sandbox.providers.daytona.lifecycle") is None
    assert _find_spec_or_none("sandbox.providers.daytona.proxy") is None


def test_deleted_legacy_sandbox_modules_are_unimportable() -> None:
    for module_name in (
        "sandbox.code_intelligence",
        "sandbox.api._changeset_projection",
        "sandbox.api.bash",
        "sandbox.api.models",
        "sandbox.api.shell_routing",
        "sandbox.api.utils.shell_routing",
        "sandbox.api.file_commands",
        "sandbox.api.transport",
        "sandbox.api.audited_sandbox_api",
        "sandbox.client.async_",
        "sandbox.client.async_bridge",
        "sandbox.client.async_shutdown",
        "sandbox.client.credentials",
        "sandbox.client.sync",
        "sandbox.daytona",
        "sandbox.daytona.transport",
        "sandbox.errors",
        "sandbox.lifecycle.proxy",
        "sandbox.lifecycle.service",
    ):
        assert _find_spec_or_none(module_name) is None


def test_deleted_code_intelligence_package_raises_module_not_found() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("sandbox.code_intelligence")


def test_deleted_sandbox_transport_symbol_raises_import_error() -> None:
    with pytest.raises(ImportError):
        __import__("sandbox.api.transport", fromlist=["SandboxTransport"])


def test_sandbox_source_has_no_code_intelligence_terms() -> None:
    forbidden = {
        "code_intelligence": re.compile(r"code_intelligence", re.IGNORECASE),
        "code intelligence": re.compile(r"code intelligence", re.IGNORECASE),
        "code-intelligence": re.compile(r"code-intelligence", re.IGNORECASE),
        "standalone ci": re.compile(r"\bci\b", re.IGNORECASE),
    }
    offenders: list[str] = []
    for module in _python_files(SRC_ROOT / "sandbox"):
        text = module.read_text(encoding="utf-8")
        for label, pattern in forbidden.items():
            if pattern.search(text):
                offenders.append(f"{module.relative_to(SRC_ROOT)} contains {label}")

    assert offenders == []


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _find_spec_or_none(module_name: str) -> object | None:
    try:
        return importlib.util.find_spec(module_name)
    except ModuleNotFoundError:
        return None


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names
