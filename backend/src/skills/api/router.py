"""Skills API router — DB-backed CRUD with packaged skill file browsing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from skills.db.store import SkillDefinitionStore

# Packaged skills directory — read-only skill content shipped with the codebase
_PACKAGED_SKILLS_DIR = Path(__file__).resolve().parent.parent / "bundled" / "content"


def _resolve_packaged_skill_dir(name: str) -> Path | None:
    """Find the on-disk directory for a packaged skill by name."""
    candidate = _PACKAGED_SKILLS_DIR / name
    if candidate.is_dir():
        return candidate
    return None


def _build_file_tree(root: Path, base: Path | None = None) -> list[dict[str, Any]]:
    """Recursively build a file tree listing."""
    if base is None:
        base = root
    entries: list[dict[str, Any]] = []
    for item in sorted(root.iterdir()):
        if item.name.startswith(".") or item.name == "__pycache__":
            continue
        rel = str(item.relative_to(base))
        if item.is_dir():
            entries.append({
                "name": item.name,
                "type": "directory",
                "path": rel,
                "children": _build_file_tree(item, base),
            })
        else:
            entries.append({
                "name": item.name,
                "type": "file",
                "path": rel,
                "size": item.stat().st_size,
            })
    return entries


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1)
    content: str = Field(min_length=1)


class SkillUpdate(BaseModel):
    description: str | None = None
    content: str | None = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_skills_router(
    get_skill_store: Callable[[], "SkillDefinitionStore | None"],
) -> APIRouter:
    router = APIRouter(prefix="/api/skills", tags=["skills"])

    def _require_store() -> "SkillDefinitionStore":
        store = get_skill_store()
        if store is None:
            raise HTTPException(status_code=503, detail="Skill store not available (database not configured)")
        return store

    @router.get("")
    @router.get("/")
    async def list_skills() -> list[dict[str, Any]]:
        store = _require_store()
        records = store.list_active()
        return [
            {
                "name": r.name,
                "description": r.description,
            }
            for r in records
        ]

    @router.get("/{name}")
    async def get_skill(name: str) -> dict[str, Any]:
        store = _require_store()
        record = store.get_by_name(name)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
        return {
            "name": record.name,
            "description": record.description,
            "content": record.content,
            "version": record.version,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }

    @router.post("/", status_code=201)
    async def create_skill(body: SkillCreate) -> dict[str, Any]:
        store = _require_store()
        from uuid import uuid4
        from skills.db.model import SkillDefinitionRecord

        if store.get_by_name(body.name) is not None:
            raise HTTPException(status_code=400, detail=f"Skill '{body.name}' already exists")

        record = SkillDefinitionRecord(
            id=str(uuid4()),
            name=body.name,
            description=body.description,
            content=body.content,
        )
        record = store.create(record)
        return {"name": record.name, "message": f"Skill '{record.name}' created"}

    @router.put("/{name}")
    async def update_skill(name: str, body: SkillUpdate) -> dict[str, Any]:
        store = _require_store()
        updates = body.model_dump(exclude_unset=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        try:
            record = store.update(name, updates)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "name": record.name,
            "description": record.description,
            "version": record.version,
        }

    @router.delete("/{name}")
    async def delete_skill(name: str) -> dict[str, str]:
        store = _require_store()
        ok = store.soft_delete(name)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
        return {"deleted": name}

    @router.get("/{name}/files")
    async def list_packaged_skill_files(name: str) -> dict[str, Any]:
        """Return the file tree for a packaged skill's on-disk directory."""
        skill_dir = _resolve_packaged_skill_dir(name)
        if skill_dir is None:
            return {"name": name, "tree": []}
        return {"name": name, "tree": _build_file_tree(skill_dir)}

    @router.get("/{name}/files/{file_path:path}")
    async def get_packaged_skill_file(name: str, file_path: str) -> PlainTextResponse:
        """Serve a specific file from a packaged skill's directory."""
        skill_dir = _resolve_packaged_skill_dir(name)
        if skill_dir is None:
            raise HTTPException(status_code=404, detail=f"Packaged skill directory for '{name}' not found")

        target = (skill_dir / file_path).resolve()
        # Prevent path traversal
        try:
            target.relative_to(skill_dir)
        except ValueError:
            raise HTTPException(status_code=403, detail="Path traversal not allowed")

        if not target.is_file():
            raise HTTPException(status_code=404, detail=f"File '{file_path}' not found")

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=415, detail="Binary files not supported")

        return PlainTextResponse(content)

    return router
