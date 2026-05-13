---
phase: sandbox-overlay
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 9
files_reviewed_list:
  - backend/src/sandbox/overlay/__init__.py
  - backend/src/sandbox/overlay/cli.py
  - backend/src/sandbox/overlay/capture/changes.py
  - backend/src/sandbox/overlay/capture/types.py
  - backend/src/sandbox/overlay/capture/upperdir.py
  - backend/src/sandbox/overlay/namespace/command.py
  - backend/src/sandbox/overlay/namespace/mounts.py
  - backend/src/sandbox/overlay/runner/runtime_invoker.py
  - backend/src/sandbox/overlay/runner/snapshot_overlay_runner.py
findings:
  critical: 2
  warning: 8
  info: 4
  total: 14
status: issues_found
---

# Code Review: `sandbox.overlay`

**Reviewed:** 2026-05-13
**Depth:** standard
**Files Reviewed:** 9
**Status:** issues_found

## Summary

**Important scope correction up-front.** The review request describes this subsystem as wrapping "OverlayFS mount machinery and namespace launch" with mount/unshare invocations, lowerdir/upperdir argv assembly, and namespace argv. The code on disk does **not** match that description:

- `namespace/mounts.py` performs no `mount(2)`/`mount(8)` calls. It runs `MergedView.materialize()` into a `lower/` dir and then `shutil.copytree()` into `merged/`. No mount syscall, no `lowerdir=a:b:c` argv assembly. The module docstring says so explicitly: *"a kernel overlay mount can replace this implementation behind the same return object later."*
- `namespace/command.py` does **no namespace operations**. It is a thin wrapper over `subprocess.run(list(command), ...)`. No `unshare(2)`, no `unshare(8)` argv, no namespace flags.
- No file in the reviewed scope imports `subprocess.Popen`, `os.system`, `shell=True`, or invokes `mount`/`unshare` binaries.

Consequently the BLOCKERs the reviewer was primed to look for (mount option injection via `:`/`,` in lowerdir paths, unshare argv injection, layer-depth hard-coded caps) are **not present in this code** — there is nothing to inject into. I have NOT fabricated findings against the original brief. The misleading `namespace/` naming is itself called out below (WR-08).

What IS in scope here: filesystem walking, copy-tree, untrusted-string handling, subprocess argv hygiene, resource lifecycle of `run_dir`, whiteout/opaque-dir detection correctness, and the CLI's trust boundary (the CLI is bundled and uploaded to remote sandboxes per `bundle_upload.py`, and is the externally-reachable Python entry-point for executing arbitrary `command` argv lists). Real defects below.

## Critical Issues

### CR-01: `run_user_command` leaks host environment into the user command

**File:** `backend/src/sandbox/overlay/namespace/command.py:39`
**Issue:** The environment for the child process is constructed as:

```python
env={**os.environ, **env, "GIT_OPTIONAL_LOCKS": "0"},
```

This merges the **entire host environment** (`AWS_*`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `PATH` from the host, `HOME`, `SSH_AUTH_SOCK`, AWS/GCP credential paths, Daytona tokens, etc.) into the user-command process. Combined with the fact that `request.command` is an arbitrary argv tuple controlled by the caller, this is a credential-exfiltration path: a malicious command (`env`, `printenv`, or any script that reads `os.environ`) running in the "sandbox" gets every secret on the host that launched the overlay-shell CLI.

This sandbox claims to isolate execution from a leased snapshot; passing the host env directly defeats that isolation. The user-supplied `env` already lets the caller add any vars they need — there is no need to inherit the host environment.

**Fix:** Build a minimal allow-listed env. Either accept only the explicit `env` argument, or whitelist a small set (e.g. `PATH`, `HOME`, `LANG`, `LC_ALL`, `TERM`) and let the caller layer on top:

```python
ALLOWED_HOST_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TERM", "TZ")
base_env = {
    key: os.environ[key]
    for key in ALLOWED_HOST_ENV
    if key in os.environ
}
child_env = {**base_env, **env, "GIT_OPTIONAL_LOCKS": "0"}
```

If the existing behaviour is deliberate (the CLI is only ever invoked inside an already-isolated remote sandbox), state that contract in the docstring AND have `RuntimeInvoker` strip secrets before launching the worker. As written, an in-process import path (`from sandbox.overlay.cli import execute_request`) on the host inherits the host env directly — there is no boundary at all.

### CR-02: `run_dir` is never cleaned up — unbounded disk growth and stale-state leak

**File:** `backend/src/sandbox/overlay/runner/runtime_invoker.py:95-101`, `backend/src/sandbox/overlay/namespace/mounts.py:36-68`, `backend/src/sandbox/overlay/runner/snapshot_overlay_runner.py:107-152`
**Issue:** Each `shell()` / `shell_sync()` call computes a fresh `run_dir = runtime_root / f"{safe_id}-{uuid4().hex[:8]}"` and materializes:

- a full lowerdir copy of the leased snapshot,
- a `merged/` copy of the lowerdir (full duplicate),
- `upper/`, `work/`,
- `stdout.bin`, `stderr.bin`, `result.json`.

There is no `shutil.rmtree(run_dir)` anywhere. `mount_snapshot` wipes `upper`/`work`/`merged` at entry (line 43-46), but the *parent* run_dir from prior invocations is never removed. Each request leaves a directory tree on disk forever; over a workload of many requests this is **unbounded growth of `storage_root/runtime/overlay_shell/`**, which is essentially a denial-of-service / cost vector in any production deployment.

Worse, on `subprocess.TimeoutExpired` (CR-02 also touches WR-04) the request exits via exception and the artifacts never get a chance to be reaped at a higher level, because nothing tracks `run_dir` outside the worker.

**Fix:** Wrap the body of `execute_request` (or the invoker) in `try/finally` and `shutil.rmtree(run_dir, ignore_errors=True)` on exit. If the caller needs `stdout.bin`/`stderr.bin` for streaming, copy those into the response or stream them inline, and clean the temp tree unconditionally:

```python
def execute_request(*, request_payload, manifest_payload, storage_root, run_dir):
    run_path = Path(run_dir)
    try:
        ...  # existing body
        return capture
    finally:
        shutil.rmtree(run_path, ignore_errors=True)
```

If artifacts must persist for debugging, add an explicit retention policy with a TTL sweeper, not silent accumulation forever.

## Warnings

### WR-01: Duplicate `opaque_dir` emission when both opaque marker file and overlay opaque xattr are present

**File:** `backend/src/sandbox/overlay/capture/upperdir.py:109-136`
**Issue:** `_walk_upperdir` emits an `opaque_dir` change in **two** independent branches:

1. When it encounters a file named `OPAQUE_MARKER` (".wh..wh..opq") — yields `path=rel.parent.as_posix()`.
2. When it encounters a directory carrying the `trusted.overlay.opaque` (or `user.overlay.opaque`) xattr — yields `path=rel.as_posix()` for the dir itself.

A directory `<upper>/foo/` carrying the overlay opaque xattr **and** containing a `.wh..wh..opq` marker (a real overlayfs upperdir can have both, particularly when the kernel and userspace tooling disagree on which marker convention to use) will be emitted twice with `path="foo"`. Downstream consumers receive a duplicate `OverlayPathChange`. Whether that is a hard bug depends on whether the layer-stack reducer is idempotent on duplicate `opaque_dir` records; if it dedupes, this is a latent inefficiency; if it doesn't, it's a correctness bug.

**Fix:** Track which `rel` paths have already been emitted as opaque, or canonicalize the emit step:

```python
emitted_opaque: set[str] = set()
# in opaque marker branch:
opaque_path = rel.parent.as_posix() if rel.parent.as_posix() != "." else ""
if opaque_path not in emitted_opaque:
    emitted_opaque.add(opaque_path)
    yield OverlayPathChange(path=opaque_path, kind="opaque_dir", ...)
# similar guard in the xattr branch
```

### WR-02: Malformed whiteout markers (`.wh.` with empty target) crash the walker

**File:** `backend/src/sandbox/overlay/capture/upperdir.py:120-126, 175-180`
**Issue:** `_is_whiteout_marker` returns `True` for any name that starts with ".wh." and isn't exactly the opaque marker. A file literally named `.wh.` (prefix only, empty target) satisfies that predicate. `_whiteout_target` then produces `rel.parent / ""` and the emitted `path` is the empty string of the parent dir, fed to `normalize_layer_path` which raises `ValueError("path must not be empty")`. That exception propagates out of the generator and aborts capture mid-walk, so all subsequent changes are dropped.

The same applies to a name like `.wh.foo/` if a directory happens to be named with the whiteout prefix (the dir branch at line 128 short-circuits, but an empty file `.wh.` at upper root is sufficient to trigger this).

Whether this is reachable from a user command depends on whether overlay enforces well-formed whiteout names, but the upperdir is also populated by `_populate_upperdir_from_diff` and by arbitrary user commands writing files; this is reachable.

**Fix:** Guard against the empty-target case:

```python
def _is_whiteout_marker(entry: Path) -> bool:
    return (
        entry.name.startswith(WHITEOUT_PREFIX)
        and entry.name != OPAQUE_MARKER
        and len(entry.name) > len(WHITEOUT_PREFIX)
    )
```

And/or wrap the per-entry emit in a try/except that logs-and-skips malformed entries rather than killing the whole capture.

### WR-03: `_is_overlay_whiteout` treats `st_rdev = None` as a match — false positives on platforms without `st_rdev`

**File:** `backend/src/sandbox/overlay/capture/upperdir.py:183-193`
**Issue:**

```python
if stat.S_ISCHR(st.st_mode) and getattr(st, "st_rdev", None) in (0, None):
    return True
```

The membership test `in (0, None)` matches when `st_rdev` is missing (`None`). On Linux `st_rdev` is always present, so this is effectively dead-equivalent to `== 0` there. On other platforms or with a mocked `os.stat` result that omits `st_rdev`, any character-special file would be flagged as an overlay whiteout — including `/dev/null`, `/dev/zero`, etc. were they to end up in the walk (they shouldn't, but the logic is loose).

More importantly the intent of the overlayfs whiteout convention is `S_ISCHR(st_mode) && st_rdev == makedev(0, 0)`. Today this works on Linux by accident (rdev for a 0/0 char device is `0`), but the `None`-tolerant form will mis-fire if anything ever swaps the stat backend.

**Fix:** Tighten the predicate:

```python
if stat.S_ISCHR(st.st_mode) and st.st_rdev == 0:
    return True
```

### WR-04: `subprocess.TimeoutExpired` is not handled — timed-out commands raise instead of returning a partial result

**File:** `backend/src/sandbox/overlay/namespace/command.py:36-44`
**Issue:** `subprocess.run(..., timeout=timeout_seconds)` raises `subprocess.TimeoutExpired` when the timeout fires. Nothing here catches it. The exception escapes through `execute_request` and bubbles up to whatever called `RuntimeInvoker`; no `CommandResult` is constructed, the partial stdout/stderr captured into `stdout.bin`/`stderr.bin` is orphaned, and the caller cannot distinguish "command timed out" from "infrastructure crashed".

For a sandbox boundary this matters: a user command can deterministically force the runner to crash by exceeding the configured timeout.

**Fix:**

```python
try:
    completed = subprocess.run(
        list(command),
        cwd=resolved_cwd,
        env=child_env,
        stdout=stdout_file,
        stderr=stderr_file,
        timeout=timeout_seconds,
        check=False,
    )
    exit_code = int(completed.returncode)
except subprocess.TimeoutExpired:
    exit_code = 124  # GNU convention for timeout
return CommandResult(
    exit_code=exit_code,
    stdout_ref=str(stdout_path),
    stderr_ref=str(stderr_path),
)
```

Also kill the child reliably on timeout (use `Popen` + manual `wait(timeout=...)` + `kill` + `communicate()` if partial output and process tree termination matter — `subprocess.run` will reap the immediate child but not its descendants).

### WR-05: `_populate_upperdir_from_diff` silently drops empty-directory creations and permission changes

**File:** `backend/src/sandbox/overlay/capture/upperdir.py:52-101`
**Issue:** `_payload_paths` only counts entries that satisfy `is_symlink() or is_file()` (line 91). Empty directories created by the user command (and present in `merged` but not `lower`) never appear in either set, so they are never copied into the upperdir, and they never produce an `OverlayPathChange`. Any workflow that creates an empty dir (e.g. `mkdir build`, `git init` on an empty repo) loses that mutation.

Similarly `_entries_match` (line 96-101) compares only bytes (for files) and readlink target (for symlinks). It does not compare mode bits. `chmod +x file.sh` produces `_entries_match() == True` and is silently dropped from the captured changes. That is a correctness gap for any tool that relies on executable-bit churn.

**Fix:** Extend `_payload_paths` to enumerate directories as well (tagged with a kind so `_entries_match` can compare them appropriately), and have `_entries_match` compare `st_mode & 0o777` between sides. Or document explicitly that this populate path is unit-test-only and not used in production (and add a guard so it cannot be invoked from `RuntimeInvoker`).

### WR-06: `_resolve_cwd` calls `mkdir(parents=True)` during input validation

**File:** `backend/src/sandbox/overlay/namespace/command.py:52-61`
**Issue:** `_resolve_cwd` does two jobs: it validates that `cwd` stays inside the workspace, and it creates the dir if it doesn't exist. The path traversal check (`os.path.commonpath`) is correct, but the `mkdir(parents=True, exist_ok=True)` side-effect happens unconditionally **after** validation. That means:

- A caller can probe arbitrary subpaths under `workspace_root` to materialize empty directories, including overwriting an existing symlink target's parent semantics in subtle ways (since `mkdir(parents=True)` follows symlinks).
- The traversal check happens before `resolve()` is called on the *parent* of a non-existent cwd, but the resolve call on the candidate itself (line 57) can dereference an existing symlink inside the workspace. If `workspace_root/foo` is a symlink to `/etc`, then `cwd="foo/bar"` resolves to `/etc/bar`, fails the commonpath check — good. But `mkdir(parents=True, exist_ok=True)` is only called *after* the check passes, so this is just code-smell, not a vulnerability today.

**Fix:** Split validation from materialization. Have callers explicitly request creation if they want it:

```python
def _resolve_cwd(workspace_root: Path, cwd: str, *, create: bool = False) -> Path:
    ...
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
    elif not resolved.exists():
        raise FileNotFoundError(...)
    return resolved
```

Also note the `resolve()` call uses Python's default (strict=False), which means non-existent components are accepted; this is fine for the create-if-missing path but worth confirming once `strict=True` semantics matter.

### WR-07: `os.symlink(os.readlink(merged_entry), target)` preserves absolute targets without validation

**File:** `backend/src/sandbox/overlay/capture/upperdir.py:78-79`
**Issue:**

```python
if merged_entry.is_symlink():
    os.symlink(os.readlink(merged_entry), target)
```

The target of the symlink is copied verbatim from `merged_entry`. If the user's command created `merged/escape -> /etc/shadow` (or `merged/escape -> ../../../host_secret`), this populate step copies that exact dangling target into the upperdir. The captured change then carries a symlink `OverlayPathChange` whose `content_path` (the upperdir file) is a symlink pointing outside the workspace.

Downstream consumers of `OverlayPathChange` that later call `content_hash(...path..., symlink=True)` will hash the readlink target string, which is benign for hashing — but any consumer that subsequently *follows* the symlink (or commits the symlink into the layer stack and then materializes it for a future command) effectively allows a user command to write symlinks pointing at arbitrary host paths.

**Fix:** Either reject absolute or `..`-escaping symlink targets at capture time, or normalize them to relative paths confined to the workspace. The layer-stack admission policy may handle this already, but the overlay capture layer should not be the place that produces an unrestricted absolute-pointing symlink record.

```python
target_str = os.readlink(merged_entry)
if PurePosixPath(target_str).is_absolute() or any(p == ".." for p in PurePosixPath(target_str).parts):
    raise ValueError(f"symlink target escapes workspace: {target_str!r}")
os.symlink(target_str, target)
```

### WR-08: Module/directory naming claims namespace operations but performs none — misleading future readers

**File:** `backend/src/sandbox/overlay/namespace/command.py`, `backend/src/sandbox/overlay/namespace/mounts.py`
**Issue:** The directory is named `namespace/` and both files imply namespace/mount machinery. Neither performs any namespace operation (`unshare`, `setns`, `clone`) nor any mount(2)/mount(8) call. `command.py` is a plain subprocess wrapper; `mounts.py` is a copy-tree wrapper. The misleading naming is exactly the kind of trap that makes future security reviewers (or LLM-assisted refactors) miss the moment when real mount/unshare code lands in this directory and starts taking user-controlled paths.

This is a code-quality finding and also a real maintenance hazard given the user memory note that mount(2) is the planned eventual implementation (Overlay 16-layer cap is util-linux mount(8), not kernel).

**Fix:** Either rename the directory to `runtime/` / `process/` while it is still copy-backed, or land a docstring at the package level that explicitly states the file's CURRENT responsibility and what the eventual mount-namespace implementation would replace. When the kernel-mount version lands, do this review again — and at that point, the BLOCKER list at the top of this brief (mount option injection, unshare argv, lowerdir `:`/`,` injection) becomes real.

## Info

### IN-01: `lowerdir_for` is a fragile back-reference

**File:** `backend/src/sandbox/overlay/namespace/mounts.py:71-72`
**Issue:** `lowerdir_for` recomputes the lower path by walking up from `workdir`:

```python
return str(Path(mounted.workdir).parent / "lower")
```

This depends on the convention that `lower/` is a sibling of `work/`. That's true today by construction in `mount_snapshot`, but `MountedSnapshot` doesn't expose `lowerdir` directly. Future refactors that change the layout (e.g. tmpfs upper/work in a separate root) will silently break this.

**Fix:** Add `lowerdir: str` to `MountedSnapshot` and have `mount_snapshot` populate it directly; drop `lowerdir_for`.

### IN-02: `del snapshot_manifest` parameter never used

**File:** `backend/src/sandbox/overlay/capture/upperdir.py:31`
**Issue:** `capture_changes` declares `snapshot_manifest: Manifest` as a required keyword arg, immediately discards it (`del snapshot_manifest`), and never uses it. Either the parameter is a forward-compatibility placeholder (then mark it `_snapshot_manifest` and document it) or it should be removed.

**Fix:** Remove the parameter from the signature and the callsite in `cli.py:64`, or rename to a leading-underscore unused placeholder and add a `# TODO: ...` comment explaining what it's reserved for.

### IN-03: `invoke_start` set inside the `try` — would be unbound if first line raised

**File:** `backend/src/sandbox/overlay/runner/snapshot_overlay_runner.py:114-126, 143-152`
**Issue:** Both `shell()` and `shell_sync()` set `invoke_start = time.perf_counter()` as the first statement of the `try`. The `finally` then computes `time.perf_counter() - invoke_start`. Since the very first statement of `try` is the assignment, the variable is bound before any line that can raise — so this is *currently* safe. But the assignment is one line away from being moved or being preceded by a future call that raises, which would make `invoke_start` unbound in the finally and mask the real exception with `UnboundLocalError`. Move the assignment ABOVE the `try`:

```python
invoke_start = time.perf_counter()
try:
    capture = await self._invoker.invoke(...)
finally:
    timings["overlay.invoke_total_s"] = time.perf_counter() - invoke_start
    ...
```

This is a defensive style fix, not a current bug.

### IN-04: `runtime_invoker.py` resume-wait timing math can underflow before the `max(0.0, ...)` guard

**File:** `backend/src/sandbox/overlay/runner/runtime_invoker.py:58-62, 86-91`
**Issue:** The expression

```python
"overlay.invoker.resume_wait_s": max(
    0.0,
    invoke_elapsed - (worker_start - invoke_start) - worker_elapsed,
),
```

clamps to zero, which is good. But the inputs are three independent `perf_counter()` reads with float subtractions; on heavily loaded systems the clamp triggers regularly and masks the fact that the math is inconsistent. The `max(0.0, ...)` is correct as defensive code, but it would be clearer to record the three raw measurements and compute derived timings post-hoc. Low-priority style note.

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
