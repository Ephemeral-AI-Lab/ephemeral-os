"""Editing subpackage — OCC coordination, patching, undo, and audit."""

from code_intelligence.editing.arbiter import Arbiter
from code_intelligence.editing.ledger import Ledger
from code_intelligence.editing.patcher import Patcher, SearchReplaceEdit, LineRangeEdit, PatchResult
from code_intelligence.editing.time_machine import TimeMachine

__all__ = ["Arbiter", "Ledger", "LineRangeEdit", "PatchResult", "Patcher", "SearchReplaceEdit", "TimeMachine"]
