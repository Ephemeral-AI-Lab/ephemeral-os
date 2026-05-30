"""Deep import gate for the reducers redesign: import every submodule of the
source packages so a broken import after a rename surfaces immediately (a plain
``import task_center`` is a lazy facade and would hide it). Run as:

    PYTHONPATH=backend/src .venv/bin/python docs/plans/seam_maps/_deep_import.py

Exits non-zero if any module fails to import. Modules that do not yet exist at a
given step are simply absent from the walk (no failure).
"""

from __future__ import annotations

import importlib
import pkgutil
import sys

PACKAGES = ["task_center", "tools", "agents", "db", "task_center_runner"]


def main() -> int:
    ok = 0
    failures: list[str] = []
    for pkg_name in PACKAGES:
        try:
            pkg = importlib.import_module(pkg_name)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{pkg_name} -> {type(exc).__name__}: {exc}")
            continue
        for mod in pkgutil.walk_packages(pkg.__path__, pkg_name + "."):
            try:
                importlib.import_module(mod.name)
                ok += 1
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{mod.name} -> {type(exc).__name__}: {exc}")
    for line in failures:
        print("FAIL", line)
    print(f"DEEP_IMPORT ok={ok} bad={len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
