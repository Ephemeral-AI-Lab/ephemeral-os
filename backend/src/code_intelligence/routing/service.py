"""Per-sandbox :class:`CodeIntelligenceService` orchestrator.

This module wires together the analysis, editing, and routing
components. Heavy concerns live in their own modules:

* Semantic write helper → :mod:`code_intelligence.editing.write_coordinator`
* File IO              → :mod:`code_intelligence.routing.content_manager`
* Registry lifecycle   → :mod:`code_intelligence.routing.registry`

The registry helpers are re-exported from this module for backwards
compatibility with callers that import them from ``routing.service``.
"""

from __future__ import annotations

import asyncio
import base64
import errno
import inspect
import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from team._path_utils import normalize_scope_paths, scope_paths_overlap
from tools.daytona_toolkit._daytona_utils import _extract_exit_code, _wrap_bash_command

from code_intelligence.analysis.symbol_index import SymbolIndex
from code_intelligence.analysis.tree_cache import TreeCache
from code_intelligence.editing.arbiter import Arbiter
from code_intelligence.editing.patcher import Patcher
from code_intelligence.editing.time_machine import TimeMachine
from code_intelligence.editing.write_coordinator import WriteCoordinator
from code_intelligence.hashing import content_hash
from code_intelligence.lsp.client import LspClient
from code_intelligence.routing.backend_protocol import (
    LspBackendAdapter,
    SymbolIndexBackendAdapter,
)
from code_intelligence.routing.content_manager import ContentManager
from code_intelligence.routing.overlay_auditor import (
    OverlayAuditor,
    OverlayAuditorConfig,
)
from code_intelligence.routing.overlay_probe import OverlayCapabilityCache
from code_intelligence.routing.query_router import IntelligenceQueryRouter
from code_intelligence.routing.registry import (
    dispose_all_code_intelligence,
    dispose_code_intelligence,
    get_all_services_status,
    get_code_intelligence,
    get_code_intelligence_if_exists,
)
from code_intelligence.tuning import CODE_INTELLIGENCE_TUNING
from code_intelligence.types import (
    CITelemetry,
    Diagnostic,
    EditRequest,
    EditResult,
    EditSpec,
    HoverResult,
    MoveSpec,
    OperationChange,
    OperationResult,
    ReferenceInfo,
    SemanticFileChange,
    SemanticRenamePlan,
    SymbolInfo,
    WriteSpec,
)

__all__ = [
    "CodeIntelligenceService",
    "OverlayCapabilityMissingError",
    "dispose_all_code_intelligence",
    "dispose_code_intelligence",
    "get_all_services_status",
    "get_code_intelligence",
    "get_code_intelligence_if_exists",
]


class OverlayCapabilityMissingError(RuntimeError):
    """Raised when :meth:`svc.cmd` cannot honor its OCC contract.

    Either the sandbox lacks overlay / tmpfs / userxattr support, or the
    outer lowerdir snapshot cannot be materialized. In both cases
    ``svc.cmd`` fails closed rather than degrade to the pre-OCC
    ``ProcessAuditor`` path — re-introducing unaudited mutation would
    re-open the bug the OCC migration closes.
    """

logger = logging.getLogger(__name__)
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DEF_CLASS_NAME_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class _RenamePreviewSnapshot:
    """Reusable base data for dry-run rename previews."""

    refs: tuple[ReferenceInfo, ...]
    base_by_path: dict[str, tuple[str, bool]]
    old_name: str


@dataclass
class _InflightRenamePreview:
    """One in-progress dry-run preview snapshot shared by callers."""

    event: threading.Event


class CodeIntelligenceService:
    """Orchestrates code intelligence queries and edits for one sandbox."""

    def __init__(
        self,
        sandbox_id: str,
        workspace_root: str = "/workspace",
        sandbox: Any = None,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        self._sandbox = sandbox
        self._initialized = False
        self._lsp_bootstrap_attempted = False
        self._init_lock = threading.Lock()

        self.tree_cache = TreeCache(sandbox=sandbox)
        self.symbol_index = SymbolIndex(
            workspace_root=workspace_root,
            sandbox=sandbox,
            tree_cache=self.tree_cache,
        )

        self.arbiter = Arbiter(workspace_root=workspace_root)
        self.time_machine = TimeMachine()
        self.patcher = Patcher()
        self.lsp_client = LspClient(workspace_root=workspace_root, sandbox=sandbox)
        self.query_router = IntelligenceQueryRouter()
        self.query_router.register(LspBackendAdapter(self.lsp_client))
        self.query_router.register(SymbolIndexBackendAdapter(self.symbol_index))

        self._content = ContentManager(workspace_root, sandbox=sandbox)
        self._overlay_capability = OverlayCapabilityCache()
        self._overlay_auditor: OverlayAuditor | None = None
        self._overlay_lowerdir: str | None = None
        self._overlay_init_lock = asyncio.Lock()
        self._write_coordinator = WriteCoordinator(
            arbiter=self.arbiter,
            time_machine=self.time_machine,
            patcher=self.patcher,
            symbol_index=self.symbol_index,
            lsp_client=self.lsp_client,
            content=self._content,
        )
        self._rename_preview_cache_lock = threading.Lock()
        self._rename_preview_cache: OrderedDict[
            tuple[str, int, int, int, int, int],
            _RenamePreviewSnapshot,
        ] = OrderedDict()
        self._rename_preview_inflight: dict[
            tuple[str, int, int, int, int, int],
            _InflightRenamePreview,
        ] = {}
        self._rename_preview_fast_fallbacks = 0

    # -- Initialization -------------------------------------------------------

    def ensure_initialized(self, wait: bool = True) -> bool:
        """Initialize symbol indexing + LSP. Returns True once ready."""
        with self._init_lock:
            if self._initialized:
                return True

        ready = self.symbol_index.ensure_built(wait=wait)
        lsp_ready = self.lsp_client.ensure_ready(languages=("python",))
        if (
            self._sandbox is not None
            and not lsp_ready.get("python")
            and not self._lsp_bootstrap_attempted
        ):
            self._lsp_bootstrap_attempted = True
            self.lsp_client.ensure_ready(install_missing=True, languages=("python",))

        with self._init_lock:
            self._initialized = ready or self.symbol_index.is_built
        return self.is_initialized

    @property
    def is_initialized(self) -> bool:
        with self._init_lock:
            if self._initialized:
                return True
        if self.symbol_index.is_built:
            with self._init_lock:
                self._initialized = True
            return True
        return False

    # -- Sandbox binding ------------------------------------------------------

    def rebind_sandbox(self, sandbox: Any) -> None:
        """Refresh the sandbox handle on this service and its collaborators."""
        if sandbox is None:
            return
        self._sandbox = sandbox
        self.symbol_index.bind_sandbox(sandbox)
        old_sandbox = getattr(self.lsp_client, "_sandbox", None)
        self.lsp_client._sandbox = sandbox
        if old_sandbox is not sandbox:
            self.lsp_client._worker_enabled_default = True
            self.lsp_client.reset_backend_availability()
            self._clear_rename_preview_cache()
        self._content.bind_sandbox(sandbox)

    # -- Audited shell command execution (OCC-gated) -------------------------

    async def cmd(
        self,
        sandbox: Any,
        command: str,
        *,
        timeout: int | None = None,
        description: str = "",
        agent_id: str = "",
        team_run_id: str = "",
        agent_run_id: str = "",
        task_id: str = "",
        attribute_changes: bool = True,
    ) -> Any:
        """Run one shell command through the OCC-gated overlay audit path.

        Fail-closed: if the sandbox lacks the overlay pipeline or its
        lowerdir snapshot cannot be materialized, raise
        :class:`OverlayCapabilityMissingError`. There is no fallback to
        the pre-OCC ``ProcessAuditor`` — a single OCC boundary is the
        whole point of the migration.
        """
        self.rebind_sandbox(sandbox)
        probe = await self._overlay_capability.probe(
            self.sandbox_id, sandbox, self._exec_sandbox_process,
        )
        if not probe.supported:
            raise OverlayCapabilityMissingError(
                f"svc.cmd requires overlay OCC; sandbox {self.sandbox_id}: "
                f"{probe.reason}"
            )
        auditor = await self._ensure_overlay_auditor(sandbox)
        return await auditor.execute(
            sandbox,
            command,
            timeout=timeout,
            description=description,
            agent_id=agent_id,
            team_run_id=team_run_id,
            agent_run_id=agent_run_id,
            task_id=task_id,
            attribute_changes=attribute_changes,
        )

    async def _ensure_overlay_auditor(self, sandbox: Any) -> OverlayAuditor:
        cached = await self._live_overlay_auditor_or_none(sandbox)
        if cached is not None:
            return cached

        async with self._overlay_init_lock:
            cached = await self._live_overlay_auditor_or_none(sandbox)
            if cached is not None:
                return cached

            lowerdir = await self._ensure_overlay_lowerdir(sandbox)

            async def _provider(_repo_root: str) -> str:
                return lowerdir

            self._overlay_auditor = OverlayAuditor(
                workspace_root=self.workspace_root,
                exec_process=self._exec_sandbox_process,
                write_coordinator=self._write_coordinator,
                lowerdir_provider=_provider,
                lowerdir_refresh=self._refresh_overlay_lowerdir,
                config=OverlayAuditorConfig(),
            )
            return self._overlay_auditor

    async def _live_overlay_auditor_or_none(self, sandbox: Any) -> OverlayAuditor | None:
        auditor = self._overlay_auditor
        if auditor is None:
            return None
        lowerdir = self._overlay_lowerdir
        if not lowerdir:
            self._overlay_auditor = None
            return None
        if await self._lowerdir_is_live(sandbox, lowerdir):
            return auditor
        self._overlay_auditor = None
        self._overlay_lowerdir = None
        return None

    async def _ensure_overlay_lowerdir(self, sandbox: Any) -> str:
        """Materialize a snapshot of the live workspace as the outer lowerdir.

        Per P0.7 / P0.9, the outer lowerdir must reflect the current
        workspace state (tracked + untracked + dirty) so strict-base
        drift detection compares against what peers actually see, not
        against HEAD. Kernel-aware probe:

        * Linux → ``cp -a --reflink=always`` (btrfs/XFS/bcachefs). A
          non-CoW filesystem like ext4 falls back to plain ``cp -a``.
          Byte copies are slower but still independent; hardlinks are
          the unsafe form because later writes can alias into the base.
        * Darwin → plain ``cp -a`` (APFS invokes ``clonefile(2)``
          implicitly; there is no ``--reflink`` flag in BSD ``cp``).

        Callers hold ``_overlay_init_lock`` while this runs.
        """
        if self._overlay_lowerdir is not None:
            return self._overlay_lowerdir

        import shlex

        lowerdir = f"/tmp/overlay-lower-{self.sandbox_id}"
        ready = f"{lowerdir}.ready"
        # Merge stderr into stdout (2>&1 on every step) so cp / mkdir failures
        # surface in the diagnostic payload even when the shell aborts under
        # set -eu. Probe also ls-verifies the destination at the end so we
        # refuse to memoize a path the probe didn't actually populate.
        probe_cmd = (
            "set -eu\n"
            f"src={shlex.quote(self.workspace_root)}\n"
            f"dst={shlex.quote(lowerdir)}\n"
            f"ready={shlex.quote(ready)}\n"
            'if [ -d "$dst" ] && [ -f "$ready" ] && [ -n "$(ls -A "$dst" 2>/dev/null)" ]; then '
            'echo exists; exit 0; fi\n'
            'rm -rf "$dst" "$ready" 2>&1\n'
            'mkdir -p "$dst" 2>&1\n'
            'case "$(uname -s)" in\n'
            "  Linux)\n"
            '    err="$(mktemp /tmp/overlay-lower-reflink-XXXXXX.err)"\n'
            '    if cp -a --reflink=always "$src/." "$dst/" 2>"$err"; then\n'
            "      echo cow-reflink\n"
            "    else\n"
            '      reflink_err="$(cat "$err" 2>/dev/null || true)"\n'
            '      rm -rf "$dst" 2>&1\n'
            '      mkdir -p "$dst" 2>&1\n'
            '      if cp -a "$src/." "$dst/" 2>&1; then\n'
            "        echo byte-copy\n"
            "      else\n"
            "        echo snapshot-unavailable\n"
            '        printf "%s\\n" "$reflink_err"\n'
            "        exit 2\n"
            "      fi\n"
            "    fi\n"
            '    rm -f "$err" 2>/dev/null || true\n'
            "    ;;\n"
            "  Darwin)\n"
            '    if cp -a "$src/." "$dst/" 2>&1; then\n'
            "      echo cow-clonefile\n"
            "    else\n"
            "      echo snapshot-unavailable; exit 2\n"
            "    fi\n"
            "    ;;\n"
            "  *)\n"
            '    if cp -a "$src/." "$dst/" 2>&1; then\n'
            "      echo byte-copy\n"
            "    else\n"
            "      echo snapshot-unavailable; exit 2\n"
            "    fi\n"
            "    ;;\n"
            "esac\n"
            # Verify destination exists and is non-empty before declaring success.
            'if [ ! -d "$dst" ] || [ -z "$(ls -A "$dst" 2>/dev/null)" ]; then\n'
            '  echo "probe-dst-empty $dst"; exit 3\n'
            "fi\n"
            ': > "$ready"\n'
        )
        response = await self._exec_sandbox_process(
            sandbox,
            _wrap_bash_command(f"bash -c {shlex.quote(probe_cmd)}"),
            timeout=120,
        )
        stdout, exit_code = _extract_exit_code(
            str(getattr(response, "result", "") or ""),
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        stdout = stdout.strip()
        if "snapshot-unavailable" in stdout:
            raise OverlayCapabilityMissingError(
                f"svc.cmd lowerdir snapshot failed on {lowerdir}; "
                f"Probe stdout: {stdout!r}"
            )
        if "probe-dst-empty" in stdout:
            raise OverlayCapabilityMissingError(
                f"svc.cmd lowerdir probe reports empty destination {lowerdir} "
                f"after cp — workspace may be empty or /tmp writes are not "
                f"persisting across execs. Probe stdout: {stdout!r}"
            )
        if exit_code != 0:
            raise OverlayCapabilityMissingError(
                f"svc.cmd lowerdir probe failed on {lowerdir}: "
                f"exit_code={exit_code} stdout={stdout!r}"
            )
        self._overlay_lowerdir = lowerdir
        return lowerdir

    async def _lowerdir_is_live(self, sandbox: Any, lowerdir: str) -> bool:
        """Cheap verification that the memoized lowerdir path still exists.

        Daytona sandboxes sometimes reset ``/tmp`` between ``process.exec``
        calls (observed 2026-04-19 during P0.9 live validation: the probe
        `cp -a` succeeded but the destination was gone by the next exec).
        Before ``svc.cmd`` hands its memoized auditor out, confirm the
        lowerdir is still there. If not, the caller drops both caches and
        re-materializes — the next call pays cold-start, which the
        amortization gate then catches as a regression.
        """
        import shlex

        ready = f"{lowerdir}.ready"
        check_cmd = (
            f'[ -d {shlex.quote(lowerdir)} ] '
            f'&& [ -f {shlex.quote(ready)} ] '
            f'&& [ -n "$(ls -A {shlex.quote(lowerdir)} 2>/dev/null)" ] '
            "&& echo live || echo missing"
        )
        response = await self._exec_sandbox_process(
            sandbox,
            _wrap_bash_command(check_cmd),
            timeout=30,
        )
        stdout, exit_code = _extract_exit_code(
            str(getattr(response, "result", "") or ""),
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        return exit_code == 0 and stdout.strip().endswith("live")

    async def _refresh_overlay_lowerdir(
        self,
        committed_changes: Sequence[OperationChange],
    ) -> None:
        """Mirror a successful coordinator commit back into the lowerdir snapshot.

        The lowerdir must track ``ContentManager`` head so the next
        ``svc.cmd`` computes ``base_hash`` against current workspace
        state, not a stale snapshot. Runs after
        :meth:`WriteCoordinator.commit_operation_against_base` returns a
        committed status; best-effort — a refresh failure is logged but
        does not unwind the commit (the next overlay run will re-probe).
        """
        lowerdir = self._overlay_lowerdir
        if not lowerdir:
            return
        workspace = self.workspace_root.rstrip("/") + "/"
        items: list[dict[str, str | None]] = []
        for change in committed_changes:
            rel = change.file_path
            if rel.startswith(workspace):
                rel = rel[len(workspace):]
            elif rel.startswith("/"):
                # Absolute path outside workspace: skip — out of snapshot scope.
                continue
            items.append({"rel": rel, "final_content": change.final_content})
        if not items:
            return

        sandbox = self._sandbox
        if sandbox is None:
            self._refresh_local_overlay_lowerdir(lowerdir, items)
            return
        await self._refresh_remote_overlay_lowerdir(sandbox, lowerdir, items)

    @staticmethod
    def _refresh_local_overlay_lowerdir(
        lowerdir: str,
        items: Sequence[dict[str, str | None]],
    ) -> None:
        for item in items:
            rel = item["rel"]
            target = os.path.join(lowerdir, rel)
            try:
                final_content = item["final_content"]
                if final_content is None:
                    try:
                        os.remove(target)
                    except FileNotFoundError:
                        pass
                    except OSError as exc:
                        if exc.errno != errno.ENOENT:
                            logger.debug(
                                "overlay lowerdir refresh: remove %s failed: %s",
                                target, exc,
                            )
                else:
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with open(target, "w", encoding="utf-8") as fh:
                        fh.write(final_content)
            except OSError:
                logger.debug(
                    "overlay lowerdir refresh: write %s failed",
                    target, exc_info=True,
                )

    async def _refresh_remote_overlay_lowerdir(
        self,
        sandbox: Any,
        lowerdir: str,
        items: Sequence[dict[str, str | None]],
    ) -> None:
        payload = base64.b64encode(
            json.dumps(list(items), separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        import shlex

        script = """
import base64
import json
import os
import shutil
import sys

root = os.path.abspath(sys.argv[1])
items = json.loads(base64.b64decode(sys.argv[2]).decode("utf-8"))
for item in items:
    rel = str(item["rel"]).replace("\\\\", "/")
    target = os.path.abspath(os.path.join(root, rel))
    if target != root and not target.startswith(root + os.sep):
        raise RuntimeError(f"lowerdir refresh path escaped snapshot: {rel}")
    final_content = item.get("final_content")
    if final_content is None:
        try:
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
        except FileNotFoundError:
            pass
    else:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(str(final_content))
print("refreshed")
"""
        command = _wrap_bash_command(
            f"python3 -c {shlex.quote(script)} "
            f"{shlex.quote(lowerdir)} {shlex.quote(payload)}"
        )
        response = await self._exec_sandbox_process(sandbox, command, timeout=60)
        stdout, exit_code = _extract_exit_code(
            str(getattr(response, "result", "") or ""),
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        if exit_code != 0:
            raise RuntimeError(
                f"overlay lowerdir refresh failed for {lowerdir}: "
                f"exit_code={exit_code} stdout={stdout.strip()!r}"
            )

    async def _exec_sandbox_process(
        self,
        sandbox: Any,
        command: str,
        *,
        timeout: int | None,
    ) -> Any:
        process = getattr(sandbox, "process", None)
        exec_fn = getattr(process, "exec", None) if process is not None else None
        if not callable(exec_fn):
            raise RuntimeError("Sandbox process.exec is unavailable")
        if not inspect.iscoroutinefunction(exec_fn):
            raise RuntimeError("Sandbox process.exec must be async")
        return await exec_fn(command, timeout=timeout) if timeout is not None else await exec_fn(command)

    # -- Query API ------------------------------------------------------------

    def find_definitions(
        self, file_path: str, symbol: str, line: int = 0, character: int = 0,
    ) -> list[SymbolInfo]:
        return self.query_router.find_definitions(file_path, symbol, line, character)

    def find_references(
        self, file_path: str, symbol: str, line: int = 0, character: int = 0,
    ) -> list[ReferenceInfo]:
        return self.query_router.find_references(file_path, symbol, line, character)

    def hover(self, file_path: str, line: int, character: int) -> HoverResult | None:
        return self.query_router.hover(file_path, line, character)

    def diagnostics(self, file_path: str) -> list[Diagnostic]:
        return self.query_router.diagnostics(file_path)

    def query_symbols(self, query: str) -> list[SymbolInfo]:
        return self.symbol_index.find(query)

    def rename_symbol_plan(
        self, file_path: str, line: int, character: int, new_name: str,
    ) -> SemanticRenamePlan:
        """Build a :class:`SemanticRenamePlan` for a semantic rename operation.

        For each affected file, capture current content so callers can render
        a dry-run preview or build one process-backed rename command.
        """
        final_by_path = self.lsp_client.rename_symbol(
            file_path, int(line), int(character), new_name,
        )
        changes: list[SemanticFileChange] = []
        try:
            base_by_path = self._content.read_many(
                list(final_by_path.keys()),
                allow_missing=True,
            )
        except Exception:  # pragma: no cover - defensive I/O
            base_by_path = {}
        for path, final_content in final_by_path.items():
            base_content, existed = base_by_path.get(path, ("", False))
            # Missing files are skipped: Jedi would not have produced a
            # rewrite against a file it could not see.
            if not existed and not base_content:
                continue
            changes.append(
                SemanticFileChange(
                    file_path=path,
                    base_content=base_content,
                    base_hash=content_hash(base_content),
                    final_content=final_content,
                ),
            )
        return SemanticRenamePlan(
            new_name=new_name,
            origin=(file_path, int(line), int(character)),
            changes=tuple(changes),
        )

    def preview_rename_symbol_plan(
        self, file_path: str, line: int, character: int, new_name: str,
    ) -> SemanticRenamePlan:
        """Build a dry-run rename plan without invoking Jedi refactoring.

        Dry-run callers only need before/after file contents for a diff. A
        full Jedi ``rename`` computes a write-ready refactor plan and is much
        more expensive under concurrent sandbox load. For previews, use LSP
        references and apply verified identifier replacements against a single
        batched snapshot. If any reference cannot be verified locally, fall
        back to the full semantic plan for correctness.
        """
        try:
            plan = self._preview_rename_symbol_plan_fast(
                file_path,
                int(line),
                int(character),
                new_name,
            )
        except Exception:
            logger.warning(
                "fast rename preview failed for %s:%s",
                file_path,
                line,
                exc_info=True,
            )
            plan = None
        if plan is not None:
            return plan
        with self._rename_preview_cache_lock:
            self._rename_preview_fast_fallbacks += 1
        return self.rename_symbol_plan(file_path, int(line), int(character), new_name)

    def _preview_rename_symbol_plan_fast(
        self,
        file_path: str,
        line: int,
        character: int,
        new_name: str,
    ) -> SemanticRenamePlan | None:
        snapshot = self._rename_preview_snapshot(file_path, line, character)
        if snapshot is None:
            return None
        if snapshot.old_name == new_name:
            return SemanticRenamePlan(
                new_name=new_name,
                origin=(file_path, int(line), int(character)),
                changes=(),
            )
        final_by_path = _apply_reference_replacements(
            refs=snapshot.refs,
            base_by_path=snapshot.base_by_path,
            old_name=snapshot.old_name,
            new_name=new_name,
        )
        if final_by_path is None:
            return None
        changes = []
        for path, final_content in final_by_path.items():
            base_content, existed = snapshot.base_by_path.get(path, ("", False))
            if not existed:
                continue
            changes.append(
                SemanticFileChange(
                    file_path=path,
                    base_content=base_content,
                    base_hash=content_hash(base_content),
                    final_content=final_content,
                ),
            )
        return SemanticRenamePlan(
            new_name=new_name,
            origin=(file_path, int(line), int(character)),
            changes=tuple(changes),
        )

    def _rename_preview_snapshot(
        self,
        file_path: str,
        line: int,
        character: int,
    ) -> _RenamePreviewSnapshot | None:
        key = (
            file_path,
            int(line),
            int(character),
            self.arbiter.generation,
            self.symbol_index.generation,
            id(getattr(self.lsp_client, "_sandbox", None)),
        )
        while True:
            with self._rename_preview_cache_lock:
                cached = self._rename_preview_cache.get(key)
                if cached is not None:
                    self._rename_preview_cache.move_to_end(key)
                    return cached
                inflight = self._rename_preview_inflight.get(key)
                if inflight is None:
                    inflight = _InflightRenamePreview(event=threading.Event())
                    self._rename_preview_inflight[key] = inflight
                    owner = True
                    break
                owner = False
            if not owner:
                inflight.event.wait()

        try:
            snapshot = self._build_rename_preview_snapshot(file_path, line, character)
            if snapshot is None:
                return None
            with self._rename_preview_cache_lock:
                self._rename_preview_cache[key] = snapshot
                self._rename_preview_cache.move_to_end(key)
                while (
                    len(self._rename_preview_cache)
                    > CODE_INTELLIGENCE_TUNING.rename_preview_cache_max
                ):
                    self._rename_preview_cache.popitem(last=False)
            return snapshot
        finally:
            with self._rename_preview_cache_lock:
                self._rename_preview_inflight.pop(key, None)
                inflight.event.set()

    def _build_rename_preview_snapshot(
        self,
        file_path: str,
        line: int,
        character: int,
    ) -> _RenamePreviewSnapshot | None:
        refs = tuple(self.lsp_client.find_references(file_path, line, character))
        if not refs:
            return None
        paths = [file_path, *(ref.file_path for ref in refs if ref.file_path)]
        base_by_path = self._content.read_many(paths, allow_missing=True)
        origin_content, origin_exists = base_by_path.get(file_path, ("", False))
        if not origin_exists:
            return None
        old_name = _identifier_at_position(origin_content, line, character)
        if not old_name:
            return None
        return _RenamePreviewSnapshot(
            refs=refs,
            base_by_path=base_by_path,
            old_name=old_name,
        )

    # -- Edit API (delegated) -------------------------------------------------

    def apply_edit(self, request: EditRequest) -> EditResult:
        """Apply a single search/replace edit through the service helper path."""
        current, existed = self._content.read(request.file_path, allow_missing=True)
        if not existed:
            return EditResult(
                success=False,
                file_path=request.file_path,
                message=f"Path does not exist: {request.file_path}",
            )
        if request.old_text not in current:
            return EditResult(
                success=False,
                file_path=request.file_path,
                message="Search text not found",
            )
        new_content = current.replace(request.old_text, request.new_text, 1)
        operation = self._write_coordinator.commit_operation_against_base(
            [
                SemanticFileChange(
                    file_path=request.file_path,
                    base_content=current,
                    base_hash=content_hash(current),
                    final_content=new_content,
                    base_existed=True,
                )
            ],
            agent_id=request.agent_id,
            edit_type="edit",
            description=request.description,
        )
        if operation.files:
            return operation.files[0]
        return EditResult(
            success=operation.success,
            file_path=request.file_path,
            message=operation.conflict_reason,
            conflict=bool(operation.conflict_file),
            conflict_reason=operation.status if operation.conflict_file else "",
            timings=dict(operation.timings),
        )

    def commit_operation_against_base(
        self,
        changes: Sequence[OperationChange],
        *,
        agent_id: str = "",
        edit_type: str,
        description: str = "",
    ) -> OperationResult:
        return self._write_coordinator.commit_operation_against_base(
            changes,
            agent_id=agent_id,
            edit_type=edit_type,
            description=description,
        )

    # -- Typed mutation APIs (OCC-gated, batch-capable) ----------------------

    def write_file(
        self,
        specs: Sequence[WriteSpec] | WriteSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        """Write one or more files through a single OCC-gated batch commit.

        ``WriteSpec(overwrite=True)`` replaces an existing file via a
        strict-base rewrite; ``overwrite=False`` requires the path to be
        absent at commit time. All specs in the batch land atomically or
        none land — any slot's ``aborted_version`` aborts the whole batch.
        """
        normalized = [specs] if isinstance(specs, WriteSpec) else list(specs)
        changes = [self._write_spec_to_change(spec) for spec in normalized]
        return self._write_coordinator.commit_operation_against_base(
            changes,
            agent_id=agent_id,
            edit_type="write_file",
            description=description,
        )

    def edit_file(
        self,
        specs: Sequence[EditSpec] | EditSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        """Apply search/replace (or line-range) edits to one or more files.

        Each :class:`EditSpec` is resolved against a plan-time base read
        from :class:`ContentManager`; edits are applied host-side via
        :class:`Patcher`. Any spec whose edits cannot be applied is
        surfaced as a failure in the returned :class:`OperationResult`
        without touching disk.
        """
        normalized = [specs] if isinstance(specs, EditSpec) else list(specs)
        changes, early_failure = self._edit_specs_to_changes(normalized)
        if early_failure is not None:
            return early_failure
        return self._write_coordinator.commit_operation_against_base(
            changes,
            agent_id=agent_id,
            edit_type="edit_file",
            description=description,
        )

    def rename_symbol(
        self,
        file_path: str,
        line: int,
        character: int,
        new_name: str,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        """Run LSP-backed symbol rename and commit every affected file atomically.

        Reuses :meth:`rename_symbol_plan` for the LSP planning pass, then
        submits the resulting :class:`SemanticFileChange` list as one
        OCC batch. An empty plan returns a ``committed`` result with no
        files — callers can distinguish "no references" from a semantic
        rename failure by inspecting ``result.files``.
        """
        plan = self.rename_symbol_plan(file_path, line, character, new_name)
        if not plan.changes:
            return OperationResult(
                success=True,
                status="committed",
                files=(),
                conflict_file=None,
                conflict_reason="",
                timings={},
            )
        return self._write_coordinator.commit_operation_against_base(
            list(plan.changes),
            agent_id=agent_id,
            edit_type="rename_symbol",
            description=description or f"rename to {new_name}",
        )

    def delete_file(
        self,
        paths: Sequence[str],
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        """Delete one or more files through the OCC-gated commit path.

        Reads each current content through :class:`ContentManager`, builds
        one ``OperationChange(final_content=None)`` per path, and submits
        the whole list as one batch. The coordinator's delete branch
        requires ``current_hash == base_hash`` exactly — any drift aborts
        with ``aborted_version`` (no merge fallback for deletes).
        """
        changes: list[OperationChange] = []
        for path in paths:
            current, existed = self._content.read(path, allow_missing=True)
            if not existed:
                return _not_found_result(path)
            changes.append(
                OperationChange(
                    file_path=path,
                    base_content=current,
                    base_hash=content_hash(current),
                    final_content=None,
                    base_existed=True,
                )
            )
        return self._write_coordinator.commit_operation_against_base(
            changes,
            agent_id=agent_id,
            edit_type="delete_file",
            description=description,
        )

    def move_file(
        self,
        specs: Sequence[MoveSpec] | MoveSpec,
        *,
        agent_id: str = "",
        description: str = "",
    ) -> OperationResult:
        """Atomically move one or more files through the OCC-gated path.

        Each :class:`MoveSpec` expands to a delete-src + create-dst pair
        of :class:`OperationChange` entries; the whole list is submitted
        as one batch so sorted-path locks, two-pass resolve-then-apply,
        and TimeMachine rollback make the moves atomic across every
        spec in the batch.
        """
        normalized = [specs] if isinstance(specs, MoveSpec) else list(specs)
        changes: list[OperationChange] = []
        for spec in normalized:
            if spec.src_path == spec.dst_path:
                return _identical_paths_result(spec.src_path)
            src_content, src_existed = self._content.read(
                spec.src_path, allow_missing=True,
            )
            if not src_existed:
                return _not_found_result(spec.src_path)
            dst_content, dst_existed = self._content.read(
                spec.dst_path, allow_missing=True,
            )
            if dst_existed and not spec.overwrite:
                return _dst_exists_result(spec.dst_path)
            changes.append(
                OperationChange(
                    file_path=spec.src_path,
                    base_content=src_content,
                    base_hash=content_hash(src_content),
                    final_content=None,
                    base_existed=True,
                )
            )
            if dst_existed:
                changes.append(
                    OperationChange(
                        file_path=spec.dst_path,
                        base_content=dst_content,
                        base_hash=content_hash(dst_content),
                        final_content=src_content,
                        base_existed=True,
                        strict_base=True,
                    )
                )
            else:
                changes.append(
                    OperationChange(
                        file_path=spec.dst_path,
                        base_content="",
                        base_hash="",
                        final_content=src_content,
                        base_existed=False,
                    )
                )
        return self._write_coordinator.commit_operation_against_base(
            changes,
            agent_id=agent_id,
            edit_type="move_file",
            description=description,
        )

    # -- Spec -> OperationChange adapters ------------------------------------

    def _write_spec_to_change(self, spec: WriteSpec) -> OperationChange:
        current, existed = self._content.read(spec.file_path, allow_missing=True)
        if spec.overwrite:
            return OperationChange(
                file_path=spec.file_path,
                base_content=current,
                base_hash=content_hash(current) if existed else "",
                final_content=spec.content,
                base_existed=existed,
                strict_base=True,
            )
        return OperationChange(
            file_path=spec.file_path,
            base_content="",
            base_hash="",
            final_content=spec.content,
            base_existed=False,
        )

    def _edit_specs_to_changes(
        self,
        specs: Sequence[EditSpec],
    ) -> tuple[list[OperationChange], OperationResult | None]:
        changes: list[OperationChange] = []
        for spec in specs:
            current, existed = self._content.read(spec.file_path, allow_missing=True)
            if not existed:
                return [], _not_found_result(spec.file_path)
            patch = self.patcher.apply_edits(current, list(spec.edits))
            if not patch.success:
                return [], _patch_failed_result(spec.file_path, patch.errors)
            changes.append(
                OperationChange(
                    file_path=spec.file_path,
                    base_content=current,
                    base_hash=content_hash(current),
                    final_content=patch.content,
                    base_existed=True,
                )
            )
        return changes, None

    def undo_last_edit(self, file_path: str) -> EditResult:
        return self._write_coordinator.undo_last_edit(file_path)

    # -- Scope status --------------------------------------------------------

    def scope_status(
        self,
        scope_paths: list[str] | tuple[str, ...] | None,
        *,
        team_run_id: str | None = None,
        briefing_versions: list[dict[str, Any]] | None = None,
        context_pressure: dict[str, Any] | None = None,
        shared_context: list[dict[str, Any]] | None = None,
        baseline_packet: dict[str, Any] | None = None,
        recent_seconds: float = CODE_INTELLIGENCE_TUNING.scope_recent_seconds,
    ) -> dict[str, Any]:
        """Return the authoritative live coordination snapshot for *scope_paths*."""
        normalized = normalize_scope_paths(scope_paths)
        history_ready = getattr(self.arbiter, "initialized", False)

        recent_changes: list[dict[str, Any]] = []
        if history_ready:
            for entry in self.arbiter.recent_edits(
                seconds=recent_seconds,
                team_run_id=team_run_id,
            ):
                fp = str(entry.file_path or "")
                if _scope_excludes(fp, normalized):
                    continue
                recent_changes.append(
                    {
                        "file_path": fp,
                        "agent_run_id": str(entry.agent_run_id or ""),
                        "task_id": str(entry.task_id or ""),
                        "timestamp": entry.created_at.timestamp() if entry.created_at else 0.0,
                        "edit_type": str(entry.edit_type or ""),
                    }
                )
        recent_changes.sort(key=lambda item: (item["file_path"], item["timestamp"]))

        hotspots: list[dict[str, Any]] = []
        if history_ready:
            for fp, count in self.arbiter.hotspots(
                limit=25,
                team_run_id=team_run_id,
            ):
                fp_str = str(fp)
                if _scope_excludes(fp_str, normalized):
                    continue
                hotspots.append({"file_path": fp_str, "edit_count": int(count)})
                if len(hotspots) >= 10:
                    break

        return {
            "scope_paths": normalized,
            "arbiter_generation": self.arbiter.generation,
            "symbol_index_generation": self.symbol_index.generation,
            "recent_changes": recent_changes[:25],
            "hotspots": hotspots,
            "generated_at": time.time(),
        }

    # -- Telemetry ------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return service status summary."""
        lsp = self._lsp_telemetry_fields()
        return {
            "sandbox_id": self.sandbox_id,
            "initialized": self.is_initialized,
            "workspace_root": self.workspace_root,
            "symbol_index": {
                "built": self.symbol_index.is_built,
                "files": self.symbol_index.indexed_files,
                "symbols": self.symbol_index.size,
                "generation": self.symbol_index.generation,
            },
            "arbiter": self.arbiter.status(),
            "edit_buffer": {
                "entries": self.arbiter.metrics.total_edits,
                "generation": self.arbiter.generation,
            },
            "tree_cache": self.tree_cache.stats,
            "rename_preview_cache": self._rename_preview_cache_stats(),
            "rename_preview_fast_fallbacks": self._rename_preview_fast_fallbacks,
            "lsp": lsp,
        }

    def get_telemetry(self) -> CITelemetry:
        lsp = self._lsp_telemetry_fields()
        return CITelemetry(
            symbol_index_size=self.symbol_index.size,
            symbol_index_generation=self.symbol_index.generation,
            indexed_files=self.symbol_index.indexed_files,
            lsp_connected=lsp["connected"],
            lsp_query_count=lsp["queries"],
            lsp_cache_hits=lsp["cache_hits"],
            arbiter_active_locks=self.arbiter.active_lock_count,
            total_edits=self.arbiter.metrics.total_edits,
        )

    def _lsp_telemetry_fields(self) -> dict[str, Any]:
        tel = self.lsp_client.telemetry
        worker_status = self.lsp_client.worker_status()
        return {
            "connected": self.lsp_client.connected,
            "queries": tel.queries,
            "successes": tel.successes,
            "errors": tel.errors,
            "cache_hits": tel.cache_hits,
            "script_runs": tel.script_runs,
            "script_successes": tel.script_successes,
            "script_errors": tel.script_errors,
            "worker_successes": tel.worker_successes,
            "worker_fallbacks": tel.worker_fallbacks,
            "worker_errors": tel.worker_errors,
            "worker_active": worker_status.get("active", False),
            "worker_enabled": worker_status.get("enabled", False),
            "worker_transport": worker_status.get("transport"),
            "worker_pid": worker_status.get("pid"),
            "worker_pid_path": worker_status.get("pid_path"),
            "worker_socket_path": worker_status.get("socket_path"),
            "worker_log_path": worker_status.get("log_path"),
            "worker_stdio_fallback": worker_status.get("stdio_fallback", False),
        }

    def _rename_preview_cache_stats(self) -> dict[str, int]:
        with self._rename_preview_cache_lock:
            return {
                "entries": len(self._rename_preview_cache),
                "inflight_entries": len(self._rename_preview_inflight),
            }

    def _clear_rename_preview_cache(self) -> None:
        with self._rename_preview_cache_lock:
            self._rename_preview_cache.clear()
            self._rename_preview_inflight.clear()

    # -- Cleanup --------------------------------------------------------------

    def dispose(self) -> None:
        """Cleanup all resources."""
        self.arbiter.cleanup_locks()
        self.time_machine.clear()
        try:
            self.lsp_client.close()
        except Exception:  # pragma: no cover - defensive
            logger.debug("lsp_client.close() failed during dispose", exc_info=True)
        logger.info("CodeIntelligenceService disposed for sandbox %s", self.sandbox_id)


def _scope_excludes(file_path: str, normalized_scope: list[str]) -> bool:
    """True if *normalized_scope* is non-empty and *file_path* does not overlap any entry."""
    if not normalized_scope:
        return False
    return not any(scope_paths_overlap(file_path, scope) for scope in normalized_scope)


def _not_found_result(file_path: str) -> OperationResult:
    return OperationResult(
        success=False,
        status="failed",
        files=(
            EditResult(
                success=False,
                file_path=file_path,
                message=f"Path does not exist: {file_path}",
            ),
        ),
        conflict_file=None,
        conflict_reason="not_found",
        timings={},
    )


def _identical_paths_result(file_path: str) -> OperationResult:
    return OperationResult(
        success=False,
        status="failed",
        files=(
            EditResult(
                success=False,
                file_path=file_path,
                message="src_path and dst_path are identical",
            ),
        ),
        conflict_file=None,
        conflict_reason="identical_paths",
        timings={},
    )


def _dst_exists_result(dst_path: str) -> OperationResult:
    return OperationResult(
        success=False,
        status="failed",
        files=(
            EditResult(
                success=False,
                file_path=dst_path,
                message=(
                    f"Destination exists: {dst_path} "
                    "(pass overwrite=True to replace)"
                ),
            ),
        ),
        conflict_file=dst_path,
        conflict_reason="dst_exists",
        timings={},
    )


def _patch_failed_result(file_path: str, errors: list[str]) -> OperationResult:
    detail = "; ".join(errors) if errors else "edit apply failed"
    return OperationResult(
        success=False,
        status="failed",
        files=(
            EditResult(
                success=False,
                file_path=file_path,
                message=detail,
            ),
        ),
        conflict_file=file_path,
        conflict_reason="patch_failed",
        timings={},
    )


def _identifier_at_position(content: str, line: int, character: int) -> str:
    """Return the identifier at or immediately after a 1-indexed position."""
    lines = content.splitlines()
    if line < 1 or line > len(lines):
        return ""
    text = lines[line - 1]
    match = _DEF_CLASS_NAME_RE.match(text)
    if match and character <= match.end(1):
        return match.group(1)
    bounds = _identifier_bounds_near(text, character)
    if bounds is None:
        return ""
    start, end = bounds
    return text[start:end]


def _identifier_bounds_near(text: str, character: int) -> tuple[int, int] | None:
    if not text:
        return None
    pos = max(0, min(int(character), len(text)))
    if pos < len(text) and _is_identifier_char(text[pos]):
        start = pos
        end = pos
    elif pos > 0 and _is_identifier_char(text[pos - 1]):
        start = pos - 1
        end = pos - 1
    else:
        match = _IDENTIFIER_RE.search(text, pos)
        if match is None:
            return None
        return match.start(), match.end()
    while start > 0 and _is_identifier_char(text[start - 1]):
        start -= 1
    while end < len(text) and _is_identifier_char(text[end]):
        end += 1
    return start, end


def _apply_reference_replacements(
    *,
    refs: Sequence[ReferenceInfo],
    base_by_path: dict[str, tuple[str, bool]],
    old_name: str,
    new_name: str,
) -> dict[str, str] | None:
    """Apply verified identifier-span replacements for LSP references.

    Returns ``None`` when any reference does not point exactly at the
    expected identifier. Callers can then fall back to Jedi's full rename.
    """
    grouped: dict[str, set[tuple[int, int]]] = {}
    for ref in refs:
        if not ref.file_path:
            continue
        grouped.setdefault(ref.file_path, set()).add((int(ref.line), int(ref.character)))

    final_by_path: dict[str, str] = {}
    for path, positions in grouped.items():
        base_content, existed = base_by_path.get(path, ("", False))
        if not existed:
            return None
        lines = base_content.splitlines(keepends=True)
        changed = False
        for line, column in sorted(positions, reverse=True):
            if line < 1 or line > len(lines) or column < 0:
                return None
            text = lines[line - 1]
            end = column + len(old_name)
            if text[column:end] != old_name:
                return None
            if column > 0 and _is_identifier_char(text[column - 1]):
                return None
            if end < len(text) and _is_identifier_char(text[end]):
                return None
            lines[line - 1] = text[:column] + new_name + text[end:]
            changed = True
        if changed:
            final_content = "".join(lines)
            if final_content != base_content:
                final_by_path[path] = final_content
    return final_by_path


def _is_identifier_char(char: str) -> bool:
    return char == "_" or char.isalnum()
