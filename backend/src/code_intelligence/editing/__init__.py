"""Editing subpackage — OCC coordination, patching, undo, and audit."""

from ephemeralos.code_intelligence.editing.arbiter import Arbiter
from ephemeralos.code_intelligence.editing.ledger import Ledger
from ephemeralos.code_intelligence.editing.patcher import Patcher, SearchReplaceEdit, LineRangeEdit, PatchResult
from ephemeralos.code_intelligence.editing.time_machine import TimeMachine

__all__ = ["Arbiter", "Ledger", "LineRangeEdit", "PatchResult", "Patcher", "SearchReplaceEdit", "TimeMachine"]
