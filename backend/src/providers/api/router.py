"""Model CRUD API routes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from providers.api.schemas import RegisterModelRequest, SelectModelRequest

if TYPE_CHECKING:
    from db.stores.model_store import ModelStore


def create_models_router(model_store: ModelStore) -> APIRouter:
    """Build the model management API router."""
    router = APIRouter(prefix="/api/db/models", tags=["models"])

    def _require_db() -> None:
        if not model_store.is_available:
            raise HTTPException(status_code=503, detail="Database not configured")

    @router.get("")
    async def list_models():
        _require_db()
        models = model_store.list_all(redact=True)
        active = model_store.get_active(redact=True)
        return JSONResponse(content={
            "models": models,
            "active": active["key"] if active else None,
        })

    @router.get("/active")
    async def get_active_model():
        _require_db()
        active = model_store.get_active(redact=True)
        if active is None:
            return JSONResponse(status_code=404, content={"error": "No active model"})
        return JSONResponse(content=active)

    @router.get("/{key}")
    async def get_model(key: str):
        _require_db()
        model = model_store.get(key, redact=True)
        if model is None:
            return JSONResponse(status_code=404, content={"error": "Model not found"})
        return JSONResponse(content=model)

    @router.post("/register")
    async def register_model(req: RegisterModelRequest):
        _require_db()
        result = model_store.register(
            key=req.key,
            label=req.label,
            class_path=req.class_path,
            kwargs=req.kwargs,
            activate=req.activate,
        )
        return JSONResponse(content={"ok": True, "model": result})

    @router.post("/select")
    async def select_model(req: SelectModelRequest):
        _require_db()
        result = model_store.select_active(req.key)
        if result is None:
            return JSONResponse(status_code=404, content={"error": "Model not found"})
        return JSONResponse(content={"ok": True, "model": result})

    @router.delete("/{key}")
    async def delete_model(key: str):
        _require_db()
        deleted = model_store.delete(key)
        if not deleted:
            return JSONResponse(status_code=404, content={"error": "Model not found"})
        return JSONResponse(content={"ok": True, "deleted": key})

    return router
