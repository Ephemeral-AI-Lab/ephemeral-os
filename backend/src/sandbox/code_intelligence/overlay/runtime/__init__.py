"""Sandbox-side overlay runtime package."""

from __future__ import annotations

from .mounts import (
    OverlayMountError,
    _NS_LOWER,
    _NS_MERGED,
    _NS_ROOT,
    _NS_TMP,
    _NS_UPPER,
    _NS_WORK,
    setup_mounts,
)
from .ndjson import write_diff_ndjson, write_reject_ndjson
from .runner import (
    REJECT_UPPER_FULL,
    _parse_args,
    _write_result_json,
    is_opaque_dir,
    is_whiteout,
    main,
    reject_exit_code,
    run_user_command,
    walk_upperdir,
)
from .types import PolicyRejectOutcome, UpperChange, UpperChangeKind, UpperEntry

__all__ = [
    "OverlayMountError",
    "PolicyRejectOutcome",
    "REJECT_UPPER_FULL",
    "UpperChange",
    "UpperChangeKind",
    "UpperEntry",
    "_NS_LOWER",
    "_NS_MERGED",
    "_NS_ROOT",
    "_NS_TMP",
    "_NS_UPPER",
    "_NS_WORK",
    "_parse_args",
    "_write_result_json",
    "is_opaque_dir",
    "is_whiteout",
    "main",
    "reject_exit_code",
    "run_user_command",
    "setup_mounts",
    "walk_upperdir",
    "write_diff_ndjson",
    "write_reject_ndjson",
]
