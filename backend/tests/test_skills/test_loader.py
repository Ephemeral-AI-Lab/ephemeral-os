"""Tests for skill loading."""

from __future__ import annotations

from pathlib import Path

from config.paths import get_config_skills_dir
from skills import get_config_skills_dir as get_loaded_config_skills_dir, load_skill_registry

_BACKEND_SRC_DIR = Path(__file__).resolve().parents[2] / "src"
_CONFIG_SKILLS_DIR = get_config_skills_dir()


def _read_bundled_skill(skill_name: str) -> str:
    return (_CONFIG_SKILLS_DIR / skill_name / "SKILL.md").read_text(encoding="utf-8")


def _read_bundled_reference(skill_name: str, reference_name: str) -> str:
    return (
        _CONFIG_SKILLS_DIR / skill_name / "references" / reference_name
    ).read_text(encoding="utf-8")


def _assert_contains_all(content: str, required: tuple[str, ...]) -> None:
    for text in required:
        assert text in content


def _assert_absent(content: str, forbidden: tuple[str, ...]) -> None:
    for text in forbidden:
        assert text not in content


def test_load_skill_registry_includes_bundled(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EPHEMERALOS_CONFIG_DIR", str(tmp_path / "config"))
    registry = load_skill_registry()

    names = [skill.name for skill in registry.list_skills()]
    assert "team-planner-playbook" in names
    assert "team-replanner-playbook" in names
    assert "team-developer-playbook" in names


def test_skill_root_is_backend_config(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("EPHEMERALOS_CONFIG_DIR", str(tmp_path / "config"))

    assert get_loaded_config_skills_dir() == get_config_skills_dir()


def test_team_replanner_playbook_uses_planner_style_contract() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-replanner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    contract = (
        _CONFIG_SKILLS_DIR
        / "team-replanner-playbook"
        / "references"
        / "terminal-contract.md"
    ).read_text(encoding="utf-8")
    action_add = (
        _CONFIG_SKILLS_DIR
        / "team-replanner-playbook"
        / "references"
        / "action-add-tasks.md"
    ).read_text(encoding="utf-8")
    action_cancel = (
        _CONFIG_SKILLS_DIR
        / "team-replanner-playbook"
        / "references"
        / "action-cancel-and-redraft.md"
    ).read_text(encoding="utf-8")
    reference_names = {
        path.name
        for path in (_CONFIG_SKILLS_DIR / "team-replanner-playbook" / "references").glob("*.md")
    }

    assert reference_names == {
        "action-add-tasks.md",
        "action-cancel-and-redraft.md",
        "terminal-contract.md",
    }
    assert len(skill.splitlines()) <= 260
    assert len(action_add.splitlines()) <= 50
    assert len(action_cancel.splitlines()) <= 45
    assert len(contract.splitlines()) <= 160
    assert "## Workflow Map" in skill
    assert "```mermaid" not in skill
    assert "Caption: replanner recovery path" in skill
    assert "References support action and submit time" in skill
    assert "Reference Map" not in skill
    assert "terminal-contract" in skill
    assert "The intended path uses one action reference in Stage 3 and `terminal-contract` in Stage 4" in skill
    assert "Classify failure mode" in skill
    assert "Direct replan" in skill
    assert "Diagnostics" in skill
    assert "Synthesize the repair mapping" in skill
    assert "check proposed one-line fixes against every observed value" in skill
    assert "Caption: value-rule sanity check" in skill
    assert "astype(uint8)" in skill
    assert "trace-gap triplets" in skill
    assert "Launch one scout per remaining triplet" in skill
    assert "Wait for all required `read_task_details` results before calling `read_task_graph()`" in skill
    assert "Do not batch `read_task_graph()` with any required task-detail read" in skill
    assert "Classification: <scope_expansion|wrong_owner_or_role|unresolved_blocker>" in skill
    assert "Diagnostics decision: trivial_direct_replan" in skill
    assert "Diagnostics decision: deep_diagnostics" in skill
    assert "it is never stale sibling work and stays out of `cancel_ids`" in skill
    assert "If the fix target remains under any failed-task `scope_paths` entry" in skill
    assert "A failed task's \"test design issue\" label does not drop a named fail-to-pass variant" in skill
    assert "Enumerate distinct trace-gap triplets in visible reasoning before scout calls" in skill
    assert '"target_paths": ["<one or more scoped production paths for that one triplet>"]' in skill
    assert "Keep failing tests in scout `context`, not `target_paths`" in skill
    assert "Replanner-created tasks use only `developer` repair lanes and `validator` verification lanes" in skill
    assert "`new_tasks` is non-empty" in skill

    assert "## Call Shape" in contract
    assert "submit_replan({ new_tasks: NewTaskDefinition[], cancel_ids: string[] })" in contract
    assert "Top-level input has only required `new_tasks` and required `cancel_ids`" in contract
    assert "include `cancel_ids: []` when no cancellation is needed" in contract
    assert "Compare every `cancel_ids` entry against the failed task id from the prompt" in contract
    assert "Same-parent graph position does not make the failed task cancellable" in contract
    assert "No `cancel_ids` entry equals the failed task id from the prompt" in contract
    assert "Same-owner-file repairs are not scope expansion" in contract
    assert "`new_tasks` contains at least one corrective task" in contract
    assert "Empty Replan" not in contract
    assert '"new_tasks": []' not in contract
    assert 'agent: "developer" | "validator";' in contract
    assert "Every `agent` is exactly `developer` or `validator`" in contract
    assert "team_planner is accepted" not in contract
    assert "## Examples" in contract
    assert "## Final Checklist" in contract
    assert (
        "If `detail` uses `Classification: unresolved_blocker`, it must also include the exact field `Diagnostics decision: trivial_direct_replan` or `Diagnostics decision: deep_diagnostics`"
        in contract
    )
    assert (
        "Every spec with `Classification: unresolved_blocker` also includes `Diagnostics decision: trivial_direct_replan` or `Diagnostics decision: deep_diagnostics` inside `detail`."
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
    assert "Test repair by proxy" in action_add
    assert "correct task specs, acceptance criteria, or test filters" in action_add
    assert "handoff metadata" in action_add
    assert "production `scope_paths` paired with test-evidence mutation instructions" in contract
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
    assert "Map every named failing variant to a repair/diagnostic task" in skill
    assert "Before `trivial_direct_replan`, check proposed one-line fixes" in skill
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
    assert "action reference matching the final cancellation decision" in skill
    assert "No `cancel_ids` entry equals the failed task id from the prompt" in skill

    assert "goal`, `detail`, and `acceptance_criteria`" in skill
    assert "`spec.detail`" in contract
    assert "2. Task Detail:" not in skill
    assert "2. Task Detail:" not in contract
    assert "Valid replan trigger" not in skill
    assert "Replan trigger gate" not in skill


def test_team_replanner_playbook_recommends_references_for_action_and_submit_stages() -> None:
    skill = _read_bundled_skill("team-replanner-playbook")

    assert "Caption: replanner recovery path. References support action and submit time." in skill
    assert "The intended path uses one action reference in Stage 3 and `terminal-contract` in Stage 4" in skill
    assert "Prefer reading references when the current stage has the evidence it needs" in skill
    assert 'reference_name="action-add-tasks"' in skill
    assert 'reference_name="action-cancel-and-redraft"' in skill
    assert 'reference_name="terminal-contract"' in skill
    assert "Enter this stage after classification is written and diagnostics are complete or explicitly skipped" in skill
    assert "Enter this stage after the matching Stage 3 action reference has shaped the corrective mapping" in skill
    assert "## Reference Map" not in skill


def test_team_planner_playbook_uses_structured_spec_fields() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "structured `spec` with non-empty `goal`, `detail`, and `acceptance_criteria`" in skill
    assert "2. Task Detail:" not in skill
    assert "`spec.detail`" in skill
    assert "`Task Detail`" not in skill


def test_team_root_planner_playbook_uses_structured_spec_fields() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-root-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    reference = (
        _CONFIG_SKILLS_DIR
        / "team-root-planner-playbook"
        / "references"
        / "synthesize-and-submit.md"
    ).read_text(encoding="utf-8")
    content = f"{skill}\n{reference}"

    assert "structured `spec` containing non-empty `goal`, `detail`, and `acceptance_criteria`" in content
    assert "2. Task Detail:" not in content
    assert "`spec.detail`" in content
    assert "`Task Detail`" not in content


def test_team_root_planner_playbook_keeps_acceptance_criteria_evidence_focused() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-root-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    reference = (
        _CONFIG_SKILLS_DIR
        / "team-root-planner-playbook"
        / "references"
        / "synthesize-and-submit.md"
    ).read_text(encoding="utf-8")
    content = f"{skill}\n{reference}"

    assert "Tests and benchmark ids" in content
    assert "Keep them in `spec.detail` or `spec.acceptance_criteria`, not `scope_paths`" in content
    assert "`spec.acceptance_criteria`" in content
    assert (
        "Do not treat skipped, expected-failed, missing optional dependency, or clear `ImportError` outcomes as passing fail-to-pass closure"
        in content
    )
    assert "Named failing clusters" in content
    assert "Give each cluster a repair/decomposition owner" in content
    assert "daytona_shell-safe" not in content


def test_team_root_planner_playbook_requires_parallel_scout_fanout() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-root-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    reference = _read_bundled_reference(
        "team-root-planner-playbook", "synthesize-and-submit.md"
    )

    assert "Caption: fan out by owner family" in skill
    assert "scout_required" in skill
    assert "mark each broad family as `scout_required` even when the first-pass owner label looks plausible" in skill
    assert "Use at most one targeted `ci_workspace_structure` or `ci_query_symbol` call" in skill
    assert "Do not merge unrelated rows into one scout" in skill
    assert "A scout wave is usually 1-3 owner families" in skill
    assert "Multi-path scout = same row only" in skill
    assert '"target_paths": ["<one or more scoped production paths for that one owner family>"]' in skill
    assert "Prefer one stable boundary path" in skill
    assert "read_file_note(file_paths=[...])" in skill
    assert "CLI/config scout" not in skill
    assert "does not need to discover every leaf fix" in reference
    assert "When unsure, route to `team_planner`" in reference


def test_team_planner_playbook_requires_scout_required_fanout() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    reference = _read_bundled_reference(
        "team-planner-playbook", "submit-child-plan.md"
    )

    assert "Caption: one scout per owner-ledger row" in skill
    assert "Different rows stay in different scout calls" in skill
    assert "put each broad family, matrix family, or likely expandable first-pass owner in `scout_required`" in skill
    assert "Launch scouts only for high-value `scout_required` or unresolved owner families" in skill
    assert "Multi-path scouts are valid only when every path belongs to the same owner-ledger row" in skill
    assert "read_file_note(file_paths=[...])" in skill
    assert "When uncertain and depth remains, use `team_planner`" in reference
    assert "Preserve concrete pytest ids" in reference


def test_team_root_planner_playbook_recommends_synthesis_reference() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-root-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Caption: root planner stage machine. References support the stage that uses them." in skill
    assert "Enter this stage after the ledger is complete and scouts are either done or explicitly skipped" in skill
    assert 'skill_name="team-root-planner-playbook"' in skill
    assert "Load the synthesis reference when it helps draft or check the plan" in skill
    assert 'reference_name="synthesize-and-submit"' in skill
    assert "Keep benchmark test paths as verification evidence; they are not owner proof" in skill
    assert "After loading the reference, normally continue with draft/check/submit" in skill
    assert "make a bounded routing check" in skill
    assert "## Reference Map" not in skill


def test_team_planner_playbook_recommends_submit_child_reference() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Caption: child planner stage machine. References support synthesis." in skill
    assert "Enter this stage after context is loaded, the owner ledger is written" in skill
    assert 'skill_name="team-planner-playbook"' in skill
    assert "Load the synthesis reference when it helps draft or check the child plan" in skill
    assert 'reference_name="submit-child-plan"' in skill
    assert "After loading the reference, normally continue with drafting and submission" in skill
    assert "make a bounded routing check" in skill
    assert "## Reference Map" not in skill


def test_team_root_planner_playbook_loads_synthesize_submit_reference() -> None:
    skill_dir = _CONFIG_SKILLS_DIR / "team-root-planner-playbook"
    skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    reference = (skill_dir / "references" / "synthesize-and-submit.md").read_text(
        encoding="utf-8"
    )
    reference_names = {path.name for path in (skill_dir / "references").glob("*.md")}

    assert reference_names == {"synthesize-and-submit.md"}
    assert "## Reference Map" not in skill
    assert "Load the synthesis reference when it helps draft or check the plan" in skill
    assert "Use the reference's clustering, lane selection, coverage/evidence, dependency DAG, and submission rules" in skill
    assert 'skill_name="team-root-planner-playbook"' in skill
    assert 'reference_name="synthesize-and-submit"' in skill
    assert "This reference supports Stage 3 synthesis" in reference
    assert "Loading it does not freeze the workflow" in reference
    assert "make only bounded routing checks" in reference
    assert "## Routing Flow" in reference
    assert "## Submission Shape" in reference
    assert "## Payload Pattern" in reference
    assert "submit_plan({" in reference
    assert 'id: "val-root"' in reference
    assert 'deps: ["dev-focused-owner", "plan-runtime-cluster"]' in reference
    assert 'description: "Validate the owner"' not in reference
    assert "## DAG Patterns" in reference
    assert "## Final Checklist" in reference
    assert "## Case Examples" not in reference
    assert "### Case " not in reference


def test_team_root_planner_playbook_prefers_top_down_decomposition() -> None:
    skill = _read_bundled_skill("team-root-planner-playbook")
    reference = _read_bundled_reference(
        "team-root-planner-playbook", "synthesize-and-submit.md"
    )

    _assert_contains_all(
        skill,
        (
            "## Workflow Map",
            "Caption: root planner stage machine",
            "Caption: split evidence from ownership",
            "Caption: fan out by owner family",
            "Caption: lane routing during synthesis",
            "### 1. Analyze",
            "### 2. Scout",
            "### 3. Synthesize and submit",
            'skill_name="team-root-planner-playbook"',
            'reference_name="synthesize-and-submit"',
            "The root routes top-down",
            "child `team_planner`",
            "single-owner work",
        ),
    )
    _assert_absent(
        skill,
        (
            "## When to Use",
            "## Hierarchical Planning Principle",
            "## Lane Selection",
            "```mermaid",
            "## Terminal Tool Contract",
            "### 4. Submit",
        ),
    )
    _assert_contains_all(
        reference,
        (
            "## Routing Flow",
            "## Atomic vs Expandable",
            "Prefer top-down decomposition",
            "does not need to discover every leaf fix",
            "Benchmark, fail-to-pass, migration, compatibility",
            "Four or more independent leaf fixes",
            "When unsure, route to `team_planner`",
            "## Payload Pattern",
            "## DAG Patterns",
        ),
    )
    _assert_absent(reference, ("Depth Gate", "current_depth", "max_depth", "failure signal"))


def test_team_planner_playbook_prefers_recursive_decomposition() -> None:
    skill = _read_bundled_skill("team-planner-playbook")
    reference = _read_bundled_reference(
        "team-planner-playbook", "submit-child-plan.md"
    )

    assert len(skill.splitlines()) <= 140
    _assert_contains_all(
        skill,
        (
            "## Workflow Map",
            "Caption: child planner stage machine",
            "Caption: inherited context becomes routing rows",
            "Caption: one scout per owner-ledger row",
            "Caption: lane routing with depth",
            "### 1. Load context",
            "### 2. Scout",
            "### 3. Synthesize and submit",
            "child `team_planner`",
            "large benchmark/test-matrix work",
            "grandchild_depth <= max_depth",
            "broader direct `developer` or `validator` tasks",
            "Choose each task's agent while drafting",
            "cover every named failing cluster with a repair/decomposition owner or child `team_planner`",
            'skill_name="team-planner-playbook"',
            'reference_name="submit-child-plan"',
            "make a bounded routing check",
            "restructured packages with multiple plausible owner files",
            "scout first instead of assigning sibling-file owners from test names",
        ),
    )
    _assert_absent(
        skill,
        (
            "## Hierarchical Planning Principle",
            "Team plans are hierarchical",
            "top-down routing",
            "```mermaid",
            "current_depth",
            "Depth rules",
        ),
    )
    _assert_contains_all(
        reference,
        (
            "## Routing Flow",
            "## Atomic vs Expandable",
            "grandchild_depth <= max_depth",
            "child `team_planner`",
            "max depth reached",
            "Four or more independent leaf fixes",
            "When uncertain and depth remains, use `team_planner`",
            "avoid one catch-all developer task",
            "## Payload Pattern",
            "## DAG Patterns",
        ),
    )


def test_planner_playbooks_lock_expandable_slices_out_of_developer_lanes() -> None:
    root_skill = _read_bundled_skill("team-root-planner-playbook")
    root_reference = _read_bundled_reference(
        "team-root-planner-playbook", "synthesize-and-submit.md"
    )
    team_skill = _read_bundled_skill("team-planner-playbook")
    team_reference = _read_bundled_reference(
        "team-planner-playbook", "submit-child-plan.md"
    )

    _assert_contains_all(
        "\n".join((root_skill, root_reference)),
        (
            "Broad, clustered, matrix-shaped, or unresolved work -> child `team_planner`",
            "Before submit, audit every `developer` task",
            "Expandable, route to `team_planner`",
            "When unsure, route to `team_planner`",
        ),
    )
    _assert_contains_all(
        "\n".join((team_skill, team_reference)),
        (
            "when `grandchild_depth <= max_depth`",
            "expandable slice + grandchild_depth <= max_depth -> team_planner",
            "Before submit, audit every `developer` task",
            "Expandable path",
            "avoid one catch-all developer task",
        ),
    )


def test_team_planner_playbook_requires_fail_to_pass_coverage_owners() -> None:
    content = "\n".join(
        (
            _read_bundled_skill("team-planner-playbook"),
            _read_bundled_reference("team-planner-playbook", "submit-child-plan.md"),
        )
    )

    _assert_contains_all(
        content,
        (
            "named failing cluster",
            "repair/decomposition",
            "Validators",
        ),
    )


def test_team_planner_playbook_preserves_exact_inherited_pytest_ids() -> None:
    reference = _read_bundled_reference(
        "team-planner-playbook", "submit-child-plan.md"
    )

    _assert_contains_all(
        reference,
        (
            "Inherited failing targets",
            "Preserve concrete pytest ids",
            "variants",
            "file-level commands verbatim",
            "`spec.detail` or `spec.acceptance_criteria`",
        ),
    )


def test_team_developer_playbook_requires_exact_in_scope_fix_before_replan() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-developer-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert (
        "Do not use `request_replan` as a handoff for exact code you already know how to change"
        in skill
    )
    assert "assigned-scope or adjacent production-path actionable code defect" in skill


def test_planner_and_scout_playbooks_keep_benchmark_tests_as_evidence() -> None:
    planner_skill = (
        _CONFIG_SKILLS_DIR / "team-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    planner_reference = (
        _CONFIG_SKILLS_DIR
        / "team-planner-playbook"
        / "references"
        / "submit-child-plan.md"
    ).read_text(encoding="utf-8")
    planner_content = f"{planner_skill}\n{planner_reference}"
    root_planner_skill = (
        _CONFIG_SKILLS_DIR / "team-root-planner-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    root_planner_reference = (
        _CONFIG_SKILLS_DIR
        / "team-root-planner-playbook"
        / "references"
        / "synthesize-and-submit.md"
    ).read_text(encoding="utf-8")
    root_planner_content = f"{root_planner_skill}\n{root_planner_reference}"
    scout_skill = (
        _CONFIG_SKILLS_DIR / "team-scout-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")
    scout_contract = (
        _CONFIG_SKILLS_DIR
        / "team-scout-playbook"
        / "references"
        / "completion-contract.md"
    ).read_text(encoding="utf-8")

    for skill in (planner_content, root_planner_content):
        _assert_contains_all(
            skill,
            (
                "target_paths",
                "production-only",
                "scout `context`",
                "optional-dependency errors",
                "benchmark",
                "`spec`",
                "`scope_paths`",
                "skip",
                "xfail",
                "Tests and benchmark ids",
            ),
        )

    for skill in (scout_skill, scout_contract):
        assert "Never prescribe" in skill or "Must not recommend" in skill
        assert "skipping" in skill
        assert "xfail" in skill
        assert "pytest configuration" in skill

    assert "use at most one file-path `ci_query_symbol(...)` per assigned path" in scout_skill
    assert "the next tool must be `submit_file_notes(...)`" in scout_skill
    assert "Missing exact-target gate" in scout_skill
    assert "report zero coverage for that exact path in the note and stop" in scout_skill
    assert "one non-empty `prompt` with the full `scoped_paths` list" in scout_skill
    assert "the tool stores one note per scoped path" in scout_contract
    assert "do not use the old per-item note shape" in scout_contract
    assert "do not call `ci_workspace_structure(...)` or extra symbol/test queries" in scout_contract
    assert "the next tool must be `submit_file_notes(...)`" in scout_contract
    assert "For a missing exact file, the note should say the scout recorded zero coverage" in scout_contract


def test_planner_and_scout_playbooks_lock_single_file_scout_scope() -> None:
    planner_skill = _read_bundled_skill("team-planner-playbook")
    scout_skill = _read_bundled_skill("team-scout-playbook")
    scout_contract = _read_bundled_reference(
        "team-scout-playbook", "completion-contract.md"
    )

    _assert_contains_all(
        planner_skill,
        (
            "Keep `target_paths` production-only",
            "If an adjacent owner is only a hypothesis",
                "launch a separate scout for that path when it changes current-layer routing, or carry it as uncertainty",
        ),
    )
    _assert_contains_all(
        scout_skill,
        (
            "Context may mention benchmark ids, hypotheses, or adjacent production files, but it does not widen scope.",
            "If `context` asks you to inspect `core.py` while `target_paths` contains only `groupby.py`",
            "only `target_paths` authorize file or directory exploration",
            "Do not hunt for nearby files, sibling modules, or package structure",
        ),
    )
    _assert_contains_all(
        scout_contract,
        (
            "Context hypotheses do not widen the handed file set.",
            'If `target_paths` is `["pkg/groupby.py"]`, do not query `pkg/core.py`',
            "record the adjacent path as an unresolved gap instead",
            "run `ci_query_symbol(...)` on nearby helper names like `read_*` or `to_*`",
        ),
    )


def test_team_planner_reference_requires_live_proof_for_scope_paths() -> None:
    reference = _read_bundled_reference(
        "team-planner-playbook", "submit-child-plan.md"
    )

    _assert_contains_all(
        reference,
        (
            "Scout gaps",
            "Missing notes, cold paths, and adjacent-owner hypotheses stay as uncertainty",
            "unless live scout evidence proves the path",
            "`scope_paths`",
            "proven by inherited context or scout evidence",
        ),
    )


def test_team_validator_playbook_uses_root_planner_style_contract() -> None:
    validator_dir = _CONFIG_SKILLS_DIR / "team-validator-playbook"
    skill = (validator_dir / "SKILL.md").read_text(encoding="utf-8")
    reference_files = list((validator_dir / "references").glob("*.md"))

    assert "## Workflow Map" in skill
    assert "Decision flow:" in skill
    assert "## Workflow Details" in skill
    assert "| Section | Contract |" in skill
    assert "#### Steps" in skill
    assert "```mermaid" not in skill
    assert "### 1. Read task details" in skill
    assert "### 2. Build validation plan" in skill
    assert "### 3. Run diagnostics and exact verification" in skill
    assert "### 6. Submit terminal summary" in skill
    assert "submit_task_success({ summary: string })" in skill
    assert "request_replan({ reason: string })" in skill
    assert "public-surface guardrail" in skill

    assert reference_files == []
    assert "load_skill_reference" not in skill
    assert "## Conditional references" not in skill


def test_team_developer_playbook_uses_root_planner_style_contract() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-developer-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "## Workflow Map" in skill
    assert "Decision flow:" in skill
    assert "## Workflow Details" in skill
    assert "| Section | Contract |" in skill
    assert "#### Steps" in skill
    assert "```mermaid" not in skill
    assert "### 1. Read task details" in skill
    assert "### 2. Plan" in skill
    assert "### 3. Implement" in skill
    assert "### 4. Verify" in skill
    assert "### 5. Root cause analysis" in skill
    assert "### 6. Submit terminal summary" in skill
    assert "submit_task_success({ summary: string })" in skill
    assert "request_replan({ reason: string })" in skill
    assert "Trigger -> budget warning appears" in skill
    assert "make the next tool call `submit_task_success(...)` or `request_replan(...)`" in skill
    assert "one more edit or command to chase a known next fix" in skill


def test_developer_and_validator_playbooks_do_not_include_depth_gate_policy() -> None:
    for playbook_name in ("team-developer-playbook", "team-validator-playbook"):
        skill = (_CONFIG_SKILLS_DIR / playbook_name / "SKILL.md").read_text(
            encoding="utf-8"
        )

        assert "Depth Gate" not in skill
        assert "Planning depth" not in skill
        assert "current_depth" not in skill
        assert "max_depth" not in skill
        assert "child_depth" not in skill
        assert "grandchild_depth" not in skill


def test_terminal_summary_playbooks_require_explicit_residual_risk() -> None:
    for playbook_name in ("team-developer-playbook", "team-validator-playbook"):
        skill = (_CONFIG_SKILLS_DIR / playbook_name / "SKILL.md").read_text(
            encoding="utf-8"
        )

        assert "Do not omit a line because the answer is \"none\"" in skill
        assert "Residual risk" in skill


def test_developer_playbook_rejects_success_without_runtime_verification() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-developer-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Clean diagnostics are not acceptance verification" in skill
    assert "the required runtime command was not run after the final edit" in skill
    assert "verification was not run, was skipped due to budget" in skill
    assert "success because diagnostics are clean or the blocker seems external" in skill
    assert "success that labels the red command unrelated" in skill
    assert "pass or skip" in skill
    assert "ended in collection/import/no-tests/optional-dependency failure" in skill
    assert "supported only by diagnostics is not a success summary" in skill


def test_developer_playbook_rejects_wrapped_or_suppressed_verification() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-developer-playbook" / "SKILL.md"
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
        _CONFIG_SKILLS_DIR / "team-validator-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Do not suppress or alter pytest configuration" in skill
    assert "`--override-ini`" in skill
    assert "`filterwarnings=`" in skill
    assert "pytest-config-overridden command" in skill


def test_developer_playbook_keeps_parametrized_f2p_variants_as_evidence() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-developer-playbook" / "SKILL.md"
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
        skill = (_CONFIG_SKILLS_DIR / playbook_name / "SKILL.md").read_text(
            encoding="utf-8"
        )

        for trigger in allowed:
            assert trigger in skill
        for trigger in banned:
            assert trigger not in skill


def test_developer_playbook_limits_out_of_scope_production_edits_before_replan() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-developer-playbook" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "`scope_paths` are the primary ownership surface, not a hard mutation sandbox" in skill
    assert "You may widen reads, diagnostics, and test commands" in skill
    assert "A few lightweight out-of-scope production writes, moves, deletes, or creates are acceptable" in skill
    assert "The third outside-scope mutation" in skill
    assert "exits through `request_replan`" in skill
    assert (
        "The next required change would be a broad or ambiguous production change that this lane cannot responsibly finish."
        in skill
    )
    assert "ambiguous new production file whose missing path and mechanism are not proven" in skill
    assert (
        "Before every mutation, verify the target file path, source path, destination path, or rename file hint"
        in skill
    )
    assert "Out-of-scope production writes, copies, moves, deletes, and new files are limited" in skill
    assert "If the work needs three or more outside-scope mutations" in skill
    assert "A third outside-scope warning" in skill
    assert "with trigger `scope_expansion`" in skill
    assert (
        "Do not create missing modules, shims, re-exports, or bridges unless live production evidence names the missing path and mechanism"
        in skill
    )
    assert (
        "A failing test import, grep hit, or similarly named sibling path is still test-only or consumer-only evidence"
        in skill
    )
    assert (
        "If only tests or downstream consumers import a missing path, request replan unless a live production import or explicit assignment proves that path is the repair location."
        in skill
    )
    assert (
        "if a benchmark test imports `dask._compatibility` but the assigned evidence only names `dask/compatibility.py`"
        in skill
    )
    assert "request replan instead of creating `dask/_compatibility.py`" in skill
    assert (
        "The next required edit is outside `scope_paths`, even when production evidence proves that path is required."
        not in skill
    )


def test_developer_and_validator_playbooks_keep_shell_api_boundary() -> None:
    for playbook_name in ("team-developer-playbook", "team-validator-playbook"):
        skill = (_CONFIG_SKILLS_DIR / playbook_name / "SKILL.md").read_text(
            encoding="utf-8"
        )

        assert "use `command` only for Python source snippets" not in skill
        assert "only when no valid equivalent can preserve the needed evidence" in skill
        assert "A pre-hook block after sanitization or another policy denial is terminal tooling evidence" not in skill
        assert "commands already start at the sandbox repo root" in skill
        assert "never `cd` to a host/local workspace path" in skill
        assert "Never prefix commands with `cd /testbed &&`" in skill


def test_validator_playbook_routes_out_of_scope_corrections_to_replan() -> None:
    skill = (
        _CONFIG_SKILLS_DIR / "team-validator-playbook" / "SKILL.md"
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
