"""Shared provider payload normalization helpers."""

from __future__ import annotations


def normalize_string_dict(payload: dict[str, str] | None) -> dict[str, str]:
    if not payload:
        return {}
    return {
        str(key).strip(): str(value).strip()
        for key, value in payload.items()
        if str(key).strip()
    }


__all__ = ["normalize_string_dict"]
