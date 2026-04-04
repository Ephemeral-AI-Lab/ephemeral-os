## Implementation Plan: Remove Disallowed Tools, Use Allowed Toolkits

### Task Type
- [x] Backend
- [x] Frontend (UI update)

### Technical Solution

**Current state:** Agents have three tool-access fields: `tools` (individual tool names), `disallowed_tools` (exclusion list), and `toolkits` (factory-only names like "daytona"). The `disallowed_tools` field is used by Explore/Plan/Verification agents to exclude write tools, but **tool filtering is never actually enforced** at the query engine level — the full ToolRegistry is always passed to the API.

**Target state:** Remove `disallowed_tools` entirely. Agents declare their allowed toolkits (by built-in toolkit name, e.g. "filesystem", "execution", "web") and optionally individual tools. The `toolkits` field becomes the primary mechanism for granting capabilities, referencing both built-in toolkit names AND factory-created toolkits.

**Key design decision:** For the built-in read-only agents (Explore, Plan, Verification), convert their `disallowed_tools=["agent", "file_edit", "file_write", "notebook_edit"]` to an explicit `toolkits=[...]` list of allowed toolkit names that excludes write-capable toolkits. The `tools` field remains available for fine-grained individual tool access alongside `toolkits`.

### Implementation Steps

#### Step 1: Add `toolkits` field to `AgentDefinition` model
**File:** `backend/src/coordinator/agent_definitions.py:94-95`

- Add `toolkits: list[str] | None = None` to `AgentDefinition` (currently missing — it exists on DB model and schemas but NOT on the Pydantic runtime model)
- Remove `disallowed_tools: list[str] | None = None` (line 95)
- Update the docstring field mapping (line 68) to remove `disallowedTools` reference
- Expected: `AgentDefinition` now has `tools`, `toolkits` (no `disallowed_tools`)

#### Step 2: Convert built-in agents from `disallowed_tools` to `toolkits`
**File:** `backend/src/coordinator/agent_definitions.py:555-620`

Map each agent to its allowed toolkits. The built-in toolkit names are:
- `filesystem`, `execution`, `web`, `task_management`, `scheduling`, `worktree`, `planning`, `collaboration`, `code_analysis`, `discovery`, `system`

Convert:
- **Explore** (line 566): `disallowed_tools=[...]` → `toolkits=["filesystem", "execution", "web", "task_management", "scheduling", "code_analysis", "discovery", "system"]` (read-oriented toolkits, exclude planning/collaboration/worktree as write-heavy)
- **Plan** (line 581): `disallowed_tools=[...]` → `toolkits=["filesystem", "execution", "web", "task_management", "scheduling", "code_analysis", "discovery", "system", "planning"]`
- **Verification** (line 610): `disallowed_tools=[...]` → `toolkits=["filesystem", "execution", "web", "task_management", "scheduling", "code_analysis", "discovery", "system"]`
- **Worker** (line 595): `tools=None` → `toolkits=None` (None = all toolkits, unchanged semantics)

> **Note:** The exact toolkit assignments should be reviewed — the key principle is read-only agents get read-oriented toolkits. Individual tool-level exclusions (like `file_edit`, `file_write`) can still be controlled by which toolkits are included, since those tools live inside `filesystem`.

#### Step 3: Update frontmatter loader to remove `disallowed_tools` parsing
**File:** `backend/src/coordinator/agent_definitions.py:749-753`

- Remove the `disallowed_raw` / `disallowed_tools` parsing block (lines 749-753)
- Add `toolkits` parsing from frontmatter (parse `toolkits` key as a string list)
- Update the docstring (lines 708-709) to remove `disallowedTools` / `disallowed_tools` and document `toolkits`

#### Step 4: Remove `disallowed_tools` from DB model
**File:** `backend/src/db/models/agent_definition.py:32`

- Remove line 32: `disallowed_tools: Mapped[list | None] = mapped_column(JSON, nullable=True)`
- The `toolkits` column already exists (line 33) — no addition needed

> **Migration note:** If there's existing data with `disallowed_tools`, a DB migration should drop the column. For now, just remove from the model; SQLAlchemy will ignore the orphaned column.

#### Step 5: Remove `disallowed_tools` from API schemas
**File:** `backend/src/server/schemas/agent_schemas.py`

- Remove `disallowed_tools` from `AgentDefinitionCreate` (line 30)
- Remove `disallowed_tools` from `AgentDefinitionUpdate` (line 91)
- Remove `disallowed_tools` from `AgentDefinitionResponse` (line 155)

#### Step 6: Update AgentBuilderService
**File:** `backend/src/services/agent_builder/builder.py`

- `record_to_definition()` (line 44): Remove `disallowed_tools=record.disallowed_tools`, add `toolkits=record.toolkits`
- `_record_to_response()` (line 73): Remove `disallowed_tools=record.disallowed_tools` (toolkits already passed at line 74)
- `create_agent()` (line 119): Remove `disallowed_tools=data.disallowed_tools` (toolkits already passed at line 120)

#### Step 7: Update validation service
**File:** `backend/src/services/agent_builder/validation.py`

- The validator currently checks `toolkits` against toolkit **factories** only (line 46-50). Update to also accept built-in toolkit names from ToolRegistry:
  ```python
  # Check toolkit names exist in registry OR have a factory
  if toolkits:
      known = {tk.name for tk in self._tool_registry.list_toolkits()}
      from ephemeralos.toolkits.factory import has_factory
      for tk in toolkits:
          if tk not in known and not has_factory(tk):
              errors.append(f"Unknown toolkit: {tk}")
  ```

#### Step 8: Update coordinator_mode.py (if referencing disallowed_tools)
**File:** `backend/src/coordinator/coordinator_mode.py`

- Grep for any `disallowed_tools` references and remove/update them

#### Step 9: Frontend — No changes needed to types or forms
**Files:** Frontend `types.ts`, `CreateAgentModal.tsx`, `EditAgentModal.tsx`

- The frontend types (`AgentDetail`, `CreateAgentRequest`, `UpdateAgentRequest`) **already don't have** `disallowed_tools` — they only have `tools` and `toolkits`
- The UI already has both "Tools" and "Toolkits" multi-selects in Create and Edit modals
- The `useToolkits()` hook fetches from `/api/tools/toolkits` which returns built-in toolkit names from ToolRegistry — this is correct
- **No frontend changes required** — the UI already supports the target model

#### Step 10: Update `/api/agents/toolkits/available` endpoint
**File:** `backend/src/server/routers/agents.py:89-94`

- Currently returns only factory names. Should also include built-in toolkit names from ToolRegistry:
  ```python
  @router.get("/toolkits/available")
  async def list_available_toolkits() -> list[str]:
      from ephemeralos.toolkits.factory import list_factories
      names = set()
      tr = get_tool_registry()
      if tr:
          names.update(tk.name for tk in tr.list_toolkits())
      names.update(list_factories())
      return sorted(names)
  ```

### Key Files

| File | Operation | Description |
|------|-----------|-------------|
| `backend/src/coordinator/agent_definitions.py:60-135` | Modify | Add `toolkits` field, remove `disallowed_tools` from AgentDefinition |
| `backend/src/coordinator/agent_definitions.py:555-620` | Modify | Convert built-in agents to use `toolkits=` |
| `backend/src/coordinator/agent_definitions.py:749-753` | Modify | Remove disallowed_tools frontmatter parsing, add toolkits |
| `backend/src/db/models/agent_definition.py:32` | Modify | Remove `disallowed_tools` column |
| `backend/src/server/schemas/agent_schemas.py:30,91,155` | Modify | Remove `disallowed_tools` from all 3 schemas |
| `backend/src/services/agent_builder/builder.py:44,73,119` | Modify | Remove disallowed_tools references, add toolkits to record_to_definition |
| `backend/src/services/agent_builder/validation.py:43-50` | Modify | Validate toolkits against registry + factories |
| `backend/src/server/routers/agents.py:89-94` | Modify | Include built-in toolkit names in available toolkits endpoint |

### Risks and Mitigation

| Risk | Mitigation |
|------|------------|
| Existing DB records with `disallowed_tools` data | Column ignored by SQLAlchemy after removal from model; optional migration to drop column later |
| Custom `.md` agent definitions using `disallowedTools` frontmatter | Parser simply stops reading it — harmless. Log a deprecation warning if encountered |
| Built-in agents get wrong toolkit sets | Carefully map each agent's read/write needs to toolkit names; verify by checking which tools each toolkit contains |
| Toolkit names could change | Use constants for toolkit names; document the canonical list |
