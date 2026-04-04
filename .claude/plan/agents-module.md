# Plan: Consolidate Agents into a First-Class Module

## Status: PLANNING

## Problem

Agent-related code is scattered across 6+ directories:
- `coordinator/agent_definitions.py` — AgentDefinition model, built-ins, YAML loader, registry
- `services/agent_builder/` — builder service, validation
- `db/models/agent_definition.py` — DB model
- `db/stores/agent_definition_store.py` — DB store
- `ui/schemas/agent_schemas.py` — API schemas
- `ui/routers/agents.py` — API router
- `tools/agent_tool.py` — agent spawning tool

This makes agents hard to reason about as a cohesive domain. The user wants agents to be first-class citizens.

## Target Structure

```
backend/src/agents/
├── __init__.py              # Public API: AgentDefinition, registry functions, AgentBuilderService
├── types.py                 # AgentDefinition model + constants (AGENT_COLORS, EFFORT_LEVELS, etc.)
├── registry.py              # Runtime registry: register/unregister/get/list definitions
├── builtins.py              # Built-in agent definitions (general-purpose, Explore, Plan, etc.)
├── loader.py                # YAML/Markdown file loader (load_agents_dir, parse frontmatter)
├── db/
│   ├── __init__.py
│   ├── model.py             # AgentDefinitionRecord (SQLAlchemy)
│   └── store.py             # AgentDefinitionStore (CRUD)
├── builder/
│   ├── __init__.py
│   ├── service.py           # AgentBuilderService (DB ↔ runtime bridge)
│   └── validation.py        # AgentDefinitionValidator
├── api/
│   ��── __init__.py
│   ├── schemas.py           # Pydantic request/response models
│   └── router.py            # FastAPI router (/api/agents)
└── tool.py                  # AgentTool (spawning tool, moved from tools/)
```

## Key Design Principles

1. **Self-contained domain module** — everything about agents in one place
2. **Clean public API** via `__init__.py` — other modules import from `agents`, not deep paths
3. **Agent fields are first-class**:
   - `name` — unique identifier
   - `model_key` — assigned LLM model (renamed from `model` for clarity)
   - `description` — when-to-use description
   - `system_prompt` — agent instructions
   - `skills` — list of skill slugs
   - `toolkits` — list of toolkit names (daytona, mcp, etc.)
   - `type` — agent type/category (renamed from `subagent_type`)
4. **Backward compatible** — re-export everything from old paths initially

## Implementation Steps

### Step 1: Create `agents/types.py`

Move from `coordinator/agent_definitions.py`:
- `AgentDefinition` Pydantic model
- Constants: `AGENT_COLORS`, `EFFORT_LEVELS`, `PERMISSION_MODES`, `MEMORY_SCOPES`, `ISOLATION_MODES`
- Helper functions: `_parse_str_list`, `_parse_positive_int`

**Add new field aliases:**
- `model_key` as alias for `model` (both accepted)
- `type` as alias for `subagent_type` (both accepted)

### Step 2: Create `agents/registry.py`

Move from `coordinator/agent_definitions.py`:
- `_DEFINITIONS` dict
- `register_definition()`
- `unregister_definition()`
- `get_definition()`
- `list_definitions()`
- `initialize_builtin_definitions()`

### Step 3: Create `agents/builtins.py`

Move from `coordinator/agent_definitions.py`:
- All system prompt constants (`_SHARED_AGENT_PREFIX`, `_GENERAL_PURPOSE_SYSTEM_PROMPT`, etc.)
- `_BUILTIN_AGENTS` list
- `get_builtin_agent_definitions()`

### Step 4: Create `agents/loader.py`

Move from `coordinator/agent_definitions.py`:
- `_parse_agent_frontmatter()`
- `load_agents_dir()`
- `_get_user_agents_dir()`
- `get_all_agent_definitions()`
- `get_agent_definition()` (the full scan fallback)
- `has_required_mcp_servers()`
- `filter_agents_by_mcp_requirements()`

### Step 5: Move `agents/db/model.py`

Move from `db/models/agent_definition.py` → `agents/db/model.py`
- Keep import in `db/models/__init__.py` for backward compat

### Step 6: Move `agents/db/store.py`

Move from `db/stores/agent_definition_store.py` → `agents/db/store.py`
- Keep import in `db/stores/__init__.py` for backward compat

### Step 7: Move `agents/builder/service.py`

Move from `services/agent_builder/builder.py` → `agents/builder/service.py`
- Update imports to use `agents.types`, `agents.registry`, `agents.db.store`

### Step 8: Move `agents/builder/validation.py`

Move from `services/agent_builder/validation.py` → `agents/builder/validation.py`
- Update imports

### Step 9: Move `agents/api/schemas.py`

Move from `ui/schemas/agent_schemas.py` → `agents/api/schemas.py`
- Update imports to use `agents.types` for constants

### Step 10: Move `agents/api/router.py`

Move from `ui/routers/agents.py` → `agents/api/router.py`
- Update imports

### Step 11: Move `agents/tool.py`

Move from `tools/agent_tool.py` → `agents/tool.py`
- Update imports to use `agents.registry`, `agents.loader`

### Step 12: Create `agents/__init__.py` (public API)

```python
"""Agents module — first-class agent definitions, builder, and registry."""

from agents.types import AgentDefinition, AGENT_COLORS, EFFORT_LEVELS, PERMISSION_MODES
from agents.registry import register_definition, unregister_definition, get_definition, list_definitions
from agents.builtins import get_builtin_agent_definitions
from agents.loader import get_agent_definition, get_all_agent_definitions, load_agents_dir
from agents.builder.service import AgentBuilderService
from agents.builder.validation import AgentDefinitionValidator
from agents.api.router import create_agents_router
```

### Step 13: Backward compatibility shims

Update old locations to re-export from new module:
- `coordinator/agent_definitions.py` → `from ephemeralos.agents import *`
- `services/agent_builder/__init__.py` → `from ephemeralos.agents.builder import *`
- `db/models/agent_definition.py` → `from ephemeralos.agents.db.model import *`
- `db/stores/agent_definition_store.py` �� `from ephemeralos.agents.db.store import *`
- `ui/schemas/agent_schemas.py` → `from ephemeralos.agents.api.schemas import *`
- `ui/routers/agents.py` → `from ephemeralos.agents.api.router import *`

### Step 14: Update all internal imports

Update `web_server.py`, `runtime.py`, and any other files that import from old locations to use the new `agents` module.

## Key Files

| New Path | Old Path | Description |
|----------|----------|-------------|
| `agents/__init__.py` | (new) | Public API |
| `agents/types.py` | `coordinator/agent_definitions.py` (partial) | AgentDefinition model + constants |
| `agents/registry.py` | `coordinator/agent_definitions.py` (partial) | Runtime registry |
| `agents/builtins.py` | `coordinator/agent_definitions.py` (partial) | Built-in agents + prompts |
| `agents/loader.py` | `coordinator/agent_definitions.py` (partial) | YAML loader + full scan |
| `agents/db/model.py` | `db/models/agent_definition.py` | SQLAlchemy model |
| `agents/db/store.py` | `db/stores/agent_definition_store.py` | CRUD store |
| `agents/builder/service.py` | `services/agent_builder/builder.py` | Builder service |
| `agents/builder/validation.py` | `services/agent_builder/validation.py` | Validator |
| `agents/api/schemas.py` | `ui/schemas/agent_schemas.py` | Pydantic schemas |
| `agents/api/router.py` | `ui/routers/agents.py` | FastAPI router |
| `agents/tool.py` | `tools/agent_tool.py` | Agent spawning tool |

## Risks

| Risk | Mitigation |
|------|------------|
| Import breakage across codebase | Backward compat shims re-export from old paths |
| Circular imports (agents ↔ tools ↔ toolkits) | Lazy imports within functions, same pattern already used |
| Large refactor surface | Do in atomic commits: move → shim → update imports → remove shims |
