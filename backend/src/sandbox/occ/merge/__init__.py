"""Facade for OCC merge engines used by runtime probes and diagnostics."""

from __future__ import annotations

from sandbox.occ.merge.direct import DirectMerge
from sandbox.occ.merge.gated import GatedMerge


__all__ = ["DirectMerge", "GatedMerge"]
