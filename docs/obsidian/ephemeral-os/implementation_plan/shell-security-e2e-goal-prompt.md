/goal Complete and verify the shell-exec security e2e suite defined in `cli-operation-e2e-live-test/runtime/shell_security/test_cases.md` (40 cases: 10 easy, 15 medium, 15 hard). Turn the catalog from a spec into runnable, green pytest coverage.

DELIVERABLES
1. Implement every SS-E01..E10, SS-M01..M15, SS-H01..H15 case as runnable pytest, split by tier into `test_shell_security_easy.py`, `_medium.py`, `_hard.py`, each marked `@pytest.mark.{easy,medium,hard}`. Fold the existing `test_shell_security.py` CS-01..CS-06 into the matching SS cases so there is no duplicate coverage. Reuse the `helpers.py` fixtures/probe.
2. Extend `helpers.py::PROBE_SOURCE` additively for every case marked † in the catalog: `unshare(CLONE_NEWUSER)`, raw `clone(flags)` (NEW* vs plain SIGCHLD), `setns`, `open_by_handle_at`, new mount API (`fsopen/fsconfig/fsmount/fspick/move_mount/open_tree`), `umount2/pivot_root/mount_setattr`, `init_module/finit_module`, `io_uring_enter/register`, `userfaultfd/perf_event_open/fanotify_init`, `swapon/swapoff/quotactl/reboot`, block-device + regular `mknod`, and `ptrace(ATTACH)` of a forked child. Add each new key to `DENIED_SYSCALLS`/`ALLOWED_SYSCALLS` so the set-loop cases (SS-M01/M02) pick them up. Keep it one file, no crates, async-signal-safe, with per-arch `nr` numbers for x86_64 AND aarch64.
3. SS-H04 (X32 reject, x86_64 only): a caller that issues a syscall with the X32 bit (`nr|0x40000000`); assert the child is killed by the filter; `pytest.mark.skipif` on aarch64.
4. SS-H11/H12: exec a setuid-root helper and a `setcap cap_sys_admin+ep` binary; assert no privilege is gained across `execve` (NoNewPrivs). Skip cleanly if the image lacks the tool.

HARD CONSTRAINTS
- Drive sandboxes ONLY through `sandbox-runtime-cli` and `sandbox-manager-cli`. Sandbox lifecycle (create/destroy/inspect/list) via `manager.management.helpers` → sandbox-manager-cli; commands/workspace-sessions/files (exec_command, read_command_lines, write_command_stdin, file ops) via `core.cli.runtime` → sandbox-runtime-cli. NO `docker exec`, NO direct daemon RPC/socket, NO gateway HTTP, NO shelling around the CLIs. Verify the helpers actually route through these two CLIs; if any path bypasses them, fix it.
- Image: `ubuntu:24.04`. Pin it explicitly (fixture / `E2E_IMAGE`), and UPDATE THE SPEC — both `test_cases.md` and `test_spec.md` — to state ubuntu:24.04 as the required image. Every apt/util-linux step assumes ubuntu24.
- Additive, localized edits; do not revert others' in-flight refactor of the policy types.

VERIFICATION (all must pass before "done")
- The Rust policy is mid-refactor (collapsing to a compiled-in `enforce` constant). First get the workspace building — `cargo build`, `cargo clippy --all-targets`, and `cargo clippy --target aarch64-unknown-linux-musl -p sandbox-runtime-namespace-process --all-targets` for the Linux-gated `shell_security` module — and confirm the enforce policy is actually installed at the `shell_exec` child pre_exec.
- Rebuild the in-container daemon: `bin/start-sandbox-docker-gateway --rebuild-binary`, then `E2E_REBUILD_BINARY=1 pytest runtime/shell_security -v`. Run each tier (`-m easy|medium|hard`) and the whole suite.
- Sign-off matrix: full suite green on aarch64 AND x86_64 native Docker (no VM/QEMU/emulated path counts); SS-H04 exercises the X32 reject on x86_64.
- Honesty: any case not yet landed must `xfail`/`skip` with a logged reason — never silently pass. Egress-dependent installs (SS-M09) skip when the profile has no egress; `fchmodat2` is allowed on `OK` or `ENOSYS`.

DONE = all 40 cases implemented, tiered, and green on both arches, driven only through sandbox-runtime-cli and sandbox-manager-cli, on ubuntu:24.04, with the probe extended and test_cases.md + test_spec.md updated.
