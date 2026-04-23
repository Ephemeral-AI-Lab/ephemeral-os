"""Tests for skill loading."""

from __future__ import annotations

from pathlib import Path

from skills import get_user_skills_dir, load_skill_registry

_BUNDLED_SKILLS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "skills" / "bundled" / "content"
)


def test_load_skill_registry_includes_bundled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EPHEMERALOS_CONFIG_DIR", str(tmp_path / "config"))
    registry = load_skill_registry()

    names = [skill.name for skill in registry.list_skills()]
    assert "team-planner-playbook" in names
    assert "team-replanner-playbook" in names
    assert "team-developer-playbook" in names


def test_load_skill_registry_includes_user_skills(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EPHEMERALOS_CONFIG_DIR", str(tmp_path / "config"))
    skills_dir = get_user_skills_dir()
    (skills_dir / "deploy.md").write_text("# Deploy\nDeployment workflow guidance\n", encoding="utf-8")

    registry = load_skill_registry()
    deploy = registry.get("Deploy")

    assert deploy is not None
    assert deploy.source == "user"
    assert "Deployment workflow guidance" in deploy.content


def test_team_replanner_playbook_uses_planner_style_contract() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-replanner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    contract = (
        _BUNDLED_SKILLS_DIR
        / "team-replanner-playbook"
        / "references"
        / "terminal-contract.md"
    ).read_text(encoding="utf-8")
    prompt = (
        _BUNDLED_SKILLS_DIR.parents[2]
        / "prompt"
        / "user_prompt"
        / "task_replanner.md"
    ).read_text(encoding="utf-8")
    action_add = (
        _BUNDLED_SKILLS_DIR
        / "team-replanner-playbook"
        / "references"
        / "action-add-tasks.md"
    ).read_text(encoding="utf-8")
    action_cancel = (
        _BUNDLED_SKILLS_DIR
        / "team-replanner-playbook"
        / "references"
        / "action-cancel-and-redraft.md"
    ).read_text(encoding="utf-8")
    reference_names = {
        path.name
        for path in (_BUNDLED_SKILLS_DIR / "team-replanner-playbook" / "references").glob("*.md")
    }

    assert reference_names == {
        "action-add-tasks.md",
        "action-cancel-and-redraft.md",
        "terminal-contract.md",
    }
    assert len(skill.splitlines()) <= 170
    assert len(action_add.splitlines()) <= 50
    assert len(action_cancel.splitlines()) <= 45
    assert len(contract.splitlines()) <= 160
    assert "## Workflow" in skill
    assert "```mermaid" in skill
    assert "Reference Map" in skill
    assert "terminal-contract" in skill
    assert "Every branch must load the matching action reference and then `terminal-contract`" in skill
    assert "Classify Failure Mode" in skill
    assert "Direct replan" in skill
    assert "Diagnostics" in skill
    assert "Synthesize repair mapping" in skill
    assert "check it against every observed value in the same failing assertion" in skill
    assert "compact value table" in skill
    assert "copying the handoff into a repair task" in skill
    assert "trace-gap triplets" in skill
    assert "Launch one scout per remaining triplet" in skill
    assert "Wait for all required `read_task_details` results before calling `read_task_graph()`" in skill
    assert "Do not batch `read_task_graph()` with any required task-detail read" in skill
    assert "Classification: <scope_expansion|wrong_owner_or_role|unresolved_blocker>" in skill
    assert "Diagnostics decision: trivial_direct_replan" in skill
    assert "Diagnostics decision: deep_diagnostics" in skill
    assert "it is never stale sibling work and must stay out of `cancel_ids`" in skill
    assert "If your draft `cancel_ids` contains the failed task id from the prompt" in skill
    assert "If the fix target remains under any failed-task `scope_paths` entry" in skill
    assert "A failed task's \"test design issue\" label does not drop a named fail-to-pass variant" in skill
    assert "Enumerate distinct trace-gap triplets in visible reasoning before any scout call" in skill
    assert '"target_paths": ["<one production path>"]' in skill
    assert "Keep failing tests in scout `context`, not `target_paths`" in skill
    assert "Replanner-created tasks are limited to `developer` repair lanes and `validator` verification lanes" in skill
    assert "Do not submit an empty or no-op replan" in skill

    assert "## Call Shape" in contract
    assert "submit_replan({ new_tasks: NewTaskSpec[], cancel_ids: string[] })" in contract
    assert "Top-level input has only required `new_tasks` and required `cancel_ids`" in contract
    assert "include `cancel_ids: []` when no cancellation is needed" in contract
    assert "Compare every `cancel_ids` entry against the failed task id from the prompt" in contract
    assert "Same-parent graph position does not make the failed task cancellable" in contract
    assert "No `cancel_ids` entry equals the failed task id from the prompt" in contract
    assert "Same-owner-file repairs are not scope expansion" in contract
    assert "`new_tasks` contains at least one corrective task" in contract
    assert "Empty Replan" not in contract
    assert '"new_tasks": []' not in contract
    assert 'name: "developer" | "validator";' in contract
    assert "Every `name` is exactly `developer` or `validator`" in contract
    assert "team_planner is accepted" not in contract
    assert "## Examples" in contract
    assert "## Final Checklist" in contract
    assert (
        "If `Task Details` uses `Classification: unresolved_blocker`, it must also include the exact field `Diagnostics decision: trivial_direct_replan` or `Diagnostics decision: deep_diagnostics`"
        in contract
    )
    assert (
        "Every spec with `Classification: unresolved_blocker` also includes `Diagnostics decision: trivial_direct_replan` or `Diagnostics decision: deep_diagnostics` inside `2. Task Details:`."
        in contract
    )
    assert (
        "Acceptance criteria must not use `-k`, parametrization filters, or prose like \"do not treat this as a repair target\" to avoid a named failing fail-to-pass variant"
        in contract
    )
    assert (
        "No named fail-to-pass variant is dropped as a test design issue, unsupported parametrization, cross-engine mismatch, or \"not a repair target\"."
        in contract
    )
    assert (
        "Do not satisfy this requirement with residual risk prose, \"out of scope\" text"
        in action_add
    )
    assert (
        "No named fail-to-pass variant appears only as residual risk, \"out of scope\", unsupported/test-design prose, broad validator coverage, or validator-only closure without an upstream repair."
        in contract
    )
    assert (
        "A rule that fixes one value while breaking another is not a direct repair"
        in contract
    )
    assert (
        "No proposed one-line rule contradicts another observed value in the same failing assertion."
        in contract
    )
    assert "Account for every named failing variant in the failed task summary." in prompt
    assert "If the failed task proposes a concrete code rule or one-line fix" in prompt
    assert "Final payload shape lives in `terminal-contract`" in action_add
    assert "Final payload shape lives in `terminal-contract`" in action_cancel
    assert "same-scope repair with a named production mechanism is valid" in action_add
    assert "Keep the required top-level `cancel_ids` key explicitly set to `[]`" in action_add
    assert (
        "Dropping a named fail-to-pass variant by labeling it a test design issue, unsupported parametrization, or cross-engine mismatch."
        in action_add
    )
    assert (
        "Keep every named failing variant assigned to a production repair, a diagnostic developer that tests a concrete production seam, or an explicitly identified live repair owner whose task details or terminal summary covers that same variant and seam."
        in action_add
    )
    assert (
        "reject a repair task whose proposed rule cannot satisfy every observed expected/actual row"
        in action_add
    )
    assert "separate verification lane" in action_cancel
    assert "local replacement ids it verifies" in action_cancel
    assert "even when `read_task_graph()` shows it as a same-parent sibling" in action_cancel
    assert "Example terminal payload" not in action_add
    assert "Example terminal payload" not in action_cancel
    assert "numbered colon labels" not in action_add
    assert "numbered colon labels" not in action_cancel
    assert "loaded the action reference matching your cancellation decision" in prompt
    assert "if a validation error still rejects `cancel_ids`" in prompt

    assert "2. Task Details:" in skill
    assert "2. Task Details:" in contract
    assert "2. Task Detail:" not in skill
    assert "2. Task Detail:" not in contract
    assert "Valid replan trigger" not in skill
    assert "Replan trigger gate" not in skill


def test_team_planner_playbook_uses_plural_task_details_label() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "2. Task Details:" in skill
    assert "2. Task Detail:" not in skill
    assert "`Task Details`" in skill
    assert "`Task Detail`" not in skill


def test_team_root_planner_playbook_uses_plural_task_details_label() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-root-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "2. Task Details:" in skill
    assert "2. Task Detail:" not in skill
    assert "`Task Details`" in skill
    assert "`Task Detail`" not in skill


def test_team_root_planner_playbook_keeps_acceptance_criteria_evidence_focused() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-root-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Put benchmark tests and verification commands in `spec`, not `scope_paths`" in skill
    assert "Acceptance Criteria` must be test-suite focused with concrete commands" in skill
    assert "Every `Acceptance Criteria` is test-suite focused" in skill
    assert (
        "No fail-to-pass acceptance criterion treats skipped tests, expected failures, clear `ImportError`, or missing optional dependencies as passing closure"
        in skill
    )
    assert "a coverage ledger of every named failing cluster or variant" in skill
    assert "no named failing cluster may appear only in a validator spec" in skill
    assert (
        "No named fail-to-pass cluster is covered only by a validator without a repair/decomposition owner"
        in skill
    )
    assert "CodeAct-safe" not in skill


def test_team_root_planner_playbook_prefers_top_down_decomposition() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-root-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "## Hierarchical Planning Principle" in skill
    assert "Team plans are hierarchical" in skill
    assert "At the root level, explore only enough to identify defensible owner families" in skill
    assert "Do not try to fully decompose every region in one root payload" in skill
    assert "The root planner's job is top-down routing, not exhaustive single-layer discovery" in skill
    assert "Clear owner names do not automatically mean direct developer lanes are best" in skill
    assert "large benchmark/test-matrix work" in skill
    assert "Clustering-job checkpoint" in skill
    assert "include at least one child `team_planner` in the root payload" in skill
    assert "flat all-developer root fan-out" in skill
    assert "A clustering root payload with four or more independent developer lanes and no child `team_planner` is invalid" in skill
    assert "even when scouts named plausible owners or files" in skill
    assert "Do not flatten those families into sibling root developers just because owner files are known" in skill
    assert "Tasks submitted in your plan run at `current_depth + 1`" in skill
    assert "When `current_depth + 2 <= max_depth`" in skill
    assert "When `current_depth + 2 > max_depth`" in skill
    assert "emit direct `developer` and `validator` tasks with broader scopes instead" in skill
    assert (
        "Use a child `team_planner` lane for broad, shared, unresolved, multi-family, clustered, or large benchmark/test-matrix work instead of forcing exhaustive root-layer exploration"
        in skill
    )
    assert "route the uncertainty to a child `team_planner`" in skill


def test_team_planner_playbook_prefers_recursive_decomposition() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "## Hierarchical Planning Principle" in skill
    assert "Team plans are hierarchical" in skill
    assert "Explore only enough at your current layer to separate exact owner work" in skill
    assert "Do not try to fully decompose every descendant task in one payload" in skill
    assert "Your job is top-down routing for this layer, not exhaustive single-layer discovery" in skill
    assert "Clear owner names do not automatically mean direct developer lanes are best" in skill
    assert "large benchmark/test-matrix work" in skill
    assert "Clustering-job checkpoint" in skill
    assert "include at least one child `team_planner` in this payload" in skill
    assert "flat all-developer fan-out" in skill
    assert "A clustering payload with four or more independent developer lanes and no child `team_planner` is invalid" in skill
    assert "even when scouts named plausible owners or files" in skill
    assert "Do not flatten those families into sibling developers at the current layer just because owner files are known" in skill
    assert "Tasks submitted in your plan run at `current_depth + 1`" in skill
    assert "When `current_depth + 2 <= max_depth`" in skill
    assert "When `current_depth + 2 > max_depth`" in skill
    assert "emit direct `developer` and `validator` tasks with broader scopes instead" in skill
    assert (
        "Use another child `team_planner` lane for broad, shared, unresolved, multi-family, clustered, or large benchmark/test-matrix work instead of forcing exhaustive current-layer exploration"
        in skill
    )
    assert "route the uncertainty to another child `team_planner`" in skill


def test_team_planner_playbook_requires_fail_to_pass_coverage_owners() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "a coverage ledger of every named failing cluster or variant" in skill
    assert "a terminal validator is not an owner for otherwise unassigned failures" in skill
    assert "no named failing cluster may appear only in a validator spec" in skill
    assert (
        "No named fail-to-pass cluster is covered only by a validator without a repair/decomposition owner"
        in skill
    )


def test_team_developer_playbook_requires_exact_in_scope_fix_before_replan() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-developer-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert (
        "Do not use `request_replan` as a handoff for exact code you already know how to change"
        in skill
    )
    assert "assigned-scope or adjacent production-path actionable code defect" in skill


def test_planner_and_scout_playbooks_keep_benchmark_tests_as_evidence() -> None:
    planner_skill = (
        _BUNDLED_SKILLS_DIR / "team-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    root_planner_skill = (
        _BUNDLED_SKILLS_DIR / "team-root-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    scout_skill = (
        _BUNDLED_SKILLS_DIR / "team-scout-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    scout_contract = (
        _BUNDLED_SKILLS_DIR
        / "team-scout-playbook"
        / "references"
        / "completion-contract.md"
    ).read_text(encoding="utf-8")

    for skill in (planner_skill, root_planner_skill):
        assert (
            "If any candidate target matches `*/tests/*`, `test_*.py`, a benchmark harness, or a verification-only path, do not launch"
            in skill
        )
        assert "optional-dependency errors" in skill
        assert (
            "No fail-to-pass acceptance criterion treats skipped tests, expected failures, clear `ImportError`, or missing optional dependencies as passing closure"
            in skill
        )
        assert (
            "Treat any scout conclusion that names benchmark tests, skips, xfails, rewrites, pytest configuration, or benchmark harness edits as evidence only"
            in skill
        )
        assert (
            "Never write a developer goal or task details that instruct the child to edit, skip, xfail, rewrite, or reconfigure benchmark tests"
            in skill
        )

    for skill in (scout_skill, scout_contract):
        assert "Never prescribe" in skill or "Must not recommend" in skill
        assert "skipping" in skill
        assert "xfail" in skill
        assert "pytest configuration" in skill


def test_team_validator_playbook_uses_developer_style_contract() -> None:
    validator_dir = _BUNDLED_SKILLS_DIR / "team-validator-playbook"
    skill = (validator_dir / "SKILL.md").read_text(encoding="utf-8")
    reference_files = list((validator_dir / "references").glob("*.md"))

    assert "## Route" in skill
    assert "```mermaid" in skill
    assert "## 1. Read task details" in skill
    assert "## 2. Build validation plan" in skill
    assert "## 3. Run diagnostics and exact verification" in skill
    assert "## 6. Submit terminal summary" in skill
    assert "submit_task_summary({" in skill
    assert 'type: "success" | "request_replan"' in skill
    assert "content: string" in skill
    assert "public-surface guardrail" in skill

    assert reference_files == []
    assert "load_skill_reference" not in skill
    assert "## Conditional references" not in skill


def test_terminal_summary_playbooks_require_explicit_residual_risk() -> None:
    for playbook_name in ("team-developer-playbook", "team-validator-playbook"):
        skill = (_BUNDLED_SKILLS_DIR / playbook_name / "SKILL.md").read_text(
            encoding="utf-8"
        )

        assert "Do not omit a line because the answer is \"none\"" in skill
        assert '`Residual Risk:` with remaining risk, unverified surface, or "none"' in skill


def test_developer_playbook_rejects_success_without_runtime_verification() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-developer-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Clean diagnostics are not acceptance verification" in skill
    assert "the required runtime command was not run after the final edit" in skill
    assert "verification was not run, was skipped due to budget" in skill
    assert "pass or skip" in skill
    assert "ended in collection/import/no-tests/optional-dependency failure" in skill
    assert "supported only by diagnostics is not a success summary" in skill


def test_developer_playbook_rejects_wrapped_or_suppressed_verification() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-developer-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Do not wrap required pytest/build verification" in skill
    assert "`subprocess.run`" in skill
    assert "`PYTHONWARNINGS`" in skill
    assert "`--override-ini`" in skill
    assert "`filterwarnings=`" in skill
    assert "pytest-config-overridden" in skill
    assert "manual `print(\"EXIT CODE\")`" in skill
    assert "tool-reported command exit code" in skill
    assert "A wrapper that prints an inner exit code" in skill
    assert "keep that raw failure as evidence" in skill
    assert "was wrapped, warning-suppressed, pytest-config-overridden" in skill


def test_validator_playbook_rejects_pytest_config_override_evidence() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-validator-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Do not suppress or alter pytest configuration" in skill
    assert "`--override-ini`" in skill
    assert "`filterwarnings=`" in skill
    assert "pytest-config-overridden command" in skill


def test_developer_playbook_keeps_parametrized_f2p_variants_as_evidence() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-developer-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert (
        "Do not dismiss a fail-to-pass parametrized variant as a test design issue"
        in skill
    )
    assert "Keep it as production compatibility evidence" in skill
    assert (
        "Missing optional dependencies, older dependency versions, and unavailable engines are not final blockers"
        in skill
    )
    assert "production guard, fallback, explicit compatibility error" in skill
    assert "do not request replan just because the sandbox lacks the package or version" in skill
    assert (
        "it must not list install, dependency upgrade, skip, xfail, pytest config, test edit, or environment replacement as the path forward"
        in skill
    )


def test_terminal_summary_playbooks_use_shared_replan_taxonomy() -> None:
    allowed = {"scope_expansion", "wrong_owner_or_role", "unresolved_blocker"}
    banned = {
        "dependency_handoff_gap",
        "diagnostic_failure",
        "verification_failure",
        "invalid_command",
        "unmet_acceptance",
        "outside_scope",
        "repair_not_local",
        "investigation_blocker",
        "too_complex_or_out_of_scope",
        "`none`",
    }
    for playbook_name in ("team-developer-playbook", "team-validator-playbook"):
        skill = (_BUNDLED_SKILLS_DIR / playbook_name / "SKILL.md").read_text(
            encoding="utf-8"
        )

        for trigger in allowed:
            assert trigger in skill
        for trigger in banned:
            assert trigger not in skill


def test_developer_playbook_allows_advisory_out_of_scope_production_edits() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-developer-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "`scope_paths` are the primary ownership surface, not a hard mutation sandbox" in skill
    assert "You may widen reads, diagnostics, and test commands" in skill
    assert "Developers may write, copy, or create production files outside `scope_paths`" in skill
    assert "outside-scope system notification" in skill
    assert "coordination guidance, not a stop condition" in skill
    assert (
        "The next required change would be a broad or ambiguous production change that this lane cannot responsibly finish."
        in skill
    )
    assert (
        "ambiguous new production file whose missing path and mechanism are not proven by live production evidence."
        in skill
    )
    assert (
        "Before every mutation, verify the target file path, source path, destination path, or rename file hint"
        in skill
    )
    assert "Out-of-scope production writes, copies, and new files are allowed for developers" in skill
    assert "write-scope notifications are recorded" in skill
    assert "treat it as coordination context and keep working" in skill
    assert "with trigger `scope_expansion`" in skill
    assert (
        "Do not create missing modules, shims, re-exports, or bridges unless live production evidence names the missing path and mechanism"
        in skill
    )
    assert (
        "The next required edit is outside `scope_paths`, even when production evidence proves that path is required."
        not in skill
    )


def test_developer_and_validator_playbooks_keep_codeact_api_boundary() -> None:
    for playbook_name in ("team-developer-playbook", "team-validator-playbook"):
        skill = (_BUNDLED_SKILLS_DIR / playbook_name / "SKILL.md").read_text(
            encoding="utf-8"
        )

        assert "use `command` only for Python source snippets" not in skill
        assert "use `code` only for Python source snippets" in skill
        assert "only when no valid equivalent can preserve the needed evidence" in skill
        assert "A pre-hook block after sanitization or another policy denial is terminal tooling evidence" not in skill
        assert "never pass a shell command string in `code`" in skill
        assert "commands already start at the sandbox repo root" in skill
        assert "never `cd` to a host/local workspace path" in skill
        assert "Never prefix commands with `cd /testbed &&`" in skill


def test_validator_playbook_routes_out_of_scope_corrections_to_replan() -> None:
    skill = (
        _BUNDLED_SKILLS_DIR / "team-validator-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "The only apparent correction would edit, move, rename, or delete an existing file" in skill
    assert "Acceptance criteria, dependency handoffs, and test outcomes never expand `scope_paths`" in skill
    assert "by themselves" in skill
    assert "A new production file may extend scope only through `daytona_write_file`" in skill
    assert (
        "new production file whose `daytona_write_file` scope expansion was blocked or conflicted"
        in skill
    )
    assert (
        "Before every mutation, verify the target file is inside an assigned `scope_paths` entry"
        in skill
    )
    assert "For a new production file required by live evidence, use `daytona_write_file`" in skill
    assert "If an existing-file mutation is outside scope or the posthook blocks expansion" in skill
    assert "an advisory warning is workflow evidence, not permission to continue editing" in skill
    assert "with trigger `scope_expansion`" in skill
