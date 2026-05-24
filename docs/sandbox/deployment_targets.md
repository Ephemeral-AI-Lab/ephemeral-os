# Sandbox Overlay Deployment Targets

Phase 1 makes the new mount API and private user/mount namespaces the default
runtime preconditions for sandbox startup. The rollout escape hatch is
`EOS_REQUIRE_NEW_MOUNT_API=0`; it should only be used while reverting or
repairing a deployment image.

Known targets to verify before deployment:

| Target | Required check | Status |
| --- | --- | --- |
| Local Docker/SWE-EVO runtime image | `python scripts/verify_overlay_preconditions.py` inside the runtime image | Pending live image check |
| CI Linux runners | `python scripts/verify_overlay_preconditions.py` before sandbox tests | Pending CI wiring |
| Staging sandbox image | `python scripts/verify_overlay_preconditions.py` during image promotion | Pending staging check |
| Production sandbox image | `python scripts/verify_overlay_preconditions.py` during image promotion | Pending production check |

The script exits non-zero when `fsopen`/`fsconfig`/`fsmount` or private
namespaces are unavailable.
