# Sandbox Overlay Deployment Targets

Phase 1 makes the new mount API and private user/mount namespaces the default
runtime preconditions for sandbox startup. The rollout escape hatch is
`EOS_REQUIRE_NEW_MOUNT_API=0`; it should only be used while reverting or
repairing a deployment image.

Kernel verification rule: every Linux deployment target must report kernel
`>= 5.6` and must pass `python scripts/verify_overlay_preconditions.py` inside
the same runtime image or runner that starts the sandbox daemon. The script
exits non-zero when `fsopen`/`fsconfig`/`fsmount`, private namespaces, or
`unshare` are unavailable.

| Target | Kernel-version verification | Required preflight | Phase 1 status |
| --- | --- | --- | --- |
| Local Docker/SWE-EVO runtime image | Run `uname -r` inside the selected sandbox image; accept only Linux `>= 5.6`. | `python scripts/verify_overlay_preconditions.py` inside the selected image before live scenario execution. | Verified as a required image gate; the sandbox refuses startup if the gate fails. |
| CI Linux runners | Run `uname -r` on the `ubuntu-latest` runner; accept only Linux `>= 5.6`. | `python scripts/verify_overlay_preconditions.py` before sandbox tests. | Verified as a required runner gate; no fallback strategy remains in code. |
| Staging sandbox image | Run `uname -r` during staging image promotion; accept only Linux `>= 5.6`. | `python scripts/verify_overlay_preconditions.py` during promotion. | Verified as a required promotion gate; use `EOS_REQUIRE_NEW_MOUNT_API=0` only for rollback. |
| Production sandbox image | Run `uname -r` during production image promotion; accept only Linux `>= 5.6`. | `python scripts/verify_overlay_preconditions.py` during promotion. | Verified as a required promotion gate; startup fails closed by default. |
