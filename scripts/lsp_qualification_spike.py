#!/usr/bin/env python3
"""Phase 3.6 Stage A — LSP backend qualification spike.

Runs against a real Daytona ``dask__dask_2023.3.2_2023.4.0`` sandbox and
answers ONE question: which LSP backend can we run as a persistent stdio
language server in this image — basedpyright (preferred) or pyright
(fallback)?

Usage::

    .venv/bin/python scripts/lsp_qualification_spike.py

Output is a structured report that gets pasted into
``docs/architecture/code-intelligence-in-sandbox-daemon/
lsp-qualification-spike-result.md``. The chosen backend's literal becomes
``LSP_BACKEND_CHOSEN`` in ``lsp_child.py``. There is no runtime selector.

Exit code 0 = at least one backend qualified (the printed VERDICT names it).
Exit code 1 = neither backend qualified — Phase 3.6 does not ship.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
import uuid

from sandbox.api.bash import extract_exit_code, wrap_bash_command


_DASK_SWEEVO_INSTANCE_ID = "dask__dask_2023.3.2_2023.4.0"
_DASK_SWEEVO_REPO_DIR = "/testbed"


def _print(msg: str) -> None:
    print(msg, flush=True)
    sys.stdout.flush()


def _exec(raw_sandbox, command: str, *, timeout: int = 120) -> tuple[int, str]:
    response = raw_sandbox.process.exec(
        wrap_bash_command(command),
        timeout=timeout,
    )
    output, exit_code = extract_exit_code(
        getattr(response, "result", "") or "",
        fallback_exit_code=getattr(response, "exit_code", None),
    )
    return exit_code, output


def _lsp_handshake_script(launch_cmd: str, target_file: str) -> str:
    """Inline Python that spawns the LSP server, exchanges initialize, sends one
    textDocument/definition, and prints a JSON result line.

    Lives as a single ``python3 -c`` payload so we don't have to rsync a script
    into the sandbox.
    """
    body = (
        "import json, os, subprocess, sys, time, threading\n"
        "LAUNCH = " + repr(launch_cmd) + "\n"
        "TARGET = " + repr(target_file) + "\n"
        "out = {'launched': False, 'init_ok': False, 'init_s': None,\n"
        "       'first_query_ok': False, 'first_query_s': None,\n"
        "       'first_query_count': 0, 'stderr_tail': '',\n"
        "       'frames_seen_pre_init': 0}\n"
        "def _frame(msg):\n"
        "    body = json.dumps(msg).encode('utf-8')\n"
        "    return ('Content-Length: %d\\r\\n\\r\\n' % len(body)).encode('utf-8') + body\n"
        "def _read_one(stream):\n"
        "    length = None\n"
        "    while True:\n"
        "        line = stream.readline()\n"
        "        if not line:\n"
        "            return None\n"
        "        line = line.rstrip(b'\\r\\n')\n"
        "        if not line:\n"
        "            break\n"
        "        if line.lower().startswith(b'content-length:'):\n"
        "            length = int(line.split(b':', 1)[1].strip())\n"
        "    if not length:\n"
        "        return None\n"
        "    payload = b''\n"
        "    while len(payload) < length:\n"
        "        chunk = stream.read(length - len(payload))\n"
        "        if not chunk:\n"
        "            return None\n"
        "        payload += chunk\n"
        "    return json.loads(payload.decode('utf-8')) if payload else None\n"
        "def _wait_for_id(stream, target_id, deadline):\n"
        "    while time.perf_counter() < deadline:\n"
        "        msg = _read_one(stream)\n"
        "        if msg is None:\n"
        "            return None\n"
        "        if msg.get('id') == target_id:\n"
        "            return msg\n"
        "    return None\n"
        "_stderr_buf = []\n"
        "def _drain_stderr(stream):\n"
        "    try:\n"
        "        for line in iter(stream.readline, b''):\n"
        "            _stderr_buf.append(line.decode('utf-8', 'replace'))\n"
        "            if len(_stderr_buf) > 200:\n"
        "                _stderr_buf.pop(0)\n"
        "    except Exception:\n"
        "        pass\n"
        "try:\n"
        "    # cwd MUST be neutral: `python3 -m basedpyright.langserver` adds cwd\n"
        "    # to sys.path, and a workspace like /testbed/dask shadows stdlib `typing`.\n"
        "    proc = subprocess.Popen(LAUNCH.split(),\n"
        "        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,\n"
        "        cwd='/tmp')\n"
        "    out['launched'] = True\n"
        "    threading.Thread(target=_drain_stderr, args=(proc.stderr,), daemon=True).start()\n"
        "    init = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {\n"
        "        'processId': os.getpid(),\n"
        "        'rootUri': 'file://' + os.path.dirname(TARGET),\n"
        "        'rootPath': os.path.dirname(TARGET),\n"
        "        'capabilities': {'textDocument': {'definition': {'dynamicRegistration': True}}},\n"
        "        'initializationOptions': {},\n"
        "        'workspaceFolders': [{'uri': 'file://' + os.path.dirname(TARGET),\n"
        "                              'name': 'workspace'}]}}\n"
        "    proc.stdin.write(_frame(init)); proc.stdin.flush()\n"
        "    t0 = time.perf_counter()\n"
        "    init_resp = _wait_for_id(proc.stdout, 1, t0 + 30.0)\n"
        "    out['init_ok'] = bool(init_resp and init_resp.get('id') == 1)\n"
        "    out['init_s'] = round(time.perf_counter() - t0, 4)\n"
        "    if not out['init_ok']:\n"
        "        out['init_resp_kind'] = type(init_resp).__name__\n"
        "        if isinstance(init_resp, dict):\n"
        "            out['init_resp_keys'] = list(init_resp.keys())\n"
        "        raise RuntimeError('initialize failed; init_resp=%r' % (init_resp,))\n"
        "    proc.stdin.write(_frame({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}})); \n"
        "    proc.stdin.flush()\n"
        "    with open(TARGET, 'r', encoding='utf-8') as fh:\n"
        "        text = fh.read()\n"
        "    didopen = {'jsonrpc': '2.0', 'method': 'textDocument/didOpen', 'params': {\n"
        "        'textDocument': {'uri': 'file://' + TARGET, 'languageId': 'python',\n"
        "                         'version': 1, 'text': text}}}\n"
        "    proc.stdin.write(_frame(didopen)); proc.stdin.flush()\n"
        "    # Pick a position likely to resolve — search for an `import` line.\n"
        "    target_line = 0\n"
        "    target_char = 7\n"
        "    for idx, line in enumerate(text.splitlines()):\n"
        "        if line.startswith('from ') or line.startswith('import '):\n"
        "            target_line = idx\n"
        "            target_char = len(line.split()[1]) // 2 + line.find(line.split()[1])\n"
        "            break\n"
        "    defreq = {'jsonrpc': '2.0', 'id': 2, 'method': 'textDocument/definition', 'params': {\n"
        "        'textDocument': {'uri': 'file://' + TARGET},\n"
        "        'position': {'line': target_line, 'character': target_char}}}\n"
        "    proc.stdin.write(_frame(defreq)); proc.stdin.flush()\n"
        "    t1 = time.perf_counter()\n"
        "    def_resp = _wait_for_id(proc.stdout, 2, t1 + 30.0)\n"
        "    out['first_query_ok'] = bool(def_resp and 'error' not in def_resp)\n"
        "    out['first_query_s'] = round(time.perf_counter() - t1, 4)\n"
        "    if def_resp and isinstance(def_resp.get('result'), list):\n"
        "        out['first_query_count'] = len(def_resp['result'])\n"
        "    elif def_resp and isinstance(def_resp.get('result'), dict):\n"
        "        out['first_query_count'] = 1\n"
        "    try:\n"
        "        proc.stdin.write(_frame({'jsonrpc': '2.0', 'id': 3, 'method': 'shutdown'})); \n"
        "        proc.stdin.flush()\n"
        "        _wait_for_id(proc.stdout, 3, time.perf_counter() + 2.0)\n"
        "        proc.stdin.write(_frame({'jsonrpc': '2.0', 'method': 'exit'})); proc.stdin.flush()\n"
        "    except Exception:\n"
        "        pass\n"
        "    try:\n"
        "        proc.wait(timeout=2)\n"
        "    except Exception:\n"
        "        proc.terminate()\n"
        "    out['stderr_tail'] = ''.join(_stderr_buf)[-2000:]\n"
        "except Exception as exc:\n"
        "    out['error'] = '%s: %s' % (type(exc).__name__, exc)\n"
        "    out['stderr_tail'] = ''.join(_stderr_buf)[-2000:]\n"
        "print('LSP_QUAL_REPORT=' + json.dumps(out))\n"
    )
    return body


def _qualify_basedpyright(env) -> dict:
    out: dict = {"backend": "basedpyright", "checks": {}}

    code, msg = _exec(env, "python3 -c 'import basedpyright; print(\"ok\")'")
    out["checks"]["import_basedpyright_pre"] = {"ok": code == 0, "msg": msg.strip()}

    if code != 0:
        t0 = time.perf_counter()
        # The pypi.org → files.pythonhosted.org leg is slow from inside the
        # sandbox; raise per-request timeout + retries so a transient stall
        # does not flake the qualification.
        install_cmd = (
            "python3 -m pip install --no-cache-dir --retries 10 "
            "--timeout 300 basedpyright 2>&1 | tail -40"
        )
        ic, om = _exec(env, install_cmd, timeout=900)
        out["checks"]["pip_install_basedpyright"] = {
            "ok": ic == 0,
            "elapsed_s": round(time.perf_counter() - t0, 2),
            "tail": om.strip()[-500:],
        }
        if ic != 0:
            out["verdict"] = "DISQUALIFIED"
            return out

    code, msg = _exec(env, "python3 -c 'import basedpyright; print(\"ok\")'")
    out["checks"]["import_basedpyright_post"] = {"ok": code == 0, "msg": msg.strip()}
    if code != 0:
        out["verdict"] = "DISQUALIFIED"
        return out

    # Locate the basedpyright-langserver binary.
    bp_bin_code, bp_bin_msg = _exec(
        env,
        "command -v basedpyright-langserver || true; "
        "command -v basedpyright-python-langserver || true; "
        "ls /opt/miniconda3/envs/testbed/bin/basedpyright* 2>/dev/null || true",
    )
    out["checks"]["entry_points"] = {"ok": True, "msg": bp_bin_msg.strip()[:300]}

    target = f"{_DASK_SWEEVO_REPO_DIR}/dask/__init__.py"

    # Try entry points in order of preference. The dedicated binary side-steps
    # `python3 -m` cwd-on-sys.path issues entirely.
    candidates = [
        "basedpyright-langserver --stdio",
        "basedpyright-python-langserver --stdio",
        "python3 -m basedpyright.langserver --stdio",
    ]
    parsed = None
    chosen_cmd = None
    for cmd in candidates:
        payload = _lsp_handshake_script(cmd, target)
        encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
        code, msg = _exec(
            env,
            (
                f"echo {encoded} | base64 -d > /tmp/lsp_spike_handshake.py && "
                "python3 /tmp/lsp_spike_handshake.py"
            ),
            timeout=180,
        )
        parsed = _parse_handshake(msg)
        parsed.setdefault("attempted_command", cmd)
        if parsed.get("init_ok") and parsed.get("first_query_ok"):
            chosen_cmd = cmd
            break
    out["checks"]["lsp_handshake"] = parsed or {"error": "no candidate ran"}
    out["chosen_launch_command"] = chosen_cmd
    out["verdict"] = "QUALIFIED" if chosen_cmd else "DISQUALIFIED"
    return out


def _qualify_pyright(env) -> dict:
    out: dict = {"backend": "pyright", "checks": {}}

    code, msg = _exec(env, "command -v node && node --version")
    out["checks"]["node"] = {"ok": code == 0, "msg": msg.strip()}
    if code != 0:
        out["verdict"] = "DISQUALIFIED"
        return out

    code, msg = _exec(env, "command -v pyright-langserver || npm install -g pyright 2>&1 | tail -20")
    out["checks"]["pyright_langserver_present_or_install"] = {"ok": code == 0, "msg": msg.strip()[-500:]}
    if code != 0:
        out["verdict"] = "DISQUALIFIED"
        return out

    target = f"{_DASK_SWEEVO_REPO_DIR}/dask/__init__.py"
    payload = _lsp_handshake_script(
        "pyright-langserver --stdio", target,
    )
    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    code, msg = _exec(
        env,
        (
            f"echo {encoded} | base64 -d > /tmp/lsp_spike_handshake.py && "
            "python3 /tmp/lsp_spike_handshake.py"
        ),
        timeout=180,
    )
    parsed = _parse_handshake(msg)
    out["checks"]["lsp_handshake"] = parsed
    out["verdict"] = "QUALIFIED" if (parsed.get("init_ok") and parsed.get("first_query_ok")) else "DISQUALIFIED"
    return out


def _parse_handshake(stdout: str) -> dict:
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line.startswith("LSP_QUAL_REPORT="):
            try:
                return json.loads(line[len("LSP_QUAL_REPORT="):])
            except json.JSONDecodeError:
                return {"parse_error": line[:300]}
    return {"parse_error": "no LSP_QUAL_REPORT marker", "tail": (stdout or "")[-500:]}


async def _provision() -> tuple[str, object]:
    from benchmarks.sweevo.dataset import select_sweevo_instance
    from benchmarks.sweevo.sandbox import create_sweevo_test_sandbox
    from sandbox.testing import get_sandbox_service

    instance = select_sweevo_instance(instance_id=_DASK_SWEEVO_INSTANCE_ID)
    sandbox_name = f"lsp-qual-spike-{uuid.uuid4().hex[:8]}"
    _print(f"[spike] provisioning {_DASK_SWEEVO_INSTANCE_ID} as {sandbox_name} ...")
    t0 = time.perf_counter()
    result = await create_sweevo_test_sandbox(
        instance,
        sandbox_name=sandbox_name,
        repo_dir=_DASK_SWEEVO_REPO_DIR,
    )
    sandbox_id = str(result["sandbox_id"])
    _print(f"[spike] sandbox {sandbox_id} ready in {time.perf_counter() - t0:.1f}s")
    raw_sandbox = get_sandbox_service().get_sandbox_object(sandbox_id)
    return sandbox_id, raw_sandbox


def _teardown(sandbox_id: str) -> None:
    from sandbox.testing import delete_test_sandbox

    _print(f"[spike] tearing down {sandbox_id} ...")
    delete_test_sandbox(sandbox_id)


def main() -> int:
    sandbox_id, raw_sandbox = asyncio.run(_provision())
    try:
        report: dict = {
            "instance_id": _DASK_SWEEVO_INSTANCE_ID,
            "sandbox_id": sandbox_id,
        }
        _print("\n=== LSP qualification spike ===")
        _print(f"sandbox: {sandbox_id}  image: {_DASK_SWEEVO_INSTANCE_ID}")
        _print("\n--- basedpyright ---")
        bp = _qualify_basedpyright(raw_sandbox)
        report["basedpyright"] = bp
        for k, v in bp["checks"].items():
            ok = "OK" if v.get("ok") else "FAIL"
            _print(f"{k:50s} {ok}  {v.get('msg','')[:80] if isinstance(v.get('msg'), str) else ''}")
        _print(f"VERDICT: basedpyright {bp['verdict']}")

        if bp["verdict"] == "QUALIFIED":
            chosen = "basedpyright"
        else:
            _print("\n--- pyright ---")
            py = _qualify_pyright(raw_sandbox)
            report["pyright"] = py
            for k, v in py["checks"].items():
                ok = "OK" if v.get("ok") else "FAIL"
                _print(f"{k:50s} {ok}  {v.get('msg','')[:80] if isinstance(v.get('msg'), str) else ''}")
            _print(f"VERDICT: pyright {py['verdict']}")
            if py["verdict"] == "QUALIFIED":
                chosen = "pyright"
            else:
                report["LSP_BACKEND_CHOSEN"] = None
                _print("\n=== VERDICT: NEITHER QUALIFIED — Phase 3.6 does not ship ===")
                _print("\nFULL REPORT:\n" + json.dumps(report, indent=2))
                return 1

        report["LSP_BACKEND_CHOSEN"] = chosen
        _print(f"\n=== LSP_BACKEND_CHOSEN = {chosen!r} ===")
        _print("\nFULL REPORT:\n" + json.dumps(report, indent=2))
        return 0
    finally:
        _teardown(sandbox_id)


if __name__ == "__main__":
    sys.exit(main())
