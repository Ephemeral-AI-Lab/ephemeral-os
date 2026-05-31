# Review: `sandbox-daemon-go-vs-rust-comparison.md`

Reviewed document: `docs/plans/sandbox-daemon-go-vs-rust-comparison.md` (verdict: Rust for both daemon and ns-runner).

## 1. Review verdict (TL;DR)

**The Rust-for-both conclusion survives review — but for partly different reasons than the doc gives, and one of its load-bearing arguments must be demoted.**

What the user actually weighted: image-agnostic *package + build* (criterion 2) and *small/fast upload* (criterion 4) are top; performance (criterion 3) is explicitly secondary. The doc's conclusion is correct against those weights, but the *reasoning* needs correction:

- **Corrected reason A — smallest self-contained artifact.** A stripped static-musl Rust binary undercuts Go's irreducible runtime floor (~1–2 MB) at the low-dependency end. This is the genuine Rust-over-Go edge, though it narrows toward parity once `tokio` + `serde` derive code grow (Rust ~1–4 MB vs Go ~1.5–3 MB). This is the real discriminator on criterion 2/4, not perf.
- **Corrected reason B — lowest core-dependency surface.** Both compiled languages eliminate the in-image Python interpreter *and* (if syscalls are used) the in-image `unshare`/`tar`/`gzip`/`base64` tools. Rust's dependency graph (`serde`, `rustix`/`nix`, `libc`) is minimal.
- **Demote the namespace argument.** The doc's strongest stated Rust advantage — that Rust is uniquely suited to the namespace/mount/exec boundary — is **refuted as a capability claim**. Go creates user+mount namespaces and mounts overlayfs entirely via syscalls (`os/exec` `SysProcAttr{Cloneflags, Unshareflags, UidMappings, GidMappings}` + `syscall.Mount`) with **no in-image `unshare`/`mount` binary** — strictly more self-contained than today's Python path, which actually shells out to the real `unshare` binary (`namespace_runner.py:237-244`, `_unshare_path()` = `shutil.which('unshare')`). Rust's edge here is **ergonomic, not a capability gap**: a single-threaded Rust binary runs `unshare → uid_map → mount → exec` linearly in one process, whereas Go's post-fork child cannot perform the overlay-mount setup in-place (runtime restriction) and must re-exec `/proc/self/exe` with state passed over a pipe. Real, but a maintainability point, not "Rust uniquely can."

**Note on "no per-call interpreter startup":** this is a Python-vs-compiled win shared *equally* by Go and Rust (Go 0.41 ms ≈ Rust 0.51 ms vs CPython 25.84 ms), and it belongs to the **namespace runner**, not the long-lived daemon (which starts once). It justifies *leaving Python*; it does not adjudicate *Rust over Go*.

Net: keep Rust-for-both. Rewrite the justification around artifact size + dependency surface, demote the namespace-capability claim to an ergonomics footnote, and add the upload-mechanism caveat below (which dominates criterion 4 regardless of language).

## 2. What the original doc gets right

- **Performance/RPC is not the deciding factor for the daemon.** Correct: the daemon is long-lived (started once), uses AF_UNIX + loopback TCP + newline-delimited JSON, and per-envelope dispatch is ~1–3 ms. Language choice is not throughput-bound here.
- **Image-agnostic packaging is the real axis.** Correct framing: the rewrite's payoff is a packaging property (no Python/shell/tar in the image), not a speed property.
- **One binary with subcommands (`eosd daemon` / `eosd ns-runner`) for the first migration.** Reasonable; keeps one artifact per arch with a clean internal boundary and an easy later split.
- **Conservative dependency set** (`serde`, `serde_json`, `rustix`/`nix`, `libc` for gaps; `tokio` only if justified) is sound and directly serves criterion 2.
- **Both languages need one artifact per CPU arch; static linking ≠ cross-CPU.** True and worth keeping.
- **Migration shape is mostly sound**, and Step 3 already *names* the native upload requirement (see §3 — it just doesn't commit to sequencing it first).

## 3. What the original doc gets wrong or omits

1. **No Python comparison column (anywhere).** All three tables (Packaging, Daemon, Namespace Runner) compare only Rust vs Go. Python is treated as "the current problem," never as a quantified baseline, so the doc cannot show whether Go/Rust actually *improve* criteria 2 and 4. **Fix:** add a Python column with measured baselines (§4).
2. **Size claims are unquantified and partly wrong-signed.** "Typical binary size: usually smaller [Rust] / usually larger [Go]" has no numbers, and — critically — *both* compiled binaries are **LARGER on the wire than the status quo**. The current bundle is **178.7 KiB gzipped** of Python *source* (183,034 bytes, 154 files, 759.4 KiB uncompressed) and **excludes the interpreter**. A realistic self-contained binary is Rust ~1–4 MB / Go ~1.5–3 MB stripped static-musl — roughly **5–15× larger** than today's upload. The size *win* exists only versus a **frozen** Python distribution (PyInstaller ~15 MB floor; python-build-standalone ~33 MB interpreter), never versus the source bundle. (Refutes the doc's implicit "smaller package" promise.)
3. **The criterion-2-vs-criterion-4 tension is unstated.** Image-agnostic (2) **forces** self-containment, and self-containment is exactly what makes the artifact larger than a source bundle (4). These two top-weighted criteria **pull against each other**; the doc presents them as aligned wins.
4. **The upload mechanism is ignored, and it gates criterion 4.** Today's upload is base64-encoded 32 KiB chunks over `provider.exec` (docker exec): `chunked_upload.py` `DEFAULT_CHUNK_SIZE = 32*1024`; finalize is `cd … && tar -xzf … && rm … && printf …` (`runtime_bundle.py:355-360`). Binary size maps **linearly** to exec round-trips under this path. The doc's Step 3 names "provider file upload" but the `ProviderAdapter` protocol currently exposes **only `exec()`** — no `put_archive`/`copy_to` (`provider/protocol.py`). So the native-upload prerequisite is **aspirational, not implemented**, and without it the migration *regresses* upload latency (see §5.2).
5. **The namespace-runner Rust advantage is over-claimed** (see §1; Claim 1 REFUTED, Claim 7 CONFIRMED). The table's "Namespace/thread safety: direct control [Rust] / requires care with `runtime.LockOSThread()` [Go]" is misleading: the current code spawns the runner as a **fresh subprocess** (`unshare -Urm python -m …`), so the in-place self-namespacing constraint that bites Go's multithreaded runtime **does not apply** to this pattern. Both languages avoid the thread-count rule because `fork`/`clone` produces a single-threaded child.
6. **Transport mechanism is misdescribed.** The doc says "the daemon mostly uses TCP for Docker transport." The code uses an **AF_UNIX socket** for local sandbox interaction (`daemon_client.py` `DAEMON_SOCKET_PATH`) with numeric-literal loopback TCP (`host="127.0.0.1"`) only for host-side pooling/remote. Numeric-literal `127.0.0.1` and AF_UNIX do **zero name resolution**, so transport correctness is libc/language-agnostic (no glibc-NSS/musl trap). Conclusion (RPC not the discriminator) is right; the mechanism claim is wrong.
7. **The fresh-namespace vs existing-namespace duality is omitted.** The runner has two paths: fresh-namespace (`unshare` subprocess per call, `namespace_runner.py:237-244`) and existing-namespace (`setns` on pre-opened FDs for the isolated-workspace feature, `isolated_workspace/scripts/setns_exec.py` + `setns_overlay_mount.py` + `_control_plane/namespace_runtime.py`). Overlay mounts use raw `fsopen/fsconfig/fsmount/move_mount` syscalls (`kernel_mount.py`), not the `mount` binary. A port must preserve **both** paths or break isolated-workspace.
8. **Per-call cost is unquantified and the end-to-end claim needs a guardrail.** The Python-runtime overhead is ~25 ms interpreter + ~36 ms import = ~61 ms **measured on the macOS host as a proxy** (likely lower in-sandbox Linux, ~10–15 ms interpreter). A compiled ns-runner cuts the *runtime portion* ~50–120× (to <1 ms exec). But end-to-end per-call latency is bounded below by the **unmeasured, language-independent `unshare`/overlay-mount/`execve`/cleanup syscall floor**, so "~10× faster per call" is reliable for the runtime portion, **not** verified for total call time until that floor is benchmarked.

## 4. Three-language scorecard

All numbers from the measured baseline and the cited research; compiled-binary sizes are stripped static-musl (Rust) / `CGO_ENABLED=0` (Go).

| Criterion | Python (baseline / status quo) | Go | Rust |
| --- | --- | --- | --- |
| **Image-agnostic package** | No — needs in-image Python ≥3.10 + `sh`, `tar`, `gzip`, `base64`, `printf`; spawns external `unshare` | Yes — single static ELF, no libc with `CGO_ENABLED=0` | Yes — single static ELF, `*-linux-musl`, no libc |
| **Image-agnostic BUILD (cross-arch from one host)** | N/A interpreted (no compile), but freezing fails this (see §5.4) | **Trivial**: `GOOS=linux GOARCH={amd64,arm64} CGO_ENABLED=0 go build`, no C toolchain | **Easy**: `cross`/`cargo-zigbuild` to `{x86_64,aarch64}-unknown-linux-musl`; zero-C deps in serde/rustix/nix/tokio make it the easy case |
| **Upload size (vs 178.7 KiB gz baseline)** | **178.7 KiB gz** (183,034 B; source, no interpreter) | ~1.5–3 MB stripped (runtime floor ~1–2 MB) → **~8–16× larger** | ~1–4 MB stripped (serde[+tokio]); floor ~165–415 KiB → **~5–15× larger** |
| **Per-call startup (fresh process)** | ~25.84 ms (host proxy; ~61 ms incl. import) | **0.41 ms** (~63× faster than CPython) | **0.51 ms** (~51× faster) — Go≈Rust, <<1 ms apart |
| **Daemon RSS (idle, predicted)** | tens of MB (interpreter + imported sandbox modules) | ~5–15 MB (embedded runtime + GC; sub-ms STW pauses) | ~3–16 MB (no GC; allocator-dependent steady RSS) |
| **Namespace-syscall fit** | Spawns external `unshare` binary (least self-contained) | Full capability via `SysProcAttr` Cloneflags/Unshareflags + `syscall.Mount`; needs `/proc/self/exe` re-exec shim for in-namespace mount setup | Full capability; **linear single-process** `unshare→map→mount→exec` (ergonomic edge) |
| **Implementation effort** | N/A (status quo) | **Lower** — faster port, simpler concurrency, trivial cross-build | Higher — Rust expertise, musl/aarch64 setup, explicit async + `Arc<Mutex>` state ports |

## 5. Criterion-by-criterion analysis

### Criterion 2 (top): image/environment-agnostic package AND build

This is the criterion that *justifies the rewrite at all*. Python fails it structurally: the image must ship Python ≥3.10 (probed `python3.13…python3`, `daemon_client.py:36`), a POSIX shell for `launch_daemon.sh`, and `tar`/`gzip`/`base64`/`printf` for finalize — plus an external `unshare`. Both Go and Rust collapse this to a single static ELF per arch with **no in-image language runtime and no shell tools** (when file transfer moves off base64-over-exec; see Criterion 4).

**Cross-arch BUILD** (the "AND build" half, often skipped): both compiled languages build for `amd64`+`arm64` from one host. Go is trivial (`GOOS`/`GOARCH`, no C toolchain). Rust is the easy case here because the whole dependency set (`serde`, `serde_json`, `rustix`, `nix`, `tokio`, `mio`) is pure-Rust/zero-C, so `cross` or `cargo-zigbuild` to `*-linux-musl` works out of the box; the documented zigbuild header pain only hits C/C++ deps, which this set lacks.

**The criterion-2-vs-4 tension (state it explicitly).** Image-agnostic *forces* a self-contained static binary, and self-containment is *exactly* what makes the artifact larger than today's 178.7 KiB source bundle. So the two top-weighted criteria conflict: you cannot get both "no runtime in the image" and "smaller than the source upload" — you trade upload size for image-agnosticism. Go and Rust resolve it identically in kind (static binary), differing only in degree (Rust's floor is smaller).

**Keep-Python option (Criterion 2 is where it dies).** Freezing does not clear the bar:
- **PyInstaller / Nuitka / PyOxidizer cannot cross-compile** across OS or CPU arch — you must build natively per target (a per-OS/per-arch/per-oldest-glibc matrix, or QEMU emulation, which is not cross-build). PyOxidizer's own FAQ says cross-compile is "not yet," and it is effectively dormant. This **fails the build-from-one-host half of criterion 2 outright.**
- Frozen artifacts are also large: PyInstaller onefile floor ~15 MB (bare hello-world), Nuitka larger — ~80× the source bundle and ~5–10× a stripped Go/Rust binary, so they also lose criterion 4.
- `python-build-standalone` (pbs) is the *only* Python path that builds for any target from one host (prebuilt per-triple interpreters, incl. musl), but it is **not a freezer** — it ships a ~33 MB stripped interpreter floor (full stdlib). It is competitive on upload only if the interpreter is a cached/shared base layer and just thin app code is uploaded per change — which reintroduces the exact "image must carry a runtime" property the rewrite is trying to remove.

**Verdict on (2):** Go and Rust both win decisively over any Python option. Rust's only edge here is smaller artifact + smaller dependency surface; the build half slightly favors Go (no musl setup).

### Criterion 4 (top): small package for fast local→sandbox upload

**The "small package" claim is true only versus FROZEN Python, never versus the source bundle.** Today's upload is 178.7 KiB gzipped of source. A self-contained binary is larger (§4). So on the literal wire-size metric, the migration *regresses* unless the upload *mechanism* changes.

**Upload math under the current base64-over-exec path** (method: raw bytes × 4/3 base64 inflation ÷ 32 KiB chunk):

| Artifact | Raw size | Base64-encoded | 32 KiB exec round-trips |
| --- | --- | --- | --- |
| **Today (Python source, gz)** | 178.7 KiB | ~238 KiB | **~8** |
| Rust binary (1.5 MB) | 1.5 MB | ~2.0 MB | **~64** |
| Rust/Go binary (3 MB) | 3 MB | ~4.0 MB | **~128** |

So a realistic 1.5–3 MB binary needs **~64–128 exec round-trips, ~8–16× more than today** — a real upload-latency regression through the unchanged path. (Note: the original task framing's "~10 MB Go" figure is a stale pre-research placeholder; the verified equivalent-dependency Go daemon is ~1.5–3 MB, not 5–15 MB. Using the corrected number keeps the scorecard and this table consistent.)

**How a native provider upload API changes everything.** Replacing base64-over-`exec` with Docker's native file transfer (`container.put_archive()` / `docker cp`) collapses the transfer to a **single streamed put** — binary size stops mapping to round-trips, and the in-image `tar`/`gzip`/`base64` finalize dependency disappears (also serving criterion 2). But `ProviderAdapter` exposes **only `exec()`** today (`provider/protocol.py`); the Docker SDK's `container.put_archive()` is not wrapped. So this is a **prerequisite the doc must sequence first**, not a given.

**Verdict on (4):** No language wins criterion 4 against the source bundle on raw size; the real lever is the upload mechanism. Rust's smaller binary marginally softens the regression vs Go, but the dominant factor is base64-over-exec vs native upload. **This is the single highest-leverage change for criterion 4 and is language-independent.**

### Criterion 3 (secondary): performance — predicted Go/Rust improvement vs Python baseline

Performance is explicitly *not* top priority, so this is expressed as predictions from cited figures, not as a decision driver.

**Per-call (fresh-namespace tool call) — runtime-init portion only:**

| Phase | Python (measured, host proxy) | Go (predicted) | Rust (predicted) | Multiplier |
| --- | --- | --- | --- | --- |
| Interpreter/runtime init | ~25.84 ms (CPython3) | 0.41 ms (full exec) | 0.51 ms (full exec) | ~50–63× |
| Module import (`namespace_entrypoint`) | ~36 ms | 0 (compiled in) | 0 (compiled in) | — |
| **Python-runtime overhead total** | **~61 ms (host proxy)** | **<1 ms** | **<1 ms** | **~50–120×** |
| Shared `unshare`/mount/`execve`/cleanup syscall floor | unmeasured | unmeasured (same) | unmeasured (same) | 1× (language-independent) |

**Guardrail (do not overclaim):** the ~50–120× is for the *Python-runtime portion only*. End-to-end per-call latency is bounded below by the language-independent syscall floor, which has **not** been measured. The realized total speedup = 61 ms_saved / (syscall_floor + <1 ms exec). The macOS-host 61 ms is a proxy; in-sandbox Linux interpreter startup is often ~10–15 ms, so re-measure in-sandbox before quoting an end-to-end multiplier. **Go and Rust are within ~0.1 ms of each other on exec — performance does not separate them.**

**Daemon RSS (predicted, order-of-magnitude):** Python daemon carries interpreter + imported sandbox modules = tens of MB. Compiled daemon: Go ~5–15 MB (embedded runtime; worst-case STW GC pauses <100 µs, not a per-call concern), Rust ~3–16 MB (no GC; steady RSS allocator-dependent, mimalloc recommended for return-to-OS). Predicted idle-RSS improvement **~2–10× (up to ~20× if the Python baseline is import-heavy)**. The project's actual Python daemon RSS was **not measured** and is the biggest unknown — measure it before quoting a multiplier.

**Daemon RPC throughput:** ~1–3 ms/envelope, language-agnostic for this workload; not a discriminator (matches the doc).

**Verdict on (3):** Big Python→compiled win on per-call startup and RSS, **shared equally by Go and Rust** (so it argues for leaving Python, not for Rust over Go). End-to-end per-call and daemon-RSS multipliers need in-sandbox measurement before being quoted as facts.

### Criterion 1: detailed Python vs Go vs Rust comparison (daemon, ns-runner, packaging)

- **Packaging:** see §4 scorecard. Python = small source bundle but needs runtime + shell tools in-image (fails criterion 2). Rust = smallest self-contained binary + smallest dependency surface (wins 2, marginal on 4). Go = trivial cross-build, slightly larger floor.
- **Daemon:** long-lived, AF_UNIX + numeric-literal loopback TCP, newline-delimited JSON, asyncio per-connection coroutines. Porting burden is the **state-correctness machinery**, not throughput: `in_flight.py` (asyncio.Task registry + TTL reaper), `workspace_tool/dispatch.py` (per-agent `entry_lock` + inflight counter + drain event for isolated-workspace quiesce), `occ_runtime_services.py` / `layer_stack_runtime.py` (RLock-protected LRU caches), `audit_buffer.py` (3-lane eviction ring buffer). Python's GIL gives implicit synchronization on dict ops; Rust needs explicit `Arc<Mutex<T>>`, Go needs explicit channels/`sync.Mutex`. Both can match; this is the real effort sink (estimate ~1–2 weeks focused port + concurrency tests per subsystem), and the doc undersells it. A possible OCC/LayerStack **cache-lock contention** bottleneck (single `RLock` on a 256-entry cache) is unprofiled — a Rust `Mutex<HashMap>` could be faster (no GIL) or not; profile before/after.
- **Namespace runner:** see §1/§3 — Go is fully capable; Rust's edge is the linear single-process flow vs Go's `/proc/self/exe` re-exec shim. Both must preserve the fresh-namespace (`unshare` subprocess) and existing-namespace (`setns` on pre-opened FDs) paths and the raw-syscall overlay mount. The doc treats the runner as monolithic and one-path; it is neither.

## 6. Recommendation

**Choose Rust for both the daemon and namespace runner — confirmed, but for the corrected reasons:** smallest self-contained artifact (musl floor undercuts Go's ~1–2 MB runtime floor) and smallest core-dependency surface, with the namespace argument **demoted** from "uniquely capable" to "linear single-process ergonomics (no re-exec shim)." Do **not** justify Rust-over-Go by per-call startup or RSS — those are Python-vs-compiled wins Go shares equally.

**Where Go is a legitimate choice:** if implementation velocity and build simplicity outrank the last megabyte of artifact size. Go's equivalent daemon is ~1.5–3 MB (overlapping Rust's ~1–4 MB once `tokio`+`serde` are in), its cross-build needs no C toolchain or musl setup, and its service concurrency is simpler to port. Given the binary-size ranges *overlap* at full async-daemon scale, Go is a defensible pick that still satisfies criteria 2 and 4 about as well as Rust. The doc's "Go as fallback" stance is fair; it should just stop implying Go is meaningfully worse on namespaces.

**Is Python-freeze ever acceptable?** Not for this goal. Freezing (PyInstaller/Nuitka/PyOxidizer) fails the cross-arch *build* half of criterion 2 and is ~5–80× larger than the compiled alternatives. `python-build-standalone` is the only one-host-multi-target Python option, but its ~33 MB interpreter floor only works as a cached base layer — which keeps a runtime in the image, defeating the rewrite's purpose. Freeze is acceptable only as a stopgap if the rewrite is deferred and you accept a per-target build matrix.

**If you do nothing else (the criterion-4 lever):** replace the base64-32 KiB-chunk-over-`exec` upload with Docker's **native file-transfer API** (`container.put_archive()` / `docker cp`) wrapped on `ProviderAdapter`. This collapses upload to a single streamed transfer (size stops mapping to ~64–128 round-trips), removes the in-image `tar`/`gzip`/`base64` finalize dependency (also serving criterion 2), and **dominates criterion 4 regardless of which language wins.** Sequence it *first* in the migration — it is the gate on the "small/fast upload" goal and is independent of the daemon rewrite.

## 7. Concrete edits to fold back into the original doc

- [ ] **Add a Python column** to all three tables (Packaging, Daemon, Namespace Runner) with measured baselines: bundle 178.7 KiB gz / 759.4 KiB uncompressed / 154 files; per-call ~61 ms host-proxy startup; needs in-image Python ≥3.10 + `sh`/`tar`/`gzip`/`base64`/`printf` + external `unshare`.
- [ ] **Quantify the size row.** Replace "usually smaller / usually larger" with: Rust ~1–4 MB, Go ~1.5–3 MB stripped static-musl; both **5–15× larger than the 178.7 KiB source bundle**; the size win is only versus *frozen* Python (PyInstaller ~15 MB, pbs ~33 MB).
- [ ] **State the criterion-2-vs-4 tension explicitly:** image-agnostic forces self-containment, which is larger than the source bundle; the two top criteria conflict.
- [ ] **Demote the namespace claim.** Correct the "Namespace/thread safety" row: Go is fully capable via `SysProcAttr` Cloneflags/Unshareflags + `syscall.Mount` (no in-image `unshare`/`mount`); Rust's edge is linear single-process flow vs Go's `/proc/self/exe` re-exec shim — ergonomics, not capability. Both beat today's external-`unshare` Python path.
- [ ] **Fix the transport claim** (Verdict §): change "the daemon mostly uses TCP for Docker transport" to "AF_UNIX socket locally, numeric-literal loopback TCP for host-side pooling; both do zero name resolution, so transport is language/libc-agnostic."
- [ ] **Commit to the upload-mechanism change and sequence it first.** Make "add Docker native file-transfer (`container.put_archive()` / `docker cp`) to `ProviderAdapter`; remove base64-over-`exec` and in-image `tar`/`gzip`/`base64`" **Step 1**, and note that without it a larger binary regresses upload to ~64–128 round-trips (vs ~8 today).
- [ ] **Acknowledge the ns-runner duality:** the port must preserve both the fresh-namespace (`unshare` subprocess) and existing-namespace (`setns` on pre-opened FDs) paths, plus the raw-syscall (`fsopen/fsconfig/fsmount/move_mount`) overlay mount.
- [ ] **Add a performance-prediction note** with the guardrail: per-call runtime-init cut ~50–120× (Python ~61 ms → <1 ms), but end-to-end bounded by the unmeasured syscall floor; daemon RSS ~2–10× (up to ~20×); **re-measure in-sandbox before quoting end-to-end multipliers.** Note Go≈Rust on startup, so performance does not adjudicate Rust-vs-Go.
- [ ] **In the Decision section,** rewrite the reasons: lead with smallest self-contained artifact + smallest dependency surface; remove/soften "better suited to the namespace/mount/exec boundary"; drop "should reduce package size … compared with Python" unless qualified to "vs frozen Python, not vs the source bundle."
