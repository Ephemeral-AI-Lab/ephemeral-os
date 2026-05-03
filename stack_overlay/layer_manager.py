"""Filesystem-backed bounded layer manager for experiments."""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from collections import Counter
from pathlib import Path

from stack_overlay.models import LayerChange, Lease, Manifest, normalize_rel_path

_MANIFEST_FILE = "manifest.json"
_WHITEOUT_PREFIX = ".wh."


class LayerManager:
    """Manage immutable layers, leases, squash, and GC.

    This is not production code. It deliberately keeps the implementation small
    and inspectable so experiments can validate policy before touching the
    sandbox runtime.
    """

    def __init__(
        self,
        session_root: str | Path,
        *,
        max_depth: int = 100,
        squash_trigger: int | None = None,
        squash_target: int | None = None,
    ) -> None:
        squash_trigger = squash_trigger if squash_trigger is not None else min(80, max_depth)
        squash_target = (
            squash_target
            if squash_target is not None
            else max(2, min(40, squash_trigger - 1))
        )
        if squash_target < 2:
            raise ValueError("squash_target must keep at least base + one delta")
        if squash_trigger > max_depth:
            raise ValueError("squash_trigger must be <= max_depth")
        if squash_target >= squash_trigger:
            raise ValueError("squash_target must be < squash_trigger")

        self.session_root = Path(session_root)
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.max_depth = max_depth
        self.squash_trigger = squash_trigger
        self.squash_target = squash_target
        self._lock = threading.RLock()
        self._lease_layers: Counter[str] = Counter()
        self._leases: dict[str, Lease] = {}
        self._retired_layers: set[str] = set()
        self._next_layer_id = 1
        self._manifest = self._load_manifest()

    @classmethod
    def create(
        cls,
        session_root: str | Path,
        files: dict[str, str],
        *,
        max_depth: int = 100,
        squash_trigger: int | None = None,
        squash_target: int | None = None,
    ) -> "LayerManager":
        root = Path(session_root)
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        base = root / "b"
        base.mkdir()
        for rel, content in files.items():
            _write_text(base / normalize_rel_path(rel), content)
        manager = cls(
            root,
            max_depth=max_depth,
            squash_trigger=squash_trigger,
            squash_target=squash_target,
        )
        manager._manifest = Manifest(version=0, layers=("b",))
        manager._write_manifest(manager._manifest)
        return manager

    def snapshot(self) -> Manifest:
        with self._lock:
            self._manifest = self._load_manifest()
            return self._manifest

    def acquire(self, manifest: Manifest | None = None) -> Lease:
        with self._lock:
            current = manifest or self.snapshot()
            lease = Lease(lease_id=uuid.uuid4().hex, manifest=current)
            self._leases[lease.lease_id] = lease
            self._lease_layers.update(current.layers)
            return lease

    def release(self, lease: Lease) -> None:
        with self._lock:
            existing = self._leases.pop(lease.lease_id, None)
            if existing is None:
                return
            for layer in existing.manifest.layers:
                self._lease_layers[layer] -= 1
                if self._lease_layers[layer] <= 0:
                    del self._lease_layers[layer]
            self.collect_garbage()

    def commit(self, changes: list[LayerChange]) -> Manifest:
        with self._lock:
            if not changes:
                return self.snapshot()
            current = self.snapshot()
            if current.depth >= self.max_depth:
                self._squash_to_target_locked()
                current = self.snapshot()
            if current.depth >= self.max_depth:
                raise RuntimeError(
                    f"manifest depth {current.depth} is at hard cap {self.max_depth}"
                )

            layer_name = self._allocate_layer_name()
            layer_dir = self.session_root / layer_name
            layer_dir.mkdir()
            for change in changes:
                self._write_layer_change(layer_dir, change)

            next_manifest = Manifest(
                version=current.version + 1,
                layers=(layer_name, *current.layers),
            )
            self._write_manifest(next_manifest)
            self._manifest = next_manifest
            if next_manifest.depth >= self.squash_trigger:
                self._squash_to_target_locked()
            return self._manifest

    def read_text(self, path: str, manifest: Manifest | None = None) -> tuple[str, bool]:
        rel = normalize_rel_path(path)
        current = manifest or self.snapshot()
        with self._lock:
            for layer in current.layers:
                layer_dir = self.session_root / layer
                if _whiteout_path(layer_dir, rel).exists():
                    return "", False
                candidate = layer_dir / rel
                if candidate.is_file():
                    return candidate.read_text(encoding="utf-8"), True
            return "", False

    def materialize(self, destination: str | Path, manifest: Manifest | None = None) -> None:
        dest = Path(destination)
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        current = manifest or self.snapshot()
        with self._lock:
            for layer in reversed(current.layers):
                self._apply_layer_to_tree(self.session_root / layer, dest)

    def collect_garbage(self) -> list[str]:
        with self._lock:
            active = set(self._manifest.layers)
            removed: list[str] = []
            for layer in sorted(self._retired_layers):
                if layer in active or self._lease_layers.get(layer, 0) > 0:
                    continue
                shutil.rmtree(self.session_root / layer, ignore_errors=True)
                self._retired_layers.remove(layer)
                removed.append(layer)
            return removed

    def missing_manifest_layers(self) -> tuple[str, ...]:
        with self._lock:
            current = self.snapshot()
            return tuple(
                layer
                for layer in current.layers
                if not (self.session_root / layer).is_dir()
            )

    def recover_unreferenced_layers(self) -> list[str]:
        """Remove layer directories that are not reachable from any manifest.

        This models the startup fsck path needed after a crash between writing a
        layer/checkpoint directory and atomically publishing the new manifest.
        """

        with self._lock:
            missing = self.missing_manifest_layers()
            if missing:
                raise RuntimeError(
                    "manifest references missing layers: " + ", ".join(missing)
                )
            referenced = set(self.snapshot().layers) | set(self._lease_layers)
            removed: list[str] = []
            for child in sorted(self.session_root.iterdir()):
                if child.is_dir() and child.name not in referenced:
                    shutil.rmtree(child)
                    removed.append(child.name)
            return removed

    def refcount(self, layer: str) -> int:
        return self._lease_layers.get(layer, 0)

    def retired_layers(self) -> tuple[str, ...]:
        return tuple(sorted(self._retired_layers))

    def _load_manifest(self) -> Manifest:
        path = self.session_root / _MANIFEST_FILE
        if not path.exists():
            return Manifest(version=0, layers=())
        payload = json.loads(path.read_text(encoding="utf-8"))
        return Manifest(
            version=int(payload["version"]),
            layers=tuple(str(layer) for layer in payload["layers"]),
        )

    def _write_manifest(self, manifest: Manifest) -> None:
        payload = {
            "version": manifest.version,
            "layers": list(manifest.layers),
            "max_depth": self.max_depth,
            "squash_trigger": self.squash_trigger,
            "squash_target": self.squash_target,
        }
        tmp = self.session_root / f".{_MANIFEST_FILE}.tmp"
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.session_root / _MANIFEST_FILE)

    def _allocate_layer_name(self) -> str:
        while True:
            candidate = f"L{self._next_layer_id:04d}"
            self._next_layer_id += 1
            if not (self.session_root / candidate).exists():
                return candidate

    def _allocate_checkpoint_name(self, version: int) -> str:
        candidate = f"B{version:04d}"
        suffix = 0
        while (self.session_root / candidate).exists():
            suffix += 1
            candidate = f"B{version:04d}_{suffix}"
        return candidate

    def _write_layer_change(self, layer_dir: Path, change: LayerChange) -> None:
        rel = normalize_rel_path(change.path)
        if change.kind == "write":
            _write_text(layer_dir / rel, change.content)
            return
        if change.kind == "delete":
            whiteout = _whiteout_path(layer_dir, rel)
            whiteout.parent.mkdir(parents=True, exist_ok=True)
            whiteout.write_text("", encoding="utf-8")
            return
        raise ValueError(f"unsupported change kind: {change.kind}")

    def _squash_to_target_locked(self) -> None:
        current = self.snapshot()
        if current.depth <= self.squash_target:
            return
        keep_count = self.squash_target - 1
        kept_newest = current.layers[:keep_count]
        squash_layers = current.layers[keep_count:]
        checkpoint_name = self._allocate_checkpoint_name(current.version + 1)
        checkpoint_dir = self.session_root / checkpoint_name
        checkpoint_dir.mkdir()
        for layer in reversed(squash_layers):
            self._apply_layer_to_tree(self.session_root / layer, checkpoint_dir)

        next_manifest = Manifest(
            version=current.version + 1,
            layers=(*kept_newest, checkpoint_name),
        )
        self._write_manifest(next_manifest)
        self._manifest = next_manifest
        self._retired_layers.update(squash_layers)
        self.collect_garbage()

    def _apply_layer_to_tree(self, layer_dir: Path, tree_dir: Path) -> None:
        for path in layer_dir.rglob("*"):
            if path.is_dir():
                continue
            rel = path.relative_to(layer_dir)
            if path.name.startswith(_WHITEOUT_PREFIX):
                target_name = path.name[len(_WHITEOUT_PREFIX) :]
                target = tree_dir / rel.parent / target_name
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
                continue
            dest = tree_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _whiteout_path(layer_dir: Path, rel: str) -> Path:
    target = Path(rel)
    return layer_dir / target.parent / f"{_WHITEOUT_PREFIX}{target.name}"
