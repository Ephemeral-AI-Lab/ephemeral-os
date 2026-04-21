# Runtime Verification Examples

Use this reference as a required benchmark-lane preflight. Load it with `load_skill_reference(skill_name="team-validator-playbook", reference_name="runtime-verification-examples")` before the first `daytona_codeact` verification command.

## Task/Goal

- You are about to run the first benchmark-lane verification command.

## Avoid

1. **No wrappers:** Do not switch to `subprocess.run(...)`, `subprocess.Popen(...)`, helper Python wrappers, shell-output wrappers, or wrapper-health verdicts because direct command output is short or a timeout is inconvenient.
2. **No CodeAct reads or writes:** Do not inspect source, write files, move paths, redirect output, or run shell write/move commands through CodeAct. Use dedicated Daytona and CI tools for reads, diagnostics, edits, moves, and deletes.
3. **No dressed-up commands:** Do not add pipes, redirects, stderr capture, suppression, or repo-root `cd` wrappers. Use pytest flags, narrower nodes, background execution, or tool truncation for output control.
4. **No redundant confirmation:** Do not rerun a green command with collect-only/listing probes, duplicate equivalent commands, or unrelated suites. After one exact-command transient failure with no failing ids, shard only the same owned payload targets.
5. **No stale red backgrounds:** Do not leave a clearly red background suite running after progress output exposes a deterministic failure id, `FAILED`, `ERROR`, import error, or traceback.

## Workflow

1. Run the exact payload command through `daytona_codeact(command="...", timeout=N)` and use that same run's returned exit code and output for the verdict.
2. Before every benchmark CodeAct call, inspect the literal command string. If it contains `|` or `>`, rewrite it to a direct repo-root command before calling the tool.
3. Return PASS immediately when the exact command exits `0`; success evidence may cite only commands run after the final validator edit, with observed exit code or key assertion.
4. For the first red run, capture a root-cause packet with `phase`, `boundary`, and `next_question`; never replace failing ids or snippets with vibes.
5. For large suites, use `background=true`, do useful foreground review, and wait only when blocked. Use progress checks only when live output changes whether to wait, cancel, or report.

## Expected Outcome

- The validator returns one verdict backed by exact runtime evidence from the owned command surface.
