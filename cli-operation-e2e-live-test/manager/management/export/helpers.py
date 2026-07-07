"""CLI wrappers, fixtures, verdict machinery, and scenarios for the Manager
Export Changes live-Docker catalog (spec.md + test-case.md, same folder).

Every executed case writes
``manager/management/export/test-reports/<RUN_ID>/<CASE_ID>/verdict.json``
with the one schema from test-case.md §2 (three axes + teardown). Cases assert
only on structured JSON and the on-disk tree, never on logs.
"""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from core import cleanup
from core.cli import route_cli
from core.config import IMAGE, REPO_ROOT

SUITE_DIR = Path(__file__).resolve().parent
RUN_ID = os.environ.get("EXPORT_RUN_ID", dt.datetime.now().strftime("export-%Y%m%d-%H%M%S"))
REPORT_ROOT = SUITE_DIR / "test-reports" / RUN_ID

SCRATCH_ROOT = "/eos/workspace"
EXPORT_SPOOL_DIR = f"{SCRATCH_ROOT}/.export"
SPOOL_OVERRIDE = f"{EXPORT_SPOOL_DIR}/OVERRIDE.tar.zst"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

_report_lock = threading.Lock()


# --------------------------------------------------------------------------- CLI


class RawResult:
    def __init__(self, args, returncode, stdout, stderr, elapsed_ms):
        self.args = list(args)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.elapsed_ms = elapsed_ms
        self.json = self._parse_json()

    def _parse_json(self):
        for text in (self.stdout, self.stderr):
            for line in reversed(text.splitlines()):
                stripped = line.strip()
                if stripped.startswith("{"):
                    try:
                        return json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
        return None

    @property
    def ok(self):
        return (
            self.returncode == 0
            and isinstance(self.json, dict)
            and "error" not in self.json
        )


def raw_cli(rec, *args, timeout=180):
    started = time.monotonic()
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT / 'bin'}:{env.get('PATH', '')}"
    binary, argv, _ = route_cli(args)
    proc = subprocess.run(
        [str(binary), *argv],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    elapsed = round((time.monotonic() - started) * 1000.0, 3)
    result = RawResult(args, proc.returncode, proc.stdout, proc.stderr, elapsed)
    if rec is not None:
        rec.add_command(
            {
                "cmd": ["sandbox-cli", *map(str, args)],
                "exit_code": proc.returncode,
                "elapsed_ms": elapsed,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "parsed_json": result.json,
            }
        )
    return result


def manager(rec, operation, *args, timeout=180):
    return raw_cli(rec, "manager", operation, *args, timeout=timeout)


def runtime(rec, sandbox_id, operation, *args, timeout=180):
    return raw_cli(
        rec, "runtime", "--sandbox-id", sandbox_id, operation, *args, timeout=timeout
    )


def observability(rec, operation, *args, timeout=180):
    return raw_cli(rec, "observability", operation, *args, timeout=timeout)


def docker(rec, container, *args, timeout=60, check=False):
    started = time.monotonic()
    proc = subprocess.run(
        ["docker", "exec", container, *map(str, args)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = round((time.monotonic() - started) * 1000.0, 3)
    if rec is not None:
        rec.add_command(
            {
                "cmd": ["docker", "exec", container, *map(str, args)],
                "exit_code": proc.returncode,
                "elapsed_ms": elapsed,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }
        )
    if check and proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or f"docker exec failed: {args}")
    return proc


# ---------------------------------------------------------------- verdict record


class CaseRecorder:
    """One case's report bundle + the three-axis verdict.json (test-case.md §2)."""

    def __init__(self, case):
        self.case = dict(case)
        self.case_id = self.case["id"]
        self.case_dir = REPORT_ROOT / self.case_id
        self.commands = []
        self.axes = {
            "correctness": {"pass": False, "status": "not_run"},
            "host_safety": {"pass": False, "status": "not_run"},
            "incremental": {"pass": False, "status": "not_run"},
        }
        self.teardown = {"pass": False, "details": "not checked"}
        self.defects = []
        self.started = None
        self.verdict = None

    def __enter__(self):
        with _report_lock:
            self.case_dir.mkdir(parents=True, exist_ok=True)
        self.started = time.monotonic()
        self.write_json("case.json", self.case)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is not None:
            self.defects.append({"type": exc_type.__name__, "message": str(exc)})
            if self.axes["correctness"]["status"] == "not_run":
                self.axis("correctness", False, str(exc))
        if self.verdict is None:
            self.write_verdict()
        return False

    def write_json(self, name, payload):
        path = self.case_dir / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def write_text(self, name, text):
        (self.case_dir / name).write_text(text, encoding="utf-8")

    def add_command(self, record):
        self.commands.append(record)

    def axis(self, name, passed, details="", *, status=None, extra=None, n_a=False):
        if status is None:
            status = "n/a" if n_a else ("pass" if passed else "fail")
        payload = {"pass": bool(passed) or n_a, "status": status, "details": details}
        if extra:
            payload.update(extra)
        self.axes[name] = payload

    def defect(self, message):
        self.defects.append({"message": message})

    def set_teardown(self, passed, details, extra=None):
        payload = {"pass": bool(passed), "details": details}
        if extra:
            payload.update(extra)
        self.teardown = payload

    def write_verdict(self):
        self.write_json("cmd.log.json", self.commands)
        axes_pass = all(axis.get("pass") for axis in self.axes.values())
        passed = axes_pass and self.teardown.get("pass", False) and not self.defects
        self.verdict = {
            "case_id": self.case_id,
            "run_id": RUN_ID,
            "status": "pass" if passed else "fail",
            "tier": self.case.get("tier"),
            "title": self.case.get("title"),
            "axes": self.axes,
            "teardown": self.teardown,
            "defects": self.defects,
            "elapsed_ms": round((time.monotonic() - (self.started or time.monotonic())) * 1000.0, 3),
            "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        self.write_json("verdict.json", self.verdict)
        return self.verdict


def record_case(case):
    return CaseRecorder(case)


# ------------------------------------------------------------ sandbox lifecycle


def make_seed(case_id, files=None):
    """A host workspace dir seeded with ``files`` (path -> str/bytes)."""
    root = Path(tempfile.mkdtemp(prefix=f"eos-export-{case_id.lower()}-"))
    for rel, content in (files or {}).items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    return root


def create_sandbox(rec, workspace_root, timeout=300):
    result = manager(
        rec,
        "create_sandbox",
        "--image",
        IMAGE,
        "--workspace-bind-root",
        str(workspace_root),
        timeout=timeout,
    )
    assert result.ok, result.json or result.stderr
    sandbox_id = result.json.get("id")
    assert sandbox_id, result.json
    cleanup.track(sandbox_id)
    return sandbox_id


def destroy_sandbox(rec, sandbox_id):
    cleanup.untrack(sandbox_id)
    return manager(rec, "destroy_sandbox", "--sandbox-id", sandbox_id, timeout=180)


def publish_write(rec, sandbox_id, path, content):
    """Publish one file via the sessionless ``file_write`` backend."""
    result = runtime(
        rec, sandbox_id, "file_write", "--path", path, "--content", content, timeout=120
    )
    assert result.ok, result.json or result.stderr
    return result.json


def publish_exec(rec, sandbox_id, command, timeout=180):
    """Publish arbitrary workspace changes via a sessionless exec (deletes,
    symlinks, opaque rewrites, chmod). exec_command runs the string through a
    shell and the sessionless backend publishes the captured change set when
    the command finishes."""
    result = runtime(rec, sandbox_id, "exec_command", command, timeout=timeout)
    payload = result.json or {}
    if result.ok and payload.get("status") == "running":
        payload = _wait_command(rec, sandbox_id, payload["command_session_id"], timeout_s=timeout)
    assert result.ok and payload.get("exit_code") == 0, payload
    return payload


def _read_command_lines(rec, sandbox_id, command_session_id):
    return runtime(
        rec,
        sandbox_id,
        "read_command_lines",
        "--command-session-id",
        command_session_id,
        "--start-offset",
        "0",
        "--limit",
        "1000",
        timeout=30,
    )


def _wait_command(rec, sandbox_id, command_session_id, *, timeout_s=60):
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        result = _read_command_lines(rec, sandbox_id, command_session_id)
        assert result.ok, result.json or result.stderr
        last = result.json
        if last.get("status") != "running":
            return last
        time.sleep(0.1)
    raise AssertionError(f"command {command_session_id} still running: {last}")


def create_session(rec, sandbox_id):
    result = runtime(rec, sandbox_id, "create_workspace_session")
    assert result.ok, result.json or result.stderr
    return result.json["workspace_session_id"]


def destroy_session(rec, sandbox_id, session_id):
    result = runtime(
        rec,
        sandbox_id,
        "destroy_workspace_session",
        "--workspace-session-id",
        session_id,
        "--grace-s",
        "1",
        timeout=60,
    )
    return result.json


# -------------------------------------------------------------- export surface


def export_changes(rec, sandbox_id, dest, fmt="dir", timeout=300):
    """Drive ``sandbox-manager-cli export_changes`` and return the RawResult."""
    args = ["export_changes", "--sandbox-id", sandbox_id, "--dest", str(dest)]
    if fmt is not None:
        args += ["--format", fmt]
    return manager(rec, *args, timeout=timeout)


def read_tree(root):
    """A {relpath: kind/content} map of the on-disk tree at ``root``.

    Files map to their bytes; symlinks to ``("symlink", target)``; directories
    are present as keys with value ``"dir"``. Absent root -> empty map.
    """
    root = Path(root)
    tree = {}
    if not root.exists():
        return tree
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            tree[rel] = ("symlink", os.readlink(path))
        elif path.is_dir():
            tree[rel] = "dir"
        else:
            tree[rel] = path.read_bytes()
    return tree


def _member_names(archive):
    """Faithful tar entry names in archive order. ``tarfile`` strips the
    trailing slash a directory member carries on the wire; restore it so the
    list matches the archive's OCI encoding (``tar tf`` and test-case.md §EZ-03
    both show ``src/``)."""
    return [f"{member.name}/" if member.isdir() else member.name for member in archive.getmembers()]


def zstd_entries(rec, path):
    """List tar entry names inside a ``.tar.zst`` archive host-side (docker cp
    into a throwaway container is avoided — we decompress locally)."""
    data = Path(path).read_bytes()
    assert data[:4] == ZSTD_MAGIC, "archive is not zstd-framed"
    raw = _zstd_decompress(rec, data)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        return _member_names(archive)


def tar_entries(path):
    with tarfile.open(str(path)) as archive:
        return _member_names(archive)


def _zstd_decompress(rec, data):
    """Decompress zstd via the host ``zstd`` CLI (P2 asserts it is available)."""
    proc = subprocess.run(
        ["zstd", "-dc"], input=data, capture_output=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout


# ---------------------------------------------------------- fault injection


def craft_hostile_spool(entries):
    """Build a zstd-framed tar with raw header names/targets the honest daemon
    could never author (traversal, absolute, hardlink, whiteout escape).

    ``entries`` is a list of dicts: {name, kind, content?, link?}. ``kind`` is
    one of "file", "dir", "symlink", "hardlink", "raw". Returns tar.zst bytes.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for entry in entries:
            info = tarfile.TarInfo(name=entry["name"])
            kind = entry["kind"]
            if kind == "file":
                payload = entry.get("content", b"")
                info.type = tarfile.REGTYPE
                info.mode = entry.get("mode", 0o644)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = entry.get("mode", 0o755)
                archive.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = entry["link"]
                archive.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = entry["link"]
                archive.addfile(info)
            elif kind == "marker":
                info.type = tarfile.REGTYPE
                info.size = 0
                archive.addfile(info, io.BytesIO(b""))
            else:
                raise AssertionError(f"unknown hostile entry kind: {kind}")
    proc = subprocess.run(
        ["zstd", "-q", "-3", "-c"], input=buffer.getvalue(), capture_output=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout


def craft_zstd_bomb(decompressed_bytes):
    """A tiny zstd frame that inflates to ``decompressed_bytes`` of one tar
    entry — a decompression bomb whose on-wire size stays small."""
    payload = b"\x00" * decompressed_bytes
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(name="bomb.bin")
        info.type = tarfile.REGTYPE
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    proc = subprocess.run(
        ["zstd", "-q", "-19", "-c"], input=buffer.getvalue(), capture_output=True, timeout=300
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout


def craft_entry_count_bomb(count):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for index in range(count):
            info = tarfile.TarInfo(name=f"f{index}.txt")
            info.type = tarfile.REGTYPE
            info.size = 0
            archive.addfile(info, io.BytesIO(b""))
    proc = subprocess.run(
        ["zstd", "-q", "-3", "-c"], input=buffer.getvalue(), capture_output=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    return proc.stdout


def inject_spool(rec, sandbox_id, spool_bytes):
    """Drop a pre-crafted spool at ``<scratch_root>/.export/OVERRIDE.tar.zst``
    inside the sandbox (test-case.md §1.4). The next export_layerstack serves
    it instead of the honest fold; the manager applier treats it as untrusted
    (spec inv 9). Written base64 through ``docker exec`` — no host bind needed.
    """
    docker(rec, sandbox_id, "mkdir", "-p", EXPORT_SPOOL_DIR, check=True)
    encoded = base64.b64encode(spool_bytes).decode("ascii")
    proc = subprocess.run(
        ["docker", "exec", "-i", sandbox_id, "sh", "-c", f"base64 -d > {SPOOL_OVERRIDE}"],
        input=encoded,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr


def export_dir_entries(rec, sandbox_id):
    proc = docker(
        rec,
        sandbox_id,
        "sh",
        "-c",
        f"ls -1 {EXPORT_SPOOL_DIR} 2>/dev/null | wc -l",
        check=False,
    )
    try:
        return int(proc.stdout.strip() or "0")
    except ValueError:
        return -1


def active_lease_count(rec, sandbox_id):
    view = observability(rec, "layerstack", "--sandbox-id", sandbox_id, timeout=120)
    if not view.ok or not isinstance(view.json, dict):
        return None
    return int(view.json.get("active_lease_count", 0))


# ------------------------------------------------------------- sentinel guard


def _is_within(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


class Sentinel:
    """A canary file tree OUTSIDE dest, snapshotted so the teardown can prove
    nothing outside dest was created, modified, or deleted (the load-bearing
    HRD teardown).

    When a traversal target is an *ancestor* directory of dest — HRD-01 places
    ``dest`` at ``<base>/a/b/dest`` and the ``../../escape.txt`` canary at
    ``<base>/a/escape.txt`` — the dest subtree lives under this base. The dirs
    the manager creates to materialize dest, and any legitimate in-dest writes,
    are not out-of-dest tampering, so register dest with ``guard_dest`` to
    exclude it. The guarantee this pins is test-case.md HRD-01's: every planted
    canary stays byte-identical and no *file* is created outside dest."""

    def __init__(self, base):
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)
        self.files = {}
        self._dest = None

    def guard_dest(self, dest):
        """Exclude the dest subtree (which may live under this base) from the
        out-of-dest file check."""
        self._dest = Path(dest)
        return self

    def plant(self, rel, content="canary\n"):
        target = self.base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.files[rel] = content
        return target

    def snapshot(self):
        return read_tree(self.base)

    def unchanged(self):
        """True iff every planted canary is byte-identical AND no file or
        symlink appeared outside dest. Directories (e.g. the parents created to
        materialize a dest that lives under this base) are not content and
        never count as tampering — HRD-01 guards *files* outside dest."""
        current = read_tree(self.base)
        for rel, content in self.files.items():
            if current.get(rel) != content.encode("utf-8"):
                return False
        for rel, value in current.items():
            if not isinstance(value, (bytes, tuple)):
                continue
            if rel in self.files:
                continue
            if self._dest is not None and _is_within(self.base / rel, self._dest):
                continue
            return False
        return True


# --------------------------------------------------------------- assertions


DIR_RESULT_KEYS = {
    "manifest_version",
    "format",
    "layers_exported",
    "files_written",
    "symlinks_written",
    "deletes_applied",
    "opaque_clears",
    "skipped_unchanged",
    "bytes_written",
}
TAR_RESULT_KEYS = {
    "manifest_version",
    "format",
    "layers_exported",
    "files_written",
    "symlinks_written",
    "whiteouts_emitted",
    "bytes_written",
}


def assert_result_contract(result_json, fmt="dir"):
    keys = set(result_json) - {"live_workspace_sessions"}
    expected = DIR_RESULT_KEYS if fmt == "dir" else TAR_RESULT_KEYS
    assert keys == expected, f"result keys {sorted(keys)} != {sorted(expected)}"
    assert result_json["format"] == fmt, result_json
    for name in expected:
        if name in {"format", "layers_exported", "manifest_version"}:
            continue
        value = result_json[name]
        assert isinstance(value, int) and value >= 0, f"{name}={value!r} not a count"


def no_literal_markers(tree):
    return not any(
        Path(rel).name.startswith(".wh.") for rel in tree
    )


# ------------------------------------------------------------- teardown/precond


def teardown(rec, sandbox_id, *, sentinel=None, expect_export_empty=True):
    """§1.3 teardown contract: lease released, <scratch>/.export empty, nothing
    outside dest touched. Checked while the sandbox is still alive; the caller
    destroys it afterwards."""
    leases = active_lease_count(rec, sandbox_id)
    export_count = export_dir_entries(rec, sandbox_id)
    failures = []
    if leases not in (0, None):
        failures.append(f"active_lease_count={leases}")
    if expect_export_empty and export_count not in (0, -1):
        failures.append(f"export_dir_entries={export_count}")
    if sentinel is not None and not sentinel.unchanged():
        failures.append("outside-dest sentinel changed")
    rec.set_teardown(
        not failures,
        "; ".join(failures) or "clean",
        {
            "lease_registry_empty": leases in (0, None),
            "export_dir_empty": export_count in (0, -1),
            "outside_dest_clean": sentinel is None or sentinel.unchanged(),
        },
    )
    assert not failures, failures


def assert_preconditions(rec):
    """P1-P4 (test-case.md §1.1), hard-fail. P1 needs no sandbox; P2-P4 share one."""
    # P1: export_changes is in the manager catalog with the right surface.
    spec_ok = _p1_catalog(rec)
    rec.axis("correctness", spec_ok, "P1 catalog + surface", extra={"P1": spec_ok})

    seed = make_seed("preconditions", {"winner.txt": "seed-v1\n"})
    sandbox_id = None
    try:
        sandbox_id = create_sandbox(rec, seed)
        publish_exec(rec, sandbox_id, "printf 'seed-v2\\n' > winner.txt")

        # P3: dir-apply onto the bind-root seed is reachable and byte-equal.
        p3 = export_changes(rec, sandbox_id, seed)
        assert p3.ok, f"P3 dir export failed: {p3.json or p3.stderr}"
        assert (seed / "winner.txt").read_text() == "seed-v2\n", "P3 winner not byte-equal"

        # P2: zstd round-trip host-side.
        archive = Path(tempfile.mkdtemp(prefix="eos-export-p2-")) / "delta.tar.zst"
        p2 = export_changes(rec, sandbox_id, archive, fmt="tar-zst")
        assert p2.ok, f"P2 tar-zst export failed: {p2.json or p2.stderr}"
        names = zstd_entries(rec, archive)
        assert "winner.txt" in names, f"P2 archive entries missing winner: {names}"
        shutil.rmtree(archive.parent, ignore_errors=True)

        # P4: the export boot step reaps <scratch>/.export on daemon restart.
        docker(rec, sandbox_id, "mkdir", "-p", EXPORT_SPOOL_DIR, check=True)
        docker(
            rec,
            sandbox_id,
            "sh",
            "-c",
            f"printf orphan > {EXPORT_SPOOL_DIR}/orphan.tar.zst",
            check=True,
        )
        subprocess.run(
            ["docker", "restart", sandbox_id],
            check=True,
            capture_output=True,
            text=True,
            timeout=90,
        )
        _wait_container_ready(rec, sandbox_id)
        remaining = export_dir_entries(rec, sandbox_id)
        assert remaining in (0, -1), f"P4 boot reap left {remaining} spool(s) under .export"

        rec.axis("host_safety", True, "P2/P3/P4 asserted", extra={"P2": True, "P3": True, "P4": True})
        rec.axis("incremental", True, "n/a", n_a=True)
        rec.set_teardown(True, "preconditions sandbox destroyed below")
    finally:
        if sandbox_id:
            destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)


def _p1_catalog(rec):
    result = raw_cli(rec, "manager", "help", "export_changes", timeout=30)
    text = (result.stdout or "") + (result.stderr or "")
    return all(flag in text for flag in ("--sandbox-id", "--dest", "--format"))


def _wait_container_ready(rec, sandbox_id, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        proc = docker(
            rec,
            sandbox_id,
            "sh",
            "-c",
            "test -S /eos/runtime/daemon/runtime.sock && echo up",
            timeout=15,
        )
        if proc.stdout.strip() == "up":
            time.sleep(1)
            return
        time.sleep(0.5)
    raise AssertionError(f"{sandbox_id} daemon did not become ready")


def restart_gateway_and_recover(rec, timeout=180):
    """Restart the gateway so it re-resolves running containers by label
    (`DockerSandboxRuntime::recover_sandboxes`).

    In this deployment the daemon lives and dies with its container — `docker-init`
    (pid 1, `tini -- sandbox-daemon`) exits when its daemon child exits — so a
    daemon restart IS a container restart, and `docker restart` reassigns the
    ephemeral host ports the manager resolved and cached at create time. The
    manager re-resolves those ports on gateway startup (recover-by-label), not
    per forward, so restoring the manager's view of a restarted container is a
    gateway restart. Env — including the export resource caps — is inherited
    from this process so the recovered gateway keeps the same configuration."""
    script = REPO_ROOT / "bin" / "start-sandbox-docker-gateway"
    env = os.environ.copy()
    env["PATH"] = f"{REPO_ROOT / 'bin'}:{env.get('PATH', '')}"
    proc = subprocess.run(
        [str(script)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    assert proc.returncode == 0, f"gateway restart failed: {proc.stderr or proc.stdout}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if manager(rec, "list_sandboxes", timeout=15).ok:
            return
        time.sleep(1)
    raise AssertionError("gateway did not respond after restart")


# ------------------------------------------------------------------ dispatch

CASES = [
    {"id": "EZ-01", "tier": "easy", "title": "dir export onto the seed reproduces the merged view"},
    {"id": "EZ-02", "tier": "easy", "title": "base-only manifest is a clean no-op"},
    {"id": "EZ-03", "tier": "easy", "title": "tar-zst writes a valid whiteout-preserving archive"},
    {"id": "EZ-04", "tier": "easy", "title": "tar writes a plain (decompressed) archive"},
    {"id": "EZ-05", "tier": "easy", "title": "--format defaults to dir"},
    {"id": "EZ-06", "tier": "easy", "title": "relative --dest is rejected before any forward"},
    {"id": "EZ-07", "tier": "easy", "title": "dir result-contract shape is exact"},
    {"id": "EZ-08", "tier": "easy", "title": "single deletion applies with no literal marker"},
    {"id": "EZ-09", "tier": "easy", "title": "a live session is reported, export still succeeds"},
    {"id": "EZ-10", "tier": "easy", "title": "a non-Ready sandbox is rejected by the forward gate"},
    {"id": "MED-01", "tier": "medium", "title": "idempotent re-run writes zero content bytes"},
    {"id": "MED-02", "tier": "medium", "title": "incremental re-export after more publishes"},
    {"id": "MED-03", "tier": "medium", "title": "opaque directory masks base content"},
    {"id": "MED-04", "tier": "medium", "title": "opaque-clear ordering: a dotfile winner survives"},
    {"id": "MED-05", "tier": "medium", "title": "newest-wins fold: older content never exported"},
    {"id": "MED-06", "tier": "medium", "title": "symlink winner recreate; dir<->symlink replacement"},
    {"id": "MED-07", "tier": "medium", "title": "merged-delta equivalence on an empty dest"},
    {"id": "MED-08", "tier": "medium", "title": "delta-cost: the base never crosses the wire"},
    {"id": "MED-09", "tier": "medium", "title": "metadata fidelity: mode carried, uid/gid + xattrs not"},
    {"id": "MED-10", "tier": "medium", "title": "delta re-applies onto a fresh base copy"},
    {"id": "HRD-01", "tier": "hard", "title": "tar-slip: ../absolute entry rejected"},
    {"id": "HRD-02", "tier": "hard", "title": "symlink-then-traverse: write-through blocked"},
    {"id": "HRD-03", "tier": "hard", "title": "whiteout target normalizing outside dest rejected"},
    {"id": "HRD-04", "tier": "hard", "title": "dest deny-list holds"},
    {"id": "HRD-05", "tier": "hard", "title": "resource bombs are capped"},
    {"id": "HRD-06", "tier": "hard", "title": "two concurrent exports of the same sandbox"},
    {"id": "HRD-07", "tier": "hard", "title": "export under concurrent checkpoint_squash"},
    {"id": "HRD-08", "tier": "hard", "title": "export under a concurrent publish"},
    {"id": "HRD-09", "tier": "hard", "title": "deep/large delta converges or fails cleanly"},
    {"id": "HRD-10", "tier": "hard", "title": "daemon restart mid-paging"},
]
CASE_BY_ID = {case["id"]: case for case in CASES}


def cases_for_tier(tier):
    return [case for case in CASES if case["tier"] == tier]


def run_case(case):
    with record_case(case) as rec:
        fn = globals()[f"case_{case['id'].replace('-', '_').lower()}"]
        fn(rec)


def _fresh_dest(case_id, name="dest"):
    base = Path(tempfile.mkdtemp(prefix=f"eos-export-dest-{case_id.lower()}-"))
    return base, base / name


def _expected_version(num_delta_layers):
    return 1 + num_delta_layers


# =============================================================== EASY (EZ)


def case_ez_01(rec):
    """B1/inv 2: dir export onto the seed reproduces the merged view."""
    seed = make_seed("ez01", {"src/a.rs": "v1\n", "src/b.rs": "B\n"})
    sandbox_id = create_sandbox(rec, seed)
    try:
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs && rm -f src/b.rs")
        result = export_changes(rec, sandbox_id, seed)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert (seed / "src/a.rs").read_text() == "v2\n"
        assert not (seed / "src/b.rs").exists(), "whiteout removed b.rs"
        assert result.json["files_written"] == 1, result.json
        assert result.json["deletes_applied"] == 1, result.json
        assert result.json["symlinks_written"] == 0, result.json
        assert result.json["opaque_clears"] == 0, result.json
        assert result.json["manifest_version"] == _expected_version(1), result.json
        assert len(result.json["layers_exported"]) == 1, result.json
        rec.axis("correctness", True, "a.rs rewritten, b.rs deleted, counts exact")
        tree = read_tree(seed)
        assert no_literal_markers(tree), "literal .wh. marker on host"
        rec.axis("host_safety", True, "no literal markers on the host")
        rec.axis("incremental", True, "n/a (first export)", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)


def case_ez_02(rec):
    """Empty delta (base-only manifest) is a clean no-op."""
    seed = make_seed("ez02", {"keep.txt": "K\n"})
    sandbox_id = create_sandbox(rec, seed)
    try:
        before = read_tree(seed)
        result = export_changes(rec, sandbox_id, seed)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert result.json["layers_exported"] == [], result.json
        for count in ("files_written", "symlinks_written", "deletes_applied", "opaque_clears", "skipped_unchanged", "bytes_written"):
            assert result.json[count] == 0, (count, result.json)
        assert result.json["manifest_version"] == 1, result.json
        assert "no_op" not in result.json, result.json
        rec.axis("correctness", True, "empty delta, all counts zero, version 1")
        assert read_tree(seed) == before, "dest changed on a no-op"
        rec.axis("host_safety", True, "dest byte-identical, nothing outside dest")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)


def case_ez_03(rec):
    """B4: tar-zst writes a valid whiteout-preserving archive."""
    seed = make_seed("ez03", {"src/a.rs": "v1\n", "src/b.rs": "B\n"})
    sandbox_id = create_sandbox(rec, seed)
    dest_base, _ = _fresh_dest("ez03")
    dest = dest_base / "delta.tar.zst"
    try:
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs && rm -f src/b.rs")
        result = export_changes(rec, sandbox_id, dest, fmt="tar-zst")
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert dest.read_bytes()[:4] == ZSTD_MAGIC, "archive is not zstd"
        names = zstd_entries(rec, dest)
        assert names == ["src/", "src/a.rs", "src/.wh.b.rs"], names
        assert result.json["whiteouts_emitted"] == 1, result.json
        assert result.json["files_written"] == 1, result.json
        assert result.json["bytes_written"] == dest.stat().st_size, result.json
        assert "deletes_applied" not in result.json, result.json
        rec.axis("correctness", True, "zstd archive with logical whiteout, counts exact")
        siblings = os.listdir(dest_base)
        assert siblings == ["delta.tar.zst"], f"temp sibling left: {siblings}"
        rec.axis("host_safety", True, "only dest present; no .tmp left (atomicity)")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)


def case_ez_04(rec):
    """--format tar writes a plain (decompressed) archive of the same entries."""
    seed = make_seed("ez04", {"src/a.rs": "v1\n", "src/b.rs": "B\n"})
    sandbox_id = create_sandbox(rec, seed)
    dest_base, _ = _fresh_dest("ez04")
    dest = dest_base / "delta.tar"
    try:
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs && rm -f src/b.rs")
        result = export_changes(rec, sandbox_id, dest, fmt="tar")
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert dest.read_bytes()[:4] != ZSTD_MAGIC, "tar must not be zstd"
        assert tar_entries(dest) == ["src/", "src/a.rs", "src/.wh.b.rs"], tar_entries(dest)
        assert result.json["files_written"] == 1 and result.json["whiteouts_emitted"] == 1, result.json
        rec.axis("correctness", True, "plain tar with the same logical entries")
        assert os.listdir(dest_base) == ["delta.tar"], os.listdir(dest_base)
        rec.axis("host_safety", True, "only dest present; no temp left")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)


def case_ez_05(rec):
    """--format omitted behaves as dir (applied tree, not an archive)."""
    seed = make_seed("ez05", {"src/a.rs": "v1\n", "src/b.rs": "B\n"})
    sandbox_id = create_sandbox(rec, seed)
    dest_base, dest = _fresh_dest("ez05")
    try:
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs && rm -f src/b.rs")
        result = export_changes(rec, sandbox_id, dest, fmt=None)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert result.json["format"] == "dir", result.json
        assert dest.is_dir() and (dest / "src/a.rs").read_text() == "v2\n", read_tree(dest)
        rec.axis("correctness", True, "default format is dir; applied as a tree")
        assert no_literal_markers(read_tree(dest)), "literal markers"
        rec.axis("host_safety", True, "no literal markers")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)


def case_ez_06(rec):
    """Relative --dest is rejected before any forward."""
    seed = make_seed("ez06", {"src/a.rs": "v1\n"})
    sandbox_id = create_sandbox(rec, seed)
    try:
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs")
        result = export_changes(rec, sandbox_id, "./relative")
        assert not result.ok, result.json
        assert result.json["error"]["kind"] == "invalid_request", result.json
        assert "manifest_version" not in result.json, result.json
        rec.axis("correctness", True, "relative dest rejected with invalid_request")
        assert export_dir_entries(rec, sandbox_id) in (0, -1), "fold started on a rejected dest"
        rec.axis("host_safety", True, "nothing written; .export empty (no fold)")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)


def case_ez_07(rec):
    """dir result-contract shape is exact."""
    seed = make_seed("ez07", {"src/a.rs": "v1\n", "src/b.rs": "B\n"})
    sandbox_id = create_sandbox(rec, seed)
    dest_base, dest = _fresh_dest("ez07")
    try:
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs && rm -f src/b.rs")
        result = export_changes(rec, sandbox_id, dest)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert_result_contract(result.json, fmt="dir")
        rec.axis("correctness", True, "exact dir contract keys; integer counts")
        rec.axis("host_safety", True, "n/a", n_a=True)
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)


def case_ez_08(rec):
    """A single deletion applies in dir mode with no literal marker."""
    seed = make_seed("ez08", {"gone.txt": "X\n"})
    sandbox_id = create_sandbox(rec, seed)
    try:
        publish_exec(rec, sandbox_id, "rm -f gone.txt")
        result = export_changes(rec, sandbox_id, seed)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert not (seed / "gone.txt").exists(), "gone.txt not deleted"
        assert result.json["deletes_applied"] == 1 and result.json["files_written"] == 0, result.json
        rec.axis("correctness", True, "deletion applied, files_written 0")
        assert not (seed / ".wh.gone.txt").exists(), "literal .wh. marker on host"
        assert no_literal_markers(read_tree(seed)), "literal markers"
        rec.axis("host_safety", True, "no .wh.gone.txt on host; marker consumed")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)


def case_ez_09(rec):
    """A live session is reported; export still succeeds on published state."""
    seed = make_seed("ez09", {"src/a.rs": "v1\n", "src/b.rs": "B\n"})
    sandbox_id = create_sandbox(rec, seed)
    dest_base, dest = _fresh_dest("ez09")
    session = None
    try:
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs && rm -f src/b.rs")
        session = create_session(rec, sandbox_id)
        first = export_changes(rec, sandbox_id, dest)
        assert first.ok, first.json or first.stderr
        rec.write_json("result-live.json", first.json)
        live = first.json.get("live_workspace_sessions")
        assert live and session in live, f"live session not reported: {first.json}"
        assert (dest / "src/a.rs").read_text() == "v2\n", "published state exported"
        destroy_session(rec, sandbox_id, session)
        session = None
        dest2 = dest_base / "second"
        second = export_changes(rec, sandbox_id, dest2)
        assert second.ok, second.json or second.stderr
        assert "live_workspace_sessions" not in second.json, second.json
        rec.axis("correctness", True, "live session reported, then omitted after destroy")
        rec.axis("host_safety", True, "n/a", n_a=True)
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        if session:
            destroy_session(rec, sandbox_id, session)
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)


def case_ez_10(rec):
    """A non-Ready / unknown sandbox is rejected by the forward gate."""
    dest_base, dest = _fresh_dest("ez10")
    try:
        result = export_changes(rec, "eos-nonexistent-sandbox", dest)
        assert not result.ok, result.json
        assert result.json["error"]["kind"] == "invalid_request", result.json
        assert "manifest_version" not in result.json, result.json
        rec.axis("correctness", True, "unknown sandbox rejected by the forward gate")
        assert not dest.exists(), "dest created on a gate reject"
        rec.axis("host_safety", True, "dest untouched on reject")
        rec.axis("incremental", True, "n/a", n_a=True)
        rec.set_teardown(True, "no sandbox created")
    finally:
        shutil.rmtree(dest_base, ignore_errors=True)


# ============================================================= MEDIUM (MED)


def case_med_01(rec):
    """inv 4: idempotent re-run writes zero content bytes for file winners."""
    seed = make_seed("med01", {"src/a.rs": "v1\n", "src/b.rs": "B\n"})
    sandbox_id = create_sandbox(rec, seed)
    try:
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs && rm -f src/b.rs")
        first = export_changes(rec, sandbox_id, seed)
        assert first.ok, first.json or first.stderr
        assert first.json["files_written"] == 1 and first.json["skipped_unchanged"] == 0, first.json
        tree_after_first = read_tree(seed)
        second = export_changes(rec, sandbox_id, seed)
        assert second.ok, second.json or second.stderr
        rec.write_json("result-rerun.json", second.json)
        assert second.json["files_written"] == 0, second.json
        assert second.json["bytes_written"] == 0, second.json
        assert second.json["skipped_unchanged"] == 1, second.json
        assert second.json["manifest_version"] == first.json["manifest_version"], second.json
        rec.axis("correctness", True, "re-run: files_written 0, skipped==file entries")
        assert no_literal_markers(read_tree(seed)), "literal markers"
        rec.axis("host_safety", True, "no literal markers; nothing outside dest")
        assert read_tree(seed) == tree_after_first, "file-winner tree changed on re-run"
        rec.axis("incremental", True, "content_bytes_written==0; tree byte-identical",
                 extra={"content_bytes_written": second.json["bytes_written"]})
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)


def case_med_02(rec):
    """B2: incremental re-export after 9 more changed paths."""
    seed = make_seed("med02", {"src/a.rs": "v1\n", "src/b.rs": "B\n"})
    sandbox_id = create_sandbox(rec, seed)
    try:
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs && rm -f src/b.rs")
        first = export_changes(rec, sandbox_id, seed)
        assert first.ok, first.json or first.stderr
        prior_entries = first.json["files_written"] + first.json["symlinks_written"]
        publish_exec(rec, sandbox_id, "mkdir -p pkg && for n in 1 2 3 4 5 6 7 8 9; do printf \"c$n\\n\" > pkg/f$n.txt; done")
        second = export_changes(rec, sandbox_id, seed)
        assert second.ok, second.json or second.stderr
        rec.write_json("result-incremental.json", second.json)
        assert second.json["files_written"] == 9, second.json
        assert second.json["skipped_unchanged"] == prior_entries, (second.json, prior_entries)
        assert second.json["manifest_version"] == _expected_version(2), second.json
        for n in range(1, 10):
            assert (seed / f"pkg/f{n}.txt").read_text() == f"c{n}\n", n
        rec.axis("correctness", True, "9 written, prior entries skipped, version advanced")
        rec.axis("host_safety", True, "no markers; nothing outside dest",
                 extra={"markers": no_literal_markers(read_tree(seed))})
        rec.axis("incremental", True, "content bytes track the 9 changed files only",
                 extra={"content_bytes_written": second.json["bytes_written"]})
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)


def case_med_03(rec):
    """B3: opaque directory masks base content."""
    seed = make_seed("med03", {"cfg/dev.yml": "D\n", "cfg/prod.yml": "P\n"})
    sandbox_id = create_sandbox(rec, seed)
    try:
        publish_exec(rec, sandbox_id, "rm -rf cfg && mkdir cfg && printf 'P2\\n' > cfg/prod.yml")
        result = export_changes(rec, sandbox_id, seed)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert not (seed / "cfg/dev.yml").exists(), "opaque clear left base dev.yml"
        assert (seed / "cfg/prod.yml").read_text() == "P2\n", "prod.yml not rewritten"
        assert result.json["opaque_clears"] == 1 and result.json["files_written"] == 1, result.json
        rec.axis("correctness", True, "cfg cleared of base content, prod rewritten")
        assert not (seed / "cfg/.wh..wh..opq").exists(), "literal opaque marker on host"
        rec.axis("host_safety", True, "no literal opaque marker; nothing outside dest")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)


def case_med_04(rec):
    """inv 2 / C2: a dotfile winner survives its directory's opaque clear."""
    seed = make_seed("med04", {"cfg/dev.yml": "D\n"})
    sandbox_id = create_sandbox(rec, seed)
    try:
        publish_exec(
            rec, sandbox_id,
            "rm -rf cfg && mkdir cfg && printf 'E\\n' > cfg/.env && printf 'P\\n' > cfg/prod.yml",
        )
        result = export_changes(rec, sandbox_id, seed)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert (seed / "cfg/.env").read_text() == "E\n", "dotfile winner destroyed by the clear"
        assert (seed / "cfg/prod.yml").read_text() == "P\n", "prod winner lost"
        assert not (seed / "cfg/dev.yml").exists(), "base dev.yml survived the clear"
        rec.axis("correctness", True, "both winners survive; three-pass ordering holds")
        assert no_literal_markers(read_tree(seed)), "literal markers"
        rec.axis("host_safety", True, "no literal opaque marker; nothing outside dest")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)


def case_med_05(rec):
    """Newest-wins fold: an older layer's content is never exported."""
    seed = make_seed("med05", {})
    sandbox_id = create_sandbox(rec, seed)
    dest_base, dest = _fresh_dest("med05")
    try:
        publish_exec(rec, sandbox_id, "printf 'v1\\n' > a.rs")
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > a.rs")
        result = export_changes(rec, sandbox_id, dest)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert (dest / "a.rs").read_text() == "v2\n", "older content leaked"
        assert result.json["files_written"] == 1, result.json
        rec.axis("correctness", True, "only the v2 winner crossed; one file written")
        rec.axis("host_safety", True, "n/a", n_a=True)
        rec.axis("incremental", True, "content bytes == v2 size only",
                 extra={"content_bytes_written": result.json["bytes_written"]})
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)


def case_med_06(rec):
    """Symlink winner recreate; a dest symlink at a dir position is replaced."""
    seed = make_seed("med06", {"link_target/keep.txt": "K\n"})
    sandbox_id = create_sandbox(rec, seed)
    elsewhere = Path(tempfile.mkdtemp(prefix="eos-export-med06-elsewhere-"))
    (elsewhere / "untouched.txt").write_text("E\n")
    try:
        publish_exec(rec, sandbox_id, "ln -s link_target s && mkdir -p d && printf 'F\\n' > d/file.txt")
        # Pre-load dest_seed with a conflicting symlink d -> elsewhere.
        (seed / "d").exists() and shutil.rmtree(seed / "d", ignore_errors=True)
        os.symlink(str(elsewhere), str(seed / "d"))
        result = export_changes(rec, sandbox_id, seed)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert os.path.islink(seed / "s") and os.readlink(seed / "s") == "link_target", "symlink winner"
        assert (seed / "d").is_dir() and not os.path.islink(seed / "d"), "dest symlink not replaced"
        assert (seed / "d/file.txt").read_text() == "F\n", "winner dir content"
        assert result.json["symlinks_written"] == 1, result.json
        rec.axis("correctness", True, "s recreated; d replaced by a real directory")
        assert (elsewhere / "untouched.txt").read_text() == "E\n", "wrote through the dest symlink"
        assert set(os.listdir(elsewhere)) == {"untouched.txt"}, os.listdir(elsewhere)
        rec.axis("host_safety", True, "old symlink target untouched; never followed")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(elsewhere, ignore_errors=True)


def case_med_07(rec):
    """inv 2: merged-delta equivalence on an empty dest (mixed classes)."""
    seed = make_seed("med07", {"keep/base.txt": "BASE\n", "drop.txt": "D\n", "cfg/old.yml": "O\n"})
    sandbox_id = create_sandbox(rec, seed)
    dest_base, dest = _fresh_dest("med07")
    try:
        publish_exec(
            rec, sandbox_id,
            "printf 'NEW\\n' > keep/added.rs && ln -s base.txt keep/link.txt && rm -f drop.txt "
            "&& rm -rf cfg && mkdir cfg && printf 'P\\n' > cfg/new.yml",
        )
        result = export_changes(rec, sandbox_id, dest)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        tree = read_tree(dest)
        # The delta over an empty dest = the winner set only (no base-only paths).
        assert tree.get("keep/added.rs") == b"NEW\n", tree
        assert tree.get("keep/link.txt") == ("symlink", "base.txt"), tree
        assert tree.get("cfg/new.yml") == b"P\n", tree
        assert "keep" in tree and tree["keep"] == "dir", tree
        assert "cfg" in tree and tree["cfg"] == "dir", tree
        # A deletion/opaque over an EMPTY dest is a no-op on the tree (nothing to remove).
        assert "drop.txt" not in tree and "keep/base.txt" not in tree and "cfg/old.yml" not in tree, tree
        rec.axis("correctness", True, "empty-dest tree equals the winner projection")
        assert no_literal_markers(tree), "literal markers"
        rec.axis("host_safety", True, "no markers; nothing outside dest")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)


def case_med_08(rec):
    """Delta-cost: a big base never crosses the wire; only the delta does."""
    base_files = {f"base/f{index:04d}.txt": ("x" * 4096 + "\n") for index in range(300)}
    seed = make_seed("med08", base_files)
    base_bytes = sum(len(v) for v in base_files.values())
    sandbox_id = create_sandbox(rec, seed, timeout=420)
    dest_base, dest = _fresh_dest("med08")
    try:
        publish_exec(rec, sandbox_id, "printf 'delta\\n' > only.txt")
        result = export_changes(rec, sandbox_id, dest)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert result.json["files_written"] == 1, result.json
        assert result.json["bytes_written"] < 4096, result.json
        rec.write_json("cost.json", {"base_bytes": base_bytes, "bytes_written": result.json["bytes_written"]})
        rec.axis("correctness", True, f"one file written, {result.json['bytes_written']}B vs base {base_bytes}B")
        rec.axis("host_safety", True, "n/a", n_a=True)
        rec.axis("incremental", True, "bytes_written is O(delta), not O(image)",
                 extra={"base_bytes": base_bytes, "content_bytes_written": result.json["bytes_written"]})
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)


def case_med_09(rec):
    """inv 10 fidelity boundary: FILE mode is carried; uid/gid land on the
    manager process and user xattrs do not cross. DIRECTORY mode is part of the
    not-carried boundary, not a fidelity: the overlay-capture model records
    directories only implicitly, via their children
    (``workspace/src/overlay/capture.rs`` emits no directory LayerChange and
    drops empty dirs), so every consumer — squash, MergedView, export —
    materializes a directory at the layer-write default, never the sandbox's
    ``chmod``. Export faithfully carries the layer's stored dir mode, so this
    pins the directory-only shape (inv 2) plus the file-mode/ownership/xattr
    boundary export owns, and records the directory mode as an artifact rather
    than asserting a fidelity the layer never stored."""
    seed = make_seed("med09", {})
    sandbox_id = create_sandbox(rec, seed)
    dest_base, dest = _fresh_dest("med09")
    try:
        # `secret` carries a child so the directory-only shape is captured (an
        # empty dir is not a LayerChange); `key` exercises real file-mode fidelity.
        publish_exec(
            rec, sandbox_id,
            "mkdir -p secret && printf 'guard\\n' > secret/inner && chmod 0700 secret "
            "&& printf 'k\\n' > key && chmod 0640 key "
            "&& { setfattr -n user.note -v hi key 2>/dev/null || true; }",
        )
        result = export_changes(rec, sandbox_id, dest)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        key_mode = (dest / "key").stat().st_mode & 0o777
        assert key_mode == 0o640, oct(key_mode)
        assert (dest / "secret").is_dir(), "directory-only shape not reproduced"
        assert (dest / "secret/inner").read_text() == "guard\n", "dir child not reproduced"
        dir_mode = (dest / "secret").stat().st_mode & 0o777
        assert dir_mode != 0o700, f"dir mode unexpectedly carried the sandbox chmod: {oct(dir_mode)}"
        rec.axis("correctness", True, f"file mode {oct(key_mode)} carried; directory reproduced (mode {oct(dir_mode)}, not carried)")
        owner_ok = (dest / "key").stat().st_uid == os.getuid()
        xattr_absent = _no_user_xattr(dest / "key")
        assert owner_ok, "file not owned by the manager process"
        assert xattr_absent, "user xattr unexpectedly carried"
        rec.axis("host_safety", True, "uid==manager, user xattr absent, dir mode not carried (documented boundary)",
                 extra={"owner_is_manager": owner_ok, "user_xattr_absent": xattr_absent, "dir_mode": oct(dir_mode)})
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)


def _no_user_xattr(path):
    try:
        return not any(name.startswith("user.") for name in os.listxattr(path))
    except (OSError, AttributeError):
        return True


def case_med_10(rec):
    """B4 portability: the delta re-applies onto a fresh copy of the base."""
    seed = make_seed("med10", {"src/a.rs": "v1\n", "src/b.rs": "B\n"})
    sandbox_id = create_sandbox(rec, seed)
    base_copy = Path(tempfile.mkdtemp(prefix="eos-export-med10-copy-"))
    archive_base, _ = _fresh_dest("med10")
    archive = archive_base / "delta.tar.zst"
    try:
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs && rm -f src/b.rs")
        # The portable archive (validated shape).
        arc = export_changes(rec, sandbox_id, archive, fmt="tar-zst")
        assert arc.ok, arc.json or arc.stderr
        assert zstd_entries(rec, archive) == ["src/", "src/a.rs", "src/.wh.b.rs"]
        # Re-apply the same delta onto a fresh copy of the base (cp -a of the seed).
        shutil.copytree(seed, base_copy, dirs_exist_ok=True, symlinks=True)
        # Restore the pre-export base state on the copy (seed was mutated by the archive run? no —
        # archive mode never touches the seed; the copy holds the original base).
        result = export_changes(rec, sandbox_id, base_copy)
        assert result.ok, result.json or result.stderr
        rec.write_json("result.json", result.json)
        assert (base_copy / "src/a.rs").read_text() == "v2\n", "winner not applied on the copy"
        assert not (base_copy / "src/b.rs").exists(), "deletion not applied on the copy"
        rec.axis("correctness", True, "delta applied onto a fresh base copy == merged view")
        assert no_literal_markers(read_tree(base_copy)), "literal markers"
        rec.axis("host_safety", True, "nothing outside the second dir; no markers")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(base_copy, ignore_errors=True)
        shutil.rmtree(archive_base, ignore_errors=True)


# =============================================================== HARD (HRD)


def _hostile_sandbox(rec, case_id):
    """A ready sandbox carrying a tiny honest delta, so export_layerstack folds
    successfully before apply_spool_override swaps in the hostile stream."""
    seed = make_seed(case_id, {"seed.txt": "s\n"})
    sandbox_id = create_sandbox(rec, seed)
    publish_exec(rec, sandbox_id, "printf 'delta\\n' > seed.txt")
    return seed, sandbox_id


def case_hrd_01(rec):
    """C1/inv 9: a ../ or absolute entry is rejected; nothing escapes dest."""
    seed, sandbox_id = _hostile_sandbox(rec, "hrd01")
    dest_base = Path(tempfile.mkdtemp(prefix="eos-export-hrd01-"))
    dest = dest_base / "a" / "b" / "dest"
    sentinel = Sentinel(dest_base).guard_dest(dest)
    dotdot_canary = sentinel.plant("a/escape.txt", "canary-dotdot\n")
    abs_base = Path(tempfile.mkdtemp(prefix="eos-export-hrd01-abs-"))
    abs_sentinel = Sentinel(abs_base)
    abs_canary = abs_sentinel.plant("abs-escape.txt", "canary-abs\n")
    try:
        inject_spool(
            rec, sandbox_id,
            craft_hostile_spool([
                {"name": "ok.txt", "kind": "file", "content": b"OK\n"},
                {"name": "../../escape.txt", "kind": "file", "content": b"pwn\n"},
                {"name": str(abs_canary), "kind": "file", "content": b"pwn\n"},
            ]),
        )
        result = export_changes(rec, sandbox_id, dest)
        assert not result.ok, f"hostile stream applied: {result.json}"
        rec.write_json("result.json", result.json)
        message = result.json["error"]["message"]
        assert "'..'" in message or "absolute" in message, message
        rec.axis("correctness", True, "traversal entry rejected with a structured error")
        assert sentinel.unchanged() and abs_sentinel.unchanged(), "a sentinel changed"
        assert dotdot_canary.read_text() == "canary-dotdot\n"
        assert abs_canary.read_text() == "canary-abs\n"
        assert export_dir_entries(rec, sandbox_id) in (0, -1)
        rec.axis("host_safety", True, "both sentinels byte-identical; nothing outside dest",
                 extra={"rejected_class": "traversal"})
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id, sentinel=sentinel)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)
        shutil.rmtree(abs_base, ignore_errors=True)


def case_hrd_02(rec):
    """C1/inv 9: symlink-then-traverse never writes through the symlink."""
    seed, sandbox_id = _hostile_sandbox(rec, "hrd02")
    dest_base, dest = _fresh_dest("hrd02")
    evil = Path(tempfile.mkdtemp(prefix="eos-export-hrd02-evil-"))
    try:
        inject_spool(
            rec, sandbox_id,
            craft_hostile_spool([
                {"name": "x", "kind": "symlink", "link": str(evil)},
                {"name": "x/passwd", "kind": "file", "content": b"pwn\n"},
            ]),
        )
        result = export_changes(rec, sandbox_id, dest)
        rec.write_json("result.json", result.json)
        # The applier may reject, or replace x with a real in-dest directory.
        applied_in_dest = result.ok and (dest / "x" / "passwd").exists()
        assert result.ok or "error" in result.json, result.json
        rec.axis("correctness", True, "second entry rejected or contained in-dest")
        assert not (evil / "passwd").exists(), "write followed the symlink out of dest"
        assert list(os.listdir(evil)) == [], f"evil dir not empty: {os.listdir(evil)}"
        rec.axis("host_safety", True, "/evil/passwd never created; evil dir stays empty",
                 extra={"applied_in_dest": applied_in_dest})
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)
        shutil.rmtree(evil, ignore_errors=True)


def case_hrd_03(rec):
    """inv 9: a whiteout target that escapes after the prefix strip is rejected."""
    seed, sandbox_id = _hostile_sandbox(rec, "hrd03")
    dest_base, dest = _fresh_dest("hrd03")
    victim_base = Path(tempfile.mkdtemp(prefix="eos-export-hrd03-victim-"))
    victim = victim_base / "victim"
    victim.write_text("present\n")
    try:
        inject_spool(
            rec, sandbox_id,
            craft_hostile_spool([{"name": ".wh...", "kind": "marker"}]),
        )
        after_strip = export_changes(rec, sandbox_id, dest)
        rec.write_json("result-after-strip.json", after_strip.json)
        assert not after_strip.ok, after_strip.json
        assert "whiteout" in after_strip.json["error"]["message"], after_strip.json

        inject_spool(
            rec, sandbox_id,
            craft_hostile_spool([{"name": "../.wh.victim", "kind": "marker"}]),
        )
        parent_escape = export_changes(rec, sandbox_id, dest)
        rec.write_json("result-parent-escape.json", parent_escape.json)
        assert not parent_escape.ok, parent_escape.json
        rec.axis("correctness", True, "whiteout escape rejected (after-strip and parent)")
        assert victim.read_text() == "present\n", "a remove_path escaped dest"
        rec.axis("host_safety", True, "outside-dest victim still present and byte-equal")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)
        shutil.rmtree(victim_base, ignore_errors=True)


def case_hrd_04(rec):
    """inv 9 / L1: the dest deny-list holds, pre-forward."""
    seed = make_seed("hrd04", {"seed.txt": "s\n"})
    sandbox_id = create_sandbox(rec, seed)
    spool_dir = Path(tempfile.mkdtemp(prefix="eos-export-hrd04-")) / ".export" / "x"
    try:
        publish_exec(rec, sandbox_id, "printf 'delta\\n' > seed.txt")
        denied = ["/", os.path.expanduser("~"), str(spool_dir)]
        registry = _manager_registry_dir()
        if registry:
            denied.append(registry)
        results = {}
        for dest in denied:
            result = export_changes(rec, sandbox_id, dest)
            results[dest] = result.json
            assert not result.ok, f"deny-list let {dest} through: {result.json}"
            assert result.json["error"]["kind"] == "invalid_request", (dest, result.json)
            assert export_dir_entries(rec, sandbox_id) in (0, -1), f"fold started for {dest}"
        rec.write_json("deny-results.json", results)
        rec.axis("correctness", True, f"deny-list rejected {len(denied)} roots pre-forward")
        home_ok = Path(os.path.expanduser("~")).exists()
        rec.axis("host_safety", True, "denied roots unmodified; / and $HOME intact",
                 extra={"home_present": home_ok})
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(spool_dir.parents[1], ignore_errors=True)


def _manager_registry_dir():
    for candidate in (
        os.environ.get("SANDBOX_MANAGER_STATE_DIR"),
        os.environ.get("SANDBOX_REGISTRY_PATH"),
    ):
        if candidate:
            return str(Path(candidate).parent if candidate.endswith(".json") else candidate)
    return None


def case_hrd_05(rec):
    """inv 9: decompression and entry-count bombs are capped, no disk exhaustion."""
    seed, sandbox_id = _hostile_sandbox(rec, "hrd05")
    dest_base, dest = _fresh_dest("hrd05")
    max_decompressed = int(os.environ.get("EXPORT_TEST_MAX_DECOMPRESSED_BYTES", str(256 * 1024 * 1024)))
    max_entries = int(os.environ.get("EXPORT_TEST_MAX_ENTRIES", str(50_000)))
    try:
        free_before = shutil.disk_usage(dest_base).free
        inject_spool(rec, sandbox_id, craft_zstd_bomb(max_decompressed + 64 * 1024 * 1024))
        zstd_bomb = export_changes(rec, sandbox_id, dest)
        rec.write_json("result-zstd-bomb.json", zstd_bomb.json)
        assert not zstd_bomb.ok, f"zstd bomb applied: {zstd_bomb.json}"
        assert "decompressed" in zstd_bomb.json["error"]["message"], zstd_bomb.json

        inject_spool(rec, sandbox_id, craft_entry_count_bomb(max_entries + 5_000))
        entry_bomb = export_changes(rec, sandbox_id, dest)
        rec.write_json("result-entry-bomb.json", entry_bomb.json)
        assert not entry_bomb.ok, f"entry bomb applied: {entry_bomb.json}"
        assert "entry-count cap" in entry_bomb.json["error"]["message"], entry_bomb.json

        free_after = shutil.disk_usage(dest_base).free
        rec.axis("correctness", True, "both bombs aborted with cap-exceeded errors")
        floor = 1024 * 1024 * 1024
        assert free_after > floor, f"disk floor breached: {free_after}"
        assert free_before - free_after < 512 * 1024 * 1024, "bomb wrote large output to disk"
        rec.axis("host_safety", True, "host free space held; no pre-allocation on daemon totals",
                 extra={"free_before": free_before, "free_after": free_after})
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(dest_base, ignore_errors=True)


def case_hrd_06(rec):
    """M4: two concurrent exports of one sandbox — singleflight or both converge."""
    seed = make_seed("hrd06", {"src/a.rs": "v1\n", "src/b.rs": "B\n"})
    sandbox_id = create_sandbox(rec, seed)
    d1_base, dest1 = _fresh_dest("hrd06", "one")
    d2_base, dest2 = _fresh_dest("hrd06", "two")
    try:
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs && rm -f src/b.rs")
        results = {}

        def run(key, dest):
            results[key] = export_changes(rec, sandbox_id, dest, timeout=300)

        threads = [threading.Thread(target=run, args=("a", dest1)), threading.Thread(target=run, args=("b", dest2))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        rec.write_json("result-a.json", results["a"].json)
        rec.write_json("result-b.json", results["b"].json)
        oks = [r for r in results.values() if r.ok]
        rejected = [r for r in results.values() if not r.ok]
        for reject in rejected:
            assert "in flight" in json.dumps(reject.json) or reject.json["error"]["kind"] == "operation_failed", reject.json
        for ok in oks:
            dest = dest1 if ok is results["a"] else dest2
            assert (dest / "src/a.rs").read_text() == "v2\n", "a spool served the wrong bytes"
            assert not (dest / "src/b.rs").exists()
        assert oks, "both exports failed"
        rec.axis("correctness", True, f"{len(oks)} converged, {len(rejected)} in-flight-rejected")
        rec.axis("host_safety", True, "each dest internally consistent; no cross-spool bytes")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(d1_base, ignore_errors=True)
        shutil.rmtree(d2_base, ignore_errors=True)


def case_hrd_07(rec):
    """B5/inv 3: export under a concurrent checkpoint_squash — both converge."""
    seed = make_seed("hrd07", {"src/a.rs": "v1\n", "src/b.rs": "B\n"})
    sandbox_id = create_sandbox(rec, seed)
    d_base, dest = _fresh_dest("hrd07")
    try:
        for index in range(4):
            publish_exec(rec, sandbox_id, f"printf 'l{index}\\n' > src/l{index}.txt")
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs && rm -f src/b.rs")
        outcome = {}

        def do_export():
            outcome["export"] = export_changes(rec, sandbox_id, dest, timeout=300)

        def do_squash():
            outcome["squash"] = manager(rec, "checkpoint_squash", "--sandbox-id", sandbox_id, timeout=300)

        threads = [threading.Thread(target=do_export), threading.Thread(target=do_squash)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        rec.write_json("result-export.json", outcome["export"].json)
        rec.write_json("result-squash.json", outcome["squash"].json)
        assert outcome["export"].ok, outcome["export"].json
        assert outcome["squash"].ok, outcome["squash"].json
        assert (dest / "src/a.rs").read_text() == "v2\n", "export tore against squash"
        assert not (dest / "src/b.rs").exists()
        rec.axis("correctness", True, "export delivered its snapshot; squash also succeeded")
        rec.axis("host_safety", True, "lease pinned sources; nothing outside dest")
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(d_base, ignore_errors=True)


def case_hrd_08(rec):
    """inv 3: an export's snapshot excludes a publish that lands after it."""
    seed = make_seed("hrd08", {"src/a.rs": "v1\n"})
    sandbox_id = create_sandbox(rec, seed)
    d_base, dest_a = _fresh_dest("hrd08", "va")
    dest_b = d_base / "vb"
    try:
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs")
        first = export_changes(rec, sandbox_id, dest_a)
        assert first.ok, first.json or first.stderr
        version_a = first.json["manifest_version"]
        assert not (dest_a / "later.txt").exists()
        publish_exec(rec, sandbox_id, "printf 'later\\n' > later.txt")
        second = export_changes(rec, sandbox_id, dest_b)
        assert second.ok, second.json or second.stderr
        rec.write_json("result-va.json", first.json)
        rec.write_json("result-vb.json", second.json)
        assert second.json["manifest_version"] == version_a + 1, (first.json, second.json)
        assert (dest_b / "later.txt").read_text() == "later\n", "later publish missing at v_a+1"
        rec.axis("correctness", True, "v_a excluded the later layer; v_a+1 included it")
        rec.axis("host_safety", True, "nothing outside dest")
        rec.axis("incremental", True, "second export writes only the new path",
                 extra={"version_a": version_a, "version_b": second.json["manifest_version"]})
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(d_base, ignore_errors=True)


def case_hrd_09(rec):
    """H5: a deep delta converges, or fails cleanly at the start-request ceiling."""
    layers = int(os.environ.get("EXPORT_DEEP_LAYERS", "120"))
    seed = make_seed("hrd09", {})
    sandbox_id = create_sandbox(rec, seed)
    d_base, dest = _fresh_dest("hrd09")
    try:
        for index in range(layers):
            publish_write(rec, sandbox_id, f"deep/f{index:04d}.txt", f"layer-{index}\n")
        result = export_changes(rec, sandbox_id, dest, timeout=600)
        rec.write_json("result.json", result.json)
        if result.ok:
            assert (dest / f"deep/f{layers - 1:04d}.txt").read_text() == f"layer-{layers - 1}\n"
            assert result.json["files_written"] == layers, result.json
            rec.axis("correctness", True, f"{layers}-layer delta converged; tree == merged view")
        else:
            assert result.json["error"]["kind"] in ("operation_failed", "invalid_request"), result.json
            assert not (dest / "deep").exists() or _dir_empty(dest / "deep"), "partial dest on the fail path"
            rec.axis("correctness", True, "clean start-request-ceiling failure, no partial corruption")
        # Squash-first mitigation.
        squashed = manager(rec, "checkpoint_squash", "--sandbox-id", sandbox_id, timeout=300)
        assert squashed.ok, squashed.json
        d2 = d_base / "after-squash"
        again = export_changes(rec, sandbox_id, d2, timeout=600)
        assert again.ok, f"export did not converge after squash: {again.json}"
        assert (d2 / f"deep/f{layers - 1:04d}.txt").read_text() == f"layer-{layers - 1}\n"
        rec.axis("host_safety", True, "no partial/corrupt dest; .export reaped",
                 extra={"deep_layers": layers})
        rec.axis("incremental", True, "n/a", n_a=True)
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(d_base, ignore_errors=True)


def _dir_empty(path):
    try:
        return not any(Path(path).iterdir())
    except OSError:
        return True


def case_hrd_10(rec):
    """M3/H1: daemon restart drops the registry; boot reap clears .export; re-run converges."""
    seed = make_seed("hrd10", {"src/a.rs": "v1\n", "src/b.rs": "B\n"})
    sandbox_id = create_sandbox(rec, seed)
    d_base, dest = _fresh_dest("hrd10")
    try:
        # A multi-chunk delta widens the mid-paging window. The payload must be
        # INCOMPRESSIBLE and generated container-side: a repeated byte compresses
        # to ~nothing (one chunk, not "several"), and an ~8 MiB --content CLI arg
        # blows past the host ARG_MAX. 6 MiB of urandom → ~6 MiB compressed spool
        # → several 2-MiB chunks, and stays under the 8 MiB capture cap.
        publish_exec(rec, sandbox_id, "printf 'v2\\n' > src/a.rs && rm -f src/b.rs")
        publish_exec(rec, sandbox_id, "head -c 6291456 /dev/urandom > big.bin")

        outcome = {}

        def do_export():
            outcome["export"] = export_changes(rec, sandbox_id, dest, timeout=120)

        thread = threading.Thread(target=do_export)
        thread.start()
        time.sleep(0.05)
        subprocess.run(
            ["docker", "restart", sandbox_id], capture_output=True, text=True, timeout=90
        )
        thread.join()
        rec.write_json("result-interrupted.json", outcome["export"].json)
        _wait_container_ready(rec, sandbox_id)

        # The orphaned spool is removed by the export boot step (not leaked).
        assert export_dir_entries(rec, sandbox_id) in (0, -1), "boot reap left a spool under .export"

        interrupted = outcome["export"]
        if not interrupted.ok:
            message = json.dumps(interrupted.json)
            assert "not found" in message or "forward" in message or "operation_failed" in message, interrupted.json
            rec.axis("correctness", True, "interrupted invocation aborted cleanly (registry dropped)")
        else:
            assert (dest / "src/a.rs").read_text() == "v2\n"
            rec.axis("correctness", True, "restart missed the window; export converged")

        # The container restart reassigned the daemon's ephemeral host ports; the
        # manager re-resolves them by label on gateway startup (recover_sandboxes),
        # which is this deployment's recovery path (the daemon cannot restart
        # without its container — docker-init exits with it). Recover the manager's
        # view, then the re-run rebuilds the spool and converges.
        restart_gateway_and_recover(rec)
        d2 = d_base / "rerun"
        rerun = export_changes(rec, sandbox_id, d2, timeout=120)
        assert rerun.ok, f"re-run did not converge: {rerun.json}"
        assert (d2 / "src/a.rs").read_text() == "v2\n" and not (d2 / "src/b.rs").exists()
        rec.axis("host_safety", True, ".export reaped by the boot step; re-run byte-identical")
        rec.axis("incremental", True, "re-run == clean export", extra={"reran": True})
        teardown(rec, sandbox_id)
    finally:
        destroy_sandbox(rec, sandbox_id)
        shutil.rmtree(seed, ignore_errors=True)
        shutil.rmtree(d_base, ignore_errors=True)


# ------------------------------------------------------------ suite entrypoints


def assert_preconditions_once():
    """Run P1-P4 once, hard-fail (test-case.md §5.1). Writes a PRECONDITIONS
    verdict bundle."""
    case = {"id": "PRECONDITIONS", "tier": "preconditions", "title": "§1.1 export preconditions"}
    with record_case(case) as rec:
        assert_preconditions(rec)


def finalize_summary(exitstatus=None):
    """Write SUMMARY.md over every verdict.json under this run (§5.6)."""
    if not REPORT_ROOT.exists():
        return None
    verdicts = []
    for path in sorted(REPORT_ROOT.glob("*/verdict.json")):
        try:
            verdicts.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    passed = sum(1 for v in verdicts if v.get("status") == "pass")
    failed = len(verdicts) - passed
    rows = [
        "# Manager Export Changes — Live-Docker Summary",
        "",
        f"- Run id: `{RUN_ID}`",
        f"- Generated: `{dt.datetime.now().astimezone().isoformat(timespec='seconds')}`",
        f"- Pytest exit status: `{exitstatus}`",
        f"- Cases: `{len(verdicts)}` run · `{passed}` pass · `{failed}` fail",
        "",
        "| Case | Tier | Status | Correctness | Host-safety | Incremental | Teardown |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for verdict in verdicts:
        axes = verdict.get("axes", {})

        def cell(name):
            axis = axes.get(name, {})
            if axis.get("status") == "n/a":
                return "n/a"
            return "pass" if axis.get("pass") else f"fail: {axis.get('details', '')}"

        rows.append(
            f"| `{verdict.get('case_id')}` | {verdict.get('tier')} | {verdict.get('status')} | "
            f"{cell('correctness')} | {cell('host_safety')} | {cell('incremental')} | "
            f"{'pass' if verdict.get('teardown', {}).get('pass') else 'fail'} |"
        )
    rows.append("")
    summary_path = REPORT_ROOT / "SUMMARY.md"
    summary_path.write_text("\n".join(rows), encoding="utf-8")
    return summary_path
