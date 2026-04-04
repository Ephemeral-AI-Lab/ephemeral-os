# Implementation Plan: Agent Builder with Database Storage

## Status: EXECUTED

All steps implemented and verified.

---

## What Was Built

### Backend (Python / FastAPI / SQLAlchemy)

| # | Step | File(s) | Status |
|---|------|---------|--------|
| 1 | DB Model — `AgentDefinitionRecord` | `backend/src/db/models/agent_definition.py` (NEW) | Done |
| 1b | Register in models __init__ | `backend/src/db/models/__init__.py` (MODIFIED) | Done |
| 2 | DB Store — `AgentDefinitionStore` | `backend/src/db/stores/agent_definition_store.py` (NEW) | Done |
| 2b | Register in stores __init__ | `backend/src/db/stores/__init__.py` (MODIFIED) | Done |
| 3 | Pydantic API Schemas | `backend/src/ui/schemas/agent_schemas.py` (NEW) | Done |
| 4 | Validation Service | `backend/src/services/agent_builder/validation.py` (NEW) | Done |
| 5 | Agent Builder Service | `backend/src/services/agent_builder/builder.py` (NEW) | Done |
| 6 | Runtime Registry | `backend/src/coordinator/agent_definitions.py` (MODIFIED) | Done |
| 7 | API Router `/api/agents` | `backend/src/ui/routers/agents.py` (NEW) | Done |
| 8 | Bootstrap Integration | `backend/src/ui/web_server.py` (MODIFIED) | Done |
| 9 | Wire into AgentTool | Already wired via `get_agent_definition` → `get_definition` | Done |

### Frontend (React / TypeScript / Tailwind)

| # | Step | File(s) | Status |
|---|------|---------|--------|
| 10 | Agents Page (builder UI) | `frontend/web/src/pages/AgentsPage.tsx` (NEW) | Done |
| 11 | Route registration | `frontend/web/src/App.tsx` (MODIFIED) | Done |
| 12 | Nav link | `frontend/web/src/components/layout/Layout.tsx` (MODIFIED) | Done |

### API Endpoints

```
GET    /api/agents                   — List all (built-in + user), filter by source/tags
GET    /api/agents/{name}            — Get one by name (full detail)
POST   /api/agents                   — Create (DB-stored, validated)
PUT    /api/agents/{name}            — Update (version bumped)
DELETE /api/agents/{name}            — Soft-delete
POST   /api/agents/{name}/clone      — Clone under new name
POST   /api/agents/validate          — Dry-run validation
GET    /api/agents/tools/available   — List registered tools
GET    /api/agents/toolkits/available — List toolkit factories
```

### Frontend UI Features

- **Agent list** with source filter tabs (all/builtin/user), color dots, source badges
- **Agent detail view** with tools/skills/toolkits tag display, system prompt preview
- **Agent builder form** with all fields, clickable tool/toolkit chips, inline validation
- **Clone** and **soft-delete** actions
- Dark zinc theme consistent with existing pages

### Verification

- All 11 backend files pass Python AST syntax check
- All new module imports verified clean (from /tmp to avoid types/ shadow)
- Registry functional test: 7 built-in agents load, user agents register/unregister correctly
- Pydantic schema validation: rejects invalid colors, accepts valid configs
- Frontend TypeScript: `tsc --noEmit` exits 0

### Key Design Decisions

1. **Reuses existing `AgentDefinition` Pydantic model** — DB records convert to same model as YAML loader
2. **Soft delete** via `is_active` column — preserves history
3. **Version tracking** — auto-increment on update, enables optimistic concurrency
4. **Source protection** — user agents cannot shadow `source="builtin"` agents
5. **Lazy imports** — `toolkits.factory` imported inside functions to avoid eager chain loading
6. **Getter lambdas** — router uses callable getters for services initialized after app creation
