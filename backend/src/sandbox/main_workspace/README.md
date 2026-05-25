main_workspace is the persistent workspace identity: the base repository plus
LayerStack snapshots, written only through OCC.

Implementation remains in `sandbox/layer_stack/` and `sandbox/occ/`. This
package is only the ownership anchor for the durable workspace concept; import
concrete storage and OCC contracts from their owning modules.
