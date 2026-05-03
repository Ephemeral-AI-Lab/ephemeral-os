"""Live Daytona probes for the depth-100 overlay stack experiments."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

DEFAULT_SANDBOX_ID = "53a4a9b8-316f-40e8-8849-eb7b60fea3d7"
DEFAULT_MOUNT_DEPTHS = (1, 5, 10, 30, 50, 80, 100, 200)
DEFAULT_READ_DEPTHS = (1, 5, 10, 30, 50, 80, 100)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox-id", default=DEFAULT_SANDBOX_ID)
    parser.add_argument(
        "--output",
        default=f".omc/results/stack-overlay-live-{time.strftime('%Y%m%d-%H%M%S')}.jsonl",
    )
    parser.add_argument("--e2-iterations", type=int, default=1000)
    parser.add_argument("--e3-files", type=int, default=10_000)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    _configure_repo_imports(repo_root)
    output_path = repo_root / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sandbox = _get_sandbox(args.sandbox_id)
    records = []
    for experiment_id, script in (
        ("E1", _remote_e1_script(DEFAULT_MOUNT_DEPTHS)),
        ("E2", _remote_e2_script(DEFAULT_MOUNT_DEPTHS, args.e2_iterations)),
        ("E3", _remote_e3_script(DEFAULT_READ_DEPTHS, args.e3_files)),
        ("E8", _remote_e8_script()),
    ):
        started = time.perf_counter()
        print(f"[live][{time.strftime('%H:%M:%S')}] {experiment_id} start", flush=True)
        record = _run_remote_experiment(
            sandbox,
            experiment_id,
            script,
            timeout=args.timeout,
        )
        record["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        _append_jsonl(output_path, record)
        records.append(record)
        print(
            f"[live][{time.strftime('%H:%M:%S')}] "
            f"{experiment_id} end status={record['status']} "
            f"elapsed_ms={record['elapsed_ms']} "
            f"metrics={json.dumps(record['metrics'], sort_keys=True)}",
            flush=True,
        )

    print(
        json.dumps(
            {
                "output": str(output_path),
                "summary": _summary(records),
                "experiments": records,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _configure_repo_imports(repo_root: Path) -> None:
    sys.path.insert(0, str(repo_root / "backend" / "src"))
    sys.path.insert(0, str(repo_root / "backend" / "tests"))
    load_dotenv(repo_root / ".env")
    settings_path = Path.home() / ".ephemeralos" / "settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
        for key in ("daytona_api_key", "daytona_api_url", "daytona_target"):
            env_key = key.upper()
            if not os.environ.get(env_key) and settings.get(key):
                os.environ[env_key] = settings[key]


def _get_sandbox(sandbox_id: str) -> Any:
    from sandbox.testing import get_sandbox_service

    svc = get_sandbox_service()
    return svc.get_sandbox_object(sandbox_id)


def _run_remote_experiment(
    sandbox: Any,
    experiment_id: str,
    script: str,
    *,
    timeout: int,
) -> dict[str, Any]:
    from test_e2e.daytona_exec_io import write_text_via_exec

    remote_path = f"/tmp/stack_overlay_{experiment_id.lower()}_{int(time.time())}.py"
    write_text_via_exec(sandbox, remote_path, script)
    resp = sandbox.process.exec(f"unshare -Urm python3 {remote_path}", timeout=timeout)
    output = str(getattr(resp, "result", "") or "").strip()
    exit_code = getattr(resp, "exit_code", None)
    if exit_code != 0:
        return {
            "id": experiment_id,
            "name": _experiment_name(experiment_id),
            "status": "failed",
            "metrics": {"exit_code": exit_code, "output": output[-4000:]},
            "note": "remote experiment process failed",
        }
    try:
        return json.loads(output.splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        return {
            "id": experiment_id,
            "name": _experiment_name(experiment_id),
            "status": "failed",
            "metrics": {"parse_error": str(exc), "output": output[-4000:]},
            "note": "remote experiment did not emit JSON",
        }


def _remote_e1_script(depths: tuple[int, ...]) -> str:
    return f"""
from __future__ import annotations
import ctypes, json, os, shutil, subprocess, time
from pathlib import Path

depths = {json.dumps(depths)}
root = Path('/dev/shm/eos-stack-live-e1')
shutil.rmtree(root, ignore_errors=True)
session = root / 's'
session.mkdir(parents=True)
for i in range(max(depths)):
    layer = session / f'L{{i:05d}}'
    layer.mkdir()
    (layer / 'marker.txt').write_text(f'layer-{{i}}\\n')

libc = ctypes.CDLL(None, use_errno=True)
mount = libc.mount
mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
mount.restype = ctypes.c_int
umount2 = libc.umount2
umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
umount2.restype = ctypes.c_int

def syscall_probe(depth):
    run = root / f'sys_{{depth}}'
    upper, work, merged = run / 'u', run / 'w', run / 'm'
    upper.mkdir(parents=True)
    work.mkdir()
    merged.mkdir()
    lower = ':'.join(f'L{{i:05d}}' for i in range(depth - 1, -1, -1))
    opts = f'lowerdir={{lower}},upperdir={{upper}},workdir={{work}},userxattr'
    old = Path.cwd()
    started = time.perf_counter()
    try:
        os.chdir(session)
        rc = mount(b'overlay', os.fsencode(merged), b'overlay', 0, os.fsencode(opts))
    finally:
        os.chdir(old)
    elapsed_ms = (time.perf_counter() - started) * 1000
    err = ctypes.get_errno() if rc else 0
    marker = ''
    if rc == 0:
        marker = (merged / 'marker.txt').read_text()
        umount2(os.fsencode(merged), 0)
    shutil.rmtree(run, ignore_errors=True)
    return {{
        'depth': depth,
        'rc': rc,
        'errno': err,
        'elapsed_ms': elapsed_ms,
        'options_len': len(opts),
        'marker_ok': marker == f'layer-{{depth - 1}}\\n',
    }}

def mount8_probe(depth):
    run = root / f'm8_{{depth}}'
    upper, work, merged = run / 'u', run / 'w', run / 'm'
    upper.mkdir(parents=True)
    work.mkdir()
    merged.mkdir()
    lower = ':'.join(f'L{{i:05d}}' for i in range(depth - 1, -1, -1))
    opts = f'lowerdir={{lower}},upperdir={{upper}},workdir={{work}},userxattr'
    completed = subprocess.run(
        ['mount', '-t', 'overlay', 'overlay', '-o', opts, str(merged)],
        cwd=session,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode == 0:
        subprocess.run(['umount', str(merged)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(run, ignore_errors=True)
    return {{
        'depth': depth,
        'rc': completed.returncode,
        'stderr': completed.stderr[:200],
        'options_len': len(opts),
    }}

syscall = [syscall_probe(d) for d in depths]
mount8 = [mount8_probe(d) for d in (10, 100)]
shutil.rmtree(root, ignore_errors=True)
passed = all(item['rc'] == 0 and item['marker_ok'] for item in syscall if item['depth'] <= 100)
print(json.dumps({{
    'id': 'E1',
    'name': 'Nested overlayfs viable inside Daytona',
    'status': 'passed' if passed else 'failed',
    'metrics': {{
        'syscall_depths': syscall,
        'mount8_negative_control': mount8,
    }},
    'note': 'direct mount(2) syscall inside unshare -Urm; mount8 retained as negative control',
}}, sort_keys=True))
"""


def _remote_e2_script(depths: tuple[int, ...], iterations: int) -> str:
    return f"""
from __future__ import annotations
import ctypes, json, os, shutil, time
from pathlib import Path

depths = {json.dumps(depths)}
iterations = {iterations}
root = Path('/dev/shm/eos-stack-live-e2')
shutil.rmtree(root, ignore_errors=True)
session = root / 's'
session.mkdir(parents=True)
for i in range(max(depths)):
    layer = session / f'L{{i:05d}}'
    layer.mkdir()
    (layer / 'marker.txt').write_text(f'layer-{{i}}\\n')

libc = ctypes.CDLL(None, use_errno=True)
mount = libc.mount
mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
mount.restype = ctypes.c_int
umount2 = libc.umount2
umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
umount2.restype = ctypes.c_int

def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, max(0, int((len(values) - 1) * p / 100)))]

def probe_depth(depth):
    lower = ':'.join(f'L{{i:05d}}' for i in range(depth - 1, -1, -1))
    timings = []
    failures = []
    for iteration in range(iterations):
        run = root / f'd{{depth}}_{{iteration}}'
        upper, work, merged = run / 'u', run / 'w', run / 'm'
        upper.mkdir(parents=True)
        work.mkdir()
        merged.mkdir()
        opts = f'lowerdir={{lower}},upperdir={{upper}},workdir={{work}},userxattr'
        old = Path.cwd()
        started = time.perf_counter()
        try:
            os.chdir(session)
            rc = mount(b'overlay', os.fsencode(merged), b'overlay', 0, os.fsencode(opts))
        finally:
            os.chdir(old)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if rc == 0:
            timings.append(elapsed_ms)
            umount2(os.fsencode(merged), 0)
        else:
            failures.append(ctypes.get_errno())
        shutil.rmtree(run, ignore_errors=True)
    return {{
        'depth': depth,
        'iterations': iterations,
        'failures': len(failures),
        'first_errno': failures[0] if failures else None,
        'options_len': len(f'lowerdir={{lower}},upperdir=/x,workdir=/y,userxattr'),
        'p50_ms': pct(timings, 50),
        'p95_ms': pct(timings, 95),
        'p99_ms': pct(timings, 99),
    }}

started = time.perf_counter()
per_depth = [probe_depth(d) for d in depths]
shutil.rmtree(root, ignore_errors=True)
depth100 = next(item for item in per_depth if item['depth'] == 100)
passed = depth100['failures'] == 0 and depth100['p99_ms'] is not None and depth100['p99_ms'] < 5
print(json.dumps({{
    'id': 'E2',
    'name': 'Snapshot cost vs depth',
    'status': 'passed' if passed else 'failed',
    'metrics': {{
        'depths': per_depth,
        'elapsed_s': time.perf_counter() - started,
        'depth100_p99_ms': depth100['p99_ms'],
    }},
    'note': 'direct mount(2)+umount2 latency matrix inside unshare -Urm',
}}, sort_keys=True))
"""


def _remote_e3_script(depths: tuple[int, ...], file_count: int) -> str:
    return f"""
from __future__ import annotations
import ctypes, json, os, shutil, time
from pathlib import Path

depths = {json.dumps(depths)}
file_count = {file_count}
root = Path('/dev/shm/eos-stack-live-e3')
shutil.rmtree(root, ignore_errors=True)
session = root / 's'
session.mkdir(parents=True)
for i in range(max(depths)):
    layer = session / f'L{{i:05d}}'
    layer.mkdir()
base = session / 'L00000'
for i in range(file_count):
    path = base / f'd{{i // 1000:03d}}' / f'f{{i:05d}}.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'base-{{i}}\\n')
for d in range(1, max(depths)):
    layer = session / f'L{{d:05d}}'
    path = layer / 'overrides' / f'o{{d:05d}}.txt'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'override-{{d}}\\n')

libc = ctypes.CDLL(None, use_errno=True)
mount = libc.mount
mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
mount.restype = ctypes.c_int
umount2 = libc.umount2
umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
umount2.restype = ctypes.c_int

def read_all(root_path):
    files = 0
    bytes_read = 0
    started = time.perf_counter()
    for dirpath, _dirnames, filenames in os.walk(root_path):
        for name in filenames:
            with open(Path(dirpath) / name, 'rb') as fh:
                data = fh.read()
            files += 1
            bytes_read += len(data)
    return {{
        'elapsed_ms': (time.perf_counter() - started) * 1000,
        'files': files,
        'bytes': bytes_read,
    }}

def try_drop_caches():
    try:
        os.sync()
        with open('/proc/sys/vm/drop_caches', 'w', encoding='utf-8') as fh:
            fh.write('3\\n')
        return {{'supported': True, 'error': ''}}
    except OSError as exc:
        return {{'supported': False, 'error': str(exc)}}

def probe_depth(depth):
    run = root / f'd{{depth}}'
    upper, work, merged = run / 'u', run / 'w', run / 'm'
    upper.mkdir(parents=True)
    work.mkdir()
    merged.mkdir()
    lower = ':'.join(f'L{{i:05d}}' for i in range(depth - 1, -1, -1))
    opts = f'lowerdir={{lower}},upperdir={{upper}},workdir={{work}},userxattr'
    old = Path.cwd()
    try:
        os.chdir(session)
        rc = mount(b'overlay', os.fsencode(merged), b'overlay', 0, os.fsencode(opts))
    finally:
        os.chdir(old)
    if rc != 0:
        err = ctypes.get_errno()
        shutil.rmtree(run, ignore_errors=True)
        return {{'depth': depth, 'mount_errno': err}}
    drop = try_drop_caches()
    first = read_all(merged)
    warm = read_all(merged)
    umount2(os.fsencode(merged), 0)
    shutil.rmtree(run, ignore_errors=True)
    return {{
        'depth': depth,
        'mount_errno': 0,
        'drop_caches': drop,
        'first_read_ms': first['elapsed_ms'],
        'warm_read_ms': warm['elapsed_ms'],
        'files': warm['files'],
        'bytes': warm['bytes'],
    }}

started = time.perf_counter()
per_depth = [probe_depth(d) for d in depths]
shutil.rmtree(root, ignore_errors=True)
baseline = next(item for item in per_depth if item['depth'] == 1 and item.get('mount_errno') == 0)
for item in per_depth:
    if item.get('mount_errno') == 0:
        item['warm_vs_depth1'] = item['warm_read_ms'] / baseline['warm_read_ms']
        item['first_vs_depth1'] = item['first_read_ms'] / baseline['first_read_ms']
depth100 = next(item for item in per_depth if item['depth'] == 100)
passed = depth100.get('mount_errno') == 0 and depth100['warm_vs_depth1'] < 2
print(json.dumps({{
    'id': 'E3',
    'name': 'Cold/warm read latency vs depth',
    'status': 'partial' if passed else 'failed',
    'metrics': {{
        'depths': per_depth,
        'elapsed_s': time.perf_counter() - started,
        'file_count_base': file_count,
        'cold_cache_available': all(item.get('drop_caches', {{}}).get('supported', False) for item in per_depth if item.get('mount_errno') == 0),
        'depth100_warm_vs_depth1': depth100.get('warm_vs_depth1'),
    }},
    'note': 'warm read matrix is valid; cold reads are partial if drop_caches is blocked in the container',
}}, sort_keys=True))
"""


def _remote_e8_script() -> str:
    return """
from __future__ import annotations
import ctypes, hashlib, json, os, shutil, time
from pathlib import Path

ops = 200
files_per_op = 8
root = Path('/dev/shm/eos-stack-live-e8')
shutil.rmtree(root, ignore_errors=True)
session = root / 's'
base = session / 'L00000'
base.mkdir(parents=True)
(base / 'base.txt').write_text('base\\n')

libc = ctypes.CDLL(None, use_errno=True)
mount = libc.mount
mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
mount.restype = ctypes.c_int
umount2 = libc.umount2
umount2.argtypes = [ctypes.c_char_p, ctypes.c_int]
umount2.restype = ctypes.c_int

def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, max(0, int((len(values) - 1) * p / 100)))]

def write_command(target, op):
    started = time.perf_counter()
    for index in range(files_per_op):
        path = target / 'out' / f'op-{op:04d}' / f'f-{index:02d}.txt'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'op={op} file={index}\\n')
    return (time.perf_counter() - started) * 1000

def capture_upper(upper):
    started = time.perf_counter()
    entries = []
    for path in sorted(upper.rglob('*')):
        if path.is_file():
            rel = path.relative_to(upper).as_posix()
            entries.append({'path': rel, 'content': path.read_text()})
    capture_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    payload = '\\n'.join(json.dumps(item, sort_keys=True) for item in entries)
    serialize_ms = (time.perf_counter() - started) * 1000
    return entries, payload, capture_ms, serialize_ms

def occ_merge(payload, current):
    started = time.perf_counter()
    accepted = 0
    for line in payload.splitlines():
        item = json.loads(line)
        path = item['path']
        content = item['content']
        # create-only CAS for this workload; hash represents OCC comparison cost.
        hashlib.sha256(content.encode()).hexdigest()
        if path not in current:
            current[path] = content
            accepted += 1
    return (time.perf_counter() - started) * 1000, accepted

baseline_totals = []
overlay_totals = []
stage = {name: [] for name in ('mount_ms', 'command_ms', 'capture_ms', 'serialize_ms', 'occ_ms', 'payload_bytes')}
current = {}

for op in range(ops):
    direct = root / 'direct' / str(op)
    started = time.perf_counter()
    command_ms = write_command(direct, op)
    _entries, payload, capture_ms, serialize_ms = capture_upper(direct)
    occ_ms, _accepted = occ_merge(payload, {})
    baseline_totals.append((time.perf_counter() - started) * 1000)

for op in range(ops):
    run = root / 'overlay' / str(op)
    upper, work, merged = run / 'u', run / 'w', run / 'm'
    upper.mkdir(parents=True)
    work.mkdir()
    merged.mkdir()
    opts = f'lowerdir=L00000,upperdir={upper},workdir={work},userxattr'
    started_total = time.perf_counter()
    old = Path.cwd()
    started = time.perf_counter()
    try:
        os.chdir(session)
        rc = mount(b'overlay', os.fsencode(merged), b'overlay', 0, os.fsencode(opts))
    finally:
        os.chdir(old)
    mount_ms = (time.perf_counter() - started) * 1000
    if rc != 0:
        raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
    command_ms = write_command(merged, op)
    umount2(os.fsencode(merged), 0)
    _entries, payload, capture_ms, serialize_ms = capture_upper(upper)
    occ_ms, accepted = occ_merge(payload, current)
    overlay_totals.append((time.perf_counter() - started_total) * 1000)
    stage['mount_ms'].append(mount_ms)
    stage['command_ms'].append(command_ms)
    stage['capture_ms'].append(capture_ms)
    stage['serialize_ms'].append(serialize_ms)
    stage['occ_ms'].append(occ_ms)
    stage['payload_bytes'].append(len(payload.encode()))
    shutil.rmtree(run, ignore_errors=True)

shutil.rmtree(root, ignore_errors=True)
stage_stats = {
    name: {'p50': pct(values, 50), 'p95': pct(values, 95), 'p99': pct(values, 99)}
    for name, values in stage.items()
}
overlay_p99 = pct(overlay_totals, 99)
passed = overlay_p99 is not None and overlay_p99 < 500
print(json.dumps({
    'id': 'E8',
    'name': 'End-to-end perf vs today',
    'status': 'passed' if passed else 'failed',
    'metrics': {
        'ops': ops,
        'files_per_op': files_per_op,
        'baseline_total_ms': {'p50': pct(baseline_totals, 50), 'p95': pct(baseline_totals, 95), 'p99': pct(baseline_totals, 99)},
        'overlay_total_ms': {'p50': pct(overlay_totals, 50), 'p95': pct(overlay_totals, 95), 'p99': overlay_p99},
        'stage_ms': stage_stats,
        'accepted_paths': len(current),
    },
    'note': 'live Daytona proxy for overlay mount, command execution, upperdir capture/serialization, upload-sized payload, and OCC-like merge',
}, sort_keys=True))
"""


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _summary(records: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for record in records:
        status = str(record["status"])
        summary[status] = summary.get(status, 0) + 1
    return summary


def _experiment_name(experiment_id: str) -> str:
    names = {
        "E1": "Nested overlayfs viable inside Daytona",
        "E2": "Snapshot cost vs depth",
        "E3": "Cold/warm read latency vs depth",
    }
    return names.get(experiment_id, experiment_id)


if __name__ == "__main__":
    raise SystemExit(main())
