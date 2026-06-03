# Rust Parity Review — Sandbox tools (command_exec, write_stdin, write, edit, multi-edit, grep, glob)

Area key: `sandbox_tools` (domain: sandbox)
Report scope: the `tool_api` public API facade, agent-facing tool wrappers, the in-namespace
read-only primitives, and the terminal-pair allocator.

## Ground truth

Python ground truth (authoritative for dynamics/constants/ordering):
- API facade verbs: `backend/src/sandbox/api/tool/{read,write,edit,glob,grep}.py`,
  `backend/src/sandbox/api/tool/command.py`.
- Daemon-response parsing + conflict classification:
  `backend/src/sandbox/api/tool/_daemon_response_parsing.py`,
  `backend/src/sandbox/api/tool/_conflict_detection.py`,
  `backend/src/sandbox/api/tool/_operation_audit.py`.
- Op names + timeouts: `backend/src/sandbox/api/transport.py` (lines 15-29),
  `backend/src/sandbox/api/timeouts.py`.
- Agent-facing tool wrappers + registry: `backend/src/tools/sandbox/_lib/registry.py`,
  `backend/src/tools/sandbox/{edit_file,multi_edit,write_file,read_file,exec_command,write_stdin,glob,grep}/`.
- Host empty-response fail-closed set: `backend/src/sandbox/host/daemon_client.py:619-624`.

Architecture corroboration:
- `docs/architecture/tools/sandbox.html`: registry returns `read_file, write_file, edit_file,
  multi_edit, exec_command, write_stdin, glob, grep` (line 60); op-name list at line 132 names
  `api.v1.command.write_stdin`; `exec_command` `WRITE_ALLOWED`, no public `mode switch`, running
  commands return `command_session_id` controlled by `write_stdin` (line 158).
- `docs/architecture/tools/terminals.html`: terminal-tool exclusivity (sandbox tools are ordinary
  framework tools, not terminal).

IMPORTANT migration context: commit `37c13f3db` ("remove legacy python sandbox runtime
subsystems") deleted the Python in-process daemon runtime (`sandbox/occ/*`, `sandbox/overlay/*`,
`namespace_runner.py`, `occ_runtime_services.py`). The in-sandbox runtime is now the eosd Rust
binary (`sandbox/crates/eos-daemon`, `eos-runner`). Therefore the in-sandbox find/replace OCC,
glob/grep file-walk, and command-session execution have NO live Python counterpart; their ground
truth is the **wire contract** (the daemon-response envelope the Python `sandbox.api.tool` facade
parses) plus `backend/src/sandbox/shared/edit_apply.py` / `shared/models.py` (referenced by PORT
comments). This is an intentional change, not a missing dynamic.

## Rust mapping

| Concern | Rust anchor |
| --- | --- |
| API facade verbs | `agent-core/crates/eos-sandbox-api/src/tool_api/{read,write,edit,glob,grep,command,control}.rs` |
| Response parsing / conflict classify | `agent-core/crates/eos-sandbox-api/src/tool_api/parse.rs` |
| Op-name enum | `agent-core/crates/eos-sandbox-api/src/ops.rs` |
| Timeouts | `agent-core/crates/eos-sandbox-api/src/timeouts.rs` |
| Agent-facing tool wrappers + registration | `agent-core/crates/eos-tools/src/model_tools/sandbox.rs`; names in `eos-tools/src/name.rs` |
| In-namespace glob/grep primitives | `sandbox/crates/eos-runner/src/tool_primitives.rs` |
| Terminal-pair allocator | `sandbox/crates/eos-terminal-pair/src/lib.rs` |
| eosd daemon edit/exec/stdin handlers | `sandbox/crates/eos-daemon/src/{dispatcher,command}.rs`; find/replace in `eos-protocol/src/models.rs:298` |
| Host empty-response fail-closed set | `agent-core/crates/eos-sandbox-host/src/daemon_client.rs:582-592` |

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|---|---|---|---|---|---|
| 1 | Tool set named exactly: read_file, write_file, edit_file, multi_edit, exec_command, write_stdin, glob, grep | match | none | `tools/sandbox/_lib/registry.py:21-32` | `eos-tools/src/name.rs:109-116`; `model_tools/sandbox.rs:906-995` | All 8 present, same wire names |
| 2 | write_stdin writes to running command session stdin (terminal pair) | bug | high | `api/tool/command.py:74-95` (`DAEMON_OP_COMMAND_WRITE_STDIN`), `transport.py:17` | `tool_api/command.rs:74-79` → `DaemonOp::ExecStdin` (`ops.rs:31` = `api.v1.exec_stdin`) | Rust client sends `api.v1.exec_stdin`; eosd daemon registers only `api.v1.write_stdin`/`api.v1.command.write_stdin` (`dispatcher.rs:149-152`) → `unknown_op`. See Disparity D1. |
| 3 | edit = single find/replace w/ uniqueness/occurrence semantics; multi-edit applies edits in order | match | none | `shared/edit_apply.py` (PORT), `multi_edit/multi_edit.py:99-126` | `eos-protocol/src/models.rs:298-321`; `model_tools/sandbox.rs:369-487` | `apply_search_replace`: empty→EmptyAnchor, 0→NotFound, replace_all replaces all, !replace_all & >1→CountMismatch; multi_edit reports submitted-count |
| 4 | write (create/overwrite) + read semantics preserved | match | none | `api/tool/write.py:25-39`, `read.py:25-34` | `tool_api/write.rs:18-40`, `read.rs:14-25`; `parse.rs:314-323,377-387` | write sends path/content/description/overwrite; read decodes content/exists/encoding(default utf-8) |
| 5 | grep + glob semantics (patterns, output shape) preserved | partial | medium | `api/tool/glob.py:25-36`, `grep.py:25-47`; `parse_glob_result`/`parse_grep_result` `_daemon_response_parsing.py:87-120` | `tool_api/glob.rs`, `grep.rs`; `parse.rs:325-348`; `eos-runner/src/tool_primitives.rs:12-123` | Facade payload + parse match. Namespace primitive divergences (line-number indexing, fnmatch, num_lines) — see D3/D4. |
| 6 | exec_command foreground vs background routing + daemon response parsing | match | none | `api/tool/command.py:33-71,138-182` | `tool_api/command.rs:20-48`; `parse.rs:402-445` | success=`status not in {error,timed_out}`; running→command_session_id; exec uses UNFILTERED changed_paths/kinds |
| C1 | Per-verb timeout constants + dispatch grace arithmetic | match | none | `api/timeouts.py:5-22` | `timeouts.rs:13-29` | READ=WRITE=GLOB=GREP=60, EDIT=20, default_cmd=60, grace=30, dispatch=cmd+grace |
| C2 | strict_int rejects bool-as-int; exit_code accepts bool-as-int (Python isinstance quirk) | match | none | `_daemon_response_parsing.py:66-74`; `command.py:146` | `parse.rs:171-187` (strict), `parse.rs:422-426` (exit_code bool→0/1) | Both quirks reproduced |
| C3 | edit conflict transport error → recoverable Ok(success:false) result, else propagate | match | none | `api/tool/edit.py:56-78`; `_conflict_detection.py:13-52` | `tool_api/edit.rs:47-84`; `parse.rs:63-91` | Codes + markers identical; aborted_overlap status, overlap ConflictInfo |
| C4 | write_stdin Ctrl-C (\x03) while running → cancel session | match | none | `tools/sandbox/write_stdin/write_stdin.py:63-69` | `model_tools/sandbox.rs:850-868` | Python triggers on `status == "running"`; Rust on `status == "running"` (matches) |
| C5 | Host empty-response fail-closed set includes write_stdin op name | bug | medium | `host/daemon_client.py:619-624` lists `api.v1.write_stdin` + `api.v1.command.write_stdin` | `eos-sandbox-host/src/daemon_client.rs:582-592` lists `api.v1.exec_stdin` | Set diverges in lockstep with D1; see D2 |
| C6 | DEFAULT_GLOB_LIMIT / MAX_FILE_BYTES constants | unverifiable | low | (Python primitive deleted in 37c13f3db) | `eos-protocol/src/models.rs:19,22` (MAX_FILE_BYTES=2MiB, DEFAULT_GLOB_LIMIT=100) | No live Python anchor to diff; values look intentional |

## Disparities

### D1 (HIGH, bug/divergent) — write_stdin wire op name: client sends `api.v1.exec_stdin`, daemon only handles `api.v1.write_stdin`

Evidence:
- Python: `backend/src/sandbox/api/transport.py:17` →
  `DAEMON_OP_COMMAND_WRITE_STDIN = "api.v1.write_stdin"`, used by `command.py:88-90`.
- Rust client: `agent-core/crates/eos-sandbox-api/src/tool_api/command.rs:74` uses
  `DaemonOp::ExecStdin`, which serializes to `"api.v1.exec_stdin"`
  (`ops.rs:31-32`, `ops.rs:88`). `write_stdin` is just an alias to `exec_stdin`
  (`command.rs:83-89`). The model doc literally says "through `api.v1.exec_stdin`"
  (`models.rs:417`).
- Rust daemon (eosd, the in-sandbox runtime): registers the stdin handler ONLY under
  `"api.v1.write_stdin"` and `"api.v1.command.write_stdin"`
  (`sandbox/crates/eos-daemon/src/dispatcher.rs:149-152`). There is NO
  `api.v1.exec_stdin` registration (verified by exhaustive grep). The daemon's response
  normalizer at `dispatcher.rs:3345` also matches only
  `"api.v1.write_stdin" | "api.v1.command.write_stdin"`. An unregistered op returns the
  `unknown_op` error envelope (`dispatcher.rs:229-230`).

Why it matters: the entire write_stdin path is broken end-to-end against this daemon. Every
`write_stdin` call (and therefore the Ctrl-C cancel path that depends on a `running` status) would
fail with `unknown_op`. The mismatch is internally consistent across the agent-core stack
(ops.rs, command.rs, host allowlist, model docs all say `exec_stdin`) so unit tests that mock the
transport with `DaemonOp::ExecStdin` (e.g. `model_tools/sandbox.rs:1093,1125`) pass — masking the
break. It only surfaces against the real eosd binary.

Three-way disagreement: Python facade = `api.v1.write_stdin`; arch doc = `api.v1.command.write_stdin`
(both daemon-accepted aliases); Rust client = `api.v1.exec_stdin` (NOT accepted). The doc/Python
pair are compatible; the Rust client is the outlier.

Suggested fix: rename `DaemonOp::ExecStdin` wire string to `"api.v1.write_stdin"` (keep the variant
name if desired but fix `#[serde(rename)]` + `as_wire`), OR register an `api.v1.exec_stdin` alias in
`eos-daemon/dispatcher.rs` and the dispatcher normalizer at line 3345. Prefer the former to match
ground truth. Update the host fail-closed set (D2) and `models.rs:417` doc in the same change.

### D2 (MEDIUM, bug) — host empty-response fail-closed set uses `api.v1.exec_stdin`, not `api.v1.write_stdin`

Evidence:
- Python `_can_retry_empty_response` (`host/daemon_client.py:619-624`) fail-closes
  `api.v1.write_stdin` AND `api.v1.command.write_stdin` (a write/mutation-capable op must not be
  replayed after a daemon respawn — replay could convert an isolated in-flight call into a
  default-mode publish, per the docstring).
- Rust `can_retry_empty_response` (`eos-sandbox-host/src/daemon_client.rs:582-592`) instead lists
  `api.v1.exec_stdin` (and does NOT list `api.v1.command.write_stdin`).

Why it matters: even if D1 were fixed by renaming the client op to `api.v1.write_stdin`, this set
would then NO LONGER fail-close stdin writes (it only matches `exec_stdin`), re-opening the exact
replay-publish hazard the Python docstring warns about. The two bugs currently cancel out only
because both use the wrong string. The `api.v1.command.write_stdin` alias is also missing from the
Rust set entirely.

Suggested fix: change the Rust set to `"api.v1.write_stdin" | "api.v1.command.write_stdin"` to match
Python exactly, coordinated with D1.

### D3 (MEDIUM, divergent) — namespace-runner grep line-number prefix is 1-based on `text.lines()` enumerate; Python primitive is deleted so wire-contract semantics are unpinned

Evidence:
- `sandbox/crates/eos-runner/src/tool_primitives.rs:233-248` `matching_lines` emits
  `"{rel}:{index+1}:{line}"` with `line_numbers`, iterating `text.lines()`. `num_lines` is the count
  of emitted content lines only when `output_mode == "content"` (line 116), else `0`.
- The Python namespace-runner grep that produced this envelope was deleted in `37c13f3db`; the only
  remaining Python is the `parse_grep_result` decoder (`_daemon_response_parsing.py:97-120`), which
  is shape-only and does not constrain match/line semantics.

Why it matters: line-number indexing (1-based vs byte-offset), trailing-newline handling, and the
`num_lines`-only-on-content rule are now defined solely by the Rust primitive with no Python anchor.
This is acceptable as a migration outcome, but the behavior is no longer cross-checked against
ground truth. The Rust unit test (`tool_primitives.rs:378-404`) pins `a.py:2:Hit` (1-based) and
`num_lines==1`, which is self-consistent but unverifiable against Python.

Suggested fix: none required (intentional migration). Recommend pinning these as the new contract in
`docs/architecture/tools/sandbox.html` (or a wire-contract doc) so future drift is detectable.

### D4 (LOW, divergent) — glob uses a hand-rolled `fnmatch` (greedy `*`, no `[...]`/`**` recursion); Python ground truth deleted

Evidence:
- `tool_primitives.rs:262-290` `wildcard_match` supports `*`/`?` only and special-cases a single
  leading `**/` (`glob_matches`, line 250-260). No character classes `[...]`, no general `**`
  recursive directory matching. Basename patterns (no `/`) only match root-level files
  (`!rel.contains('/')`).
- Python's `fnmatch`-based glob primitive was deleted in `37c13f3db`; the facade
  (`api/tool/glob.py`) only forwards `pattern`/`path` and parses `filenames/num_files/truncated`.

Why it matters: glob expressiveness (especially `[abc]` classes and multi-segment `**`) is now
defined by the Rust primitive alone. Low severity because the agent-facing glob contract was always
"basename or simple pattern", and `DEFAULT_GLOB_LIMIT=100` truncation matches the constant. Flag as
a divergence-without-anchor rather than a regression.

Suggested fix: none required; document the supported pattern grammar in the sandbox tools arch page.

### D5 (LOW, doc divergence) — arch doc op-name list names `api.v1.command.write_stdin`; Python code uses `api.v1.write_stdin`

Evidence: `docs/architecture/tools/sandbox.html:132` lists `api.v1.command.write_stdin`; Python
`transport.py:17` uses `api.v1.write_stdin`. The daemon accepts both as aliases
(`dispatcher.rs:149-152`), so this is a harmless doc/code naming inconsistency — but it means the
doc cannot be used to break the D1 tie by itself.

Suggested fix: align the arch doc with the primary Python constant (`api.v1.write_stdin`) or note
both aliases.

## Extra findings

- exec_command parser correctly uses the UNFILTERED changed_paths/changed_path_kinds (does not drop
  blanks), unlike the guarded write/edit parser which filters blanks — Rust reproduces both
  (`parse.rs:435,442` unfiltered vs `parse.rs:366,369` filtered; matches
  `command.py:168-177` vs `_daemon_response_parsing.py:134-135,156-163`). Good fidelity.
- exec_command attaches `api.exec_command.dispatch_total_s` timing in Python (`command.py:60-61`);
  Rust `tool_api/command.rs` deliberately omits clock/dispatch-timing recording (documented in
  `tool_api/mod.rs:1-5`: "the caller records that"). Verify the eos-tools caller actually records an
  equivalent dispatch-total timing; if not, that timing key is silently dropped. (Open question Q1.)
- `count_field` in control.rs (`control.rs:120-126`) uses `as_u64` then clamps to u32, whereas Python
  is `int(response.get("count") or 0)`. A negative count (unlikely from the daemon) would become 0 in
  Rust but negative-then-int in Python; immaterial in practice.
- terminal-pair (`eos-terminal-pair/src/lib.rs`) is Linux-only (posix_openpt/grantpt/unlockpt);
  non-Linux returns Unsupported. This is the runtime substrate for command sessions; it has no
  direct Python analog (Python used the in-process daemon). Intentional.
- Single `edit_file` reports `result.applied_edits` from the daemon (`sandbox.rs:399-407`) while
  `multi_edit` reports the SUBMITTED edit count (`sandbox.rs:483`), exactly mirroring Python
  (`edit_file` via `project_file_mutation` default vs multi_edit `success_extra={"applied_edits":
  len(...)}`). Subtle and correctly preserved.

## Open questions

- Q1: Does the eos-tools exec_command caller record an `api.exec_command.dispatch_total_s` (or
  equivalent) timing to replace the Python facade's timing (`command.py:60-61`)? If not, that timing
  key is dropped.
- Q2: Is `api.v1.exec_stdin` an intentional forward-looking rename that the eosd daemon was supposed
  to also register (i.e., the daemon is behind), or is the client wrong? Either way the current state
  is broken; ground truth (Python + daemon registration) favors `api.v1.write_stdin`.
- Q3: Are there integration tests that exercise the real eosd binary for write_stdin (not a mocked
  `DaemonOp::ExecStdin` transport)? The mocked unit tests pass while the real path would
  `unknown_op`; an integration test would have caught D1.
