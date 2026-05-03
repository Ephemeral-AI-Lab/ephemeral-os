"""Code Intelligence API router for mutation and telemetry endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/code_intelligence", tags=["code_intelligence"])


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class EditRequest(BaseModel):
    file_path: str
    old_text: str
    new_text: str
    agent_id: str = ""
    description: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_service(sandbox_id: str, workspace_root: str = "/workspace") -> Any:
    """Get or create a CI service for a sandbox via SandboxService."""
    from sandbox.lifecycle.service import SandboxService

    return SandboxService().code_intelligence_for(
        sandbox_id, workspace_root=workspace_root
    )


def _get_service_if_exists(sandbox_id: str) -> Any:
    """Get existing CI service via SandboxService, or raise 404."""
    from sandbox.lifecycle.service import SandboxService

    service = SandboxService().code_intelligence_if_exists(sandbox_id)
    if service is None:
        raise HTTPException(404, f"No CI service for sandbox '{sandbox_id}'")
    return service


@router.post("/initialize/{sandbox_id}")
async def initialize(sandbox_id: str, workspace_root: str = "/workspace") -> dict:
    """Initialize CI service for a sandbox."""
    service = _get_service(sandbox_id, workspace_root)
    ready = service.ensure_initialized(wait=True)
    return {"sandbox_id": sandbox_id, "initialized": ready}


# ---------------------------------------------------------------------------
# Edit endpoints
# ---------------------------------------------------------------------------


@router.post("/{sandbox_id}/edit")
async def apply_edit(sandbox_id: str, request: EditRequest) -> dict:
    """Apply a code-intelligence service edit."""
    service = _get_service_if_exists(sandbox_id)
    from sandbox.code_intelligence.core.types import EditRequest as CIEditRequest

    result = service.apply_edit(
        CIEditRequest(
            file_path=request.file_path,
            old_text=request.old_text,
            new_text=request.new_text,
            agent_id=request.agent_id,
            description=request.description,
        )
    )
    return {
        "success": result.success,
        "file_path": result.file_path,
        "message": result.message,
        "conflict": result.conflict,
    }


@router.post("/{sandbox_id}/undo")
async def undo_edit(sandbox_id: str, file_path: str = Query(...)) -> dict:
    """Undo the last edit to a file."""
    service = _get_service_if_exists(sandbox_id)
    result = service.undo_last_edit(file_path)
    return {
        "success": result.success,
        "file_path": result.file_path,
        "message": result.message,
    }


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@router.post("/{sandbox_id}/dispose")
async def dispose_service(sandbox_id: str) -> dict:
    """Dispose CI service for a sandbox."""
    from sandbox.lifecycle.service import SandboxService

    SandboxService().dispose_code_intelligence(sandbox_id)
    return {"disposed": sandbox_id}
