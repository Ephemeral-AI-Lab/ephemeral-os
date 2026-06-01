"""R10: ``setns_exec`` helper subprocess must stay single-threaded.

``setns(CLONE_NEWUSER)`` from libc requires the calling thread to be the only
thread in the process; importing ``logging`` / ``asyncio`` / ``subprocess`` /
``threading`` (or any module that silently spins a background thread) breaks
the syscall with EINVAL.

This fence pins the module-level import set of ``setns_exec.py`` and the
shared libc wrapper ``_setns_libc.py``. The allowlist is intentionally tight:
adding any other dependency means thinking hard about whether it touches the
threading state machine.
"""

from __future__ import annotations

import ast
from pathlib import Path


_SRC_ROOT = Path(__file__).resolve().parents[7] / "src"
_FORBIDDEN_NAMES = frozenset(
    {
        "logging",
        "asyncio",
        "subprocess",
        "threading",
        "concurrent",
        "concurrent.futures",
        "multiprocessing",
    }
)

_SETNS_EXEC_PATH = _SRC_ROOT / "sandbox/isolated_workspace/scripts/setns_exec.py"
_SETNS_LIBC_PATH = _SRC_ROOT / "sandbox/isolated_workspace/scripts/_setns_libc.py"
_SETNS_OVERLAY_MOUNT_PATH = _SRC_ROOT / "sandbox/isolated_workspace/scripts/setns_overlay_mount.py"
_CONFIGURE_DNS_PATH = _SRC_ROOT / "sandbox/isolated_workspace/scripts/configure_dns_in_ns.py"

# Module-level allowlist. Function-body imports that run AFTER the
# setns calls are intentionally permitted — the single-thread requirement
# for setns(CLONE_NEWUSER) only applies before those syscalls.
_SETNS_HELPER_ALLOWED = frozenset(
    {
        "__future__",
        "ctypes",
        "json",
        "os",
        "sys",
        "sandbox.isolated_workspace.scripts._setns_libc",
        "sandbox.isolated_workspace.scripts",
    }
)

_SETNS_LIBC_ALLOWED = frozenset(
    {
        "__future__",
        "ctypes",
        "os",
    }
)


def test_setns_exec_helper_imports_are_minimal() -> None:
    _assert_helper_discipline(_SETNS_EXEC_PATH, _SETNS_HELPER_ALLOWED)


def test_setns_overlay_mount_helper_imports_are_minimal() -> None:
    _assert_helper_discipline(_SETNS_OVERLAY_MOUNT_PATH, _SETNS_HELPER_ALLOWED)


def test_setns_overlay_mount_reuses_shared_mount_validation() -> None:
    source = _SETNS_OVERLAY_MOUNT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_SETNS_OVERLAY_MOUNT_PATH))
    kernel_mount_imports: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "sandbox.overlay.kernel_mount"
        ):
            kernel_mount_imports.update(alias.name for alias in node.names)
    assert {"mount_overlay", "validate_mount_inputs"} <= kernel_mount_imports
    assert "mount_inputs.close()" in source


def test_configure_dns_in_ns_helper_imports_are_minimal() -> None:
    _assert_helper_discipline(_CONFIGURE_DNS_PATH, _SETNS_HELPER_ALLOWED)


def test_setns_libc_helper_imports_are_minimal() -> None:
    imports = _imports(_SETNS_LIBC_PATH)
    violations = sorted(imports - _SETNS_LIBC_ALLOWED)
    assert violations == [], (
        "_setns_libc.py must keep its import set minimal so callers stay "
        f"single-threaded; unexpected imports: {violations}"
    )
    forbidden = sorted(imports & _FORBIDDEN_NAMES)
    assert forbidden == [], (
        "_setns_libc.py must not import any module that may spawn background "
        f"threads: {forbidden}"
    )


def _assert_helper_discipline(path: Path, allowed: frozenset[str]) -> None:
    imports = _imports(path)
    violations = sorted(imports - allowed)
    assert violations == [], (
        f"{path.name} must keep its import set minimal so the helper stays "
        f"single-threaded; unexpected imports: {violations}"
    )
    forbidden = sorted(imports & _FORBIDDEN_NAMES)
    assert forbidden == [], (
        f"{path.name} must not import any module that may spawn background "
        f"threads: {forbidden}"
    )


def _imports(path: Path) -> set[str]:
    """Return module-level imports only.

    The setns single-thread requirement applies AT setns-call time. Imports
    nested in function bodies (e.g., deferred after ``setns()`` returns)
    are intentionally outside the fence — we only inspect ``tree.body``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names
