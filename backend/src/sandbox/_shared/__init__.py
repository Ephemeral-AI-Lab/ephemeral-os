"""Cross-subsystem shared kernel for the sandbox package.

Contains types and infrastructure used by `sandbox.daemon`,
`sandbox.ephemeral_workspace`, `sandbox.isolated_workspace`,
`sandbox.layer_stack`, `sandbox.occ`, and host-side audit/api code.

The leading underscore signals package-private — importers outside `sandbox.*`
should not reach into this subpackage.
"""
