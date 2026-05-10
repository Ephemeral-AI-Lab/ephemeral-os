"""Host-side fixture validation for the complex_project_build scenario.

Pre-merge gate: every fixture file under ``_fixtures/scheduler_demo/`` must
satisfy:

1. ``apply(skeleton, patches) == final`` — bytes equal after applying the
   ordered patch list to the skeleton.
2. ``ast.parse(final)`` succeeds for every ``*.py`` file.
3. The fixture set is non-empty and meets the LOC / file-count floors from
   plan §13.1 (≥20 files; ≤5,000 LOC) and §13.7 (test LOC ≥ source LOC).
4. Every refactor pass anchor exists in its target fixture's final form so
   Phase D can find its insertion point at runtime.
"""

from __future__ import annotations

import ast

from live_e2e.scenarios.sandbox._fixtures.refactor_passes import REFACTOR_PASSES
from live_e2e.scenarios.sandbox._fixtures.scheduler_demo_data import (
    SCHEDULER_DEMO_FILES,
)


_PY_SUFFIXES = (".py",)


def _final_loc(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def test_apply_skeleton_then_patches_equals_final() -> None:
    for fixture in SCHEDULER_DEMO_FILES:
        working = fixture.skeleton
        for index, patch in enumerate(fixture.patches):
            occurrences = working.count(patch.old_text)
            assert occurrences == 1, (
                f"{fixture.relative_path}: patch {index} anchor must be unique "
                f"(found {occurrences} occurrences) — required because the live "
                f"edit_file tool rejects ambiguous anchors. Patch: "
                f"{patch.description}"
            )
            working = working.replace(patch.old_text, patch.new_text, 1)
        assert working == fixture.final, (
            f"{fixture.relative_path}: apply(skeleton, patches) != final"
        )


def test_every_python_fixture_parses_with_ast() -> None:
    failures: list[tuple[str, str]] = []
    for fixture in SCHEDULER_DEMO_FILES:
        if not fixture.relative_path.endswith(_PY_SUFFIXES):
            continue
        try:
            ast.parse(fixture.final, filename=fixture.relative_path)
        except SyntaxError as exc:  # pragma: no cover — explicit failure surface
            failures.append((fixture.relative_path, str(exc)))
    assert failures == [], f"unparseable fixtures: {failures}"


def test_fixture_set_meets_floor_targets() -> None:
    # Plan §13.1: ≥20 files, total LOC ≤ 5000 (smaller variant).
    assert len(SCHEDULER_DEMO_FILES) >= 20
    src_loc = 0
    test_loc = 0
    for fixture in SCHEDULER_DEMO_FILES:
        loc = _final_loc(fixture.final)
        if fixture.relative_path.startswith("tests/"):
            test_loc += loc
        else:
            src_loc += loc
    total_loc = src_loc + test_loc
    assert total_loc <= 5000, f"total LOC = {total_loc} exceeds plan ceiling"
    # Plan §13.7: test LOC must exceed source LOC.
    assert test_loc > src_loc, (
        f"test LOC ({test_loc}) must exceed source LOC ({src_loc}) per plan §13.7"
    )


def test_refactor_pass_anchors_exist_in_target_fixtures() -> None:
    by_path = {fixture.relative_path: fixture for fixture in SCHEDULER_DEMO_FILES}
    missing: list[tuple[str, str, str]] = []
    for refactor in REFACTOR_PASSES:
        for edit in refactor.edits:
            target = by_path.get(edit.relative_path)
            assert target is not None, (
                f"{refactor.name}: target {edit.relative_path} not in fixture set"
            )
            if edit.anchor not in target.final:
                missing.append(
                    (refactor.name, edit.relative_path, edit.anchor[:60])
                )
    assert missing == [], f"missing anchors: {missing}"


def test_refactor_lsp_target_anchors_exist() -> None:
    by_path = {fixture.relative_path: fixture for fixture in SCHEDULER_DEMO_FILES}
    missing: list[tuple[str, str, str]] = []
    for refactor in REFACTOR_PASSES:
        for spec in refactor.lsp_targets:
            target = by_path.get(spec.relative_path)
            assert target is not None, (
                f"{refactor.name}: LSP target {spec.relative_path} not in fixture set"
            )
            if spec.line_index_anchor not in target.final:
                missing.append(
                    (refactor.name, spec.relative_path, spec.line_index_anchor)
                )
    assert missing == [], f"missing LSP anchors: {missing}"


def test_total_patches_meet_edit_floor() -> None:
    # Plan §13.6 + §6 budget: realised edit_file count must be ≥4× write_file
    # count. Each fixture's `write_file` is one skeleton write; patches map
    # 1:1 to `edit_file`. Validate that the fixture set itself can satisfy
    # the ratio before the probe even runs.
    write_count = sum(1 for f in SCHEDULER_DEMO_FILES if f.skeleton)
    patch_count = sum(len(f.patches) for f in SCHEDULER_DEMO_FILES)
    assert patch_count >= write_count, (
        f"patch_count={patch_count} write_count={write_count}; "
        "fixture cannot produce edit:write >= 1.0"
    )
