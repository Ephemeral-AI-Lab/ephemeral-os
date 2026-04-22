"""Quality checks for bundled team playbooks."""

from __future__ import annotations

import re
from pathlib import Path


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_CONTENT = _BACKEND_ROOT / "src/skills/bundled/content"
_PLAYBOOKS = [
    _CONTENT / "team-developer-playbook/SKILL.md",
    _CONTENT / "team-validator-playbook/SKILL.md",
    _CONTENT / "team-planner-playbook/SKILL.md",
    _CONTENT / "team-replanner-playbook/SKILL.md",
    _CONTENT / "team-scout-playbook/SKILL.md",
]
_PLAYBOOKS = [path for path in _PLAYBOOKS if path.exists()]
_ALL_SKILLS = _PLAYBOOKS + [
    _CONTENT / "verification-replan/SKILL.md",
]
_ALL_SKILLS = [path for path in _ALL_SKILLS if path.exists()]
_REFERENCES = [
    _CONTENT / "team-developer-playbook/references/codeact-runtime-examples.md",
    _CONTENT / "team-developer-playbook/references/pre-completion-validation.md",
    _CONTENT / "team-developer-playbook/references/root-cause-debugging.md",
    _CONTENT / "team-developer-playbook/references/widening-and-runtime.md",
    _CONTENT / "team-scout-playbook/references/completion-contract.md",
    _CONTENT / "team-validator-playbook/references/cross-surface-guardrails.md",
    _CONTENT / "team-validator-playbook/references/runtime-verification-examples.md",
    _CONTENT / "team-replanner-playbook/references/action-add-tasks.md",
    _CONTENT / "team-replanner-playbook/references/action-cancel-and-redraft.md",
    _CONTENT / "team-replanner-playbook/references/scout-launch-contract.md",
    _CONTENT / "verification-replan/references/triage-format.md",
]
_REFERENCES = [path for path in _REFERENCES if path.exists()]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _hard_rules_section(content: str) -> str:
    after_header = content.split("## Hard rules", 1)[1]
    return re.split(r"\n## ", after_header, maxsplit=1)[0]


def test_skills_and_references_stay_short() -> None:
    skill_line_limits = {
        "team-developer-playbook": 180,
        "team-planner-playbook": 380,
        "team-root-planner-playbook": 380,
    }
    for path in _ALL_SKILLS:
        limit = skill_line_limits.get(path.parent.name, 150)
        assert len(_read(path).splitlines()) <= limit, f"{path} should stay short"
    for path in _REFERENCES:
        assert len(_read(path).splitlines()) <= 150, f"{path} should stay short"


def test_hard_rule_numbers_do_not_repeat() -> None:
    for path in _PLAYBOOKS:
        if path.parent.name == "team-planner-playbook":
            continue
        section = _hard_rules_section(_read(path))
        labels = re.findall(r"^(\d+)\.\s", section, flags=re.MULTILINE)
        assert labels, f"expected numbered hard rules in {path}"
        assert labels == [str(i) for i in range(1, len(labels) + 1)], f"bad numbering in {path}"


def test_skills_use_clear_must_never_language() -> None:
    for path in _ALL_SKILLS:
        content = _read(path)
        assert re.search(r"\bmust\b", content, flags=re.IGNORECASE)
        assert (
            "Never " in content
            or "Never\n" in content
            or "Never use" in content
            or "Do not " in content
            or "do not " in content
            or "Must not " in content
        )

    for path in _REFERENCES:
        content = _read(path)
        assert (
            "Use this reference" in content
            or "Use this reference only" in content
            or content.startswith("# Action Reference:")
        )


def test_team_references_follow_scan_friendly_structure() -> None:
    team_references = [
        path for path in _REFERENCES if "/team-" in str(path)
    ]
    for path in team_references:
        content = _read(path)
        assert "## Task/Goal" in content, f"missing Task/Goal section in {path}"
        assert "## Avoid" in content, f"missing Avoid section in {path}"
        assert "## Workflow" in content, f"missing Workflow section in {path}"
        assert "## Expected Outcome" in content, f"missing Expected Outcome section in {path}"


def test_root_planner_playbook_is_self_contained() -> None:
    root_dir = _CONTENT / "team-root-planner-playbook"
    root = _read(root_dir / "SKILL.md")

    assert not list((root_dir / "references").glob("*.md"))
    workflow = root.index("## Workflow")
    terminal_contract = root.index("## Terminal Tool Contract")
    assert workflow < terminal_contract
    assert "flowchart TD" in root
    assert "1. Analyze the task" in root
    assert "2. Launch scouts" in root
    assert "3. Synthesize results" in root
    assert "4. Draft plan and submit" in root
    assert "| Stage | Goal | Tools | Exit condition |" not in root
    assert "Goal: classify intent and produce an owner ledger." in root
    assert "Tools:" in root
    assert "Steps:" in root
    assert "Never:" in root
    assert "check_background_progress" in root
    assert "wait_for_background_task" in root
    assert "cancel_background_task" in root
    assert "halted, blocked, or clearly not producing useful output" in root
    assert "carry any canceled/missing note into synthesis as uncertainty" in root
    assert "Do:" not in root
    assert "Do not:" not in root
    assert "Relaunch scouts just to improve weak notes" in root
    assert "Root planner entry has no parent" not in root
    assert "`new_tasks` is a JSON list" in root
    assert "JSON list of task ids that must finish first" in root
    assert "Non-empty JSON list of repo-relative production paths" in root
    assert "submit_plan({ new_tasks: NewTaskSpec[] })" in root
    task_object_contract = root.split("Task object:", 1)[1].split("`new_tasks`", 1)[0]
    assert 'name: "developer" | "validator" | "team_planner";' in task_object_contract
    assert '"scout"' not in task_object_contract
    assert '"team_replanner"' not in task_object_contract
    assert "Never put `scout` or `team_replanner` in `new_tasks`" in root
    assert '"deps": [' in root
    assert '"scope_paths": [' in root
    assert "load_skill_reference" not in root


def test_team_planner_uses_root_terminal_contract_without_hard_rules() -> None:
    root = _read(_CONTENT / "team-root-planner-playbook/SKILL.md")
    planner = _read(_CONTENT / "team-planner-playbook/SKILL.md")

    marker = "## Terminal Tool Contract"
    assert planner.split(marker, 1)[1] == root.split(marker, 1)[1]
    assert "## Hard rules" not in planner


def test_team_playbooks_load_references_for_detail_and_keep_top_level_generic() -> None:
    planner = _read(_CONTENT / "team-planner-playbook/SKILL.md")
    developer = _read(_CONTENT / "team-developer-playbook/SKILL.md")
    validator = _read(_CONTENT / "team-validator-playbook/SKILL.md")
    replanner = _read(_CONTENT / "team-replanner-playbook/SKILL.md")
    scout = _read(_CONTENT / "team-scout-playbook/SKILL.md")

    assert "load_skill_reference" not in planner
    assert "1. Load task context" in planner
    assert "2. Launch scouts" in planner
    assert "3. Synthesize results" in planner
    assert "4. Draft plan and submit" in planner
    assert "read_task_details(task_id=<task id>)" in planner
    assert "read_task_details(task_id=<parent task id>)" in planner
    assert "read_task_details(task_id=<dep id>)" in planner
    assert "Call `read_task_graph()` to inspect dependency topology" in planner
    assert "do not read sibling details from graph output" in planner
    assert "read_task_details(task_id=<sibling id>)" not in planner
    assert "check_background_progress -> wait_for_background_task -> read_file_note" in planner
    assert "Make the validator depend on every same-payload non-validator id" in planner
    assert "avoid future child ids in this root payload" in planner
    assert "Use `deps` only for valid same-payload ids" in planner
    assert "child `team_planner` lane" in planner
    assert "exactly one terminal `validator`" in planner
    assert "non-empty production `scope_paths`" in planner
    assert "Submit a child `team_planner` together with its imagined child tasks" in planner
    assert 'read_file_note(file_path="...")` for every exact launched target path' in planner
    assert "Relaunch scouts to repair weak notes" in planner
    assert "Scrub `target_paths`" in planner
    assert "benchmark tests, failing ids, missing test-derived paths" in planner
    assert "live production file/directory" in planner
    assert "not `target_paths`" in planner
    assert "terminal validator whose `deps` include every other same-payload id" in planner
    assert "including child planner ids" in planner
    assert "known same-file edit ordering" in planner
    assert "Use repo-relative production `scope_paths`" in planner
    assert "never submit `/testbed/...` paths" in planner
    assert "verification commands in `spec`, not `scope_paths`" in planner
    assert "missing test-derived paths" in planner
    assert "disproved by live evidence" in planner
    assert "Every task has only the six allowed fields" in planner
    assert "Every id is unique" in planner
    assert "the final assistant action is the `submit_plan(...)` tool call" in planner.lower()
    assert "nearest stable production boundary" in planner
    assert "Bundle unrelated exact files" in planner
    assert "cancel_background_task" in planner
    assert "halted, blocked, or not producing useful output" in planner
    assert "carry the missing evidence as uncertainty" in planner
    assert "compat/re-export" not in planner
    assert "utils_dataframe.py" not in planner

    assert "## Workflow" in developer
    assert "flowchart TD" in developer
    assert "1. Read task details" in developer
    assert "2. Analyze" in developer
    assert "3. Start implementation" in developer
    assert "4. Verify" in developer
    assert "5. Submit terminal summary" in developer
    assert "Root cause analysis" in developer
    assert "submit_task_summary(type='success')" in developer
    assert "submit_task_summary(type='request_replan')" in developer
    assert "Tools:" in developer
    assert "Steps:" in developer
    assert "Never:" in developer
    assert "Exit when:" in developer
    assert "submit_task_summary({" in developer
    assert 'type: "success" | "request_replan"' in developer
    assert 'reference_name="root-cause-debugging"' in developer
    assert 'reference_name="widening-and-runtime"' in developer
    assert 'reference_name="codeact-runtime-examples"' in developer
    assert 'reference_name="pre-completion-validation"' in developer
    assert (
        'load_skill_reference(skill_name="team-developer-playbook", '
        'reference_name="codeact-runtime-examples")'
    ) in developer
    assert "Context-read pre-step: after loading the developer playbook" in developer
    assert "if no dependency task ids are listed, read only your task and parent" in developer
    assert "Benchmark CodeAct preflight: before any `daytona_codeact(...)` call" in developer
    assert "If that reference has not loaded in this agent run, do not call CodeAct" in developer
    assert "A success summary may cite only commands actually run after the final edit" in developer
    assert "treat `daytona_read_file(...)` as a narrow fallback" in developer
    assert "first assistant action must be exactly one `load_skill(skill_name=\"team-developer-playbook\")` call" in developer
    assert "The next Task Center calls must be `read_task_details(task_id=\"<header uuid>\")`" in developer
    assert "No CodeAct, CI, note, file, edit, diagnostic, reference, slug, prefix, or fabricated id" in developer
    assert "Empty note reads are successful freshness checks" in developer
    assert "Use only prefixed Daytona mutation tools" in developer
    assert "Do not use generic file tools or bypass failed coordinated tools" in developer
    assert "Test files are read-only unless explicitly owned" in developer
    assert "Benchmark and verification tests are read-only evidence unless the task explicitly owns a test-only bug" in developer
    assert "Add production helpers solely for tests" in developer
    assert "Audit the task objective for test-derived production surface requests" in developer
    assert "only benchmark/verification tests are named as consumers" in developer
    assert "submit `type=\"request_replan\"` immediately" in developer
    assert "infer live production ownership evidence from task prose or benchmark imports alone" in developer
    assert "widen only with live production ownership evidence" in developer
    assert 'daytona_codeact(command="python -m pytest ...")' in developer
    assert "leading repo-root `cd`, pipes, redirects, `2>&1`, or stderr suppression" in developer
    assert "never destructive git cleanup" in developer.lower()
    assert "never retry a failed `daytona_delete_file` or `daytona_move_file`" in developer.lower()
    assert "Treat failing tests and pytest nodes as verification evidence first" in developer
    assert "Stop after repeated scope-mismatch warnings" in developer
    assert "scope_expansion" in developer
    assert "wrong_owner_or_role" in developer
    assert "investigation_blocker" in developer
    assert "verification_failure" in developer
    assert "too_complex_or_out_of_scope" in developer
    assert "budget exhaustion" in developer
    assert "latest required post-edit command exited `0`" in developer
    assert "End the lane with exactly one `submit_task_summary(...)`" in developer
    assert "must carry (a) the concrete change" in developer
    assert "for `type=\"request_replan\"`, the replan trigger classification" in developer
    assert "uid 0 bypassing" not in developer.lower()
    assert "pkg._compatibility" not in developer

    assert "must load `cross-surface-guardrails`" in validator.lower()
    assert "must load `runtime-verification-examples`" in validator.lower()
    assert (
        'load_skill_reference(skill_name="team-validator-playbook", '
        'reference_name="runtime-verification-examples")'
    ) in validator
    assert "CodeAct preflight is mandatory" in validator
    assert "Only `load_skill(team-validator-playbook)` may precede the assigned-task-id detail pre-step" in validator
    assert "read your own task, parent, and every dependency id with `read_task_details(task_id=\"<header uuid>\")`" in validator
    assert "no CodeAct, CI, note, file, edit, diagnostic, or reference tool may run first" in validator
    assert "Preserve exact failing ids, exit codes, snippets" in validator
    assert "A validator may patch only an obvious, small, local issue" in validator
    assert "Do not use it for file reads, corrective writes, moves, source introspection" in validator
    assert "Tests are read-only unless explicitly test-owned" in validator
    assert "use `request_replan` for any nonzero, partial, invalid, or unmet result" in validator
    assert "repeated repair attempts" in validator.lower()
    assert 'daytona_codeact(command="python -m pytest ...")' in validator
    assert "If the command contains `|` or `>`, do not call CodeAct" in validator
    assert "remove shell pipes/redirections" in validator
    assert "Do not launch duplicate equivalent verification commands in parallel" in validator
    assert "latest required command after any validator fix" in validator

    assert "must load `action-add-tasks`" in replanner.lower()
    assert "must load `action-cancel-and-redraft`" in replanner.lower()
    assert "Must load `scout-launch-contract`" in replanner
    assert 'read_task_details(task_id="<failed_task>")' in replanner
    assert "for every declared dep you may preserve, cancel, or rewire" in replanner
    assert "`read_task_graph()` alone is not enough" in replanner
    assert "Final action ordering" in replanner
    assert "edits were unfinished" in replanner
    assert "never spawn a same-owner continuation developer" in replanner
    assert "invalid same-scope continuation" in replanner
    assert "Never bundle independent same-parent sibling failures" in replanner
    assert "uncancelled sibling scope" in replanner
    assert "do not put uncancelled sibling paths in `new_tasks[*].scope_paths`" in replanner
    assert "same-parent pending dependents rewired to this replanner as expected recovery gating" in replanner
    assert "Preserve already-rewired downstream validators/dependents" in replanner
    assert "never duplicate them" in replanner
    assert "test-derived helpers" in replanner
    assert "Merge same-file corrective seams into one developer task" in replanner
    assert "Split them into parallel developer tasks only when the packet proves disjoint edit regions" in replanner
    assert "Keep corrective `scope_paths` repo-relative and out of benchmark/verification tests" in replanner
    assert "Missing modules, shims, bridges, re-exports, moves, and public APIs need production ownership evidence" in replanner
    assert "never raw writes, shell moves, CodeAct bypasses, or fake authorization" in replanner
    assert "coordinated tool failures may request one coordinated retry" in replanner
    assert "Reopen benchmark bodies only for bounded read-only clarification" in replanner
    assert "only test-derived missing paths remain with no production owner" in replanner
    assert 'submit_replan(new_tasks=[], cancel_ids=[])' in replanner
    assert "The system generates the outcome summary automatically" in replanner
    assert "call tools after rejection" in replanner
    assert "submit `/testbed/...` paths or wrapper commands" in replanner

    assert "must load `completion-contract`" in scout.lower()
    assert "must not edit files" in scout.lower()
    assert "must keep missing targets missing" in scout.lower()
    assert "benchmark tests read-only evidence" in scout
    assert "May inspect bounded benchmark test snippets" in scout
    assert "do not locate, correct, or modify the test path" in scout
    assert "no-symbol exact file should not be used as `scope_paths`" in scout
    assert "unconfirmed adjacent evidence" in scout.lower()
    assert "must call exactly one `submit_file_note(...)`" in scout.lower()
    assert "never use final prose instead of `submit_file_note(...)`" in scout.lower()
    assert "must not end with only visible findings" in scout.lower()
    assert "read_task_details" not in scout
    assert "read_task_graph" not in scout


def test_reference_files_hold_specialized_detail() -> None:
    developer_runtime = _read(
        _CONTENT / "team-developer-playbook/references/codeact-runtime-examples.md"
    )
    developer_playbook = _read(_CONTENT / "team-developer-playbook/SKILL.md")
    developer_root_cause = _read(
        _CONTENT / "team-developer-playbook/references/root-cause-debugging.md"
    )
    developer_widening = _read(
        _CONTENT / "team-developer-playbook/references/widening-and-runtime.md"
    )
    scout_ref = _read(_CONTENT / "team-scout-playbook/references/completion-contract.md")
    validator_ref = _read(
        _CONTENT / "team-validator-playbook/references/runtime-verification-examples.md"
    )

    assert 'daytona_codeact(command="...", timeout=N)' in developer_runtime
    assert "required benchmark-lane preflight" in developer_runtime
    assert 'reference_name="codeact-runtime-examples"' in developer_runtime
    assert "not a shell-output wrapper" in developer_runtime
    assert 'daytona_codeact(command="python -m pytest dask/tests/test_cli.py -v 2>&1 | tail -60")' in developer_runtime
    assert "If it contains the literal character `|` or `>`" in developer_runtime
    assert "Rewrite any planned command containing `2>&1`" in developer_runtime
    assert "commands actually run after the final edit" in developer_runtime
    assert "Must not append shell capture plumbing" in developer_runtime
    assert "If you think you need `head` or `tail`, the preflight is not complete" in developer_runtime
    assert "Must not write or move files through CodeAct" in developer_runtime
    assert "Pure removals such as `rm`, `unlink`, `os.remove`" in developer_runtime
    assert "Must not inspect source through CodeAct" in developer_runtime
    assert "Code mode is not an escape hatch" in developer_runtime
    assert "Do not import or call `subprocess`" in developer_runtime
    assert "leading repo-root `cd`, pipes, redirects, `2>&1`" in developer_playbook
    assert "cd /testbed" in developer_runtime
    assert "Use a direct repo-root `daytona_codeact(command=\"python -m pytest ...\")` shape" in developer_playbook
    assert "pkg._compat" in developer_root_cause
    assert "do not emit warnings at module import time" in developer_root_cause.lower()
    assert "missing private module, shim, re-export, or import bridge" in developer_root_cause
    assert "missing module, compatibility shim, re-export module, or import bridge" in developer_widening
    assert "required for the same bug" in developer_widening
    assert "adds the target to current `scope_paths`" in developer_widening
    assert "scope_paths` itself names an absent module" in developer_widening
    assert "source and destination are separate ownership checks" in developer_widening
    assert "in-scope source file does not authorize an absent outside-scope destination path" in developer_widening
    assert "ModuleNotFoundError" in developer_widening
    assert "classify it before writing" in developer_widening
    assert "similar in-scope compatibility module is not provenance" in developer_widening
    assert "intended repository surface" in developer_widening.lower()
    assert "explicit widened-edit decision" in developer_widening.lower()
    assert "scope-added system notification" in developer_widening.lower()
    assert "a real production surface" in developer_widening
    assert "The Task Center note is the durable handoff." in scout_ref
    assert "Make exactly one `submit_file_note(...)` call" in scout_ref
    assert "assistant text with no `submit_file_note(...)` call" in scout_ref
    assert "the exact file should not be used as `scope_paths`" in scout_ref
    assert "target path is off-policy" in scout_ref
    assert "Use progress checks only when live output changes" in validator_ref
    assert "required benchmark-lane preflight" in validator_ref
    assert 'reference_name="runtime-verification-examples"' in validator_ref
    assert "shell-output wrappers" in validator_ref
    assert "Do not add pipes, redirects, stderr capture" in validator_ref
    assert "If it contains `|` or `>`, rewrite it to a direct repo-root command" in validator_ref
    assert "duplicate equivalent commands" in validator_ref
    assert "commands run after the final validator edit" in validator_ref
    assert "Do not inspect source" in validator_ref


def test_replanner_references_spell_valid_submit_replan_payload_shape() -> None:
    replanner = _read(_CONTENT / "team-replanner-playbook/SKILL.md")
    add_tasks = _read(_CONTENT / "team-replanner-playbook/references/action-add-tasks.md")
    cancel_redraft = _read(
        _CONTENT / "team-replanner-playbook/references/action-cancel-and-redraft.md"
    )

    assert "check `new_tasks` for real sequencing needs" in replanner
    assert "Scope overlap is allowed" in add_tasks
    assert "budget exhaustion" in add_tasks
    assert "same-scope continuation developer" in add_tasks
    assert "continues unfinished same-owner work" in add_tasks
    assert "Task Center rewired it to depend on this replanner" in add_tasks
    assert "Do not add a duplicate local dev->validator chain" in add_tasks
    assert "preserved downstream validator already covers the surface" in add_tasks
    assert "independent same-parent sibling failures" in add_tasks
    assert "repairs a live sibling's unrelated failure" in add_tasks
    assert "production helper/API task" in add_tasks
    assert "add helper/function so the test can call it" in add_tasks
    assert "Do not split one exact owner file into parallel developer microtasks" in add_tasks
    assert "one corrective developer task with a checklist of those seams" in add_tasks
    assert "new-file, rename, move, shim, or re-export task" in add_tasks
    assert "Self-check `cancel_ids=[]`" in add_tasks
    assert "replacement" in cancel_redraft and "test-derived" in cancel_redraft
    assert "even when the source file is in scope" in add_tasks
    assert "even when the source file is in scope" in cancel_redraft
    assert "production ownership evidence or clear adjacent ownership" in add_tasks
    assert "similar in-scope compatibility filename is not an exception" in cancel_redraft
    assert "do not call CI, file, graph, note, or CodeAct tools" in add_tasks
    assert "do not call CI, file, graph, note, or CodeAct tools" in cancel_redraft
    assert "Do not add a developer task whose `scope_paths` are benchmark or verification tests" in add_tasks
    assert "not add a test-edit developer task" in add_tasks
    assert "no task scopes benchmark tests" in add_tasks
    assert "use a live production boundary or submit an empty replan" in cancel_redraft
    assert "raw-write workaround" in add_tasks
    assert "whole-file overwrite fallback instructions" in cancel_redraft
    assert "repo-relative `scope_paths` with no `/testbed/...` prefixes" in add_tasks
    assert "must not say `cd /testbed`" in add_tasks
    assert "CodeAct starts at repo root and captures output automatically" in add_tasks
    assert "Replacement `scope_paths` must be repo-relative" in cancel_redraft
    assert "uncancelled sibling's scope" in cancel_redraft
    assert "only when that sibling id is in `cancel_ids`" in cancel_redraft
    assert "must not say `cd /testbed`" in cancel_redraft
    assert "CodeAct starts at repo root and captures output automatically" in cancel_redraft

    for content in (add_tasks, cancel_redraft):
        assert "`1. Goal:`" in content
        assert "`2. Task Details:`" in content
        assert "`3. Acceptance Criteria:`" in content
        assert "`2. Environment:`" not in content
        assert "`3. Scope:`" not in content
        assert "`4. Context:`" not in content
        assert "`5. Acceptance Criteria:`" not in content
        assert "Do not use Markdown headings" in content
        removed_field = "task" + "_note"
        assert f"`{removed_field}`" not in content


def test_worker_playbooks_do_not_mention_submitters_or_action_routing() -> None:
    for path in (
        _CONTENT / "team-developer-playbook/SKILL.md",
        _CONTENT / "team-validator-playbook/SKILL.md",
    ):
        content = _read(path)
        assert "submit_summary" not in content
        assert "submit_replan" not in content
        assert "request_retry" not in content
        assert "RECOMMENDED_ACTION" not in content
