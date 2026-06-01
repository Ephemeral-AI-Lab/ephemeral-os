# Sandbox Rust Migration - Phase 3T Terminal Sessions and Deferred Gate Closeout Plan

**Status:** Implementation plan.
**Date:** 2026-06-01.
**Parent plan:** `docs/plans/sandbox-rust-external-migration-PLAN.md`.
**Progress tracker:** `docs/plans/sandbox-rust-external-migration-PROGRESS.md`.

This document captures the intermediate terminal/session and deferred-gate
implementation between Phase 3 and Phase 3.5. Phase 3 is closed at the
structural core boundary. Phase 3T replaces the temporary model-facing shell
path with non-login Bash terminal tools, keeps overlay leases correct, finishes
plugin PPC execution, and closes the high-risk gates that must be tested against
the real shell/session contract instead of raw argv.

## 1. Phase Boundary and Deferred Gates

Phase 3 is accepted as the structural substrate:

- direct `write_file` / `edit_file` publish through routed `eos-occ`;
- shell/search overlay plumbing through daemon-owned LayerStack leases;
- background in-flight registry, heartbeat, cancel, and active-call TTL guard;
- LayerStack squash/GC primitives;
- PPC frame, mode-selection, no-OCC crate edge, warm-server registry, and
  teardown scaffolding;
- CP-4s raw-argv structural smoke evidence.

The following items are exported from Phase 3 to Phase 3T:

- **CP-4t** non-login Bash terminal path: `/bin/bash --noprofile --norc -c <cmd>`;
- **CP-4** throughput/contention against `read_file`, `write_file`,
  `edit_file`, non-login Bash shell, search, LayerStack maintenance, and plugin
  self-managed interleave;
- **CP-5** OCC service cache-lock contention under LRU churn;
- **AV-3** live process-tree/session cancellation and timeout cleanup;
- **AV-4** drop-free audit pull under CP-4 load;
- **AV-7** forward/back on-disk format parity, including `layer_digest`
  byte-stream parity and head-dedup decisions;
- **AV-10** process-backed plugin PPC parity across READ_ONLY, WRITE_ALLOWED,
  and self-managed modes;
- **§7 differential/property tests** under contention against the completed
  Bash/session and PPC implementation.

Do not use raw argv for CP-4/CP-4t throughput or contention gates. Existing raw
argv support is historical CP-4s structural evidence only. Phase 3T replaces
the shell implementation path with non-login Bash instead of wrapping a
model-facing gate around raw argv. Exit requires no model-facing raw-argv path
and no raw-argv performance gate.

## 2. Shell Tools, TTY, and Workspace Semantics

### Runtime Choice

Use native container Bash for the model-facing shell runtime:

```text
/bin/bash --noprofile --norc -c <cmd>
```

Do not use login Bash for model-facing command startup. In the Dask image,
`/bin/bash -lc true` and `/bin/bash -ls` cost roughly 300 ms because profile
startup activates environment machinery. The non-login command above preserves
Bash language semantics while avoiding profile startup.

There is no `/bin/sh` fallback, no host-shell fallback, no `bash -lc` fallback,
and no model-facing raw argv contract. If `/bin/bash` is missing, shell
execution fails as a sandbox readiness/setup error. The model-facing command
input remains a shell-format string.

### Command Environment

The shell runner must provide the required sandbox command environment
explicitly. Correctness must not depend on `/etc/profile`, `.bash_profile`, or
`.bashrc` side effects.

For the SWE-EVO Dask image, the verified fast environment requirement is:

```text
PATH=/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:$PATH
```

With that environment, `python` resolves to
`/opt/miniconda3/envs/testbed/bin/python` and `conda` resolves to
`/opt/miniconda3/bin/conda` without using login Bash. Current Rust overlay
execution clears the child environment and falls back to a minimal PATH; that
means plain `conda run ...` fails today unless the command uses an absolute
conda path or sets PATH inline. Phase 3T must fix this at the shell-session
environment boundary, not by reintroducing login Bash.

Avoid `conda run` on hot paths. It works when invoked through an absolute path
or explicit PATH, but it costs roughly 1.3 s in the Dask image. Direct testbed
Python is much faster, though importing Dask still costs hundreds of
milliseconds and is not a shell-startup problem.

### Current Overlay Performance Evidence

Fresh overlay-inclusive measurements captured on 2026-06-01:

| Case | Evidence | Host observation | Finding |
| --- | --- | ---: | --- |
| Non-login Bash `true` | `bench/phase3-overlay-bash-lc-rerun-20260601.json` | p50 42.7 ms, p95 43.1 ms | Fast shell startup with overlay. |
| Login Bash `true` | `bench/phase3-overlay-bash-lc-rerun-20260601.json` | p50 306.8 ms, p95 307.0 ms | Profile startup adds about 260 ms. |
| Non-login Bash `ls >/dev/null` | `bench/phase3-overlay-bash-profile-basic-commands-20260601.json` | p50 56.0 ms, p95 58.9 ms | Normal read command stays sub-100 ms. |
| Login Bash `ls >/dev/null` | `bench/phase3-overlay-bash-profile-basic-commands-20260601.json` | p50 317.6 ms, p95 317.7 ms | Login overhead dominates. |
| Non-login Bash `mkdir -p ...` | `bench/phase3-overlay-bash-profile-basic-commands-20260601.json` | p50 55.9 ms, p95 56.1 ms | Write command stays sub-100 ms. |
| Login Bash `mkdir -p ...` | `bench/phase3-overlay-bash-profile-basic-commands-20260601.json` | p50 321.3 ms, p95 325.1 ms | Login overhead dominates. |
| Non-login Bash `cat README.rst >/dev/null` | `bench/phase3-overlay-bash-profile-basic-commands-20260601.json` | p50 56.3 ms, p95 56.7 ms | Small file read stays sub-100 ms. |
| Login Bash `cat README.rst >/dev/null` | `bench/phase3-overlay-bash-profile-basic-commands-20260601.json` | p50 328.1 ms, p95 330.7 ms | Login overhead dominates. |
| PTY-proxy non-login Bash `true` | `bench/phase3-overlay-pty-bash-rerun-20260601.json` | p50 79.0 ms, p95 82.4 ms | Conservative PTY proxy is still sub-100 ms. |
| Direct testbed Python importing Dask | `bench/phase3-overlay-conda-python-rerun-20260601.json` | samples 366.3-416.8 ms | Python/Dask import dominates, not overlay. |
| Absolute `conda run -n testbed ...` | `bench/phase3-overlay-conda-absolute-rerun-20260601.json` | samples 1340.6-1395.0 ms | `conda run` is too slow for hot shell paths. |

Every accepted timing above went through the Rust daemon `api.v1.shell` overlay
path: LayerStack lease, overlay mount, command execution, capture, OCC
publish/discard, cleanup, and lease release.

### Model-Facing Tools

Create these shell tools:

```text
exec_command(cmd, tty, yield_time_ms?, timeout?)
write_stdin_exec_command(shell_session_id, chars, yield_time_ms?)
check_shell_progress(shell_session_id, seconds)
cancel_exec_command(shell_session_id)
```

`exec_command` inputs:

| Field | Meaning |
| --- | --- |
| `cmd` | Shell-format command string, executed at the overlay workspace root. |
| `tty` | `true` for interactive/session commands; `false` for finite blocking commands. |
| `yield_time_ms` | Initial output wait, only meaningful for `tty=true`. |
| `timeout` | Total command/session timeout. |

Do not expose `workdir`, `shell`, `login`, or per-call `max_output_tokens`.
Output limits are global configuration.

All shell tool responses use a minimal shape:

```text
status
output
shell_session_id    # only when tty=true is still running
```

`output` is only real captured stdout/stderr/PTY bytes. Do not synthesize
lifecycle text, command classifications, or guessed messages.

### `tty=false`: Blocking Finite Command

`tty=false` is the finite command path:

1. Spawn `/bin/bash --noprofile --norc -c <cmd>` without a PTY.
2. Block until the top-level Bash process exits or `timeout` fires.
3. When Bash exits, immediately terminate any remaining descendants from the
   command process group/cgroup.
4. Drain real stdout/stderr bytes produced before cleanup.
5. Return `status` and `output`.
6. Never return `shell_session_id`.

This means detached shell patterns do not escape the finite command boundary.
For example:

```bash
nohup python train.py 2>&1 &
```

under `tty=false` exits Bash quickly, then the remaining Python descendant is
terminated immediately. Long-running work must use `tty=true`.

For `tty=false`, capture stdout and stderr as one real combined stream when
possible, so `output` reflects the bytes emitted during execution instead of a
post-hoc concatenation.

### `tty=true`: Terminal Session

`tty=true` is the interactive and long-running session path:

1. Spawn `/bin/bash --noprofile --norc -c <cmd>` with stdin/stdout/stderr
   attached to a PTY slave.
2. Read from the PTY master.
3. If the session is still running after `yield_time_ms`, return
   `status=running`, the real terminal `output`, and `shell_session_id`.
4. Keep the PTY, process group/cgroup, output ring, and workspace resources
   alive until exit, timeout, or cancellation.

For `tty=true`, `output` is the actual terminal display transcript:

- stdout and stderr are naturally merged by the PTY;
- stdin appears only when the terminal/program echoes it;
- hidden input, raw-mode input, and no-echo prompts must not be fabricated;
- lifecycle reminders must not be inserted into `output`.

`check_shell_progress(shell_session_id, seconds)` is PTY-only. It returns the
actual terminal output observed during the last `seconds`. It does not use a
cursor and does not wait for new output.

`write_stdin_exec_command(shell_session_id, chars, yield_time_ms?)` writes bytes
to the PTY, waits up to `yield_time_ms`, and returns only the real PTY output
observed after the write.

`cancel_exec_command(shell_session_id)` terminates the session process
group/cgroup, drains final real output, and releases held resources.

### Ephemeral Workspace Handling

Every shell execution in the default collaborative workspace runs inside the
overlay workspace rooted at `/testbed`.

For `tty=false`:

1. Acquire the LayerStack snapshot lease.
2. Allocate overlay upper/work directories.
3. Mount overlay at the workspace root.
4. Run blocking Bash.
5. On Bash exit, terminate remaining descendants.
6. Capture changes.
7. Publish or discard through OCC.
8. Release the lease and delete runtime directories.

For `tty=true`:

1. Acquire the LayerStack snapshot lease.
2. Allocate overlay upper/work directories.
3. Mount overlay at the workspace root.
4. Start the PTY-backed Bash session.
5. If still running, return `shell_session_id` and keep the lease, overlay
   directories, PTY, process group/cgroup, and output ring alive.
6. On session exit, cancellation, or timeout, capture changes, publish or
   discard through OCC, release the lease, and delete runtime directories.

Overlay is never skipped for shell evidence or implementation. All Phase 3T
performance and correctness evidence must include lease acquisition, overlay
mount, command execution, capture, OCC publish/discard, cleanup, and lease
release.

If a long-running session publishes after the shared workspace has moved, the
OCC result must be reported as metadata/status and via reminders. It must not
overwrite shared workspace state.

### Isolated Workspace Handling

When an agent has entered isolated workspace mode, shell tools run inside that
agent's active isolated workspace handle.

In isolated mode:

- do not create a publishable per-command OCC overlay;
- do not publish shell writes to the shared workspace;
- keep writes inside the isolated private workspace;
- keep the isolated workspace handle alive while `shell_session_id` is active;
- reject `exit_isolated_workspace` while shell sessions are active unless the
  caller explicitly force-cancels them;
- on normal shell exit, keep changes in the isolated workspace until isolated
  exit;
- on isolated exit, discard scratch state and release the pinned snapshot lease.

## 3. Implementation Order

1. **Finite non-login Bash command path.** Add the model-facing
   `exec_command(cmd, tty=false, ...)` path on top of
   `/bin/bash --noprofile --norc -c <cmd>`, explicit command environment, and
   the existing overlay lease/mount/capture/publish lifecycle. This replaces
   raw argv for model-facing shell execution.
2. **Finite cleanup semantics.** Ensure `tty=false` kills detached descendants
   after Bash exits, enforces timeout cleanup, drains real stdout/stderr, and
   releases overlay/session resources deterministically.
3. **PTY session core.** Add `exec_command(cmd, tty=true, ...)` with PTY master,
   process group/cgroup, output ring, retained overlay lease, and terminal-state
   cleanup.
4. **Session control tools.** Add `write_stdin_exec_command`,
   `check_shell_progress`, and `cancel_exec_command`, all returning only real
   captured terminal output and status.
5. **Typed background and subagent surface.** Retire the model-facing generic
   background shell tools, keep the internal manager, and expose shell-session
   and subagent progress/cancel identifiers separately.
6. **Plugin PPC execution.** Complete process-backed warm-server
   spawn/round-trip, READ_ONLY out-of-process dispatch, WRITE_ALLOWED
   eosd-owned overlay+OCC wrapping, and self-managed OCC callback over PPC.
   Self-managed plugin callbacks must route through the same per-root OCC writer
   and storage lock as primary publishes.
7. **Gate closeout.** Run CP-4t first, then AV-3/AV-4 shell-session lifecycle
   checks, then AV-10 plugin parity, then AV-7 forward/back parity, then CP-4
   throughput/contention with non-login Bash and plugin interleave, then CP-5
   cache-lock contention, then the §7 differential/property contention suite.

## 4. Background Tool Deletion, Background Concept Kept

Retire the model-facing generic background shell path:

```text
shell background=true
check_background_task_result
wait_background_tasks
cancel_background_task
```

Keep the internal background/session manager. It remains responsible for
session ownership, cancellation, timeout cleanup, output retention, heartbeat
state, and isolated-workspace lifecycle gates.

The manager tracks typed work:

| Kind | Public identifier | Created by |
| --- | --- | --- |
| `command` | `shell_session_id` | `exec_command(..., tty=true)` when still running after yield. |
| `subagent` | `subagent_session_id` | `run_subagent(...)`. |

`tty=false` shell commands are blocking and never enter the background manager as
model-facing sessions.

## 5. Subagents Update

Keep subagents as background work, but remove their dependency on generic
background task tools.

Create or keep these model-facing subagent tools:

```text
run_subagent(agent_name, prompt)
check_subagent_progress(subagent_session_id, last_n_messages)
cancel_subagent(subagent_session_id)
```

`run_subagent` returns:

```text
status=running
subagent_session_id
```

Subagents should no longer expose generic `bg_N` identifiers to the model. The
internal manager may still use its own private record IDs, but the model-facing
identifier is `subagent_session_id`.

## 6. Notification Update

Replace generic background reminders with typed reminders.

Reminders for shell sessions include:

```text
shell_session_id
status
instruction to use check_shell_progress
instruction to use write_stdin_exec_command
instruction to use cancel_exec_command
```

Reminders for subagents include:

```text
subagent_session_id
status
instruction to use check_subagent_progress
instruction to use cancel_subagent
```

Trigger reminders when:

- a shell session or subagent starts;
- a PTY session appears to be waiting for input;
- a shell session or subagent exits, fails, times out, or is cancelled;
- the agent tries to finish while shell sessions or subagents remain active.

Prompt/input detection is observational only. It may use PTY output, idle time,
and live process state, but it must not classify command strings as policy.

Reminder text and lifecycle metadata are separate from shell tool `output`.
Shell `output` remains only the real stdout/stderr/PTY transcript.

## 7. Verification Targets

Phase 3T should close with overlay-inclusive evidence for:

- `tty=false` blocking command success;
- `tty=false` non-login Bash startup for `true`, `ls`, `mkdir`, and `cat`
  remains sub-100 ms p95 with overlay;
- `tty=false` does not use `bash -lc`, `bash -ls`, profile files, or profile
  activation as an environment fallback;
- `python` and `conda` resolve correctly in the Dask sandbox through explicit
  command environment setup, not through login Bash;
- `tty=false` timeout cleanup;
- `tty=false` `nohup ... &` descendant termination on Bash exit;
- `tty=true` short command exit with no `shell_session_id`;
- `tty=true` long-running command returning `shell_session_id`;
- `tty=true` PTY input via `write_stdin_exec_command`;
- `check_shell_progress(shell_session_id, seconds)` returning only recent real
  terminal output;
- `cancel_exec_command(shell_session_id)` killing the full process group/cgroup;
- ephemeral overlay lease retention until PTY session terminal state;
- isolated workspace exit rejection while shell sessions are active;
- isolated workspace force-cancel cleanup;
- typed subagent launch/progress/cancel;
- typed reminders for active shell sessions and subagents.

Deferred Phase 3 gates close only with the merged Phase 3T evidence:

- **CP-4t:** non-login Bash shell/session latency, correctness, concurrency,
  and daemon/session memory with overlay included;
- **CP-4:** throughput/contention using non-login Bash shell verbs, not raw
  argv, plus self-managed plugin writes interleaved with primary publishes;
- **CP-5:** OCC services cache-lock wait/contention under >256 distinct
  `layer_stack_root` LRU churn;
- **AV-3:** process group/cgroup cancellation, timeout cleanup, and retained
  overlay resource release for both finite commands and PTY sessions;
- **AV-4:** audit pull loses zero records under CP-4 load;
- **AV-7:** Python reads Rust-published state and Rust reads Python-published
  state with canonically-equal results, byte-identical `layer_digest` streams,
  and identical head-dedup decisions;
- **AV-10:** plugin PPC parity across READ_ONLY, WRITE_ALLOWED, and
  self-managed modes, including the MF-1 single-writer callback path;
- **§7 differential/property:** Python and Rust run identical operation
  sequences against separate state under contention, with shell verbs using the
  non-login Bash contract.

Performance evidence must include 1, 3, 5, and 10 concurrent shell/session
cases where applicable, and every accepted sample must run through the real
overlay path.
