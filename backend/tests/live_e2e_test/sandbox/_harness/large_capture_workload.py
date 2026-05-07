"""Helpers for the Phase 06 K-scaling shell-capture benchmark."""

from __future__ import annotations


def build_k_capture_command(prefix: str, k: int) -> str:
    """Return a bash command that creates K small files under ``prefix``.

    The shape (printf into many files) approximates the side-effect of
    ``pip install`` / ``npm install`` / ``cargo build`` without depending on
    network or any specific package layout.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if not prefix:
        raise ValueError("prefix must be non-empty")
    return (
        "set -e; "
        f"mkdir -p {prefix}; "
        f"for i in $(seq 1 {k}); do "
        f"  printf -v fname '%06d' \"$i\"; "
        f"  printf 'k=%d i=%d\\n' {k} \"$i\" > {prefix}/file_$fname.bin; "
        "done"
    )


__all__ = ["build_k_capture_command"]
