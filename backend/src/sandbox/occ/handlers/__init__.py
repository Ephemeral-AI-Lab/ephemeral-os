"""OCC runtime operation registration."""

from __future__ import annotations

from sandbox.runtime import server

from . import apply, apply_changeset, commit, edit, write


_HANDLERS = {
    "occ.apply": apply.handle,
    "occ.apply_changeset": apply_changeset.handle,
    "occ.commit_against_base": commit.handle_against_base,
    "occ.commit_many": commit.handle_many,
    "occ.edit": edit.handle,
    "occ.write": write.handle,
}


def register_handlers() -> None:
    for op, handler in _HANDLERS.items():
        existing = server.OP_TABLE.get(op)
        if existing is not None:
            if str(getattr(existing, "__module__", "")).startswith(
                "sandbox.occ.handlers."
            ):
                continue
            raise ValueError(f"runtime op already registered: {op}")
        server.register_op(op, handler)


__all__ = ["register_handlers"]
