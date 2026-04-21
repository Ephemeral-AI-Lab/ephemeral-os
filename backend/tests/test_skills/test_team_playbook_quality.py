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
    _CONTENT / "team-planner-playbook/references/scout-launch-contract.md",
    _CONTENT / "team-planner-playbook/references/plan-json-contract.md",
    _CONTENT / "team-scout-playbook/references/completion-contract.md",
    _CONTENT / "team-validator-playbook/references/cross-surface-guardrails.md",
    _CONTENT / "team-validator-playbook/references/runtime-verification-examples.md",
    _CONTENT / "team-replanner-playbook/references/action-add-tasks.md",
    _CONTENT / "team-replanner-playbook/references/action-cancel-and-redraft.md",
    _CONTENT / "verification-replan/references/triage-format.md",
]
_REFERENCES = [path for path in _REFERENCES if path.exists()]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _hard_rules_section(content: str) -> str:
    after_header = content.split("## Hard rules", 1)[1]
    return re.split(r"\n## ", after_header, maxsplit=1)[0]


def test_skills_and_references_stay_short() -> None:
    for path in _ALL_SKILLS:
        assert len(_read(path).splitlines()) <= 150, f"{path} should stay short"
    for path in _REFERENCES:
        assert len(_read(path).splitlines()) <= 150, f"{path} should stay short"


def test_hard_rule_numbers_do_not_repeat() -> None:
    for path in _PLAYBOOKS:
        section = _hard_rules_section(_read(path))
        labels = re.findall(r"^(\d+)\.\s", section, flags=re.MULTILINE)
        assert labels, f"expected numbered hard rules in {path}"
        assert labels == [str(i) for i in range(1, len(labels) + 1)], f"bad numbering in {path}"


def test_skills_use_clear_must_never_language() -> None:
    for path in _ALL_SKILLS:
        content = _read(path)
        assert (
            "Must " in content
            or "Must\n" in content
            or "Must use" in content
            or "Must treat" in content
        )
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


def test_team_playbooks_load_references_for_detail_and_keep_top_level_generic() -> None:
    planner = _read(_CONTENT / "team-planner-playbook/SKILL.md")
    developer = _read(_CONTENT / "team-developer-playbook/SKILL.md")
    validator = _read(_CONTENT / "team-validator-playbook/SKILL.md")
    replanner = _read(_CONTENT / "team-replanner-playbook/SKILL.md")
    scout = _read(_CONTENT / "team-scout-playbook/SKILL.md")

    assert "must load `scout-launch-contract`" in planner.lower()
    assert "must load `plan-json-contract`" in planner.lower()
    assert "Do not pre-load it during setup" in planner
    assert "submit_plan` tool schema is enough" in planner
    assert "top-level `deps` field lists every same-layer non-validator sibling id" in planner
    assert "child `team_planner` decomposition lanes" in planner
    assert "prose inside `spec` does not create task dependencies" in planner
    assert "run_subagent scout notes are current-task notes" in planner
    assert 'read them via `read_task_details(task_id="<your current task id>")`' in planner
    assert "Never pass `bg_*` background ids to `read_task_details`" in planner
    assert "submit with uncertainty instead of relaunching explorers" in planner
    assert "scrub each scout `target_paths` list before calling `run_subagent`" in planner
    assert "live production owner files/directories only" in planner
    assert "never launch `run_subagent` scouts on benchmark test paths" in planner.lower()
    assert "use scouts to locate or correct benchmark test paths" in planner.lower()
    assert "scout the production owner path instead" in planner
    assert "never submit a `validator` task with `deps: []`" in planner.lower()
    assert "never omit same-layer `team_planner` siblings from validator `deps`" in planner.lower()
    assert "must not add dependencies merely because `scope_paths` overlap" in planner.lower()
    assert "known edit-order dependency" in planner
    assert "do not put those paths in `scope_paths` for developer, validator, or child-planner lanes" in planner
    assert "scope_paths` to production owner paths" in planner
    assert "never put verification-only benchmark tests in developer, validator, or child-planner `scope_paths`" in planner.lower()
    assert "never pass `*/tests/*`, `test_*.py`, or unconfirmed test-derived paths in scout `target_paths`" in planner.lower()
    assert "locate/correct benchmark test paths" in planner
    assert "never guess an exact owner" in planner.lower()
    assert "never make non-submission tool calls after loading `plan-json-contract`" in planner.lower()
    assert "scope_paths` broad enough for the likely production edit set" in planner
    assert "exact new path plus its adjacent live owner" in planner
    assert "no indexed symbols for that file" in planner
    assert "Do not keep the exact file in scout `target_paths` or any `scope_paths`" in planner
    assert "Never carry a disproved exact file into `scope_paths`" in planner
    assert "read the posted Task Center notes instead of checking or waiting on that id again" in planner
    assert "Never call `check_background_progress(...)` or `wait_for_background_task(...)` again" in planner
    assert (
        "read them via "
        '`read_task_details(task_id="<your current task id>")`'
    ) in planner
    assert "Never use background tools to recover content from a `Posted.` scout result" in planner
    assert "clear adjacent live owner" in planner
    assert "split unrelated scout targets" in planner.lower()
    assert "compat/re-export" not in planner
    assert "utils_dataframe.py" not in planner

    assert "must load `root-cause-debugging`" in developer.lower()
    assert "must load `widening-and-runtime`" in developer.lower()
    assert "must load `codeact-runtime-examples`" in developer.lower()
    assert "must load `pre-completion-validation`" in developer.lower()
    assert "before any `daytona_read_file(...)`" in developer
    assert "Empty note reads are successful freshness checks." in developer
    assert "never rewrite benchmark tests" in developer.lower()
    assert "must not use `daytona_codeact` for file writes or moves" in developer.lower()
    assert "pure removals such as `rm`, `unlink`, `os.remove`" in developer.lower()
    assert "must not use `daytona_codeact` for file-content reads" in developer.lower()
    assert "writes to test files as off-policy" in developer.lower()
    assert "test files in `scope_paths` as read/verify-only" in developer.lower()
    assert "may create or edit an outside-`scope_paths` production path" in developer.lower()
    assert "adds the target to the lane's current scope" in developer
    assert "system notification listing the updated `scope_paths`" in developer
    assert "must not create a new file from test-import evidence alone" in developer.lower()
    assert "absent module, shim, re-export module, or import bridge" in developer
    assert "compatibility shim, or re-export bridge" in developer.lower()
    assert "coordination decision point" in developer
    assert "ModuleNotFoundError" in developer
    assert "live production evidence" in developer
    assert "assigned objective" in developer
    assert "ci_query_symbol" in developer
    assert "widened-edit decision" in developer
    assert "check both source and destination" in developer.lower()
    assert "in-scope source path is not permission" in developer.lower()
    assert "Never keep widening after repeated outside-scope warnings" in developer
    assert "Never treat a similar in-scope compatibility module" in developer
    assert "must not retry the same delete/move tool" in developer.lower()
    assert "test-source archaeology" in developer
    assert "Never retry a failed `daytona_delete_file` or `daytona_move_file` call" in developer
    assert "May read bounded benchmark or verification test snippets" in developer
    assert "Never use git history, speculative test-source archaeology, or another search" in developer
    assert "current lane collect" in developer.lower()
    assert "production ownership evidence" in developer.lower()
    assert "widened path, rationale, and verification" in developer.lower()
    assert "submit_task_summary(type=\"request_replan\", content=...)" in developer
    assert "never treat test paths in `scope_paths` as edit permission" in developer.lower()
    assert "never create an outside-scope compatibility shim" in developer.lower()
    assert "uid 0 bypassing" not in developer.lower()
    assert "pkg._compatibility" not in developer

    assert "must load `cross-surface-guardrails`" in validator.lower()
    assert "must load `runtime-verification-examples`" in validator.lower()
    assert "before any `daytona_read_file(...)`" in validator
    assert "must not paraphrase failure evidence" in validator.lower()
    assert "small local corrective patch" in validator.lower()
    assert "must not use `daytona_codeact` for corrective writes or moves" in validator.lower()
    assert "pure removals such as `rm`, `unlink`, `os.remove`" in validator.lower()
    assert "must not use `daytona_codeact` for file-content reads" in validator.lower()
    assert "May read bounded benchmark or verification test snippets" in validator
    assert "writes to test files as off-policy" in validator.lower()
    assert 'submit_task_summary(type="request_replan", content=...)' in validator
    assert "repeated repair attempts" in validator.lower()

    assert "must load `action-add-tasks`" in replanner.lower()
    assert "must load `action-cancel-and-redraft`" in replanner.lower()
    assert 'read_task_details(task_id="<failed_task>")' in replanner
    assert 'read_task_details(task_id="<dependent_task>")' in replanner
    assert "for every dependent you may preserve, cancel, or rewire" in replanner
    assert "`read_task_graph()` alone is not enough" in replanner
    assert "final-action ordering" in replanner.lower()
    assert "scope-quality evidence" in replanner
    assert "production ownership evidence or clear adjacent ownership" in replanner
    assert "check both source and destination" in replanner.lower()
    assert "in-scope source compatibility file is not permission" in replanner.lower()
    assert "destination must be justified as a production owner" in replanner
    assert "tests out of corrective `scope_paths`" in replanner
    assert "looks wrong is evidence, not permission" in replanner
    assert "May read bounded benchmark test snippets" in replanner
    assert "inspect git history" in replanner
    assert "outside-scope missing-module request" in replanner
    assert "benchmark test import as evidence" in replanner
    assert 'submit_replan(new_tasks=[], cancel_ids=[], summary="...")' in replanner
    assert "`summary` preserves the failure evidence" in replanner
    assert "do not call CI, file, graph, note, or CodeAct tools afterward" in replanner
    assert "Must not convert a coordinated write-tool failure into instructions to bypass coordination" in replanner
    assert "standard Python file I/O" in replanner
    assert "Never submit a corrective task with `*/tests/*`" in replanner

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


def test_reference_files_hold_specialized_detail() -> None:
    planner_json = _read(_CONTENT / "team-planner-playbook/references/plan-json-contract.md")
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

    assert "optional final helper" in planner_json
    assert "do not load it until exploration and DAG shaping are complete" in planner_json
    assert "submit_plan(new_tasks=[...])" in planner_json
    assert "Do not include `task_note`" in planner_json
    assert "`1. Goal:`" in planner_json
    assert "Do not use Markdown headings" in planner_json
    assert "Mentioning dependencies inside `spec` does not set task deps" in planner_json
    assert "verification-only test targets in `spec` context or acceptance criteria" in planner_json
    assert "Missing modules, compatibility shims, re-export modules, and import bridges named by tests" in planner_json
    assert "workspace structure shows a directory or nested files" in planner_json
    assert "child planners like `plan-parquet` or `plan-groupby`" in planner_json
    assert "Scope overlap is allowed" in planner_json
    assert "Never submit a validator with `deps: []`" in planner_json
    scout_launch = _read(
        _CONTENT / "team-planner-playbook/references/scout-launch-contract.md"
    )
    assert "scout notes live on the current task, not on siblings" in scout_launch
    assert 'read_task_details(task_id="<your current task id>")' in scout_launch
    assert "Do not pass `bg_*` background ids to `read_task_details`" in scout_launch
    assert "Do not launch a second scout wave" in scout_launch
    assert "Scrub `target_paths` first" in scout_launch
    assert "missing test-derived path in scout `target_paths`" in scout_launch
    assert "Never use a scout to locate or correct a benchmark test path mismatch" in scout_launch
    assert "do not use scouts to repair benchmark test paths" in scout_launch
    assert "Never pass an exact file to a scout after a file-symbol query found no indexed symbols" in scout_launch
    assert "Never use scouts to locate or correct benchmark test path mismatches" in scout_launch
    assert 'daytona_codeact(command="...", timeout=N)' in developer_runtime
    assert "Must not append shell capture plumbing" in developer_runtime
    assert "Must not write or move files through CodeAct" in developer_runtime
    assert "Pure removals such as `rm`, `unlink`, `os.remove`" in developer_runtime
    assert "Must not inspect source through CodeAct" in developer_runtime
    assert "cd /testbed" in developer_playbook
    assert "cd /testbed" in developer_runtime
    assert "pkg._compat" in developer_root_cause
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
    assert "check_background_progress" in validator_ref
    assert "Must not inspect source through CodeAct" in validator_ref


def test_replanner_references_spell_valid_submit_replan_payload_shape() -> None:
    replanner = _read(_CONTENT / "team-replanner-playbook/SKILL.md")
    add_tasks = _read(_CONTENT / "team-replanner-playbook/references/action-add-tasks.md")
    cancel_redraft = _read(
        _CONTENT / "team-replanner-playbook/references/action-cancel-and-redraft.md"
    )

    assert "check `new_tasks` for real sequencing needs" in replanner
    assert "Scope overlap is allowed" in add_tasks
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
    assert "instead of a test-edit developer task" in cancel_redraft
    assert "raw-write workaround" in add_tasks
    assert "whole-file overwrite fallback instructions" in cancel_redraft

    for content in (add_tasks, cancel_redraft):
        assert "`1. Goal:`" in content
        assert "`2. Environment:`" in content
        assert "`3. Scope:`" in content
        assert "`4. Context:`" in content
        assert "`5. Acceptance Criteria:`" in content
        assert "Do not use Markdown headings" in content
        assert "Do not include `task_note`" in content


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
