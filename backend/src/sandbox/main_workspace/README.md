main_workspace is the persistent workspace identity: the base repository plus
LayerStack snapshots, written only through OCC.

Implementation remains in `sandbox/layer_stack/` and `sandbox/occ/`. This
package is a thin facade so the three workspace concepts have sibling package
anchors without forcing churn through existing callers.
