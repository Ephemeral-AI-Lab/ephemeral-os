"""C1: ``IsolatedWorkspaceHandle`` is a distinct shape from OCC handles.

The point of the isolated workspace is that its exit path is structurally
different from OCC publish. If ``IsolatedWorkspaceHandle`` subclassed
``OperationOverlayHandle`` or exposed a ``publish_*`` callable, an
"innocent" cleanup refactor could call it from the wrong path.

This test asserts the negative property: no shared MRO entry, no publish-
shaped attribute.
"""

from __future__ import annotations

from sandbox.isolated_workspace.manager import IsolatedWorkspaceHandle


def test_handle_is_not_a_subclass_of_operation_overlay_handle() -> None:
    """The MRO must contain no class whose name suggests OCC parentage.

    We inspect class names rather than importing
    ``OperationOverlayHandle`` directly — importing OCC from a pre-flight test
    would defeat the import-graph fence (Tier 0
    ``test_isolated_workspace_ops_transitive_imports_exclude_occ``). Name-based
    checks are sufficient because the property under test is the *shape*, not
    a specific class identity.
    """
    forbidden_mro_names = {"OperationOverlayHandle", "OverlayHandle"}
    mro_names = {cls.__name__ for cls in IsolatedWorkspaceHandle.__mro__}
    overlap = mro_names & forbidden_mro_names
    assert not overlap, (
        f"IsolatedWorkspaceHandle MRO must not include any OCC handle: {overlap}"
    )


def test_handle_has_no_publish_attribute() -> None:
    """``publish_*`` callables are the OCC commit surface and must stay off."""
    forbidden = sorted(
        name
        for name in dir(IsolatedWorkspaceHandle)
        if name.startswith("publish")
    )
    assert forbidden == [], (
        "IsolatedWorkspaceHandle must not expose any publish_* attribute "
        f"(it would re-create the OCC commit seam): {forbidden}"
    )


def test_handle_class_does_not_inherit_publish_via_instance() -> None:
    """Same check via dataclass-style instance attribute walk."""
    # We can't construct an IsolatedWorkspaceHandle here without a full
    # manager fixture, but a class-level attribute walk catches both class
    # and instance descriptors (dataclass fields).
    annotations = getattr(IsolatedWorkspaceHandle, "__annotations__", {})
    forbidden = sorted(name for name in annotations if name.startswith("publish"))
    assert forbidden == [], (
        f"IsolatedWorkspaceHandle declares forbidden publish-shaped fields: {forbidden}"
    )
