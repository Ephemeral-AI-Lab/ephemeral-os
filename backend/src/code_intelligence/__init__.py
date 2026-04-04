"""Code intelligence service — AST caching, symbol indexing, OCC, and LSP integration."""

from ephemeralos.code_intelligence.types import (
    CITelemetry,
    Diagnostic,
    EditRequest,
    EditResult,
    HoverResult,
    ReferenceInfo,
    SymbolInfo,
)
from ephemeralos.code_intelligence.routing.gateway import CodeIntelligenceGateway
from ephemeralos.code_intelligence.routing.service import CodeIntelligenceService

__all__ = [
    "CITelemetry",
    "CodeIntelligenceGateway",
    "CodeIntelligenceService",
    "Diagnostic",
    "EditRequest",
    "EditResult",
    "HoverResult",
    "ReferenceInfo",
    "SymbolInfo",
]
