"""In-sandbox indexing CLI.

Run with::

    python -m sandbox.code_intelligence.in_sandbox.ci_index \
        --workspace-root <path> [--file <single>]

Constructs a :class:`CodeIntelligenceService` against ``sandbox=None,
transport=None`` so the existing local-FS branches activate, indexes the
workspace, and persists the snapshot via :mod:`ci_storage`.

Exit codes:

* 0 — success.
* 13 — :class:`CiStorageUnavailable` (privilege failure on
  ``$HOME/.cache/eos-ci``); structured JSON error printed to stdout so the
  Phase 1 privilege probe can read it back.
* 1 — any other failure; structured JSON with ``ok: false`` printed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from sandbox.code_intelligence.in_sandbox.ci_storage import (
    CiStorageUnavailable,
    read_snapshot,
    state_dir,
    write_snapshot,
)
from sandbox.code_intelligence.service import CodeIntelligenceService

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ci_index",
        description="Index the workspace and persist a snapshot under $HOME/.cache/eos-ci.",
    )
    parser.add_argument(
        "--workspace-root",
        required=True,
        help="Workspace root to index.",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Re-index a single file only (incremental refresh).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    started = time.perf_counter()
    try:
        state = state_dir(args.workspace_root)
    except CiStorageUnavailable as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "storage_unavailable",
                    "errno": exc.errno,
                    "path": exc.path,
                    "message": exc.message,
                }
            )
        )
        return 13

    svc = CodeIntelligenceService(
        sandbox_id="local",
        workspace_root=args.workspace_root,
        sandbox=None,
        transport=None,
    )

    if args.file:
        prior = read_snapshot(state, "index.snapshot") or {}
        if not isinstance(prior, dict):
            prior = {}
        gen = svc.symbol_index.refresh(args.file)
        prior[args.file] = svc.symbol_index.file_symbols(args.file)
        write_snapshot(state, "index.snapshot", prior)
        elapsed = time.perf_counter() - started
        print(
            json.dumps(
                {
                    "ok": True,
                    "mode": "refresh_one",
                    "file": args.file,
                    "generation": gen,
                    "elapsed_s": round(elapsed, 4),
                }
            )
        )
        return 0

    svc.ensure_initialized(wait=True)
    indexed_paths = svc.symbol_index.indexed_paths()
    snapshot = {fp: svc.symbol_index.file_symbols(fp) for fp in indexed_paths}
    write_snapshot(state, "index.snapshot", snapshot)
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "ok": True,
                "mode": "full_build",
                "file_count": svc.symbol_index.indexed_files,
                "symbol_count": svc.symbol_index.size,
                "snapshot_path": str(state / "index.snapshot"),
                "elapsed_s": round(elapsed, 4),
            }
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by CLI invocation
    sys.exit(main())
