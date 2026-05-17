# Grep & Glob Tools for Sandbox Agents

**Date:** 2026-05-16
**Source request:** `/ralplan` — implement TS-reference Grep/Glob tools in Python sandbox layer, wire to agent profiles, prove via sweevo scenario.

---

## RALPLAN-DR Summary (short mode)

### Principles
1. **Mirror existing patterns, don't invent.** Each layer (daemon handler → sandbox.api.tool wrapper → tools/sandbox host registration → agent profile → sweevo scenario) follows the `read_file` / `shell` / `edit_file` shape exactly. Concretely: **acquire a snapshot lease via `services.manager.acquire_snapshot_lease()` and read every file through `services.layer_stack` against `lease.manifest`** — matches `daemon/handler/read.py:51-60`. Deviation = rework.
2. **Snapshot-consistent reads.** Grep and glob do not touch the OCC mutation gate (no `occ_client.commit_*`, no conflict envelope), but they DO acquire the snapshot lease that pins which layer-stack manifest the scan sees. This is MVCC read isolation, not a mutation lock; without it, a concurrent `edit_file` publish during the scan produces torn results (file listed at scan time, content read after publish).
3. **Portability over micro-speed.** Use Python stdlib (`re.compile`) backed by the layer_stack snapshot view. Don't depend on `rg` being installed in the sandbox image; revisit later (see Follow-ups).
4. **Bounded output, all caps in the contract.** Glob caps at 100 files. Grep caps at default `head_limit=250` entries and 20 KB total content; per-file scan skips files > 10 MB and non-UTF-8. Truncation is signaled in the result.
5. **Behavior-preserving rollout.** Every existing agent's tool list is a strict superset before-vs-after. No agent loses a tool. Phase 4 is *narrow* additive: only profiles that already list `read_file` get `glob`; only profiles that already invoke search-style `shell` get `grep`. Phase 6 regression-runs the neighbor scenario to catch silent tool-routing changes.

### Decision Drivers (top 3)
1. **Agent capability gap.** Planner/executor/advisor can `read_file` and `shell` only — no native pattern-match across a workspace. This is the highest-leverage unblock for the sweevo scenarios.
2. **Sandbox boundary.** Search must execute *inside* the sandbox VM (workspace files are not host-accessible). It has to land as daemon ops + RPC envelopes; host-side shelling is not viable.
3. **Test-driven proof.** The sweevo harness (`SCENARIO_REGISTRY`, `run_sweevo_scenario`, `_assert_*_contract`) is the canonical surface for "agent capability works end-to-end."

### Viable Options

**Option A — Python stdlib in-daemon handler (RECOMMENDED)**
- Glob: `pathlib.Path(workspace_root).rglob(pattern)` + mtime sort + cap-100
- Grep: per-file `re.compile(pattern)` scan; `output_mode={content,files_with_matches,count}`, glob filter, head_limit, -i, -n
- Daemon module: `sandbox/daemon/handler/search.py`
- **Pros:** zero new sandbox-image deps; portable; deterministic for tests; mirrors existing handler shape exactly.
- **Cons:** slower than rg on very large repos (typically ≤2× for codebases under 100k files); no rg-specific multiline/lookahead support.

**Option B — Ripgrep subprocess inside daemon**
- Daemon handler shells out to `rg` (same as TS reference); parse rg output.
- **Pros:** maximum speed; identical semantics to the reference.
- **Cons:** requires rg in every sandbox image (image-build surface); rg-output parsing is fragile across versions; harder to make deterministic.
- **Invalidation:** sandbox image builds are not in this task's scope. Adding a runtime dependency to every base image is a separate workstream. Stdlib gives us the contract today.

**Option C — Reuse the existing `api.shell` op**
- Host-side `tools/sandbox/grep.py` translates the call into a `shell` invocation (`rg …` or `find …`).
- **Pros:** zero new daemon code.
- **Cons:** loses the typed Pydantic input/output; piggybacks on the shell mutation pipeline (OCC + conflict surfacing) for read-only work; no structured truncation signal.
- **Invalidation:** defeats the point of a typed tool API; conflates read-only with mutation semantics. Hard no.

**Decision:** Option A.

---

## Acceptance Criteria

- [ ] Daemon ops `api.find_files` and `api.search_content` (plus `api.v1.*` aliases) are registered via `register_op` in `daemon/rpc/dispatcher.py:_load_peer_bootstraps`.
- [ ] `sandbox.api.tool.glob_files(sandbox_id, GlobRequest, *, audit_sink, transport)` and `sandbox.api.tool.search_content(sandbox_id, GrepRequest, …)` exist and route through `audited_operation`.
- [ ] Host-side `@tool(name="glob")` and `@tool(name="grep")` decorators are registered in `tools/sandbox/_lib/registry.py:make_sandbox_tools` with the existing tool list.
- [ ] Every agent profile in `agents/profile/{helper,main,subagent}/*.md` that currently lists `read_file` or `shell` in `allowed_tools` also lists `grep` and `glob` (additive). Decision-only profiles (advisor-style) get `grep` only if they have any code-reading need.
- [ ] New sweevo scenario `sandbox.complex_project_build_grep_glob_smoke` registered in `SCENARIO_REGISTRY` with a contract validator.
- [ ] New test file `test_complex_project_build_grep_glob.py` mirrors the structure of `test_complex_project_build_shell_edit_lsp.py` and passes.
- [ ] Glob result truncates at exactly 100 files; `truncated: true` set in payload when capped.
- [ ] Grep result truncates at default `head_limit=250` lines/entries; respects `head_limit=0` as unlimited (matches TS).
- [ ] Grep content output capped at 20 KB total (matches TS `maxResultSizeChars`); truncation signaled in payload.
- [ ] Grep per-file skip: files larger than 10 MB are skipped silently; files that fail UTF-8 decode are skipped silently. Both counts surface in `timings` for observability.
- [ ] Grep supports `output_mode={content, files_with_matches, count}` with default `files_with_matches`.
- [ ] Read-only by construction: neither tool's daemon handler imports `occ_client.commit_*` or calls any `OccClient.apply_*` method. Verifiable via `grep -rn "occ_client\." backend/src/sandbox/daemon/handler/search.py` returning zero matches. (`BaseTool` has no `is_read_only` flag in this codebase — read-only-ness is implicit in not touching the OCC mutation surface, matching `read_file.py` which also has no such flag.)
- [ ] **Both DO acquire a snapshot lease** via `services.manager.acquire_snapshot_lease(request_id)` (mirrors `read.py:53`); release in `finally`.
- [ ] Daemon handler walks the snapshot via `services.layer_stack` against `lease.manifest`, not via raw `pathlib.Path.rglob`.
- [ ] `LayerStack.iter_paths(manifest)` exists and is unit-tested for: whiteout files, opaque-dir whiteouts, multi-layer override (top layer wins), and symlink semantics. (Mirrors `view.list_dir` contract; see `layer_stack/view.py:110-141`.)
- [ ] Existing tests pass: `test_complex_project_build_shell_edit_lsp.py`, every test under `tests/unit_test/test_sandbox/test_daemon/`.
- [ ] New sweevo test inherits the neighbor's gating: `@pytest.mark.skipif(EPHEMERALOS_DATABASE_URL gate)` and `@pytest.mark.timeout(1200)` decorators copied verbatim.
- [ ] New unit test files exist and pass: `tests/unit_test/test_sandbox/test_daemon/test_search_handler.py` (handler-level coverage of glob, grep modes, lease lifecycle, cap behavior), `tests/unit_test/test_sandbox/test_api/test_grep_glob.py` (API wrapper with mock transport), `tests/unit_test/test_tools_sandbox/test_grep_glob.py` (host-side @tool wiring with mock sandbox_api).
- [ ] Lease-leak regression test: a unit test cancels the handler's executor task mid-`acquire_snapshot_lease` and asserts `manager.active_lease_count()` returns to baseline.

---

## Implementation Steps

### Phase 1 — Daemon handler (Layer 5)

**Sub-phase 1a (PREREQ):** Inspect `sandbox.layer_stack.LayerStack` for a manifest-scoped iteration method. Today's `LayerStackView.list_dir` (`layer_stack/view.py:110-141`) walks a single directory using per-layer `LayerIndex` lookups with whiteout handling. There is **no existing full-tree iterator** that respects opaque-dir whiteouts and layer ordering. Building one is non-trivial: must combine top-down traversal with whiteout/opaque-dir handling across all `LayerRef` entries in the manifest.

Requirements:
- New method: `LayerStack.iter_paths(manifest: Manifest) -> Iterator[str]` yielding every visible file path in the snapshot.
- **Unit tests (required for AC):** whiteout file is excluded; opaque-dir whiteout masks all child paths from lower layers; multi-layer override (top layer's file wins); symlink behavior matches `view.list_dir`; deterministic ordering for tests.
- LOC realism: this is **not 3 lines**. Realistically 60–150 LOC + 100+ LOC of tests. If `LayerStackView` exposes recursive primitives this shrinks to ~30 LOC. **Do not ship Phase 1b until 1a's unit tests are green.**
- If schedule pressure surfaces during 1a, escalate to user with a "rg subprocess in shell-out mode" temporary fallback proposal — but the daemon handler must still acquire the lease.

**Sub-phase 1b — File:** `backend/src/sandbox/daemon/handler/search.py` (new)

```python
"""api.find_files / api.search_content daemon handlers (read-only).

Both ops acquire a snapshot lease (MVCC read isolation, mirrors read.py:51-60)
and read every path through services.layer_stack against lease.manifest so
the scan is consistent with the leased snapshot. They never touch the OCC
mutation gate or the occ_client.
"""
from uuid import uuid4

VCS_EXCLUDED = frozenset({".git", ".svn", ".hg", ".bzr", ".jj", ".sl"})
DEFAULT_GLOB_LIMIT = 100
DEFAULT_GREP_HEAD_LIMIT = 250
MAX_GREP_CONTENT_BYTES = 20 * 1024     # 20 KB output cap, matches TS reference
MAX_GREP_FILE_BYTES = 10 * 1024 * 1024  # skip files larger than this

async def find_files(args: dict[str, object]) -> dict[str, object]:
    """Glob: enumerate snapshot paths matching a glob pattern."""
    total_start = monotonic_now()
    layer_stack_root = require_layer_stack_root(args)
    binding = require_workspace_binding(layer_stack_root)
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("pattern is required")
    sub_path = str(args.get("path") or "").strip()  # workspace-relative dir
    services = build_occ_backend(layer_stack_root)
    request_id = uuid4().hex
    lease = await run_sync_in_executor(
        services.manager.acquire_snapshot_lease, request_id,
    )
    try:
        matches = []
        for layer_path in services.layer_stack.iter_paths(lease.manifest):
            if _is_vcs_excluded(layer_path): continue
            if sub_path and not _under(sub_path, layer_path): continue
            if not fnmatch.fnmatch(layer_path, pattern): continue
            matches.append(layer_path)
        # mtime sort (best-effort — manifest may not carry mtime; fall back to
        # path-sorted to keep behavior deterministic in tests)
        matches.sort()
        truncated = len(matches) > DEFAULT_GLOB_LIMIT
        return {
            "success": True,
            "filenames": matches[:DEFAULT_GLOB_LIMIT],
            "num_files": min(len(matches), DEFAULT_GLOB_LIMIT),
            "truncated": truncated,
            "timings": {"api.find_files.total_s": monotonic_now() - total_start},
        }
    finally:
        await run_sync_in_executor(services.manager.release_lease, lease.lease_id)

async def search_content(args: dict[str, object]) -> dict[str, object]:
    """Grep: regex-scan snapshot file contents."""
    # Same lease shape. Uses re.compile (NOT ripgrep PCRE2 — see ADR).
    # output_mode dispatch; per-file decode-and-scan; cap 20 KB content; skip
    # files > MAX_GREP_FILE_BYTES; head_limit + offset truncation.
    # Returns dict with mode, filenames, content, num_files, num_lines,
    # num_matches, applied_limit, applied_offset, timings.
```

**Registration:** add to `daemon/rpc/dispatcher.py:_load_peer_bootstraps`:
```python
from sandbox.daemon.handler import search
...
"api.find_files": search.find_files,
"api.v1.find_files": search.find_files,
"api.search_content": search.search_content,
"api.v1.search_content": search.search_content,
```

### Phase 2 — Sandbox API tool wrappers (Layer 1)

**Files:** `backend/src/sandbox/api/tool/glob.py`, `backend/src/sandbox/api/tool/grep.py` (new)

```python
# glob.py
@dataclass(frozen=True)
class GlobRequest:
    pattern: str
    path: str | None
    caller: SandboxCaller

@dataclass(frozen=True)
class GlobResult:
    success: bool
    filenames: tuple[str, ...]
    num_files: int
    truncated: bool
    duration_ms: int
    timings: dict[str, float]

async def glob_files(sandbox_id, request, *, audit_sink=None, transport=None) -> GlobResult:
    ...  # routes through audited_operation + SandboxTransport call to "api.find_files"
```

```python
# grep.py
@dataclass(frozen=True)
class GrepRequest:
    pattern: str
    path: str | None
    glob_filter: str | None
    output_mode: Literal["content", "files_with_matches", "count"]
    head_limit: int | None
    offset: int
    case_insensitive: bool
    line_numbers: bool
    context_before: int | None
    context_after: int | None
    multiline: bool
    caller: SandboxCaller

@dataclass(frozen=True)
class GrepResult:
    success: bool
    mode: str
    filenames: tuple[str, ...]
    content: str
    num_files: int
    num_lines: int
    num_matches: int
    applied_limit: int | None
    applied_offset: int | None
    timings: dict[str, float]

async def search_content(sandbox_id, request, *, audit_sink=None, transport=None) -> GrepResult:
    ...
```

**Coercions:** add `glob_result_from_daemon_response` and `grep_result_from_daemon_response` to `sandbox/api/tool/core/results.py`. Add op constants `DAEMON_OP_FIND_FILES`, `DAEMON_OP_SEARCH_CONTENT` and timeout constants to wherever `DAEMON_OP_READ_FILE` lives.

**Exports:** update `sandbox/api/tool/__init__.py` to re-export `glob_files`, `search_content`, `GlobRequest`, `GlobResult`, `GrepRequest`, `GrepResult`.

### Phase 3 — Host-side tool registrations (Layer 2)

**Files:** `backend/src/tools/sandbox/glob.py`, `backend/src/tools/sandbox/grep.py` (new)

```python
class GlobInput(BaseModel):
    pattern: str
    path: str | None = None

class GlobOutput(BaseModel):
    filenames: list[str]
    num_files: int
    truncated: bool
    duration_ms: int

@tool(
    name="glob",
    description=GLOB_DESCRIPTION,
    short_description="Find files by pattern in the sandbox.",
    input_model=GlobInput,
    output_model=GlobOutput,
)
async def glob_files(
    pattern: str,
    path: str | None = None,
    *,
    context: ToolExecutionContextService,
) -> ToolResult:
    ...  # mirrors read_file's sandbox_id_or_error + sandbox_api.glob_files + caller_from_context
```

Same shape for `grep.py` (more input fields).

**Registry:** append to `backend/src/tools/sandbox/_lib/registry.py:make_sandbox_tools`:
```python
tools: list[BaseTool] = [
    read_file,
    write_file,
    edit_file,
    shell,
    glob_files,    # new
    grep,          # new
]
```

### Phase 4 — Agent profile wiring (Layer 3) — NARROW additive

**Rule:** only add `glob` to profiles that already list `read_file`; only add `grep` to profiles that already list `read_file` **and** something search-shaped (a `shell` op or a subagent dispatcher). This prevents agents that previously used unbounded `shell`-grep from silently swapping to the 250-entry-capped `grep` tool.

Concretely (from explore-agent report):
- `main/planner.md` — has `read_file`. Add **`glob`** only (planner doesn't run shell commands).
- `main/executor.md` — has read+shell+edit access via context recipe. Add **both** `glob` and `grep`.
- `helper/advisor.md` — has `read_file` only. Add **`glob`** only.
- `subagent/*.md` — audit each; if a subagent has both read and shell, add both; otherwise add `glob` only.

**No removals.** **No tool added to a profile that has neither `read_file` nor `shell`.** Pure additive change with the routing-change safeguard above.

**Concrete verification step (run before committing Phase 4):**
1. Read `agents/definition/loader.py` and any recipe-expansion code path to confirm how `allowed_tools` resolves (literal frontmatter list vs. recipe-imported tools). Document the actual resolution rule in a 2-line comment in this plan.
2. For each `*.md` under `agents/profile/{helper,main,subagent}/`, run a Python one-liner that loads the profile via the production loader and prints its effective `allowed_tools` list. Commit the before/after diff as part of Phase 4.
3. Reject any profile add that would change the *first preferred* tool the agent reaches for in a search-style task — verified by inspecting profile system prompts (body of the .md file) for tool-name mentions.

### Phase 5 — End-to-end sweevo scenario (Layer 4)

Mirror the existing `complex_project_build_shell_edit_lsp_smoke` shape:

1. **Scenario file:** `backend/src/task_center_runner/tests/sweevo/scenarios/complex_project_build_grep_glob_smoke.py` (or wherever existing scenarios are defined). Register it in `SCENARIO_REGISTRY` as `sandbox.complex_project_build_grep_glob_smoke`. The scenario task asks the agent to:
   1. Use `glob` to find all `*.py` files in a fixture workspace.
   2. Use `grep` to find a known anchor (e.g., `TODO: replace this`) across them.
   3. Edit one matching file to replace the anchor with a target string.
   4. Verify the edit via `shell` or another `grep`.

2. **Test file:** `backend/src/task_center_runner/tests/sweevo/test_complex_project_build_grep_glob.py`. Copy ALL pytest decorators verbatim from `test_complex_project_build_shell_edit_lsp.py` (including the `EPHEMERALOS_DATABASE_URL` skipif gate and `@pytest.mark.timeout(1200)`):
```python
@pytest.mark.skipif(
    not os.getenv("EPHEMERALOS_DATABASE_URL"),
    reason="requires EPHEMERALOS_DATABASE_URL",
)
@pytest.mark.asyncio
@pytest.mark.timeout(1200)
async def test_complex_project_build_grep_glob_smoke(
    sweevo_instance, workspace, audit_dir, stores,
) -> None:
    scenario = SCENARIO_REGISTRY["sandbox.complex_project_build_grep_glob_smoke"]()
    sandbox_id = str(workspace["sandbox_id"])
    report = await run_sweevo_scenario(
        scenario, instance=sweevo_instance, sandbox_id=sandbox_id,
        audit_dir=audit_dir, stores=stores,
    )
    await _assert_grep_glob_contract(report=report, sandbox_id=sandbox_id, smoke=True)
```
(Read the neighbor test file to copy the exact decorator stack — the `skipif` predicate may use a different env-var or fixture pattern than the one shown.)

3. **Contract assertion:** `_assert_grep_glob_contract` validates: at least one `glob` op was called and returned files; at least one `grep` op found the anchor; the edit landed; the final verification call sees the new string.

### Phase 6 — Verification

Run in this order. Stop on any failure:
1. `cd backend && /Users/yifanxu/machine_learning/LoVC/EphemeralOS/.venv/bin/pytest tests/unit_test/test_sandbox/test_daemon -q` (existing daemon tests, no regression)
2. `…/.venv/bin/pytest tests/unit_test/test_sandbox/test_daemon/test_search_handler.py -q` (new unit tests for the daemon handlers — synthetic workspace, no provider)
3. `…/.venv/bin/pytest tests/unit_test/test_sandbox/test_api/test_grep_glob.py -q` (new unit tests for API wrappers with mock transport)
4. `…/.venv/bin/pytest tests/unit_test/test_tools_sandbox/test_grep_glob.py -q` (new unit tests for host-side @tool registrations with mock sandbox_api)
5. `…/.venv/bin/pytest src/task_center_runner/tests/sweevo/test_complex_project_build_grep_glob.py -x -v` (end-to-end sweevo scenario)
6. `…/.venv/bin/pytest src/task_center_runner/tests/sweevo/test_complex_project_build_shell_edit_lsp.py -x -v` (regression check on neighbor scenario; profile additions must not break it)
7. `…/.venv/bin/ruff check src/sandbox/daemon src/sandbox/api/tool src/tools/sandbox src/agents/profile src/task_center_runner/tests/sweevo` (lint clean — sweevo dir added so the new scenario module + test file are covered)

---

## Risks & Mitigations

| Risk | Likelihood | Severity | Mitigation |
|---|---|---|---|
| Python `pathlib.rglob` glob semantics diverge from TS reference (e.g., `**` vs `*` edge cases on Windows / case-insensitive FS) | Medium | Medium | Use `pathlib.Path.rglob` semantics as the contract; document in tool description. Add unit tests covering `**`, `*`, `?`, `[abc]` patterns. Sandbox runs on Linux so case sensitivity is consistent. |
| Adding grep/glob to too many agent profiles silently changes existing scenario behavior (an agent that used to pick `shell` now picks `grep`) | Medium | Medium | Phase 4 is additive only; Phase 6 step 6 runs the existing shell/edit/lsp scenario explicitly to detect this. If a regression appears, narrow Phase 4 to a smaller set of profiles. |
| Grep over a large binary file OOMs the daemon | Low | High | Skip files larger than `MAX_GREP_FILE_BYTES` (10 MB) and files that fail `utf-8` decode; cap total content output at the TS reference's 20 KB. |
| Daemon op naming conflicts with future plugin ops | Low | Low | Use the namespaced names `api.find_files` / `api.search_content` (not `api.glob` / `api.grep`) so the namespace stays open. |
| Sweevo scenario runs against a live sandbox provider and flakes | Medium | Medium | Reuse the existing `sweevo_instance` fixture which has provider-stability scaffolding; mark `@pytest.mark.timeout(1200)` like the neighbor scenario. |
| Agent profile schema validation rejects unknown tool names if `grep`/`glob` aren't registered before profile load | Low | High | Phase ordering: Phase 3 (registry) must commit before Phase 4 (profiles). Verify with `pytest tests/unit_test/test_agents/test_profile_load.py` (or whatever the loader's test surface is) after Phase 4. |
| Snapshot lease leak if executor task is cancelled between `acquire_snapshot_lease` and entering `try:` (or if an exception fires inside `try` before reaching `finally`) | Low | High | Mirror whichever pattern `read.py` / `write.py` / `edit.py` settle on (currently `try`/`finally`). Add a unit test that cancels the handler mid-acquire and asserts the manager's active-lease count returns to baseline. Also consider acquiring inside the `try` block to close the race window. |
| Sub-phase 1a (`LayerStack.iter_paths`) is larger than initial estimate, blocking Phase 1b | Medium | High | Time-box 1a to one session; if it exceeds, escalate to user with two options: (a) accept a `rg`-subprocess fallback that still acquires the lease, or (b) defer 2.5 (profile wiring) and ship 1a as a standalone PR for review. |
| Phase 4 narrow-additive rule misfires because `allowed_tools` resolves through recipe-expansion that the plan didn't fully account for | Medium | Medium | Phase 4 verification step #1 (read loader code, document resolution rule) runs *before* any md file is edited. Diff commits in Phase 4 must show effective tool lists, not just frontmatter. |

---

## ADR

- **Decision:** Implement grep and glob as new daemon ops (`api.find_files`, `api.search_content`) with **Python stdlib backends running against the snapshot-pinned `layer_stack` view (lease-protected)**, expose via the existing `sandbox.api.tool` and `tools/sandbox` patterns, and wire to agent profiles additively. Prove with a new sweevo scenario.
- **Drivers:** (1) agent capability gap, (2) sandbox FS isolation forces daemon-side execution, (3) sweevo scenario harness is the proof surface.
- **Alternatives considered:**
  - Option B (rg subprocess) — rejected for the *engine* choice only: sandbox image build is out of scope, rg-output parsing is fragile across versions. **B has the same lease/manifest requirement as A** — the engine choice is orthogonal to the snapshot-consistency requirement.
  - Option C (reuse shell op) — rejected: conflates read-only with mutation pipeline.
- **Why chosen:** Mirrors the existing four-layer pattern *including* the snapshot-lease MVCC read pattern from `daemon/handler/read.py:51-60`. Zero new system dependencies. Deterministic for tests.
- **Explicit contract divergence from TS reference (accepted):**
  - **Regex engine:** Python `re` ≠ PCRE2. No possessive quantifiers, different Unicode `\b` semantics, no recursive `(?P>name)`. We surface this in the tool's `description` so agents prompted with PCRE2 patterns get a clear failure mode rather than silent zero-match.
  - **Glob engine:** `fnmatch` over the layer_stack manifest, not the rg `--glob` matcher; some edge cases (brace expansion `{a,b}`, `**` semantics) diverge. Document in tool description.
  - **Symlinks:** layer_stack view does not follow symlinks across snapshot boundaries by design — matches our security posture, diverges from TS reference behavior. Documented.
  - **Result envelope:** identical to TS reference (`filenames[]`, `truncated`, `numFiles`, `appliedLimit`, `appliedOffset`, `mode`, `content`, `numLines`, `numMatches`).
- **Consequences:**
  - +6 new files (1 daemon handler, 2 API wrappers, 2 host-side tools, 1 scenario), +1 test file, ~5-8 profile-md edits (narrowed per Phase 4 rule), ~6 lines in dispatcher and registry.
  - Estimated ~650 LOC added (handlers ~300 — larger due to lease-and-iterate scaffolding, wrappers ~200, host tools ~150, scenario+test ~200).
  - Sandbox grep is slower than rg but correctness-equivalent under the documented divergences.
  - **One layer_stack API addition** if `iter_paths(manifest)` doesn't already exist (Sub-phase 1a) — small, contained.
- **Follow-ups:**
  - Add rg-backed Option B as a future engine swap behind the *same* daemon op contract (lease + manifest stay; only the scan kernel changes). Gate on sandbox-image rg availability.
  - Consider exposing `sort_by={mtime,name,size}` on glob if scenarios need it (manifest may need to carry mtime).
  - Add CI step running the new sweevo scenario nightly.
  - If agents using shell-grep show measurable result truncation pain, expand `grep` cap or add a `--unbounded` escape hatch.

---

## Open Questions for User

None — task is well-specified.

---

## Revision Changelog

**Rev 1 → Rev 2 (Architect feedback applied):**
- Principle 2 rewritten: removed incorrect "never acquire OCC leases" claim. Search ops *do* acquire `acquire_snapshot_lease` for MVCC read isolation (mirrors `daemon/handler/read.py:51-60`); they only skip the OCC *mutation* gate. Lease ≠ mutation lock.
- Phase 1 split into Sub-phase 1a (add `iter_paths(manifest)` to `LayerStack` if missing) and Sub-phase 1b (handlers that lease and iterate via the snapshot view, not raw `pathlib.rglob`).
- Phase 4 tightened: narrow additive rule (`glob` only follows `read_file`; `grep` requires both `read_file` and `shell`) to mitigate silent tool-routing changes for agents previously using unbounded `shell`-grep.
- Phase 5 test decorators: copy `EPHEMERALOS_DATABASE_URL` skipif gate verbatim from neighbor scenario test.
- ADR adds an explicit "Contract divergence from TS reference (accepted)" section covering Python `re` vs PCRE2, `fnmatch` vs rg `--glob`, symlink semantics, and confirming the result envelope shape matches.
- Acceptance Criteria adds: 20 KB content cap, 10 MB per-file skip, snapshot-lease requirement, layer_stack-view requirement, and the test skipif decorator.

**Rev 2 → Rev 3 (Critic feedback applied):**
- Removed duplicate `**Registration:**` block in Phase 1 (was at lines 145–153).
- Removed fabricated `is_read_only=True on BaseTool` AC; replaced with verifiable `grep -rn "occ_client\." ... | wc -l = 0` check. `BaseTool` has no read-only flag in this codebase (confirmed); read-only-ness is implicit in not touching the OCC mutation surface, matching `read_file.py`.
- Expanded Sub-phase 1a: `LayerStack.iter_paths(manifest)` requires whiteout + opaque-dir + layer-ordering + symlink handling. Sized realistically (~60–150 LOC + 100+ LOC tests); ADR consequence updated. Hard gate: Phase 1b cannot ship until 1a's unit tests are green. Includes escalation path.
- Added concrete Phase 4 verification step: read loader code, document the `allowed_tools` resolution rule, run a load-and-print one-liner against every md file, commit a before/after diff.
- Added Risks: lease leak on cancellation, Sub-phase 1a sizing risk, Phase 4 recipe-expansion misfire risk.
- Added Acceptance Criteria checkboxes for the three new unit test files + a lease-leak regression test.
- Phase 6 ruff target now includes `src/task_center_runner/tests/sweevo`.
