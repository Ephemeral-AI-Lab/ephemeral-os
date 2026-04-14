# Agents, Teams, and Customization

## Overview

EphemeralOS agents are customizable runtime units defined through Markdown files or API endpoints. Teams orchestrate multiple agents in coordinated workflows with persistent task queues and blocker mechanics. This document describes the customization surface, loading pipeline, persistence model, and team run lifecycle.

---

## 1. Customization Surface

Agents are customized via two complementary paths: **Markdown frontmatter** (for builtin agents and disk-based definitions) and **REST API** (for user-created agents stored in the database).

```mermaid
graph LR
    A["📄 Markdown Agent Definitions<br/>backend/config/agents/*.md"] -->|YAML frontmatter + body| B["AgentDefinition"]
    C["🔌 REST API<br/>POST /api/agents"] -->|JSON payload| D["AgentDefinitionCreate"]
    D -->|validation| E["AgentBuilderService"]
    E -->|insert/update| F["AgentDefinitionRecord<br/>SQL: agent_definitions"]
    B -->|loader| G["AgentRegistry"]
    F -->|builder| G
    G -->|lookup at runtime| H["🎯 AgentDefinition<br/>in memory"]
    
    style A fill:#f9f,stroke:#333
    style C fill:#9f9,stroke:#333
    style G fill:#ff9,stroke:#333
    style H fill:#9ff,stroke:#333
```

### Markdown Format

Agent definitions are written as Markdown files with YAML frontmatter:

```
---
name: developer
description: "Team-mode developer: reads, writes, and edits code."
role: developer
model: inherit
tool_call_limit: 100
toolkits: ["sandbox_operations", "code_intelligence"]
blocked_tools: ["ci_read_file"]
posthook: ["post_note", "request_replan"]
allowed_triggers: ["tc_note"]
---
# System Prompt (body of the file)
Execute one bounded coding task...
```

**Frontmatter fields:**
- `name`, `description` (required)
- `system_prompt` (optional; overridden by body text)
- `model`, `effort`, `tool_call_limit`
- `toolkits`, `skills`, `posthook`, `blocked_tools`
- `allowed_triggers`, `background`, `role`, `agent_type` (agent | subagent)
- `source` (builtin | user | plugin), capability flags

### REST API Surface

**Create custom agent:**
```
POST /api/agents
{
  "name": "my-researcher",
  "description": "...",
  "model": "claude-opus",
  "system_prompt": "...",
  "toolkits": ["search", "note_taking"],
  "blocked_tools": [],
  "effort": "high",
  "tool_call_limit": 50
}
```

**Update agent:**
```
PATCH /api/agents/{name}
{
  "system_prompt": "...",
  "toolkits": ["new_toolkit"]
}
```

**List & get:**
```
GET /api/agents?source=user
GET /api/agents/{name}
```

---

## 2. Loader → Registry → Runtime Flow

Three distinct layers orchestrate the transition from disk/database to runtime execution:

```mermaid
graph TD
    subgraph "Disk & Database"
        A1["📄 Builtin Agent<br/>Markdown Files"]
        A2["💾 Agent Definition<br/>Database Records"]
    end
    
    subgraph "Load Phase"
        B1["load_agents_dir<br/>Parse YAML frontmatter"]
        B2["load_external_agents<br/>User + plugin agents"]
        B3["AgentBuilderService<br/>record_to_definition"]
    end
    
    subgraph "Registry Phase"
        C["AgentRegistry<br/>in-memory map<br/>_DEFINITIONS"]
    end
    
    subgraph "Runtime"
        D1["get_definition<br/>Lookup by name"]
        D2["list_definitions<br/>Filter by source"]
        D3["get_role<br/>Team dispatch"]
    end
    
    A1 -->|_parse_frontmatter| B1
    B1 -->|AgentDefinition.model_validate| B2
    A2 -->|seed_builtin/ load_all_from_db| B3
    B3 -->|AgentDefinition| C
    C -->|lazy load on first access| D1
    C -->|list_definitions filter| D2
    C -->|find_by_role| D3
    
    style C fill:#ff9,stroke:#333,stroke-width:2px
    style D1 fill:#9ff,stroke:#333
    style D2 fill:#9ff,stroke:#333
    style D3 fill:#9ff,stroke:#333
```

**Key components:**

- **`AgentLoader`** (`backend/src/agents/loader.py`): Parses Markdown frontmatter via `_parse_frontmatter()`, calls `load_agents_dir()` to scan disk, calls `load_external_agents()` to gather user/plugin definitions.

- **`AgentRegistry`** (`backend/src/agents/registry.py`): Single in-memory `_DEFINITIONS` dict holding all registered `AgentDefinition` objects. Lazily loads external agents on first `get_definition()` or `list_definitions()` call. Provides lookup functions: `get_definition(name)`, `find_by_role(role)`, `has_role(agent_name, role)`.

- **`AgentBuilderService`** (`backend/src/agents/builder/service.py`): Converts database `AgentDefinitionRecord` ↔ runtime `AgentDefinition` via `record_to_definition()`. Seeds builtin definitions into DB via `seed_builtin()`.

---

## 3. Persistence Model

### Entity-Relationship Diagram

```mermaid
erDiagram
    AGENT_DEFINITIONS ||--o{ AGENT_RUNS : "spawns"
    TEAM_DEFINITIONS ||--o{ TEAM_RUNS : "starts"
    TEAM_RUNS ||--o{ TASKS : "contains"
    TEAM_RUNS ||--o{ BLOCKERS : "tracks"
    TASKS ||--o{ TASKS : "parent-child"
    AGENT_RUNS ||--|o TASKS : "assigned_to"

    AGENT_DEFINITIONS {
        string id PK
        string name UK
        string description
        string system_prompt
        string model
        string effort
        int tool_call_limit
        json toolkits
        json skills
        json blocked_tools
        json posthook
        json allowed_triggers
        json hooks
        boolean background
        string role
        string agent_type
        boolean can_spawn_subagents
        boolean require_fresh_client
        string source
        int version
        boolean is_active
        timestamp created_at
        timestamp updated_at
    }

    TEAM_DEFINITIONS {
        string id PK
        string name UK
        string description
        string planner_agent FK
        json worker_agents
        json roster
        timestamp created_at
        timestamp updated_at
    }

    TEAM_RUNS {
        string id PK
        string team_definition_id FK
        string session_id FK
        string status
        int replan_count
        timestamp created_at
        timestamp finished_at
    }

    TASKS {
        string id PK
        string team_run_id FK
        string agent_name FK
        string status
        string task
        json deps
        json scope_paths
        string cascade_policy
        string parent_id FK
        string root_id
        int depth
        int retry_count
        int max_retries
        string agent_run_id FK
        timestamp created_at
        timestamp started_at
        timestamp finished_at
    }

    BLOCKERS {
        string id PK
        string team_run_id FK
        string status
        string reason
        json root_cause_paths
        string initiating_task_id FK
        string fix_task_id FK
        double created_at
        double resolved_at
    }

    AGENT_RUNS {
        string id PK
        string agent_name FK
        string session_id FK
        string parent_task_id FK
        string status
        timestamp created_at
        timestamp finished_at
    }
```

**Key tables:**

- **`agent_definitions`** (durable): User-created agents, seeded builtin definitions. Tracks `source` (user | builtin | plugin), `version`, `is_active`.

- **`team_definitions`** (durable): Team rosters mapping roles to agent names. `planner_agent` is the entry point; `worker_agents` list eligible task executors. Mirrors legacy `roster` JSON for backward compatibility.

- **`team_runs`** (durable): Team execution instances. Tracks `team_definition_id`, `session_id`, `status` (pending | running | succeeded | failed), replan count.

- **`tasks`** (partitioned by `team_run_id`): Task queue for a single team run. Fields: `status` (pending | ready | running | expanded | done | failed), `agent_name` (assigned worker), `deps` (task IDs), `parent_id` (parent task for expansion), `depth`, `retry_count`, `agent_run_id` (link to agent execution).

- **`blockers`** (durable): Active/resolved blockers during team runs. `initiating_task_id` is the task that declared the blocker; `fix_task_id` is the task spawned to resolve it.

- **`agent_runs`** (durable): Every individual agent invocation (ephemeral or team). Links task to agent execution via `parent_task_id`.

### Ephemeral vs. Durable State

| Layer | Ephemeral | Durable |
|-------|-----------|---------|
| **Agent Definition** | In-memory registry (`_DEFINITIONS` dict) | Database table `agent_definitions` |
| **Team Definition** | In-memory registry | Database table `team_definitions` |
| **Run Execution** | Conductor state, task graph snapshot | `team_runs`, `tasks`, `blockers`, `agent_runs` rows |
| **Briefing/Notes** | Task Center in-memory cache | Note records in `team_runs` (persisted asynchronously) |

---

## 4. Creating a Custom Agent via API

Sequence showing the builder service integrating user input with database persistence:

```mermaid
sequenceDiagram
    participant User as User<br/>REST Client
    participant Router as /api/agents<br/>Router
    participant Builder as AgentBuilderService
    participant Store as AgentDefinitionStore
    participant DB as PostgreSQL<br/>agent_definitions
    participant Registry as AgentRegistry<br/>in-memory

    User->>Router: POST /api/agents<br/>{name, model, system_prompt, ...}
    Router->>Builder: create_agent(payload)
    Builder->>Builder: AgentDefinitionCreate.validate()
    Builder->>Builder: _record_payload_from_request()
    Builder->>Store: insert(name, payload)
    Store->>DB: INSERT INTO agent_definitions VALUES(...)
    DB-->>Store: AgentDefinitionRecord
    Store-->>Builder: record
    Builder->>Builder: record_to_definition(record)
    Builder-->>Router: AgentDefinition
    Router->>Registry: register_definition(defn)
    Registry-->>Registry: _DEFINITIONS[name] = defn
    Router-->>User: 200 OK<br/>AgentDefinitionResponse

    Note over Builder: Validation includes<br/>effort levels, model keys,<br/>toolkit names
    Note over Registry: Next get_definition(name)<br/>lookup returns immediately
```

**Steps:**

1. **API** receives `AgentDefinitionCreate` payload.
2. **Validation** checks effort levels, model keys, toolkit names.
3. **Builder** converts payload → `_record_payload_from_request()`.
4. **Store** inserts `AgentDefinitionRecord` into DB.
5. **Builder** converts record back → `AgentDefinition` via `record_to_definition()`.
6. **Registry** stores `AgentDefinition` in `_DEFINITIONS` map.
7. **Response** sent to user; registry is now hot for lookups.

---

## 5. Team Run Lifecycle Wiring

Sequence showing a team run from start through task dispatch to completion, integrating loader, registry, conductor, and persistence:

```mermaid
sequenceDiagram
    participant User as User
    participant API as /api/teams/runs<br/>Start Run
    participant Loader as TeamLoader<br/>+ AgentRegistry
    participant Conductor as Conductor<br/>Blocker Manager
    participant TaskStore as TaskStore<br/>Persistence
    participant Runner as Runner<br/>Dispatch Loop
    participant DB as PostgreSQL

    User->>API: POST /teams/runs<br/>{team_name, session_id}
    API->>Loader: load_team(team_name)
    Loader->>Loader: get_team_definition(team_name)<br/>from registry
    Loader->>Loader: resolve_roster()<br/>agent names → AgentDefinition
    Loader->>Loader: validate_agents()<br/>check roles, capabilities
    Loader-->>API: TeamRun object
    API->>DB: INSERT INTO team_runs<br/>(id, team_def_id, session_id, status='pending')
    API->>TaskStore: create(team_run_id)<br/>Initialize task graph
    API->>Conductor: restore()<br/>Load active blockers from DB
    API->>Runner: start_run(team_run, planner_agent)
    
    Runner->>Runner: spawn_ephemeral_agent(planner_agent)<br/>initial plan request
    Runner-->>TaskStore: insert_tasks(plan.tasks)
    TaskStore->>DB: INSERT INTO tasks<br/>(team_run_id, agent_name, status='pending', ...)
    
    loop until team_run.status terminal
        Runner->>TaskStore: get_ready_tasks()
        TaskStore-->>TaskStore: query(status='ready')<br/>in-memory lookup
        Runner->>Runner: dispatch_task(agent_name, task)
        Runner->>Runner: spawn_ephemeral_agent(agent)<br/>with task prompt
        Runner->>DB: UPDATE tasks SET status='running', agent_run_id=...
        Runner-->>TaskStore: update_task_graph()
        
        opt if task succeeds
            Runner->>Conductor: register_snapshot(task_id, messages)
            Runner->>DB: UPDATE tasks SET status='done', finished_at=...
        end
        
        opt if blocker raised
            Conductor->>DB: INSERT INTO blockers(...)
            Runner->>Runner: resolve_blocker()<br/>spawn fix task
        end
        
        opt if replan triggered
            Runner->>Runner: spawn_ephemeral_agent(replanner)<br/>replan request
            Runner-->>TaskStore: update_plan_tasks(add, cancel)
            TaskStore->>DB: INSERT (new) + UPDATE (cancel)<br/>in tasks
        end
    end
    
    Runner->>DB: UPDATE team_runs SET status='succeeded', finished_at=...
    API-->>User: 200 OK<br/>TeamRunResponse

    Note over Loader: Registry lookup is O(1)<br/>after lazy load
    Note over TaskStore: In-memory graph syncs<br/>from DB on each iteration
    Note over Conductor: Blocker state persists<br/>across crashes
    Note over DB: All state writes go through<br/>persistence layer
```

**Key interactions:**

- **Loader** resolves team roster by looking up each agent in `AgentRegistry`.
- **AgentRegistry** is lazily populated from disk (Markdown) and DB on first access.
- **TaskStore** maintains in-memory task graph synced from DB via `refresh_graph()`.
- **Conductor** persists active blockers; can restore on restart.
- **Runner** dispatches tasks via `spawn_ephemeral_agent()`, updating DB after each step.
- **AgentRunTracker** creates/finishes agent run records for every agent execution.

---

## 6. Agent Configuration Summary

**Customization knobs per agent:**

| Field | Impact | Example |
|-------|--------|---------|
| `system_prompt` | Core behavioral instruction | Markdown body or API field |
| `model` | LLM selection; "inherit" uses default | "claude-opus-4", "inherit" |
| `effort` | Heuristic budget; low/medium/high | High = larger tool_call_limit |
| `tool_call_limit` | Max tool calls before agent stops | 50, 100, unlimited (None) |
| `toolkits` | Allowed tool groups (sandbox, code_intelligence, search) | ["sandbox_operations", "code_intelligence"] |
| `blocked_tools` | Tool names to remove after assembly | ["ci_read_file"] |
| `skills` | Skill playbooks to inject | ["team-developer-playbook"] |
| `posthook` | Tools agent must call after submission | ["post_note"] |
| `role` | Team dispatch label (planner, developer, reviewer) | "developer" |
| `agent_type` | agent \| subagent (capability flag) | "agent" |
| `can_spawn_subagents` | Whether agent can spawn background work | true (default) |
| `background` | Run without awaiting completion | false (default) |
| `initial_prompt` | First-turn user message override | "Start by reading..." |
| `allowed_triggers` | External trigger types (tc_note) | ["tc_note"] |

---

## 7. Team Configuration Summary

**Customization via Markdown:**

```yaml
---
name: my_team
description: "Coordinated coding team"
entry_planner: team_planner
roster:
  planner: [team_planner]
  developer: [developer]
  reviewer: [validator, scout]
  resolver: [resolver]
---
This team coordinates...
```

**Core fields:**

- `name`: Team identifier
- `entry_planner`: Agent name that receives the initial goal
- `roster`: Mapping of role → list of agent names. Replanner/resolver can be dynamically selected by role.

---

## 8. Key Types & Classes

### Agents Module

- **`AgentDefinition`** (`types.py`): Full runtime agent config (Pydantic model).
- **`AgentDefinitionRecord`** (`db/model.py`): SQLAlchemy ORM row (durable).
- **`AgentBuilderService`** (`builder/service.py`): Converts records ↔ definitions.
- **`AgentDefinitionStore`** (`db/store.py`): CRUD on `agent_definitions` table.
- **`AgentLoader`** (`loader.py`): Parses Markdown, loads from disk.
- **`AgentRegistry`** (`registry.py`): In-memory lookup map.
- **`AgentRunTracker`** (`run_tracker.py`): Wraps agent execution lifecycle.

### Teams Module

- **`TeamDefinition`** (`models.py`): Roster + entry planner.
- **`TeamDefinitionRecord`** (`persistence/model.py`): SQLAlchemy ORM row.
- **`TeamLoader`** (`loader.py`): Parses Markdown, loads team definitions.
- **`TeamRegistry`** (`registry.py`): In-memory lookup.
- **`Task`** / **`TaskSpec`** (`models.py`): Execution units and plan items.
- **`TaskStore`** (`persistence/task_store.py`): SQL persistence for task queue.
- **`Conductor`** (`runtime/conductor.py`): Blocker assessment and resolution.
- **`TeamRun`** (`runtime/team_run.py`): Orchestrates a single team execution.

---

## 9. Configuration Directories

Builtin agent and team definitions live in:

```
backend/config/agents/
  ├── developer.md
  ├── validator.md
  ├── team_planner.md
  ├── team_replanner.md
  ├── resolver.md
  └── scout.md

backend/config/teams/
  ├── default_team.md
  └── ...
```

User-created agents are:
- **Defined via API** → stored in `agent_definitions` table
- **Loaded at startup** via `AgentLoader.load_external_agents()` → stored in `AgentRegistry`

---

## Summary

**Customization** flows through Markdown frontmatter (builtin) and REST API (user-created) into a unified database. **Loading** parses disk files and DB records into `AgentDefinition` objects, which populate the in-memory `AgentRegistry`. **Runtime** lookups are O(1) after lazy initialization. **Teams** compose agents by role and use a persistent task queue backed by PostgreSQL. **Persistence** separates ephemeral state (in-memory graphs) from durable state (DB tables), enabling crash recovery via `Conductor.restore()` and `TaskStore.refresh_graph()`.

