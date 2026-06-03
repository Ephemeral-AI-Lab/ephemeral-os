# Independent Verification — Sandbox tools (command_exec, write_stdin, write, edit, multi-edit, grep, glob)

Area key: `sandbox_tools` (domain: sandbox). Verifier re-derived every invariant from the
files themselves (Python ground truth at `/tmp/oldpy/backend/src/sandbox/...`, Rust under
`agent-core/` and `sandbox/`). Did not trust the investigation's anchors; opened and read each.

Migration note confirmed: the in-sandbox Python runtime (`sandbox/occ/*`, `sandbox/overlay/*`,
`edit_apply.py`) is deleted from the /tmp/oldpy snapshot. `sandbox/occ/path_staging.py:24` still
`import`s `from sandbox._shared.edit_apply import apply_search_replace`, but the implementation file
is gone — so the find/replace OCC, glob/grep walk, and command-session execution have NO live Python
counterpart; their ground truth is the wire contract + the surviving Python facade. This matches the
investigation's framing. (Anchor nit: the investigation cites `shared/edit_apply.py`; the real path
is `sandbox/_shared/edit_apply.py`, now deleted.)

## Invariant verdict table

| # | Invariant | Independent status | Severity | Decisive bilateral anchor |
|---|---|---|---|---|
| 1 | Tool set named exactly read_file, write_file, edit_file, multi_edit, exec_command, write_stdin, glob, grep | confirmed_match | none | PY facade/registry deleted; wire names corroborated by arch doc + Rust `eos-tools/src/name.rs:109-116` (all 8) + `model_tools/sandbox.rs:906-1002` register block |
| 2 | write_stdin writes to running command-session stdin via correct wire op | confirmed_disparity | high | PY `api/tool/command.py:88-90` sends `DAEMON_OP_COMMAND_WRITE_STDIN="api.v1.write_stdin"` (`transport.py:17`); daemon registers `api.v1.write_stdin`/`api.v1.command.write_stdin` (`eos-daemon/dispatcher.rs:143-147`). Rust client sends `DaemonOp::ExecStdin`=`"api.v1.exec_stdin"` (`tool_api/command.rs:74`, `ops.rs:31,88`) — UNREGISTERED → `unknown_op` (`dispatcher.rs:203,220-224`). **D1 confirmed.** |
| 3 | edit = single find/replace w/ uniqueness/occurrence; multi-edit applies in order, reports submitted count | confirmed_match | none | PY `apply_search_replace` deleted but `str.count` semantics doc'd; Rust `eos-protocol/models.rs:281-304` (empty→EmptyAnchor, 0→NotFound, replace_all→replace all, !replace_all&>1→CountMismatch, 1→replacen). multi_edit submitted count `model_tools/sandbox.rs:454,478`; PY facade `edit.py` confirms guarded-mutation shape |
| 4 | write (create/overwrite) + read semantics preserved | confirmed_match | none | **Request side**: PY `write.py:26-31` sends `{path,content,description,overwrite}` = Rust `write.rs:19-30` (incl. `overwrite` Bool, NOT dropped); PY `read.py:26` `{path}` = Rust `read.rs:20`. **Response side**: PY `_daemon_response_parsing.py:77-84` (content/exists/encoding default utf-8) = Rust `parse.rs:314-323,377-387` |
| 5 | grep + glob facade payload + parse shape preserved | confirmed_match (facade); divergent (in-namespace primitive, no PY anchor) | medium | **Request side**: PY `grep.py:26-39` (6 always-on flags `pattern,output_mode,offset,case_insensitive,line_numbers,multiline` + conditional `path,glob_filter,head_limit`) = Rust `grep.rs:20-40` (identical fields + same omit rules); PY `glob.py:26-28` `{pattern}`+cond `path` = Rust `glob.rs:20-22`. **Response side**: PY `_daemon_response_parsing.py:87-120` = Rust `parse.rs:325-348`. Namespace primitive `eos-runner/tool_primitives.rs:12-123` has no live PY anchor (D3/D4) |
| 6 | exec_command foreground/background routing + daemon-response parse | confirmed_match | none | PY `_parse_exec_command_result` `command.py:138-182` (`success=status not in {error,timed_out}`, `int(exit_code) if isinstance(int)` incl. bool, unfiltered changed_paths) vs Rust `parse.rs:402-445` — identical |
| C1 | Per-verb timeouts + dispatch grace arithmetic | confirmed_match | none | PY `timeouts.py:5-20` (READ=WRITE=GLOB=GREP=60, EDIT=20, default cmd=60, grace=30) vs Rust `timeouts.rs:11-31` (same; `SHELL_*`→`EXEC_*` rename only) |
| C2 | strict_int rejects bool-as-int; exit_code accepts bool-as-int | confirmed_match | none | PY `strict_int_from_daemon_field` `_daemon_response_parsing.py:66-74` + `command.py:146`; Rust `parse.rs:171-187,422-426` |
| C3 | edit conflict transport error → recoverable Ok(success:false), else propagate | confirmed_match | none | PY `edit.py:56-78` + `_conflict_detection`; Rust `tool_api/edit.rs:61-84` + `parse.rs:63-91`. Codes/markers + `aborted_overlap`/overlap ConflictInfo/`changed_paths=[path]`/`applied_edits=0` identical |
| C4 | write_stdin Ctrl-C (\x03) while running → cancel | confirmed_match (logic); unreachable under D1 | low | PY `tools/sandbox/write_stdin/*` deleted; Rust `model_tools/sandbox.rs:853` triggers on `chars.contains('\u{3}') && status=="running"`. Logic correct, but D1 makes the prior write_stdin call return `unknown_op` (status="error"), so cancel never fires in practice |
| C5 | Host empty-response fail-closed set includes write_stdin op | confirmed_disparity | medium | PY `host/daemon_client.py:614-622` fail-closes `api.v1.write_stdin` AND `api.v1.command.write_stdin`; Rust `eos-sandbox-host/daemon_client.rs:582-592` lists `api.v1.exec_stdin` and omits `api.v1.command.write_stdin`. **D2 confirmed** |
| C6 | DEFAULT_GLOB_LIMIT / MAX_FILE_BYTES constants | unproven (no live PY anchor) | low | PY primitive deleted; Rust `eos-protocol/models.rs:15,17` MAX_FILE_BYTES=2MiB, DEFAULT_GLOB_LIMIT=100. Values plausible, uncheckable against ground truth |

## Disparity adjudication

- **D1 (write_stdin op name `exec_stdin` vs `write_stdin`) — CONFIRMED, HIGH.** Re-derived end to end,
  not just by grep:
  - Client serialization: `model_tools/sandbox.rs:848` → `eos_sandbox_api::write_stdin` →
    `tool_api/command.rs:88` aliases to `exec_stdin` → sends `DaemonOp::ExecStdin`
    (`command.rs:74`) → wire `"api.v1.exec_stdin"` (`ops.rs:31,88`, pinned by test
    `daemon_op_wire_strings` `ops.rs:120`).
  - Host passthrough is verbatim: `eos-sandbox-host/daemon_client.rs:477` `op.as_wire()` →
    `serialize_envelope` with no rewrite.
  - Daemon dispatch is a direct `self.handlers.get(&request.op)` HashMap lookup
    (`dispatcher.rs:203`) with NO op-aliasing/normalization anywhere in `sandbox/` (exhaustive grep:
    only `error.rs` "alias" doc-comments). `api.v1.exec_stdin` is unregistered and is not a plugin
    op → `UnknownOp` envelope (`dispatcher.rs:220-224`).
  - Python ground truth + daemon agree on `api.v1.write_stdin` (`command.py:88-90`,
    `transport.py:17`, `dispatcher.rs:143-147`). The Rust client is the sole outlier.
  - Masking confirmed: mocked unit tests assert `DaemonOp::ExecStdin` so they pass; the dispatcher
    response-normalizer at `dispatcher.rs:3323` also matches only `write_stdin`/`command.write_stdin`.
    Only the real eosd binary surfaces the break. **Adjudication: confirmed, severity HIGH retained.**

- **D2 (host fail-closed set diverges in lockstep) — CONFIRMED, MEDIUM.** PY set
  (`daemon_client.py:614-622`) = `{api.edit_file, api.v1.edit_file, api.write_file, api.v1.write_file,
  api.v1.exec_command, api.v1.write_stdin, api.v1.command.write_stdin}`. Rust
  (`daemon_client.rs:582-592`) substitutes `api.v1.exec_stdin` for `api.v1.write_stdin` and drops
  `api.v1.command.write_stdin`. The Rust test `empty_response_gating_matches_python_set`
  (`daemon_client.rs:1212-1223`) is mislabeled — it asserts `api.v1.exec_stdin` fails closed, so it
  green-lights the divergence. The investigator's "both bugs cancel out only because both use the
  wrong string; fixing D1 alone re-opens the replay-publish hazard" reasoning is correct.
  **Adjudication: confirmed, severity MEDIUM retained.**

- **D3 (grep primitive line-number/num_lines semantics, no PY anchor) — CONFIRMED as
  divergence-without-anchor, MEDIUM→LOW.** `tool_primitives.rs:233-248` emits `{rel}:{index+1}:{line}`
  (1-based on `text.lines().enumerate()`), `num_lines` counted only when `output_mode=="content"`
  (line 116), else 0. Python primitive deleted; `parse_grep_result` is shape-only. Accurate.
  Intentional migration; behavior is real but uncheckable against ground truth. I would tag severity
  LOW (no agent-visible regression evidence), but the MEDIUM label is defensible as "uncross-checked".

- **D4 (hand-rolled glob fnmatch, no `[...]`/general `**`) — CONFIRMED, LOW.** `tool_primitives.rs:250-290`:
  `wildcard_match` handles `*`/`?` only; `glob_matches` special-cases a single leading `**/`; basename
  patterns (no `/`) match root-level only (`!rel.contains('/')`, line 252). No char classes, no
  multi-segment `**`. Python `fnmatch` primitive deleted. Accurate divergence-without-anchor.

- **D5 (arch doc names `api.v1.command.write_stdin`; PY uses `api.v1.write_stdin`) — CONFIRMED, LOW
  doc-only.** Daemon accepts both as aliases (`dispatcher.rs:143-147`), so harmless; the doc cannot
  break the D1 tie alone. Accurate.

## New findings

- **N1 (LOW, confirmed disparity — upgrade of investigator Q1): exec_command drops the
  `api.exec_command.dispatch_total_s` timing.** Python `command.py:60-61` attaches
  `timings["api.exec_command.dispatch_total_s"] = monotonic_now() - total_start` to every
  exec_command result. The Rust path records NO such timing: `tool_api/command.rs::exec_command` does
  not, and the eos-tools caller `ExecCommand::execute` (`model_tools/sandbox.rs:747-` ) has no
  `Instant`/`elapsed`/`monotonic`; grep for `dispatch_total` across all of `agent-core/` returns
  nothing. `tool_api/mod.rs` claims "the caller records that" — but the caller does not. This is a
  real dropped diagnostic field, not just an open question. Severity LOW (diagnostics-only; no
  control-flow impact). The investigator was appropriately cautious; I upgrade Q1 → confirmed LOW
  disparity.

- **N2 (informational): D2's Rust set also carries legacy non-v1 aliases `api.edit_file` /
  `api.write_file`** (`daemon_client.rs:585,587`) matching Python's legacy entries — these are correct
  and not part of the divergence; only the stdin entries diverge. Noted so the D2 fix does not
  accidentally remove the legacy aliases.

- **No investigator_missed (false-match) findings beyond N1.** Every invariant the investigator marked
  "match" was independently re-derived to genuinely match — on BOTH the request-payload side and the
  response-decoder side. This includes the create/overwrite semantic (inv 4: Rust `write.rs:30`
  forwards `overwrite` with the same shape as PY `write.py:30`) and the full grep flag-forwarding set
  (inv 5: Rust `grep.rs` forwards every flag PY `grep.py` does, with matching conditional-omit rules),
  plus the high-risk parse rules (success-from-status, exit_code bool quirk, unfiltered-vs-filtered
  changed_paths, strict_int bool-rejection, conflict-recovery mapping). The two "bug" rows (D1/D2) are
  real and correctly characterized.

## Overall verdict

The investigation is accurate and well-calibrated. The headline HIGH finding (D1: write_stdin wire
op `api.v1.exec_stdin` vs daemon-registered `api.v1.write_stdin`) is independently confirmed end to
end — client serialization, verbatim host passthrough, and a no-alias daemon HashMap dispatch all
agree that every `write_stdin` call against the real eosd binary returns `unknown_op`, which also
disables the Ctrl-C cancel path (C4). D2 (host fail-closed set) is confirmed and correctly tied to
D1, including the misleading "matches_python_set" test. The migration-boundary divergences D3/D4/C6
are correctly framed as divergence-without-live-anchor rather than regressions. All "match" invariants
hold under independent re-derivation. One upgrade: investigator Q1 → confirmed LOW disparity N1
(`dispatch_total_s` timing silently dropped). No false matches detected.

Tally (12 invariant rows): confirmed_match = 9 (inv 1, 3, 4, 5, 6, C1, C2, C3, C4 — C4 logic-matches
but is unreachable under D1); confirmed_disparity = 2 (inv 2 = D1 HIGH, C5 = D2 MEDIUM); unproven = 1
(C6, no live PY anchor). Plus 1 new confirmed LOW disparity (N1, dispatch_total_s dropped) outside the
12-row table.

DONE sandbox_tools: 9 confirmed_match, 2 confirmed_disparity, 1 unproven; no investigator_missed (investigator Q1 upgraded to confirmed LOW disparity N1)
