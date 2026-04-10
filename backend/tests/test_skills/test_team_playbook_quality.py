"""Quality regressions for team playbook hard-rule sections."""

from __future__ import annotations

import re
from pathlib import Path


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PLAYBOOKS = [
    _BACKEND_ROOT / "src/skills/bundled/content/team-developer-playbook/SKILL.md",
    _BACKEND_ROOT / "src/skills/bundled/content/team-validator-playbook/SKILL.md",
    _BACKEND_ROOT / "src/skills/bundled/content/team-posthook-decision-playbook/SKILL.md",
    _BACKEND_ROOT / "src/skills/bundled/content/team-planner-playbook/SKILL.md",
    _BACKEND_ROOT / "src/skills/bundled/content/team-replanner-playbook/SKILL.md",
]
_SWEEVO_CONTEXT = _BACKEND_ROOT / "src/skills/bundled/content/sweevo-project-context/SKILL.md"
_COORDINATION_SKILLS = [
    _BACKEND_ROOT / "src/skills/bundled/content/coordination-analyze/SKILL.md",
    _BACKEND_ROOT / "src/skills/bundled/content/coordination-synthesize/SKILL.md",
    _BACKEND_ROOT / "src/skills/bundled/content/coordination-plan-tasks/SKILL.md",
    _BACKEND_ROOT / "src/skills/bundled/content/coordination-runtime-basics/SKILL.md",
    _BACKEND_ROOT / "src/skills/bundled/content/task-decompose/SKILL.md",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _hard_rules_section(content: str) -> str:
    after_header = content.split("## Hard rules", 1)[1]
    return re.split(r"\n---\n|\n## ", after_header, maxsplit=1)[0]


def test_hard_rule_numbers_do_not_repeat() -> None:
    for path in _PLAYBOOKS:
        section = _hard_rules_section(_read(path))
        labels = re.findall(r"^(\d+)\.\s", section, flags=re.MULTILINE)
        assert labels, f"expected numbered hard rules in {path}"
        duplicates = sorted({label for label in labels if labels.count(label) > 1})
        assert not duplicates, f"duplicate hard-rule numbers in {path}: {duplicates}"


def test_planner_playbook_gates_share_briefing_on_tool_availability() -> None:
    planner = _read(_BACKEND_ROOT / "src/skills/bundled/content/team-planner-playbook/SKILL.md")
    assert "submit_plan_agent" not in planner
    assert "only when `share_briefing` is actually available in your tool list" in planner
    assert "calling a tool that is not visibly available" in planner
    assert "representative deduped subset" in planner
    assert "Every entry in `items` must be its own `{...}` object" in planner
    assert 'A missing `class` hit from `ci_query_symbols(kind="class")` is not enough to conclude a public API is absent.' in planner
    assert 'Do not claim "class X is missing from the codebase" from planner-side symbol misses alone.' in planner
    assert "On fresh benchmark root turns, do **not** open with `atlas_lookup`." in planner
    assert "on fresh benchmark roots, use `ci_scope_status(...)` and fresh scouts before any atlas lookup" in planner
    assert "load `exploration-script` before the first non-reference tool call" in planner
    assert "`WAIT_REQUIRES_PROGRESS_CHECK`, duplicate-scout rejection, or a budget warning are stop-and-plan signals" in planner
    assert 'If you plan to join `task_id="all"`, inspect each fresh scout in that batch first' in planner
    assert 'Never call `run_subagent` with `agent_name="team_planner"`' in planner
    assert "duplicate-scout rejection over an already mapped path is terminal planning evidence" in planner
    assert "If a downstream developer or validator would still need fresh ownership discovery to start" in planner
    assert "Every execution lane should also receive the minimal handoff packet it needs to start immediately" in planner
    assert "Retry/replan handoff packets must preserve clustered failures, affected files, and what changed since the last healthy checkpoint or validator pass." in planner
    assert "do not expect validator or developer lanes to rediscover the owner map with fresh repo-wide probing" in planner
    assert "Build the `items` array one sibling object at a time." in planner
    assert "Count sibling items before you stop." in planner
    assert "A validator-only extracted payload means the JSON boundaries are broken." in planner
    assert "Every entry in `briefings` must be a complete object with a stable `name`, a valid `source`, and the matching payload field for that source." in planner
    assert 'For `run_subagent(agent_name="scout", ...)`, supply exactly one channel' in planner
    assert "keep at most two root validators" in planner
    assert "The global validator cap still applies inside child plans." in planner
    assert "Do not emit one validator per developer when that would exceed the cap." in planner
    assert "if you cannot quote an exact FAIL_TO_PASS node id verbatim from the prompt, use the exact benchmark test file path instead" in planner
    assert "a missing guessed owner file means re-anchor on the nearest exact existing production directory/package or hand the slice to a residual child planner" in planner
    assert "`ci_query_symbols(...)` results that only point back into the benchmark test files are symptom evidence, not production ownership" in planner
    assert "Child `owned_files` must contain only confirmed existing checkout-relative paths." in planner
    assert "keep the exact failing test file in `owned_failures`, move the unresolved production guess into `expansion_hint` or `notes`" in planner


def test_sweevo_context_treats_missing_share_briefing_as_non_blocking() -> None:
    sweevo = _read(_SWEEVO_CONTEXT)
    assert "should not spend tool budget on explicit `share_briefing` promotion unless that tool is visibly available" in sweevo
    assert "treat that as a no-promotion profile, not as a blocker" in sweevo
    assert "representative deduped subset of failing ids" in sweevo
    assert "repeat `local_id`, `agent_name`, `kind`, or `payload` keys inside one JSON object" in sweevo
    assert "broken JSON boundaries" in sweevo
    assert 'A planner-side `ci_query_symbols(kind="class")` miss does not prove a public type is absent from the repo.' in sweevo
    assert "After a bounded export fix, rerun the named pytest entry point before widening the same lane to additional public names." in sweevo
    assert "Once that missing public name is anchored to a local export file, do not spend developer budget on dependency version checks" in sweevo
    assert "Fresh benchmark roots should stay live-first." in sweevo
    assert "prefer `ci_scope_status(scope_paths=[...])` plus fresh scouts over `atlas_lookup`" in sweevo
    assert "Retry/replan handoff must preserve the evidence packet." in sweevo
    assert "Ownership mismatch is a planning problem." in sweevo
    assert "Exact existing paths only." in sweevo
    assert "Exact validator evidence is enough to branch." in sweevo
    assert "Planner briefings must be execution-ready." in sweevo
    assert "Every planner `briefings` entry needs a stable `name`, a valid `source`, and the matching payload field for that source." in sweevo
    assert "Do not push that rediscovery work down to the next developer or validator lane." in sweevo
    assert "Preserve exact pytest node ids verbatim in planner payloads." in sweevo
    assert "Do not shorten `test_info_versions` to `test_info`" in sweevo
    assert "At the submitted benchmark root, keep at most two validators total." in sweevo
    assert "Child benchmark plans inherit that same cap." in sweevo
    assert "instead of emitting a third validator" in sweevo
    assert "If a guessed production owner file turns out to be missing, re-anchor on the nearest exact existing production directory/package path or park that cluster behind a child planner." in sweevo
    assert "`owned_files` is not a hypothesis bucket." in sweevo
    assert "keep missing guessed owners out of `owned_files`" in sweevo
    assert "Root planner symbol hits that only land in benchmark test files are not ownership evidence." in sweevo
    assert "if a validator or inherited note cites a missing alias path such as `pyarrow.py` while live CI resolves the surface to `arrow.py`" in sweevo
    assert 'Do not "repair" the benchmark by editing the unowned test file' in sweevo
    assert "mentioned only in `owned_failures`, `verify`, or a failing command is not test ownership" in sweevo
    assert "the developer should report `scope_mismatch` with the exact missing import/export and likely owner path" in sweevo
    assert 'A developer claim that a named benchmark test now encodes "old behavior" after a contradicted patch is not enough to open a test-edit lane.' in sweevo
    assert "do not re-read the test body or shared parameter-plumbing files to author a patch recipe" in sweevo
    assert "Scout launches must satisfy the literal runtime schema" in sweevo


def test_scout_playbook_keeps_missing_file_targets_missing() -> None:
    scout = _read(_BACKEND_ROOT / "src/skills/bundled/content/team-scout-playbook/SKILL.md")
    assert "If a file target is missing, keep that exact path missing." in scout
    assert "do not inspect nearby replacements such as `parquet/core.py` for a missing `parquet.py`" in scout


def test_replanner_playbook_requires_exact_existing_paths() -> None:
    replanner = _read(_BACKEND_ROOT / "src/skills/bundled/content/team-replanner-playbook/SKILL.md")
    corrective_fast_path = _read(
        _BACKEND_ROOT
        / "src/skills/bundled/content/team-replanner-playbook/references/corrective-fast-path.md"
    )
    assert 'read `references/corrective-fast-path.md` before any deeper analysis' in replanner
    assert 'load_skill_reference("team-replanner-playbook", "corrective-fast-path")' in replanner
    assert "the first one must be `ci_scope_status(...)` on the exact owner surface or owning directory" in replanner
    assert "every corrective `scope_paths`, owned file, and candidate owner path must already exist in the live checkout packet or be re-confirmed by CI before you reuse it" in replanner
    assert "if a cited path cannot be read or `ci_scope_status(...)` / `ci_read_file(...)` says it does not exist, treat that as an owner-map mismatch" in replanner
    assert "do not preserve guessed module aliases across replans; if the live repo uses `arrow.py`, do not draft corrective work against invented siblings such as `pyarrow.py`" in replanner
    assert "corrective payload paths must be exact existing checkout-relative paths, never guessed aliases or nonexistent siblings" in replanner
    assert "Missing paths are mismatch signals, not evidence." in replanner
    assert "once you can name the exact failing cluster, the exact existing owner file(s), and the exact retry or verification target for the next worker, stop exploring and draft the corrective JSON immediately" in replanner
    assert "one confirmatory read/query per unresolved cluster is usually enough" in replanner
    assert "do not reopen test source files or shared router/plumbing files such as `core.py`" in replanner
    assert "do not read the test body or shared parameter-plumbing files to reverse-engineer semantics" in replanner
    assert "If two clusters already have distinct owner files or distinct retry targets, do not merge them back into one omnibus developer item" in replanner
    assert "Replanners do not debug like developers." in replanner
    assert "Describe the observed symptom, likely owner, and guardrail targets; do not encode a precise patch prescription unless a validator packet or sibling artifact already proved that exact edit." in replanner
    assert "Do not emit `specific_fixes`, condition rewrites, exact line edits, or message-text prescriptions from replanner-side reasoning alone." in replanner
    assert "Handoff evidence, not speculative patches." in replanner
    assert "Do not draft a test-edit corrective lane from a developer's contradicted patch alone." in replanner
    assert "Exact failing ids plus exact owner files are enough." in replanner
    assert "if you need any live confirmation at all, the first confirmation step is `ci_scope_status(...)`" in replanner
    assert "do not query benchmark test decorators, parametrization markers, or test headers such as `PYARROW_MARK`, `parametrize`, or top-of-file skips" in replanner
    assert "Repeated same-surface reads are a stop signal." in replanner
    assert "Benchmark replans anchor live context with `ci_scope_status` first." in replanner
    assert "incoming validator packet already names exact failing pytest ids and exact existing owner file(s)" in corrective_fast_path
    assert "The default first live-tool call is `ci_scope_status(scope_paths=[...])`" in corrective_fast_path
    assert "If a benchmark corrective turn opens with `ci_read_file(...)` or symbol queries on the owner files before first calling `ci_scope_status(...)`" in corrective_fast_path
    assert "marker or parametrization queries such as `PYARROW_MARK`, `skipif`, or `parametrize`" in corrective_fast_path
    assert "If you have already reopened the same owner cluster once and can still name the owner plus retry target, emit JSON now." in corrective_fast_path


def test_developer_playbook_anchors_import_failures_to_named_pytest_surface() -> None:
    developer = _read(_BACKEND_ROOT / "src/skills/bundled/content/team-developer-playbook/SKILL.md")
    assert "If that first entry point is an import or collection failure" in developer
    assert "Do not promote a probe-only theory into broader code edits" in developer
    assert 'A `ci_query_symbols(kind="class")` miss is not proof that a public type is absent.' in developer
    assert "When the first pytest failure is a missing public name" in developer
    assert "After fixing one missing export or public name, rerun the named pytest entry point before adding any other symbols." in developer
    assert "inspect the package export bridge next" in developer
    assert "exact failing import path succeeds in a fresh Python process" in developer
    assert "In coordinated team developer lanes, `daytona_codeact` is intentionally unavailable." in developer
    assert "Do not escalate a surgical same-file export or alias fix into `daytona_codeact`." in developer
    assert "After a targeted retest fails, re-read the edited block before writing custom debug scripts." in developer
    assert "Budget warnings require the identified patch point, not more diagnosis." in developer
    assert "Rejected mutating shell probes are a stop sign." in developer
    assert "patch the last merge/update function that overwrites the public field" in developer
    assert "If the first failing pytest surface is inside an unowned test file" in developer
    assert "When the first failing import/collection surface points to a missing export/module in a different production file than your `owned_files`, report `scope_mismatch`" in developer
    assert "Named-node mismatches are not permission to rewrite tests." in developer
    assert "`owned_failures` is not a write allowlist." in developer
    assert "If the first reproducible failure names an unowned test file and the missing import/export lives outside your assigned production files, stop with `scope_mismatch`" in developer
    assert "A failing test path in `owned_failures`, `verify`, or reproduction output is evidence, not write permission." in developer
    assert 'Do not claim the test encodes "old behavior", "stale expectations", or needs a test-only follow-up' in developer
    assert "Do not synthesize hybrid public strings to satisfy competing tests." in developer
    assert "If the runtime says `Unknown tool: edit_file`, `write_file`, or `read_file`" in developer
    assert "Treat `daytona_bash` as an execution tool, not a discovery or editing tool." in developer
    assert "Do not fall back to `daytona_bash` for file reads, file writes, search, globbing, or ad hoc patch application" in developer


def test_validator_playbook_mentions_codeact_is_unavailable_in_team_lanes() -> None:
    validator = _read(_BACKEND_ROOT / "src/skills/bundled/content/team-validator-playbook/SKILL.md")
    assert "coordinated team validation lanes intentionally omit `daytona_codeact`" in validator
    assert "Ownership mismatch is not a validator discovery task." in validator
    assert "return `plan_gap` with exact evidence." in validator
    assert "Validators are not backup planners." in validator
    assert "If the command already prints the exact failing pytest node ids, that is terminal evidence." in validator
    assert "After a payload-specified broad regression command fails and yields failing node ids, the very next action must be the verdict block." in validator
    assert "A pytest FAIL with exact node ids is already enough." in validator
    assert "Do not turn a failing node list into theories like \"test expectation mismatch\"" in validator
    assert "A failed broad regression command ends execution." in validator
    assert "RECOMMENDED_ACTION" not in validator


def test_worker_playbooks_do_not_mention_submitters_or_action_routing() -> None:
    developer = _read(_BACKEND_ROOT / "src/skills/bundled/content/team-developer-playbook/SKILL.md")
    validator = _read(_BACKEND_ROOT / "src/skills/bundled/content/team-validator-playbook/SKILL.md")
    sweevo = _read(_SWEEVO_CONTEXT)

    for content in (developer, validator, sweevo):
        assert "submit_summary" not in content
        assert "submit_replan" not in content
        assert "RECOMMENDED_ACTION" not in content


def test_coordination_skills_do_not_tell_main_agents_about_posthook_formatters() -> None:
    for path in _COORDINATION_SKILLS:
        content = _read(path)
        assert "posthook formatter" not in content
        assert "formatter/posthook" not in content


def test_posthook_decision_playbook_forbids_clarifying_questions_on_worker_output() -> None:
    posthook = _read(_BACKEND_ROOT / "src/skills/bundled/content/team-posthook-decision-playbook/SKILL.md")
    assert "Every incoming message is worker output from the previous phase" in posthook
    assert "Do not ask clarifying questions." in posthook
    assert "Malformed worker output still requires a decision." in posthook
    assert "If a developer reports `partially_fixed`, names exact remaining failing tests" in posthook
    assert 'Do not accept a claim that remaining failures are "test issues", "scope mismatch", or "outside this task"' in posthook
    assert "Partial fixes with same-surface failures are not terminal." in posthook
    assert "If the worker's assigned `verification_command` or named `verify` targets are still red" in posthook
    assert 'If the worker says the residual failure is "separate", "pre-existing", or "another issue"' in posthook
    assert "Owned red verify surfaces block summary." in posthook
