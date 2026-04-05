"""Routing subpackage — query dispatch and service orchestration."""

from code_intelligence.routing.backend_protocol import (
    CodeIntelligenceBackend,
    LspBackendAdapter,
    SymbolIndexBackendAdapter,
)
from code_intelligence.routing.query_router import IntelligenceQueryRouter
from code_intelligence.routing.service import (
    CodeIntelligenceService,
    dispose_all_code_intelligence,
    dispose_code_intelligence,
    get_all_services_status,
    get_code_intelligence,
    get_code_intelligence_if_exists,
)

__all__ = [
    "CodeIntelligenceBackend",
    "CodeIntelligenceService",
    "IntelligenceQueryRouter",
    "LspBackendAdapter",
    "SymbolIndexBackendAdapter",
    "dispose_all_code_intelligence",
    "dispose_code_intelligence",
    "get_all_services_status",
    "get_code_intelligence",
    "get_code_intelligence_if_exists",
]
