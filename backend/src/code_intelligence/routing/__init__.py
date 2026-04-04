"""Routing subpackage — query dispatch, service orchestration, and gateway."""

from ephemeralos.code_intelligence.routing.backend_protocol import (
    CodeIntelligenceBackend,
    LspBackendAdapter,
    SymbolIndexBackendAdapter,
)
from ephemeralos.code_intelligence.routing.query_router import IntelligenceQueryRouter
from ephemeralos.code_intelligence.routing.service import (
    CodeIntelligenceService,
    dispose_all_code_intelligence,
    dispose_code_intelligence,
    get_all_services_status,
    get_code_intelligence,
    get_code_intelligence_if_exists,
)
from ephemeralos.code_intelligence.routing.gateway import (
    CodeIntelligenceGateway,
    get_code_intelligence_gateway,
)

__all__ = [
    "CodeIntelligenceBackend",
    "CodeIntelligenceGateway",
    "CodeIntelligenceService",
    "IntelligenceQueryRouter",
    "LspBackendAdapter",
    "SymbolIndexBackendAdapter",
    "dispose_all_code_intelligence",
    "dispose_code_intelligence",
    "get_all_services_status",
    "get_code_intelligence",
    "get_code_intelligence_gateway",
    "get_code_intelligence_if_exists",
]
