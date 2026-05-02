# Phase 3.6 Stage A — LSP qualification spike result

**Date:** 2026-05-02
**Sandbox image:** `dask__dask_2023.3.2_2023.4.0`
**Spike script:** [`scripts/lsp_qualification_spike.py`](../../../scripts/lsp_qualification_spike.py)

## Verdict

```
LSP_BACKEND_CHOSEN = "basedpyright"
LAUNCH_COMMAND     = "basedpyright-langserver --stdio"
```

basedpyright is the chosen backend. pyright was not attempted because
basedpyright qualified on the first sweep.

## Evidence

### basedpyright

| Step | Result | Notes |
|---|---|---|
| `python3 -c 'import basedpyright'` (pre-install) | FAIL | Not bundled in the sandbox image. |
| `python3 -m pip install --no-cache-dir --retries 10 --timeout 300 basedpyright` | OK (296.78s) | Pulls `basedpyright-1.39.3` and `nodejs-wheel-binaries-24.15.0`. The pypi.org→files.pythonhosted.org leg is slow from inside this image — explicit retries are load-bearing. |
| `python3 -c 'import basedpyright; print("ok")'` (post-install) | OK | Module imports cleanly. |
| Entry-point discovery | OK | `/opt/miniconda3/envs/testbed/bin/basedpyright-langserver` (also `basedpyright`). |
| LSP `initialize` handshake | OK (0.6122s) | Via `basedpyright-langserver --stdio`. |
| LSP `textDocument/definition` (cold) | OK (3.1228s, 1 result) | First query on `/testbed/dask/__init__.py`. Subsequent warm-cache queries expected to be much faster. |

### pyright

Not evaluated — basedpyright qualified first.

## Investigation gotchas (worth recording before Stage B starts)

Three iterations of the spike were needed to surface the qualified
launch command. Documenting them so the next phase doesn't re-discover:

1. **`python3 -m basedpyright.langserver --stdio` is NOT viable on this
   image.** The `python3 -m` convention adds the current working directory
   to `sys.path`, and `/testbed/dask/typing.py` shadows the stdlib
   `typing` module. The bundled-node trampoline (`basedpyright/run_node.py`)
   imports from `nodejs_wheel.executable`, which in turn imports
   `typing.Iterable` — and gets `dask.typing` instead, causing
   `ImportError: cannot import name 'TYPE_CHECKING' from partially
   initialized module 'typing'` and the langserver process exits before
   the handshake completes. Even setting `cwd=/tmp` in the spawn does not
   help if a future caller forgets the discipline. Stage B MUST use the
   `basedpyright-langserver` shell entry point, NOT `python3 -m`.

2. **basedpyright sends LSP server-state notifications (e.g.
   `window/logMessage`, `$/progress`) BEFORE responding to `initialize`.**
   A naive read-one-frame loop hangs / mis-attributes the response. The
   spike now uses a `_wait_for_id(reader, target_id, deadline)` loop that
   discards any frame whose `id` doesn't match the request — Stage B's
   `LspBackendChild` MUST do the same.

3. **stderr drains lazily.** When the langserver process dies during
   handshake, `proc.stderr.read()` AFTER `proc.wait()` returned the
   stderr — but only if there was a separate drain thread. Without one,
   stderr buffers can fill, deadlock the child, and silently drop the
   error message. The spike now uses a daemon drain thread; Stage B's
   `LspBackendChild` should mirror this if it ever needs to surface the
   child's stderr in error envelopes.

4. **Sandbox networking is slow.** `pip install basedpyright` takes
   145–280s wall-clock on this image (the pypi.org leg flakes). Stage B
   should NOT install basedpyright on demand — pre-bake it into the
   sandbox image, or warm-install at sandbox-create time, or accept the
   first-query cold-start cost on a fresh sandbox.

## What this means for Stage B

- `lsp_child.py` hardcodes `LSP_BACKEND_CHOSEN = "basedpyright"` and
  `_LAUNCH_CMD = ["basedpyright-langserver", "--stdio"]` (NOT
  `["python3", "-m", "basedpyright.langserver", "--stdio"]`).
- The spike's frame-loop pattern is the reference implementation for
  `LspBackendChild._wait_for_response(req_id, timeout)`.
- The compatibility probe (Task 3.6.G, P36-C3) HARD requires
  `command -v basedpyright-langserver` to succeed — not the bare
  `python3 -c 'import basedpyright'` check, since that succeeds even on
  images where the launch command is broken.
- Sandbox image work item (out of scope for Phase 3.6 source code, but
  flagged for ops): pre-install `basedpyright` and
  `nodejs-wheel-binaries` into the base image so the first daemon spawn
  doesn't pay the 280s install cost.

## Headline numbers (Stage A only — Stage C will replace)

| Metric | Value |
|---|---|
| Sandbox provisioning | 21.5s |
| basedpyright install | 296.78s (one-shot, can be amortized) |
| Entry-point discovery | <0.1s |
| LSP `initialize` round-trip | 0.6122s |
| LSP `textDocument/definition` (cold) | 3.1228s, 1 result |

The cold definition cost will be replaced by Stage C's 50-sample
distribution against today's jedi.Script baseline. The `initialize`
cost at 0.6s makes lazy-on-first-query the right lifecycle for the
daemon child (Task 3.6.E).
