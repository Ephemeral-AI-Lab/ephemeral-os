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
    _CONTENT / "sweevo-project-context/SKILL.md",
    _CONTENT / "verification-replan/SKILL.md",
]
_ALL_SKILLS = [path for path in _ALL_SKILLS if path.exists()]
_REFERENCES = [
    _CONTENT / "team-developer-playbook/references/codeact-runtime-examples.md",
    _CONTENT / "team-developer-playbook/references/pre-completion-validation.md",
    _CONTENT / "team-developer-playbook/references/root-cause-debugging.md",
    _CONTENT / "team-developer-playbook/references/widening-and-runtime.md",
    _CONTENT / "team-planner-playbook/references/exploration-script.md",
    _CONTENT / "team-planner-playbook/references/scout-launch-contract.md",
    _CONTENT / "team-planner-playbook/references/non-root-context-reuse.md",
    _CONTENT / "team-planner-playbook/references/task-planning-decomposition.md",
    _CONTENT / "team-planner-playbook/references/root-plan-self-check.md",
    _CONTENT / "team-planner-playbook/references/plan-json-contract.md",
    _CONTENT / "team-planner-playbook/references/terminal-validation-contract.md",
    _CONTENT / "team-scout-playbook/references/completion-contract.md",
    _CONTENT / "team-validator-playbook/references/cross-surface-guardrails.md",
    _CONTENT / "team-validator-playbook/references/runtime-verification-examples.md",
    _CONTENT / "team-replanner-playbook/references/corrective-fast-path.md",
    _CONTENT / "team-replanner-playbook/references/action-add-tasks.md",
    _CONTENT / "team-replanner-playbook/references/action-cancel-and-redraft.md",
    _CONTENT / "team-replanner-playbook/references/action-declare-blocker.md",
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


def test_team_playbooks_load_references_for_detail_and_keep_top_level_generic() -> None:
    planner = _read(_CONTENT / "team-planner-playbook/SKILL.md")
    developer = _read(_CONTENT / "team-developer-playbook/SKILL.md")
    validator = _read(_CONTENT / "team-validator-playbook/SKILL.md")
    replanner = _read(_CONTENT / "team-replanner-playbook/SKILL.md")
    scout = _read(_CONTENT / "team-scout-playbook/SKILL.md")

    assert "must load `exploration-script`" in planner.lower()
    assert "must load `scout-launch-contract`" in planner.lower()
    assert "must load `task-planning-decomposition`" in planner.lower()
    assert "must load `plan-json-contract`" in planner.lower()
    assert "never guess an exact owner" in planner.lower()
    assert "never make non-submission tool calls after loading `plan-json-contract`" in planner.lower()
    assert "compat/re-export" not in planner
    assert "utils_dataframe.py" not in planner

    assert "must load `root-cause-debugging`" in developer.lower()
    assert "must load `widening-and-runtime`" in developer.lower()
    assert "must load `codeact-runtime-examples`" in developer.lower()
    assert "must load `pre-completion-validation`" in developer.lower()
    assert "never rewrite benchmark tests" in developer.lower()
    assert "uid 0 bypassing" not in developer.lower()
    assert "pkg._compatibility" not in developer

    assert "must load `cross-surface-guardrails`" in validator.lower()
    assert "must load `runtime-verification-examples`" in validator.lower()
    assert "must not paraphrase failure evidence" in validator.lower()

    assert "must load `corrective-fast-path`" in replanner.lower()
    assert "must load `action-declare-blocker`" in replanner.lower()
    assert "must load `action-add-tasks`" in replanner.lower()
    assert "must load `action-cancel-and-redraft`" in replanner.lower()

    assert "must load `completion-contract`" in scout.lower()
    assert "must stay read-only" in scout.lower()
    assert "must keep missing targets missing" in scout.lower()


def test_reference_files_hold_specialized_detail() -> None:
    planner_ref = _read(_CONTENT / "team-planner-playbook/references/exploration-script.md")
    planner_json = _read(_CONTENT / "team-planner-playbook/references/plan-json-contract.md")
    developer_runtime = _read(
        _CONTENT / "team-developer-playbook/references/codeact-runtime-examples.md"
    )
    developer_root_cause = _read(
        _CONTENT / "team-developer-playbook/references/root-cause-debugging.md"
    )
    scout_ref = _read(_CONTENT / "team-scout-playbook/references/completion-contract.md")
    validator_ref = _read(
        _CONTENT / "team-validator-playbook/references/runtime-verification-examples.md"
    )

    assert "Never keep a guessed exact leaf once live evidence disproves it." in planner_ref
    assert "submit_task_plan(new_tasks=[...])" in planner_json
    assert 'daytona_codeact(command="...", timeout=N)' in developer_runtime
    assert "pkg._compat" in developer_root_cause
    assert "The Task Center note is the durable handoff." in scout_ref
    assert "check_background_progress" in validator_ref


def test_sweevo_context_stays_shared_and_runtime_focused() -> None:
    sweevo = _read(_CONTENT / "sweevo-project-context/SKILL.md")
    assert "Must report a missing named test or node as `benchmark_surface_mismatch`." in sweevo
    assert (
        "Must not label a missing transitive import, helper, or adjacent production module as `benchmark_surface_mismatch`"
        in sweevo
    )
    assert "Must keep commands repo-root-relative." in sweevo
    assert 'daytona_codeact(command="...", timeout=N)' in sweevo
    assert "Python process wrappers" in sweevo
    assert "append `2>&1`" in sweevo
    assert "Must keep roles separate" in sweevo
    assert "Must treat `docs/architecture/plan-a-team-coordination-redesign.md` as the design intent" in sweevo
    assert "Must keep shared context in the Task Center" in sweevo
    assert "Must prefer Task Center notes, exact runtime evidence, and CI symbol tools over raw file reads on ready owner lanes." in sweevo
    assert "Must not spend a ready leaf's opening moves reading benchmark tests" in sweevo
    assert (
        "must not create planner/scout ownership tasks whose scope is benchmark-test archaeology"
        in sweevo.lower()
    )
    assert "Must not derive an exact production file from benchmark filename resemblance alone" in sweevo
    assert "Must use `read_task_note(paths=[...])` to check for existing findings before launching duplicate scouts." in sweevo
    assert "Must treat scope-change notifications and `task_center_changed_since()` as freshness signals." in sweevo
    assert "Must keep `scope_paths` as soft coordination hints" in sweevo
    assert "Must treat any advisory outside-scope write as a tainted packet" in sweevo


def test_worker_playbooks_do_not_mention_submitters_or_action_routing() -> None:
    for path in (
        _CONTENT / "team-developer-playbook/SKILL.md",
        _CONTENT / "team-validator-playbook/SKILL.md",
        _CONTENT / "sweevo-project-context/SKILL.md",
    ):
        content = _read(path)
        assert "submit_summary" not in content
        assert "submit_replan" not in content
        assert "request_retry" not in content
        assert "RECOMMENDED_ACTION" not in content
