# Runtime Verification Examples

Use this reference as a required benchmark-lane preflight. Load it with `load_skill_reference(skill_name="team-validator-playbook", reference_name="runtime-verification-examples")` before the first `daytona_codeact` verification command.

## Task/Goal

- You are about to run the first benchmark-lane verification command.

## Avoid

- Must not switch to `subprocess.run(...)`, `subprocess.Popen(...)`, or helper Python wrappers just because direct command output is short.
- Must not write or move files through CodeAct; no `sed -i`, `tee file`, output redirects, shell write/move commands, `mv`, `shutil.move`, `os.rename`, `git rm`, `git mv`, or inline Python writes. Pure removals such as `rm`, `unlink`, `os.remove`, `Path.unlink`, and `shutil.rmtree` may run through CodeAct because the overlay audit path converts tracked removals into OCC-gated deletes and rejects unsupported removal shapes. Use `daytona_move_file` for repo path moves.
- Must not inspect source through CodeAct; no `cat`, `sed -n`, `grep`/`rg`, `head`/`tail`/`nl`, `git diff`, Python file reads, or source introspection.
- Must not rerun a green command with `--collect-only`, `ls`, or extra probes to "confirm" the pass, and must not rerun a failing broad regression command once it already printed the failing ids or original-message producer you need.
- After one exact-command `transient_runtime` failure with no failing ids, may shard only the same owned payload targets into disjoint equivalent chunks.
- Do not fall back to `subprocess.run(...)` or `subprocess.Popen(...)` to work around timeouts, and do not leave a clearly red background suite running after a progress check already exposed the decisive failure.

## Workflow

- Must run the exact payload command through `daytona_codeact(command="...", timeout=N)`, use that same run's exit code and returned output, and return PASS immediately when it exits `0`.
- Before every benchmark CodeAct call, inspect the exact `command` string. If it contains the literal character `|` or `>` anywhere, do not call CodeAct; rewrite to a direct repo-root command first.
- Must run the repo command itself, not a shell-output wrapper. Use pytest flags, narrower nodes, background execution, or tool truncation for volume control; do not pipe to `head` or `tail`, even to keep output short.
- Bad: `daytona_codeact(command="python -m pytest dask/tests/test_config.py -v 2>&1 | tail -60")`
- Rewrite any planned command containing `2>&1`, `2>/dev/null`, `>`, `>>`, `| head`, or `| tail` before you call CodeAct.
- Must not launch duplicate equivalent verification commands in parallel; one exact command per suite is enough unless sharding after a transient no-output failure.
- Must treat wrapper success, manifest output, and `__CODEX_EXIT_CODE__` as wrapper health only; the verdict comes from the returned exit code.
- Must turn the first red run into a root-cause packet with `phase`, `boundary`, and `next_question`. Never replace that packet with vibes.
- For large suites, use `background=true` on `daytona_codeact`, keep doing useful foreground review, and call `wait_for_background_task(timeout=120)` when blocked on the result. Avoid `check_background_progress(...)` unless live output changes whether you keep waiting, cancel, or report.
- If a progress check already shows a deterministic failure id, `FAILED`, `ERROR`, `ImportError`, or traceback, cancel the task and use that partial output as the runtime evidence.
- A success verdict may cite only commands actually run after the final validator edit, with their observed exit code or key assertion.

## Expected Outcome

- The validator returns one verdict backed by exact runtime evidence from the owned command surface.
