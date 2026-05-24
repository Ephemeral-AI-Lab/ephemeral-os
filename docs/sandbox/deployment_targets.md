# Sandbox Overlay Deployment Targets

The required mount syscalls and private user/mount namespaces are runtime preconditions
for sandbox startup. No copy-backed fallback or rollout escape hatch remains.

Kernel verification rule: every Linux deployment target must report kernel
`>= 5.6` and must pass `python scripts/verify_overlay_preconditions.py` inside
the same runtime image or runner that starts the sandbox daemon. The script
exits non-zero when `fsopen`/`fsconfig`/`fsmount`, private namespaces, or
`unshare` are unavailable.

| Target | Kernel-version verification | Required preflight | Phase 1 status |
| --- | --- | --- | --- |
| Local Docker/SWE-EVO runtime image | Run `uname -r` inside the selected sandbox image; accept only Linux `>= 5.6`. | `python scripts/verify_overlay_preconditions.py` inside the selected image before live scenario execution. | Verified as a required image gate; the sandbox refuses startup if the gate fails. |
| CI Linux runners | Run `uname -r` on the `ubuntu-latest` runner; accept only Linux `>= 5.6`. | `python scripts/verify_overlay_preconditions.py` before sandbox tests. | Verified as a required runner gate; no fallback strategy remains in code. |
| Staging sandbox image | Run `uname -r` during staging image promotion; accept only Linux `>= 5.6`. | `python scripts/verify_overlay_preconditions.py` during promotion. | Verified as a required promotion gate; startup fails closed if the gate fails. |
| Production sandbox image | Run `uname -r` during production image promotion; accept only Linux `>= 5.6`. | `python scripts/verify_overlay_preconditions.py` during promotion. | Verified as a required promotion gate; startup fails closed by default. |
