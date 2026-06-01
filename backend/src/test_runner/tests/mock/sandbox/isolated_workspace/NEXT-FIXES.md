# Next-fixes — isolated_workspace live e2e suite

Companion to [DEFERRED-WORK.md](./DEFERRED-WORK.md). That doc captures
what was resolved in the 2026-05-23 session and what was deferred
because of environmental flakes; this doc lays out **how to resolve the
remaining items** with concrete recipes.

Each item is independent and shippable on its own.

---

## 1. Pre-cache iproute2 + nftables .deb closure (HIGHEST PRIORITY)

**Why:** The session-scoped `iws_sandbox` fixture runs `apt-get update && apt-get install -y iproute2 nftables` on every fresh sweevo container. Ubuntu's apt mirror returns 502 from `archive.ubuntu.com` / `security.ubuntu.com` over Docker Desktop's NAT often enough (≈30% of runs in May 2026) that Tier 3 (network) and Tier 6 (concurrency) — both of which require `ip` and `nft` — are effectively un-runnable on a flaky network day.

**Resolution recipe (offline .deb closure shipped from the host):**

1. **One-time cache build script** at `backend/scripts/cache_iws_apt_debs.sh`:

   ```bash
   #!/usr/bin/env bash
   # Downloads the iproute2 + nftables dep closure for the dask sweevo image's
   # ubuntu:22.04 base. Run once per host; the cache is committed (or stored
   # in a registry) so test fixtures never depend on the live apt mirror.
   set -euo pipefail
   CACHE_DIR="${1:-backend/tests/_assets/iws_apt_cache/jammy-amd64}"
   mkdir -p "$CACHE_DIR"

   # Use the SAME ubuntu base the sweevo image derives from, so the .deb
   # versions match what dpkg expects (libc6, libcap2, etc.).
   docker run --rm \
     -v "$PWD/$CACHE_DIR:/cache" \
     -w /cache \
     ubuntu:22.04 \
     bash -c "\
       apt-get update -qq && \
       apt-get install -y -qq --download-only --reinstall \
         iproute2 nftables \
         libmnl0 libnftnl11 libxtables12 libcap2 libbpf0 \
         libbsd0 libmd0 libnftables1 libjansson4 libelf1 && \
       cp /var/cache/apt/archives/*.deb /cache/"
   ls -la "$CACHE_DIR"
   ```

   Run once: `bash backend/scripts/cache_iws_apt_debs.sh`. This produces
   `backend/tests/_assets/iws_apt_cache/jammy-amd64/*.deb`. The cache is
   small (~5 MB) — fine to commit; gitignore the parent and rebuild on a
   sweevo-image bump.

2. **Conftest fixture change** in
   `backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/conftest.py`:

   Replace the current `apt-get update + install` raw_exec with a
   `docker cp` of the cached debs + `dpkg -i`:

   ```python
   # Before the existing apt-get block:
   from pathlib import Path
   from sandbox.provider.registry import get_adapter

   cache_dir = Path(__file__).resolve().parents[6] / "tests" / "_assets" / "iws_apt_cache" / "jammy-amd64"
   if cache_dir.is_dir():
       debs = sorted(cache_dir.glob("*.deb"))
       if debs:
           # docker cp the closure into /tmp/iws-debs/ then dpkg -i it.
           adapter = get_adapter(sandbox_id)
           container_name = adapter.container_name_for(sandbox_id)  # may need a small helper
           import subprocess
           subprocess.run(
               ["docker", "cp", str(cache_dir), f"{container_name}:/tmp/iws-debs"],
               check=True, capture_output=True,
           )
           with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
               await raw_exec(
                   sandbox_id,
                   "dpkg -i /tmp/iws-debs/*.deb 2>&1 | tail -3 || true",
                   cwd="/", timeout=60,
               )
   # Keep the existing apt-get fallback for non-cached envs.
   ```

3. **Verification:** after the change, `command -v ip` and `command -v nft`
   succeed in <2 s on a fresh sweevo container, even with `apt-get update`
   failing.

**Trade-offs:**
- Pro: zero network dependency; survives any apt mirror outage; idempotent.
- Con: cache must be rebuilt when the sweevo base ubuntu version changes
  (jammy → noble would require a fresh cache build).
- Con: needs a docker-CLI shell-out from the test process. The adapter
  doesn't currently expose `docker cp`; either add a `copy_file` method to
  the docker adapter or use raw `subprocess.run(["docker", "cp", ...])`.

**Alternative if we'd rather not commit binary debs:**
Bake the packages into a custom sweevo image variant
(`xingyaoww/sweb.eval.x86_64.dask_s_dask-10042-iws`) by running
`apt-get install iproute2 nftables` in a docker build step, then point
`EOS_SWEEVO_INSTANCE` at the new tag. Cleaner architecturally, but
requires image registry write access.

---

## 2. Validate Tiers 2-7 end-to-end (BLOCKED ON #1)

Once `ip` + `nft` are reliable in the fixture, the following tiers
should be validated:

| Tier | Tests | Expected outcome |
|---|---:|---|
| 2 — isolation | 5 | All PASS (overlay/upperdir behavior, no kernel-edge surface) |
| 3 — network | 15 | All PASS once `nft` is available |
| 4 — failure_modes | 8 | All PASS (these test rollback paths, mostly daemon-internal) |
| 5 — resource_controls | 7 | All PASS (cgroup writes work after the remount fix from session 2) |
| 6 — concurrency | 11 | All PASS (zombie fix from session 2 removes the historical degradation) |
| 7 — gc_and_persistence | 14 | All PASS (no new kernel surfaces beyond what happy_path covers) |

**Run command** (after fix #1 lands):

```bash
EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
EOS_SANDBOX_PROVIDER=docker \
EOS_ISOLATED_WORKSPACE_ENABLED=true \
EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED=true \
EPHEMERALOS_DATABASE_URL="sqlite:///./.ephemeralos/ephemeralos.db" \
  .venv/bin/pytest \
    backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/ \
    -m "not live_e2e_soak" \
    -v --tb=short
```

Budget: ~12-15 min wall time on a healthy macOS Docker Desktop. If any
test fails, the failure is now real (no longer hidden by zombies); fix
on the merits.

---

## 3. Backstop test — root-cause the `raw_exec` heredoc helper failure

**Why:** `test_mount_overlay_backstop` is currently `xfail(strict=False)`.
The 4 daemon-path happy_path tests cover the surface, but the diagnostic
signal is lost. Re-attaching it would let a future overlay regression
surface here first instead of in the noisier daemon path.

**Resolution recipe:**

1. Reproduce by running the test in isolation with verbose stderr capture:
   ```bash
   EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
   EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED=true \
   EPHEMERALOS_DATABASE_URL="sqlite:///./.ephemeralos/ephemeralos.db" \
     .venv/bin/pytest \
       backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/happy_path/test_mount_overlay_backstop.py \
       -p no:randomly --runxfail -v --tb=long
   ```

2. The failure message will include `helper_stderr` from the `mount_overlay`
   subprocess. Likely candidates:
   - `setns_overlay_mount.py` runs `python3 -m sandbox.overlay.kernel_mount`
     but the heredoc script's `sys.executable` is `python3.10` (system) while
     the daemon's is `python3.10` (also system). Should match — eliminate this
     possibility first by printing `sys.executable` from inside the script.
   - PYTHONPATH propagation: the helper's subprocess inherits PYTHONPATH from
     parent (`/eos/daemon`). Confirm by printing `sys.path` in
     the helper.
   - Capability inheritance: the heredoc shell starts under `bash -lc`. Run
     `cat /proc/self/status | grep ^Cap` to verify CAP_SYS_ADMIN survived.

3. Once root-caused, remove the `xfail` marker and add a one-line comment
   explaining the actual fix.

---

## 4. Tier 9 latency budget — refresh `_data/latency_budget.json`

**Why:** Tier 9 tests `assert_ratio_to_baseline` works today (session-relative),
but the absolute-p95 half silently passes until `_data/latency_budget.json`
is committed.

**Resolution recipe:** See PLAN §17 governance. Summary:

1. On the reference CI host, run:
   ```bash
   EOS_CI_REFERENCE_HOST=true \
   EOS_ISOLATED_WORKSPACE_BASELINE_RUNS=100 \
     .venv/bin/pytest \
       backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/performance/ \
       -v
   ```

2. Dump the median + p95 + p99 from the captured audit JSONL into the
   schema documented in RUNNING-LIVE-TESTS.md §6.

3. Commit `_data/latency_budget.json` with the reference host's fingerprint.

Owner-rotation + staleness CI cron is PR 7 per PLAN §17 — separate ticket.

---

## 5. Kernel-side proc-mount permission (UPSTREAM ASK)

**Why:** The `unshare --mount-proc` EPERM on Docker Desktop's LinuxKit kernel
is what forced session 2's workaround (rbind `/proc` in `ns_holder.py`). The
workaround is correct but leaks host process visibility into the iws's
new pid_ns — an isolation regression even if no current test exercises it.

**Resolution recipe (long-term):**

1. File an upstream issue against LinuxKit / Docker Desktop kernel:
   "procfs mount in non-init user_ns returns EPERM even with full CapEff in
   the new ns." Reference kernel 6.10.14-linuxkit; reproduce with
   `unshare -Urmpf --mount-proc -- true` in a `--cap-add=SYS_ADMIN
   --security-opt=seccomp=unconfined --security-opt=apparmor=unconfined`
   container.

2. If the upstream fix lands, revert commit `190ce851e` (drop the rbind
   and re-add `--mount-proc` to the unshare invocation). Run the full iws
   suite to confirm no regression.

3. Until upstream lands, the rbind workaround is the right call —
   document the leaked-pid-visibility caveat in the iws PRD.

---

## 6. Cross-references

- [DEFERRED-WORK.md](./DEFERRED-WORK.md) — session 2 resolution log + env blockers.
- [RUNNING-LIVE-TESTS.md](./RUNNING-LIVE-TESTS.md) — environment setup.
- [PLAN.md](./PLAN.md) — original phase plan.
- [NEXT-AGENT-GUIDE.md](./NEXT-AGENT-GUIDE.md) — broader project context.
