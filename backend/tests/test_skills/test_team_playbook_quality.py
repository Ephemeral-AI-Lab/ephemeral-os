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
    assert "Call `atlas_lookup` only after that fresh current-turn context is exhausted" in planner
    assert "use current-turn scout / dep artifacts before Atlas in a changing repo" in planner
    assert "on fresh benchmark roots, use one narrow `ci_workspace_structure(...)`, then `ci_scoped_status(...)`, then fresh scouts before any atlas lookup" in planner
    assert "Planner sibling-awareness should come from `ci_scoped_status(...)` packets first." in planner
    assert "load `exploration-script` before the first non-reference tool call" in planner
    assert "Fresh benchmark root: start with a narrow `ci_workspace_structure(path=\"<nearest likely production directory/package>\", max_depth<=4)` pass, then call `ci_scoped_status(...)` on an exact existing production path from that listing or inherited evidence." in planner
    assert "Do not open with root-wide `ci_workspace_structure()`, `ci_query_symbols(...)`, or other broad live CI queries before that sequence completes." in planner
    assert "do not draft or narrate a concrete scout wave until one `ci_workspace_structure(...)` pass and one `ci_scoped_status(...)` anchor" in planner
    assert "listing scout targets or calling `run_subagent(...)` before that scope grounding is not." in planner
    assert "before every `run_subagent(agent_name=\"scout\", ...)` call, compare the proposed `target_paths` against the named benchmark test files" in planner
    assert "do not guess a leaf file before that pass." in planner
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
    assert "Keep root validators attached to concrete root lanes" in planner
    assert "let a residual `team_planner` branch carry its own downstream validation" in planner
    assert "keep child-plan validators branch-local and risk-weighted" in planner
    assert "open with one narrow `ci_workspace_structure(path=..., max_depth<=4)` pass and then `ci_scoped_status(scope_paths=[...])`" in planner
    assert "Do not open with root-wide `ci_workspace_structure()`, `ci_query_symbols(...)`, or other broad live CI queries before that sequence completes." in planner
    assert "the first scout wave should usually cover multiple disjoint production-owner slices instead of only the top two clusters by failure count" in planner
    assert "Cluster size can order the wave, but it should not force an artificially narrow first pass." in planner
    assert "Do not bundle unrelated owner surfaces into one scout just to force an artificially narrow wave" in planner
    assert "Do not pack unrelated owner surfaces into one scout lane just to honor an outdated first-wave cap." in planner
    assert "Do not spend one of those first-wave scout slots on a guessed missing file such as `parquet.py`" in planner
    assert "Do not guess file names from test names such as `parquet.py`, `utils_dataframe.py`, or similar prompt-shaped aliases." in planner
    assert "spend at most one parent-side `ci_workspace_structure(...)` pass per unresolved top-level owner cluster before opening scouts" in planner
    assert "Only exact existing production paths from live CI may become scout targets." in planner
    assert "if you cannot quote an exact FAIL_TO_PASS node id verbatim from the prompt, use the exact benchmark test file path instead" in planner
    assert "Keep `owned_failures` entries literal checkout-relative prompt ids only" in planner
    assert "only use a `::pytest_node` suffix in `owned_failures`, `reproduction`, `verification`, or validator `verify` commands when that exact node id was explicitly confirmed" in planner
    assert "If the benchmark evidence is still file-scoped, keep validator `verify` commands and planner-provided verification commands on that exact checkout-relative test file path." in planner
    assert 'Broad parametrized clusters are not a license to pick one "representative" node for retry commands.' in planner
    assert "keep planner-supplied `reproduction`, `verification`, and validator `verify` commands on the exact benchmark test file path until a live runtime artifact proves that specific node still collects in the current checkout." in planner
    assert "Do not widen to repo-wide keyword sweeps such as `pytest pkg/ -k compatibility`" in planner
    assert "Keep command-bearing payload keys canonical and minimal." in planner
    assert "Do not invent ad hoc command fields like `retries` to smuggle in guessed pytest paths." in planner
    assert "Cluster summaries are not exact retry targets." in planner
    assert "A copied exact node id is still stale if the current checkout cannot collect it." in planner
    assert "a missing guessed owner file means re-anchor on the nearest exact existing production directory/package or hand the slice to a residual child planner" in planner
    assert "If a proposed first-wave `target_paths` entry still equals a named benchmark test file" in planner
    assert "`ci_query_symbols(...)` results that only point back into the benchmark test files are symptom evidence, not production ownership" in planner
    assert "Prompt-named benchmark test files are symptom surfaces, not settled implementation ownership." in planner
    assert "Do not emit a direct developer lane whose `owned_files` are only those test files unless live evidence says the slice truly belongs to test/support infrastructure." in planner
    assert "Do not invent sibling directories like `dask/cli`, and do not redirect the lane into `dask/tests` as a substitute for missing production ownership." in planner
    assert "Do not infer an optional-dependency or environment root cause from cluster size alone." in planner
    assert "Child `owned_files` must contain only confirmed existing checkout-relative paths." in planner
    assert "do not forward that `tests/...` path as the child developer lane's `owned_files` unless live evidence says test/support infrastructure is the real owner." in planner
    assert "keep the exact failing test file in `owned_failures`, move the unresolved production guess into `expansion_hint` or `notes`" in planner
    assert "An expandable child planner is not a readiness barrier for descendant code verification." in planner
    assert "A broad pytest command over residual test files is proof that the validator is misplaced and must move into the child branch." in planner
    assert "Zero-coverage or wrong-path scout evidence supports only ownership/path-shape conclusions." in planner
    assert "preserve them byte-for-byte downstream" in planner
    assert 'Keep `test_cli.py` as `test_cli.py`, not `test_dask_cli.py`' in planner
    assert "spend at most one `ci_scoped_status(...)` freshness check before emitting direct developer/validator lanes" in planner
    non_root = _read(
        _BACKEND_ROOT
        / "src/skills/bundled/content/team-planner-playbook/references/non-root-context-reuse.md"
    )
    assert "If only one residual owner guess still needs confirmation, spend at most one live confirmation step on that unresolved owner and then emit direct lanes for the already-mapped siblings." in non_root
    assert "keep `owned_failures`, `reproduction`, `verification`, and validator `verify` at the exact file path until a live artifact proves the exact `::pytest_node` suffix" in non_root
    assert "Do not shorten `compatibility.py` to `compat.py`, `configuration.py` to `config.py`, or similar prompt-shaped aliases while expanding the child plan." in non_root
    assert "Do not conclude the owner is absent while a same-stem live file such as `compatibility.py` is sitting next to the guessed `compat.py`." in non_root
    assert "Downgrade the child payload to the exact test file path or report the benchmark-surface mismatch; do not invent a same-stem production helper or replacement node from the missing test name." in non_root
    assert "Keep that test path in `owned_failures`, but recover a production/export owner or at least a candidate package/directory before emitting a direct developer lane." in non_root


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
    assert "prefer one narrow `ci_workspace_structure(...)` pass followed by `ci_scoped_status(scope_paths=[...])` plus fresh scouts over `atlas_lookup`" in sweevo
    assert "start with one narrow `ci_workspace_structure(path=\"<nearest likely production directory/package>\")` pass, then call `ci_scoped_status(scope_paths=[...])`" in sweevo
    assert "Same-run scout context beats Atlas in a moving repo." in sweevo
    assert "the first wave should usually cover multiple disjoint production-owner scouts" in sweevo
    assert "otherwise use the smallest useful disjoint wave" in sweevo
    assert "Do not bundle unrelated owner surfaces into one scout lane just to mimic an old fixed-lane default" in sweevo
    assert "Retry/replan handoff must preserve the evidence packet." in sweevo
    assert "Ownership mismatch is a planning problem." in sweevo
    assert "Ground payload paths in live existing paths." in sweevo
    assert "The one exception is a missing module file spelled verbatim by the failing import path" in sweevo
    assert "Exact validator evidence is enough to branch." in sweevo
    assert "Planner briefings must be execution-ready." in sweevo
    assert "Every planner `briefings` entry needs a stable `name`, a valid `source`, and the matching payload field for that source." in sweevo
    assert "Do not push that rediscovery work down to the next developer or validator lane." in sweevo
    assert "Preserve exact pytest node ids verbatim in planner payloads." in sweevo
    assert "Do not shorten `test_info_versions` to `test_info`" in sweevo
    assert "File-level fallback applies to retry commands too." in sweevo
    assert "Representative node ids are evidence, not automatic retry commands." in sweevo
    assert "it should still keep `reproduction`, `verification`, and validator `verify` file-scoped until a live worker packet proves that one concrete node still collects in the current checkout." in sweevo
    assert "Exact-file benchmark evidence should stay exact-file for validation too." in sweevo
    assert "Do not widen a validator from `dask/tests/test_compatibility.py` to a repo-wide keyword sweep like `pytest dask/ -k compatibility`" in sweevo
    assert "do not invent a same-stem production helper, public symbol, or replacement node from the missing test name." in sweevo
    assert "At any submitted benchmark plan level, keep validators paired with the concrete developer lanes they actually verify." in sweevo
    assert "An expandable child planner is not a readiness barrier for descendant code verification." in sweevo
    assert "Child benchmark plans should keep validators branch-local and risk-weighted" in sweevo
    assert "instead of emitting one validator per developer or recreating an umbrella validation layer" in sweevo
    assert "choose the graph shape dynamically from the mapped owner surface" in sweevo
    assert "Do not force a fixed lane recipe." in sweevo
    assert "If a guessed production owner file turns out to be missing, re-anchor on the nearest exact existing production directory/package path or park that cluster behind a child planner." in sweevo
    assert "Do not shorten `compatibility.py` to `compat.py`, `configuration.py` to `config.py`, or similar prompt-shaped guesses when live structure already names the real file." in sweevo
    assert "Prompt-named benchmark test files are symptom evidence, not default implementation ownership." in sweevo
    assert "Do not emit root developer lanes whose `owned_files` contain only those tests unless live evidence says the slice truly belongs to test/support infrastructure." in sweevo
    assert "Treat `owned_files` as a grounded edit surface, not a hypothesis bucket." in sweevo
    assert "Keep missing guessed owners out of `owned_files`" in sweevo
    assert "Keep benchmark tests in `owned_failures`; use `owned_files` only for confirmed production/support owners or, when still unresolved, a confirmed candidate package/directory." in sweevo
    assert "Root planner symbol hits that only land in benchmark test files are not ownership evidence." in sweevo
    assert "if a validator or inherited note cites a missing alias path such as `pyarrow.py` while live CI resolves the surface to `arrow.py`" in sweevo
    assert "if live structure resolves `compatibility.py`, do not keep planning against guessed siblings like `compat.py` or `_compat.py`." in sweevo
    assert "When the failure packet itself names a missing module import path such as `from dask._compatibility import PY_VERSION`" in sweevo
    assert 'Do not "repair" the benchmark by editing the unowned test file' in sweevo
    assert "mentioned only in `owned_failures`, `verify`, or a failing command is not test ownership" in sweevo
    assert "`owned_files` defines the default edit surface, not an absolute write-permission wall." in sweevo
    assert "the developer may widen to that likely owner when it remains the same cohesive bug" in sweevo
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
    assert "start with `ci_scoped_status(...)` on the exact owner surface or owning directory" in replanner
    assert "every corrective `scope_paths`, owned file, and candidate owner path must already exist in the live checkout packet or be re-confirmed by CI before you reuse it" in replanner
    assert "except for one exact missing module file spelled verbatim by the failing import path when its parent package/directory already exists live" in replanner
    assert "if a cited path cannot be read or `ci_scoped_status(...)` / `ci_read_file(...)` says it does not exist, treat that as an owner-map mismatch" in replanner
    assert "do not preserve guessed module aliases across replans; if the live repo uses `arrow.py`, do not draft corrective work against invented siblings such as `pyarrow.py`" in replanner
    assert "corrective payload paths must be exact existing checkout-relative paths, never guessed aliases or nonexistent siblings" in replanner
    assert "If Python is trying to import `pkg._compat` and the live parent package `pkg/` exists, you may assign the exact import-path file `pkg/_compat.py`" in replanner
    assert "Missing paths are mismatch signals, not evidence." in replanner
    assert "The only exception is an exact missing module file named by the failing import path itself" in replanner
    assert "once you can name the exact failing cluster, the exact existing owner file(s) or exact missing import-path module target, and the exact retry or verification target for the next worker, stop exploring and draft the corrective JSON immediately" in replanner
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
    assert "Do not prescribe an export-only fix for a missing module import unless the module path would actually resolve." in replanner
    assert "if you need any live confirmation at all, the first confirmation step is `ci_scoped_status(...)`" in replanner
    assert "do not query benchmark test decorators, parametrization markers, or test headers such as `PYARROW_MARK`, `parametrize`, or top-of-file skips" in replanner
    assert "Repeated same-surface reads are a stop signal." in replanner
    assert "Benchmark replans anchor live context with `ci_scoped_status` first." in replanner
    assert "incoming validator packet already names exact failing pytest ids and exact existing owner file(s)" in corrective_fast_path
    assert "The default first live-tool call is `ci_scoped_status(scope_paths=[...])`" in corrective_fast_path
    assert "If the failure packet itself names a missing module import path and the live parent package/directory exists" in corrective_fast_path
    assert "Example: `from dask._compatibility import PY_VERSION` may justify a corrective target on `dask/_compatibility.py`." in corrective_fast_path
    assert "If a benchmark corrective turn opens with `ci_read_file(...)` or symbol queries on the owner files before first calling `ci_scoped_status(...)`" in corrective_fast_path
    assert "marker or parametrization queries such as `PYARROW_MARK`, `skipif`, or `parametrize`" in corrective_fast_path
    assert "If you have already reopened the same owner cluster once and can still name the owner plus retry target, emit JSON now." in corrective_fast_path
    assert 'when the actual failure is `from dask._compatibility import PY_VERSION` and the missing module path itself remains unresolved' in corrective_fast_path


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
    assert "When the first failing import/collection surface points to a missing export/module in a different production file than your `owned_files`, you may widen to that owner when it is the clear minimal fix." in developer
    assert "Named-node mismatches are not permission to rewrite tests." in developer
    assert "`owned_failures` is not a write allowlist." in developer
    assert "If the first reproducible failure names an unowned test file and the missing import/export lives outside your assigned production files, patch the true owner when it is clearly the same bug and the change stays bounded." in developer
    assert "a failing test path in `owned_failures`, `verify`, or reproduction output is evidence, not automatic proof that the test file is the right first patch target." in developer
    assert "Harness/config files are not the default fix surface." in developer
    assert "Do not patch `setup.cfg`, `pytest.ini`, `pyproject.toml`, warning filters, or similar test-runner configuration just to make a product failure disappear." in developer
    assert "Pytest warning/config parse failures usually still belong to product code." in developer
    assert "If pytest fails while parsing warning filters or import-time deprecations and your lane already owns a production/import/export surface" in developer
    assert "Treat `owned_files` as the default landing zone, not a hard barrier." in developer
    assert "`owned_files` guides the default edit surface." in developer
    assert "Widened writes require a fresh scope packet on the widened target." in developer
    assert "Before the first edit or write to any file outside `owned_files`, call `ci_scoped_status(scope_paths=[<exact widened file or its nearest owning directory>])`" in developer
    assert "Compose with sibling work on widened files." in developer
    assert "If that widened target already shows sibling reservations or recent edits, re-read the live file and extend the current implementation instead of overwriting it with a fresh variant." in developer
    assert "if another lane already created `dask/_compatibility.py`, do not replace that file with a new private shim" in developer
    assert 'if you catch yourself reasoning "the failing test is listed in `owned_failures`, so I should patch that test import first," stop.' in developer
    assert 'Do not claim the test encodes "old behavior", "stale expectations", or needs a test-only follow-up' in developer
    assert "Do not synthesize hybrid public strings to satisfy competing tests." in developer
    assert "read the exact observed-vs-expected mismatch from that failure output before the next edit" in developer
    assert "If a narrow debug probe shows a helper already returns the value or shape the test expects, stop editing that helper." in developer
    assert "A missing test-node stem is not a product API spec." in developer
    assert "If the payload cites a missing node such as `test_dataframe_overlap` and the live checkout has no such node, do not invent `dataframe_overlap`, `mask`, or another same-stem helper from that test name alone." in developer
    assert "An absent named pytest node ends the lane." in developer
    assert "do not broaden to whole-file `pytest` runs, `-k` sweeps, or nearby failing tests to guess a substitute target." in developer
    sweevo = _read(_SWEEVO_CONTEXT)
    assert "If a product bug manifests as pytest import-time warning/config parsing fallout, keep the developer lane on the product import/export surface first." in sweevo
    assert "Do not retarget the lane to `setup.cfg`, `pytest.ini`, `pyproject.toml`, or warning filters unless live evidence proves the config file itself owns the regression." in sweevo
    assert "when the failing lane already owns a compatibility/import/export module and pytest now dies while parsing warning filters, assume the product module changed import-time warning behavior until a direct read proves otherwise." in sweevo
    assert "A missing named pytest node is terminal for that developer lane." in sweevo
    assert "Do not keep debugging the broader test file, run `-k` sweeps for nearby failures, or substitute a different failing test from the same module" in sweevo
    assert "keep those entry points behaviorally distinct unless the live failing test proves they should converge" in developer
    assert "If the runtime says `Unknown tool: edit_file`, `write_file`, or `read_file`" in developer
    assert "the default first live coordination step is `ci_scoped_status(scope_paths=[<exact owned file(s) or nearest owning directory>])`" in developer
    assert "Treat `daytona_bash` as an execution tool, not a discovery or editing tool." in developer
    assert "Do not fall back to `daytona_bash` for file reads, file writes, search, globbing, or ad hoc patch application" in developer
    assert "Do not use `daytona_bash` for `ls`, `pwd`, `cd`, `find`, or other workspace-discovery probes." in developer
    assert "if `dask/dataframe/io/tests/test_hdf.py` fails on `from dask._compatibility import PY_VERSION` while your lane owns only `dask/dataframe/io/hdf.py` / `dask/dataframe/io/json.py`, treat `dask/_compatibility.py` as an allowed supporting edit when it is the clear minimal owner" in developer
    assert "Cross-lane widening requires live sibling awareness." in developer
    assert "A widened owner file that another lane already edited is a compose-with-live-state surface, not a blank file you may rewrite from scratch." in developer


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
    assert "the default first live coordination step is `ci_scoped_status(scope_paths=[<exact verification file(s) or owning directory>])`" in validator
    assert "RECOMMENDED_ACTION" not in validator


def test_worker_playbooks_do_not_mention_submitters_or_action_routing() -> None:
    developer = _read(_BACKEND_ROOT / "src/skills/bundled/content/team-developer-playbook/SKILL.md")
    validator = _read(_BACKEND_ROOT / "src/skills/bundled/content/team-validator-playbook/SKILL.md")
    sweevo = _read(_SWEEVO_CONTEXT)

    for content in (developer, validator, sweevo):
        assert "submit_summary" not in content
        assert "submit_replan" not in content
        assert "RECOMMENDED_ACTION" not in content


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
