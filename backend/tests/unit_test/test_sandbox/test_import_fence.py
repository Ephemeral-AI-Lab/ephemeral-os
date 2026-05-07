"""Import-fence tests for the sandbox public API cutover."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
_TOOL_ALLOWED = {
    "sandbox.api",
    "sandbox.api.tool.edit",
    "sandbox.api.tool.read",
    "sandbox.api.tool.shell",
    "sandbox.api.tool.write",
}
_TOOL_FORBIDDEN_PREFIXES = (
    "sandbox.api.tool.raw_exec",
    "sandbox.providers",
    "sandbox.occ",
    "sandbox.overlay",
    "sandbox.daemon",
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


def test_daemon_code_does_not_import_daytona_provider_modules() -> None:
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
# Provider-agnostic status/control fence (locks the seam from the plan)
# ---------------------------------------------------------------------------


# Allowlisted importer of sandbox.providers.daytona.* outside the daytona
# package itself: the single startup bootstrap call.
_DAYTONA_IMPORT_ALLOWLIST = {
    Path("server/app_factory.py"),
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


def test_host_runtime_api_do_not_import_daytona_sdk() -> None:
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


def test_host_runtime_api_status_do_not_import_daytona_provider() -> None:
    """The locked seam: control/, runtime/, and api/status are
    provider-neutral — none of them imports sandbox.providers.daytona.*."""
    offenders: list[str] = []
    for path in (
        SRC_ROOT / "sandbox" / "control",
        SRC_ROOT / "sandbox" / "runtime",
        SRC_ROOT / "sandbox" / "api" / "status",
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
        "control/, runtime/, and api/status must not import "
        f"sandbox.providers.daytona.*: {offenders}"
    )


def test_occ_policy_modules_depend_on_layer_stack_ports_not_manager() -> None:
    """OCC internals use role protocols instead of the concrete storage facade."""
    offenders: list[str] = []
    occ_root = SRC_ROOT / "sandbox" / "occ"
    allowed = {
        occ_root / "ports.py",
    }
    forbidden = (
        "sandbox.layer_stack.stack_manager",
        "sandbox.layer_stack.merged_view",
        "sandbox.layer_stack.publisher",
        "sandbox.layer_stack.lease_registry",
        "sandbox.daemon.services.workspace_server",
    )
    for module in _python_files(occ_root):
        if module in allowed:
            continue
        for imported in _imports(module):
            if imported in forbidden or any(
                imported.startswith(f"{prefix}.") for prefix in forbidden
            ):
                offenders.append(f"{module.relative_to(SRC_ROOT)} imports {imported}")

    assert offenders == []


def test_layer_stack_package_has_no_occ_command_exec_or_git_policy_imports() -> None:
    offenders: list[str] = []
    forbidden = (
        "sandbox.occ",
        "sandbox.command_exec",
        "sandbox.daemon.services.workspace_binding",
        "pathspec",
    )
    for module in _python_files(SRC_ROOT / "sandbox" / "layer_stack"):
        for imported in _imports(module):
            if imported in forbidden or any(
                imported.startswith(f"{prefix}.") for prefix in forbidden
            ):
                offenders.append(f"{module.relative_to(SRC_ROOT)} imports {imported}")

    assert offenders == []


def test_removed_lowerdir_cache_modules_stay_absent() -> None:
    for module in (
        "sandbox.layer_stack.snapshot_cache",
        "sandbox.layer_stack.metrics",
    ):
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            assert exc.name == module
        else:
            raise AssertionError(f"{module} should not be importable")


def test_command_exec_imports_only_client_protocol_boundaries() -> None:
    offenders: list[str] = []
    command_exec_root = SRC_ROOT / "sandbox" / "command_exec"
    forbidden = (
        "sandbox.layer_stack",
        "sandbox.occ.service",
        "sandbox.occ.commit_transaction",
        "sandbox.occ.content.gitignore_oracle",
        "sandbox.occ.direct",
        "sandbox.occ.gated",
        "sandbox.occ.orchestrator",
        "sandbox.occ.runtime_ops",
        "sandbox.daemon.services.workspace_server",
    )
    for module in _python_files(command_exec_root):
        for imported in _imports(module):
            if imported in forbidden or any(
                imported.startswith(f"{prefix}.") for prefix in forbidden
            ):
                offenders.append(f"{module.relative_to(SRC_ROOT)} imports {imported}")

    assert offenders == []


def _python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if "__pycache__" not in path.parts)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names
