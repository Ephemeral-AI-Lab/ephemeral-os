---
phase: command_exec-review
reviewed: 2026-05-13T00:00:00Z
depth: standard
files_reviewed: 11
files_reviewed_list:
  - backend/src/sandbox/command_exec/__init__.py
  - backend/src/sandbox/command_exec/contract/__init__.py
  - backend/src/sandbox/command_exec/contract/ports.py
  - backend/src/sandbox/command_exec/contract/request.py
  - backend/src/sandbox/command_exec/contract/result.py
  - backend/src/sandbox/command_exec/workspace/__init__.py
  - backend/src/sandbox/command_exec/workspace/capture.py
  - backend/src/sandbox/command_exec/workspace/environment.py
  - backend/src/sandbox/command_exec/workspace/mount.py
  - backend/src/sandbox/command_exec/workspace/namespace_entrypoint.py
findings:
  blocker: 1
  warning: 7
  info: 2
  total: 10
status: issues_found
---

# command_exec Subsystem: Code Review Report

**Reviewed:** 2026-05-13
**Depth:** standard
**Files Reviewed:** 11
**Status:** issues_found

## Summary

The `command_exec` subsystem constructs the workspace environment and process invocation for arbitrary commands inside a leased workspace. Direct subprocess usage is consistently argv-form (no `shell=True`), and the overlay mount path correctly avoids shell interpolation by passing argv tokens to `mount(8)`. However, the central security boundary — the *workspace replacement root* — is enforced only on the absolute-path branch of `cwd` resolution; relative paths are concatenated without validation and `mkdir(parents=True)` plus `subprocess.run(cwd=...)` silently follow them outside the sandbox. This is a real escape vector and the highest-priority finding.

Secondary concerns: env-variable allowlisting is non-existent, `LD_PRELOAD`/`PATH`/`PYTHONPATH` flow through unfiltered; `shutil.rmtree` is invoked on spec-supplied paths without prefix containment; the `workspace_root` regex rewriter applies indiscriminately to argv data; TOCTOU between mount validation and the `mount` syscall; and several mount-option metacharacters beyond `,` go unguarded.

The capture path (`capture.py`) only delegates; the underlying reader lives in `sandbox.overlay.capture.upperdir` which is out of scope for this review — buffer-overflow / unbounded-read claims about capture cannot be confirmed from these files alone.

## Blocker Issues

### BL-01: `cwd` path traversal via relative path skips workspace-root validation

**File:** `backend/src/sandbox/command_exec/workspace/environment.py:28-34`
**Issue:**
`resolve_workspace_cwd` validates only the absolute-path branch against `declared_workspace_root` via `_relative_to_declared_workspace` (which uses `os.path.commonpath`). The relative-path branch does not validate at all:

```python
if candidate.is_absolute():
    rel = _relative_to_declared_workspace(candidate, declared_root)
    resolved = mounted_root / rel
else:
    resolved = mounted_root / candidate          # no validation
resolved.mkdir(parents=True, exist_ok=True)
```

`CommandExecRequest.cwd` is sourced from a caller-controlled mapping in `sandbox/runtime/daemon/service/shell_runner.py::_command_request`, and `CommandExecRequest.__post_init__` only strips whitespace and defaults empty strings to `"."` — it does not reject `..`, `../../etc`, or other escapes. A request with `cwd="../../../etc"` produces the literal path `<mounted_root>/../../../etc`, which `mkdir(parents=True, exist_ok=True)` creates if it does not exist (silently traversing parents) and `subprocess.run(cwd=...)` then resolves to `/etc` (verified empirically: `/tmp/mounted_root/../../../etc` resolves to `/private/etc`). This means the guarded-command surface can run a command with `cwd` pointing anywhere on the host filesystem that the caller can express via `..` sequences.

This is the exact boundary the absolute-path branch is documented to enforce ("Absolute paths must stay under the declared workspace root"). The relative branch must enforce the same invariant.

In `_run_private_mount_namespace` mode the mounted root *is* the workspace root, so the overlay constrains visible filesystem to the leased view — but the unsharing happens before the command runs, and `os.chdir`/`subprocess(cwd=...)` does not bind the mount; a `..` cwd still escapes the workspace-root directory (still inside the namespace, but outside the leased workspace tree, e.g. into `/`, `/etc`, `/usr`, etc., which are the host's passthrough). In copy-backed mode the resolution happens on the host's real filesystem with no isolation at all.

**Fix:**
Validate the relative branch with the same `commonpath` check, computed against `mounted_root` after path resolution:

```python
if candidate.is_absolute():
    rel = _relative_to_declared_workspace(candidate, declared_root)
    resolved = (mounted_root / rel).resolve(strict=False)
else:
    resolved = (mounted_root / candidate).resolve(strict=False)

mounted_root_resolved = mounted_root.resolve(strict=False)
try:
    resolved.relative_to(mounted_root_resolved)
except ValueError as exc:
    raise ValueError(f"cwd escapes workspace replacement root: {cwd}") from exc

resolved.mkdir(parents=True, exist_ok=True)
return resolved
```

Additionally, reject `..` segments at the request boundary in `CommandExecRequest.__post_init__` as a belt-and-suspenders defense; the contract is the right place to fail closed.

## Warnings

### WR-01: Unfiltered env pass-through allows `LD_PRELOAD` / `PATH` / `PYTHONPATH` override

**File:** `backend/src/sandbox/command_exec/workspace/environment.py:82-83`
**Issue:**
`_command_environment` builds the child env as `{**os.environ, **dict(extra), "GIT_OPTIONAL_LOCKS": "0"}`. Caller-supplied `extra` (i.e. `request.env`) overrides any value in `os.environ`, including `LD_PRELOAD`, `LD_LIBRARY_PATH`, `PATH`, `PYTHONPATH`, `PYTHONSTARTUP`, `IFS`, `BASH_ENV`. If `request.env` is reachable from an untrusted caller (which is the normal posture for an "arbitrary commands" sandbox subsystem), this is straightforward privilege/behavior escalation — even though `subprocess.run` itself uses argv, the child interpreter is then under the agent's control.

**Fix:**
Apply an explicit blocklist / allowlist before merging, and forbid keys that influence dynamic linking or interpreter bootstrap:

```python
_FORBIDDEN_ENV = frozenset({
    "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT",
    "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH",
    "PYTHONSTARTUP", "BASH_ENV", "ENV", "IFS",
})

def _command_environment(extra: Mapping[str, str]) -> dict[str, str]:
    sanitized = {k: v for k, v in extra.items() if k not in _FORBIDDEN_ENV}
    return {**os.environ, **sanitized, "GIT_OPTIONAL_LOCKS": "0"}
```

If `extra` is assumed trusted, document the assumption in `environment.py` and at the request boundary so the invariant is auditable.

### WR-02: `shutil.rmtree` on spec-supplied paths with only an emptiness check

**File:** `backend/src/sandbox/command_exec/workspace/mount.py:78-81`
**Issue:**
`_run_copy_backed_mount` calls `shutil.rmtree(directory)` on `upperdir`, `workdir`, and `merged`. `WorkspaceReplacementMountSpec.__post_init__` only validates that those fields are non-empty (and `workspace_root` is absolute). A misconfigured or attacker-influenced spec pointing `upperdir` to `/var/lib/something` or `~` would be wiped without warning. The merged directory is derived from `run_dir`, but `upperdir`/`workdir` come directly from the spec.

**Fix:**
Require `upperdir`, `workdir` to live under a known root (e.g. `run_dir` or a per-request scratch root) in `WorkspaceReplacementMountSpec.__post_init__`, e.g.:

```python
def __post_init__(self) -> None:
    ...
    for field_name in ("upperdir", "workdir"):
        value = Path(str(getattr(self, field_name)))
        if not value.is_absolute():
            raise ValueError(f"{field_name} must be absolute")
        if ".." in value.parts:
            raise ValueError(f"{field_name} must not contain '..' segments")
```

And in `_run_copy_backed_mount`, guard `rmtree` with a prefix check (`directory.is_relative_to(run_dir)` for the merged dir; explicit allowed-roots for upper/workdir).

### WR-03: `_rewrite_declared_workspace_refs` rewrites argv data indiscriminately

**File:** `backend/src/sandbox/command_exec/workspace/mount.py:171-188`
**Issue:**
The regex `re.compile(rf"{re.escape(root)}(?=/|$|[\s'\":;,&|)])")` is applied to every argv token. Any literal occurrence of `workspace_root` in argv (e.g. `git log --grep="/testbed migration"`, `--message="updated /testbed/README"`, `echo "/testbed"`) is silently rewritten to the on-host mounted path. That changes program semantics: the user sees `/testbed` in their command but the program receives `/tmp/run_xxx/workspace`. This both leaks the host path into logs and can produce surprising behavior (e.g. a `grep`/`sed` regex over content no longer matches).

Additionally, environment values are *not* rewritten — copy-backed mode silently breaks commands that read `WORKSPACE_DIR=/testbed` from `env`.

**Fix:**
- Document that argv rewriting is path-only and instruct callers to use relative paths; or
- Restrict rewriting to tokens that *parse* as paths (start with the root and are followed by `/` or end-of-token), rather than embedded substrings; and
- Either rewrite env consistently, or document that env passes through unmodified.

### WR-04: TOCTOU between `_validate_mount_inputs` and `_mount_overlay`

**File:** `backend/src/sandbox/command_exec/workspace/namespace_entrypoint.py:39-54`
**Issue:**
`_validate_mount_inputs` checks `is_dir()` on `workspace_root` and `lowerdir`; the subsequent `_mount_overlay` call invokes `mount(8)` separately. Between the two, another process (in the same mount namespace before unshare propagation, or with shared host access for `lowerdir`) could swap the directory for a symlink or replace contents. Within the unshared namespace the surface is narrow, but `lowerdir` is the host-visible leased path and is reachable by host processes.

**Fix:**
Open the directories once via `os.open(..., O_DIRECTORY | O_NOFOLLOW)` and use `/proc/self/fd/<n>` paths in the mount options, or verify post-mount that the mounted overlay still points at the same inode (`os.stat` before vs. after). At minimum, reject symlinks explicitly with `Path.is_symlink()` before `is_dir()`.

### WR-05: Mount-option string sanitization checks only `,`

**File:** `backend/src/sandbox/command_exec/workspace/namespace_entrypoint.py:104, 134-135`
**Issue:**
`_mount_overlay` formats `lowerdir`, `upperdir`, `workdir` into a comma-separated `-o` options string. `_validate_mount_inputs` rejects `,` in paths (good), but not `\` (the kernel option parser treats `\` as escape), and not `\n` or other ASCII control characters that some kernels parse loosely. While the realistic injection vector via this path is narrow (paths come from internal lease/scratch managers), the assumption is undocumented.

**Fix:**
Tighten validation to a path-character allowlist (`[A-Za-z0-9_./-]`), or reject any of `,\\\n\r\t` explicitly. Document in the function docstring that mount-option metacharacters are forbidden in path inputs.

### WR-06: `KeyError` propagates uncaught for missing payload keys

**File:** `backend/src/sandbox/command_exec/workspace/namespace_entrypoint.py:29-35`
**Issue:**
`payload["workspace_root"]`, `payload["lowerdir"]`, `payload["upperdir"]`, `payload["workdir"]`, `payload["stdout_ref"]`, `payload["stderr_ref"]`, `payload["timings_ref"]` are accessed before the `try`/`except` block. If any key is missing, `KeyError` propagates and `main()` exits with a Python traceback to the parent's captured-stderr buffer rather than the structured error-path the rest of the function takes. `payload["command"]` (line 77) has the same issue.

`execute` mixes "robust" handling (try around mount) with "fragile" key access — be consistent.

**Fix:**
Validate required keys at the top of `execute` and either raise a typed error caught by `main`, or extract all keys inside the `try` block so failures route through the existing 126-exit path with a written stderr_ref.

### WR-07: Exception handler in execute swallows the original mount error in the `_validate_mount_inputs` path

**File:** `backend/src/sandbox/command_exec/workspace/namespace_entrypoint.py:61-64`
**Issue:**
The generic `except Exception as exc` catches `RuntimeError` from `_validate_mount_inputs` *and* `KeyError`, `TypeError`, `AttributeError` from upstream payload coercion. Writing only `str(exc)` to `stderr_ref` (without distinguishing kind) makes debugging mount issues hard, and `mkdir` failures on `upperdir`/`workdir` (e.g. `PermissionError` writing under a not-yet-mounted path) get the same vague message.

The broader concern: the function returns exit code 126 for any failure, conflating validation errors, mount errors, permission errors, and runtime errors. Callers cannot distinguish "spec was malformed" from "kernel refused to mount."

**Fix:**
Keep distinct except branches for `(RuntimeError, OSError)` from validation/mkdir vs. `CalledProcessError` from `mount` (already handled), and write a stable error-code prefix to stderr_ref (e.g. `MOUNT_VALIDATION_FAILED:`, `MOUNT_SYSCALL_FAILED:`) so callers can route on it.

## Info

### IN-01: `_ensure_refs` writes unbounded captured-process buffer to ref files

**File:** `backend/src/sandbox/command_exec/workspace/mount.py:203-213`
**Issue:**
The parent calls `subprocess.run(..., capture_output=True)` on `unshare` (no size cap). If the namespace child writes to *its* stdout/stderr before opening `stdout_ref`/`stderr_ref` — e.g. a Python startup error in `sandbox.command_exec.workspace.namespace_entrypoint` itself — those bytes are buffered in memory by the parent and then written wholesale to the ref files via `_ensure_refs`. A misbehaving entrypoint could produce arbitrarily large output and OOM the parent.

**Fix:**
Use `stdout=open(stdout_ref, "wb")`, `stderr=open(stderr_ref, "wb")` directly on the unshare invocation, so the kernel streams the bytes to disk. The `_ensure_refs` post-hoc fallback then becomes a no-op for the normal case and a small cap-bounded fallback for unhandled exits.

### IN-02: `cwd = "."` after strip is silently mapped to mounted root; no normalization for `./../foo` style

**File:** `backend/src/sandbox/command_exec/contract/request.py:44` and `environment.py:26`
**Issue:**
`cwd` is normalized via `str(self.cwd).strip() or "."`. Once the BL-01 fix lands, the same validation should also normalize `./..`, `foo/../..`, etc. before resolution. Currently `Path("./../foo")` flows through unchanged and the absolute/relative branch decision is made on the raw `Path.is_absolute()`. Consolidate normalization at the request-boundary so the workspace layer has a single trusted invariant ("cwd is a non-escaping relative path or absolute path under declared_workspace_root").

**Fix:**
After validation lands, use `posixpath.normpath` at the request boundary and reject any path whose normalized form contains `..` or starts with `/` outside `workspace_root`.

## Out of Scope / Not Findings

- **Shell injection**: every `subprocess.run` in this subsystem uses argv form. No `shell=True`. No `os.system`. No `eval`. The lurking risk is via env / mount-option strings (covered in WR-01 and WR-05), not via shell metacharacters in command tokens.
- **Capture buffer overflows**: `capture.py` is a thin shim over `sandbox.overlay.capture.upperdir.capture_changes`. The actual reader is in a different package and not part of this review. Reviewer should look at `sandbox/overlay/capture/upperdir.py` separately if buffer concerns matter.
- **Symlink handling in copy-backed mount**: `shutil.copytree(..., symlinks=True)` preserves symlinks as symlinks rather than following them, which is the safer choice — flagging for awareness only.

---

_Reviewed: 2026-05-13_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
