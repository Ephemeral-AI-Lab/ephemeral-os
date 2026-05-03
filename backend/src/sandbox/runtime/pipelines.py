"""Runtime pipeline placeholders.

OCC and Overlay fill these in during the next relocation slices. They remain
unregistered and unreachable from agent paths in this slice.
"""

from __future__ import annotations

from typing import NoReturn


def shell_pipeline(*_args: object, **_kwargs: object) -> NoReturn:
    raise NotImplementedError("shell_pipeline is implemented in Slice 5b")


def edit_pipeline(*_args: object, **_kwargs: object) -> NoReturn:
    raise NotImplementedError("edit_pipeline is implemented in Slice 4")


def write_pipeline(*_args: object, **_kwargs: object) -> NoReturn:
    raise NotImplementedError("write_pipeline is implemented in Slice 4")


__all__ = ["edit_pipeline", "shell_pipeline", "write_pipeline"]

