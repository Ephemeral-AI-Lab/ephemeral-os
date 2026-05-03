"""Sandbox-side overlay runtime package."""

from __future__ import annotations

from .capture import build_upper_change, is_opaque_dir, is_whiteout, walk_upperdir
from .cli import REJECT_UPPER_FULL, main, parse_args, reject_exit_code
from .command import run_user_command
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
from .ndjson import write_diff_ndjson, write_reject_ndjson, write_result_json
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
    "build_upper_change",
    "is_opaque_dir",
    "is_whiteout",
    "main",
    "parse_args",
    "reject_exit_code",
    "run_user_command",
    "setup_mounts",
    "walk_upperdir",
    "write_diff_ndjson",
    "write_reject_ndjson",
    "write_result_json",
]
