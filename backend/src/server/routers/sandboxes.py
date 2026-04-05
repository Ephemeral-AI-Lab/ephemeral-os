"""Sandbox (Daytona) API routes — delegates to SandboxService."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from sandbox.service import SandboxService

logger = logging.getLogger(__name__)


class CreateSandboxRequest(BaseModel):
    name: str = Field(min_length=1)
    snapshot: str | None = None
    image: str | None = None
    env_vars: dict[str, str] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)


class ExecRequest(BaseModel):
    command: str
    timeout: int = 30


def create_sandbox_router(service: SandboxService | None = None) -> APIRouter:
    """Build the sandbox API router."""
    router = APIRouter(prefix="/api/sandboxes")
    svc = service or SandboxService()

    # --- Static path routes MUST be registered before parameterized routes ---

    @router.get("/health")
    async def sandbox_health():
        """Check Daytona connection health."""
        result = await asyncio.to_thread(svc.get_health)
        return JSONResponse(content=result)

    @router.get("/available/snapshots")
    async def list_snapshots():
        """List available Daytona snapshots."""
        try:
            items = await asyncio.to_thread(svc.list_snapshots)
            return JSONResponse(content=items)
        except Exception as exc:
            logger.warning("Failed to list snapshots: %s", exc)
            return JSONResponse(content=[])

    @router.get("")
    async def list_sandboxes():
        """List all Daytona sandboxes."""
        try:
            items = await asyncio.to_thread(svc.list_sandboxes)
            return JSONResponse(content=items)
        except Exception as exc:
            return JSONResponse(status_code=503, content={"error": str(exc)})

    # --- Parameterized routes below ---

    @router.post("")
    async def create_sandbox(req: CreateSandboxRequest):
        """Create a new Daytona sandbox."""
        try:
            result = await asyncio.to_thread(
                svc.create_sandbox,
                name=req.name,
                snapshot=req.snapshot,
                image=req.image,
                env_vars=req.env_vars,
                labels=req.labels,
            )
            return JSONResponse(status_code=201, content=result)
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})

    @router.get("/{sandbox_id}")
    async def get_sandbox(sandbox_id: str):
        """Get a single sandbox."""
        try:
            result = await asyncio.to_thread(svc.get_sandbox, sandbox_id)
            return JSONResponse(content=result)
        except ValueError as exc:
            return JSONResponse(status_code=404, content={"error": str(exc)})
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})

    @router.post("/{sandbox_id}/start")
    async def start_sandbox(sandbox_id: str):
        """Start a stopped sandbox."""
        try:
            result = await asyncio.to_thread(svc.start_sandbox, sandbox_id)
            return JSONResponse(content=result)
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})

    @router.post("/{sandbox_id}/stop")
    async def stop_sandbox(sandbox_id: str):
        """Stop a running sandbox."""
        try:
            result = await asyncio.to_thread(svc.stop_sandbox, sandbox_id)
            return JSONResponse(content=result)
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})

    @router.delete("/{sandbox_id}")
    async def delete_sandbox(sandbox_id: str):
        """Delete a sandbox."""
        try:
            await asyncio.to_thread(svc.delete_sandbox, sandbox_id)
            return JSONResponse(status_code=204, content=None)
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})

    @router.post("/{sandbox_id}/exec")
    async def exec_in_sandbox(sandbox_id: str, req: ExecRequest):
        """Execute a command in a sandbox."""
        try:
            sb = await asyncio.to_thread(svc.get_sandbox_object, sandbox_id)
            resp = sb.process.exec(req.command, timeout=req.timeout)
            return JSONResponse(content={
                "result": resp.result,
                "exit_code": resp.exit_code,
            })
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})

    @router.get("/{sandbox_id}/files")
    async def list_sandbox_files(sandbox_id: str, path: str = "/home/daytona"):
        """List files in a sandbox directory."""
        try:
            items = await asyncio.to_thread(
                svc.list_files_recursive, sandbox_id, path,
            )
            return JSONResponse(content=items)
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})

    @router.get("/{sandbox_id}/preview-url")
    async def get_preview_url(
        sandbox_id: str,
        port: int = Query(default=3000, ge=1, le=65535),
    ):
        """Get a preview URL for a sandbox port."""
        try:
            result = await asyncio.to_thread(
                svc.get_signed_preview_url, sandbox_id, port,
            )
            return JSONResponse(content=result)
        except Exception as exc:
            return JSONResponse(status_code=500, content={"error": str(exc)})

    return router
