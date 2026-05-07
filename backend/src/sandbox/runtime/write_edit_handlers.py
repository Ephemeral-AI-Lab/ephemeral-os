"""Backward-compat re-export module for the per-verb handler split.

The post-Phase-05 refactor moved the host-facing api.{write,edit,read}_file
dispatch into :mod:`sandbox.runtime.handlers` (one module per verb) plus
shared scaffolding in :mod:`sandbox.runtime.handlers._common`. Existing
test fixtures still import the old ``write_edit_handlers`` symbol set;
this module preserves that surface while routing every call to the
canonical location.

After Phase 05.5 the OCC backend tuple itself is owned by
:mod:`sandbox.runtime.occ_server`; ``_services`` resolves through the
shared cache there, so monkey-patches that need to redirect construction
must target ``occ_server`` (e.g.,
``monkeypatch.setattr(occ_server, "LayerStackClient", ...)``).
"""

from __future__ import annotations

from sandbox.runtime.handlers._common import (
    ClassifiedPath,
    _layer_stack_root,
    _required_single_path,
    _services,
    _services_cache_clear,
    classify_path,
    drop_services_cache,
)
from sandbox.runtime.handlers.edit_handler import edit_file
from sandbox.runtime.handlers.read_handler import read_file
from sandbox.runtime.handlers.write_handler import write_file


__all__ = [
    "ClassifiedPath",
    "classify_path",
    "drop_services_cache",
    "edit_file",
    "read_file",
    "write_file",
    "_services",
    "_services_cache_clear",
    "_layer_stack_root",
    "_required_single_path",
]
