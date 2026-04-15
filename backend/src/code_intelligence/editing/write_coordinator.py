"""OCC-coordinated write pipeline: prepare, commit, refresh, abort.

The coordinator owns the read-hash-reserve-write dance for a single
:class:`CodeIntelligenceService` sandbox. It depends only on the
collaborators it mutates — no knowledge of the global service registry.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from code_intelligence.editing.arbiter import Arbiter
from code_intelligence.editing.merge import (
    detect_edit_window,
    merge_non_overlapping_edit,
)
from code_intelligence.editing.patcher import Patcher, SearchReplaceEdit
from code_intelligence.editing.time_machine import TimeMachine
from code_intelligence.routing.content_manager import ContentManager
from code_intelligence.types import (
    EditRequest,
    EditResult,
    PreparedWrite,
    WriteRequest,
)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _result(
    file_path: str,
    message: str,
    *,
    success: bool = False,
    conflict: bool = False,
    conflict_reason: str = "",
    snapshot_id: str = "",
    timings: dict[str, float] | None = None,
) -> EditResult:
    return EditResult(
        success=success,
        file_path=file_path,
        message=message,
        conflict=conflict,
        conflict_reason=conflict_reason,
        snapshot_id=snapshot_id,
        timings=dict(timings or {}),
    )


def _record_timing(timings: dict[str, float], key: str, started_at: float) -> None:
    timings[key] = round(time.perf_counter() - started_at, 6)


def _conflict_result(
    arbiter: Arbiter,
    file_path: str,
    message: str,
    *,
    conflict_reason: str,
    snapshot_id: str = "",
    timings: dict[str, float] | None = None,
) -> EditResult:
    arbiter.record_conflict(conflict_reason)
    return _result(
        file_path,
        message,
        conflict=True,
        conflict_reason=conflict_reason,
        snapshot_id=snapshot_id,
        timings=timings,
    )


class WriteCoordinator:
    """Encapsulates the OCC write pipeline for one sandbox."""

    def __init__(
        self,
        *,
        arbiter: Arbiter,
        time_machine: TimeMachine,
        patcher: Patcher,
        symbol_index: Any,
        lsp_client: Any,
        content: ContentManager,
    ) -> None:
        self._arbiter = arbiter
        self._time_machine = time_machine
        self._patcher = patcher
        self._symbol_index = symbol_index
        self._lsp_client = lsp_client
        self._content = content

    # -- High-level entry points ---------------------------------------------

    def apply_edit(self, request: EditRequest) -> EditResult:
        """Apply an OCC-coordinated search/replace edit."""
        prepared = self.prepare_write(
            request.file_path,
            agent_id=request.agent_id,
            expected_hash=request.expected_hash,
        )
        if isinstance(prepared, EditResult):
            return prepared

        try:
            edit = SearchReplaceEdit(old_text=request.old_text, new_text=request.new_text)
            patch_result = self._attempt_patch(prepared, edit)
            if not patch_result.success:
                self._time_machine.discard_snapshot(request.file_path)
                return EditResult(
                    success=False,
                    file_path=request.file_path,
                    message="; ".join(patch_result.errors),
                )

            refreshed = self.refresh_prepared_write(prepared)
            if (
                refreshed.token_id != prepared.token_id
                or refreshed.current_hash != prepared.current_hash
            ):
                prepared = refreshed
                patch_result = self._attempt_patch(prepared, edit)
                if not patch_result.success:
                    self._time_machine.discard_snapshot(request.file_path)
                    self._arbiter.record_conflict("version_mismatch")
                    return EditResult(
                        success=False,
                        file_path=request.file_path,
                        message=(
                            "Write precheck failed: search text no longer matches the latest file "
                            "content. Re-read the file and retry."
                        ),
                        conflict=True,
                        conflict_reason="version_mismatch",
                    )

            return self.commit_prepared_write(
                prepared,
                patch_result.content,
                edit_type="edit",
                description=request.description,
                message=f"Applied {patch_result.edits_applied} edit(s)",
            )
        finally:
            self.abort_prepared_write(prepared)

    def apply_write(self, request: WriteRequest) -> EditResult:
        """Apply an OCC-coordinated full-file write."""
        prepared = self.prepare_write(
            request.file_path,
            agent_id=request.agent_id,
            expected_hash=request.expected_hash,
            allow_missing=True,
        )
        if isinstance(prepared, EditResult):
            return prepared
        try:
            return self.commit_prepared_write(
                prepared,
                request.content,
                edit_type=request.edit_type,
                description=request.description,
                message="Wrote file",
            )
        finally:
            self.abort_prepared_write(prepared)

    # -- OCC primitives -------------------------------------------------------

    def prepare_write(
        self,
        file_path: str,
        *,
        agent_id: str = "",
        expected_hash: str = "",
        allow_missing: bool = False,
    ) -> PreparedWrite | EditResult:
        """Capture a stable read snapshot and issue a write reservation token."""
        timings: dict[str, float] = {}
        read_started = time.perf_counter()
        try:
            current, existed = self._content.read(file_path, allow_missing=allow_missing)
        except Exception as exc:
            _record_timing(timings, "prepare_read", read_started)
            timings["prepare_total"] = timings["prepare_read"]
            return _result(file_path, f"Cannot read file: {exc}", timings=timings)
        _record_timing(timings, "prepare_read", read_started)

        current_hash = content_hash(current)
        if expected_hash and current_hash != expected_hash:
            timings["prepare_total"] = round(time.perf_counter() - read_started, 6)
            return _conflict_result(
                self._arbiter,
                file_path,
                "Write precheck failed: file content changed since it was read. "
                "Re-read the file and retry.",
                conflict_reason="version_mismatch",
                timings=timings,
            )
        token = self._arbiter.issue_token(file_path, current_hash, agent_id)
        return PreparedWrite(
            file_path=file_path,
            token_id=token.token_id,
            current_content=current,
            current_hash=current_hash,
            agent_id=agent_id,
            existed=existed,
        )

    def commit_prepared_write(
        self,
        prepared: PreparedWrite,
        new_content: str,
        *,
        edit_type: str,
        description: str = "",
        message: str = "Wrote file",
    ) -> EditResult:
        """Commit a prepared write after validating the reservation is still current."""
        timings: dict[str, float] = {}
        commit_started = time.perf_counter()
        lock_started = time.perf_counter()
        if not self._arbiter.acquire_file_lock(prepared.file_path):
            _record_timing(timings, "lock_wait", lock_started)
            timings["commit_total"] = round(time.perf_counter() - commit_started, 6)
            return _conflict_result(
                self._arbiter,
                prepared.file_path,
                "Could not acquire file lock (timeout)",
                conflict_reason="lock_timeout",
                timings=timings,
            )
        _record_timing(timings, "lock_wait", lock_started)

        try:
            validate_started = time.perf_counter()
            ok, reason = self._arbiter.validate_token(
                prepared.token_id,
                file_path=prepared.file_path,
                content_hash=prepared.current_hash,
            )
            _record_timing(timings, "validate_token", validate_started)
            if not ok:
                timings["commit_total"] = round(time.perf_counter() - commit_started, 6)
                return _conflict_result(
                    self._arbiter,
                    prepared.file_path,
                    f"Write precheck failed: {reason}",
                    conflict_reason="stale_reservation",
                    timings=timings,
                )

            reread_started = time.perf_counter()
            try:
                current_now, _ = self._content.read(prepared.file_path, allow_missing=True)
            except Exception as exc:
                _record_timing(timings, "reread_under_lock", reread_started)
                timings["commit_total"] = round(time.perf_counter() - commit_started, 6)
                return _result(prepared.file_path, f"Cannot re-read file before commit: {exc}", timings=timings)
            _record_timing(timings, "reread_under_lock", reread_started)

            resolve_started = time.perf_counter()
            write_content, old_hash, conflict = self._resolve_pending_write(
                prepared, current_now, new_content,
            )
            _record_timing(timings, "resolve_pending_write", resolve_started)
            if conflict is not None:
                conflict.timings.update(timings)
                conflict.timings["commit_total"] = round(time.perf_counter() - commit_started, 6)
                return conflict

            snapshot_started = time.perf_counter()
            self._time_machine.save(prepared.file_path, current_now)
            _record_timing(timings, "time_machine_save", snapshot_started)
            write_started = time.perf_counter()
            try:
                self._content.write(prepared.file_path, write_content)
            except Exception as exc:
                _record_timing(timings, "content_write", write_started)
                timings["commit_total"] = round(time.perf_counter() - commit_started, 6)
                return _result(prepared.file_path, f"Write failed: {exc}", timings=timings)
            _record_timing(timings, "content_write", write_started)

            record_started = time.perf_counter()
            gen = self._arbiter.record_edit(
                file_path=prepared.file_path,
                actor_label=prepared.agent_id,
                edit_type=edit_type,
                old_hash=old_hash,
                new_hash=content_hash(write_content),
                description=description,
            )
            _record_timing(timings, "arbiter_record_edit", record_started)
            symbol_started = time.perf_counter()
            self._symbol_index.refresh(prepared.file_path, write_content)
            _record_timing(timings, "symbol_refresh", symbol_started)
            lsp_started = time.perf_counter()
            self._lsp_client.invalidate(prepared.file_path)
            _record_timing(timings, "lsp_invalidate", lsp_started)
            release_started = time.perf_counter()
            self._arbiter.release_token(prepared.token_id)
            _record_timing(timings, "release_token", release_started)
            timings["commit_total"] = round(time.perf_counter() - commit_started, 6)
            return _result(
                prepared.file_path,
                message,
                success=True,
                snapshot_id=str(gen),
                timings=timings,
            )
        finally:
            self._arbiter.release_file_lock(prepared.file_path)

    def commit_change_against_base(
        self,
        file_path: str,
        *,
        base_content: str | None,
        final_content: str | None,
        agent_id: str = "",
        edit_type: str,
        description: str = "",
        write_message: str = "Wrote file",
        delete_message: str = "Deleted file",
    ) -> EditResult:
        """Commit a file change against an explicit base snapshot."""
        timings: dict[str, float] = {}
        commit_started = time.perf_counter()
        if final_content is None and base_content is None:
            return _result(file_path, "Nothing to commit", timings=timings)

        lock_started = time.perf_counter()
        if not self._arbiter.acquire_file_lock(file_path):
            _record_timing(timings, "lock_wait", lock_started)
            timings["commit_total"] = round(time.perf_counter() - commit_started, 6)
            return _conflict_result(
                self._arbiter,
                file_path,
                "Could not acquire file lock (timeout)",
                conflict_reason="lock_timeout",
                timings=timings,
            )
        _record_timing(timings, "lock_wait", lock_started)

        try:
            reread_started = time.perf_counter()
            try:
                current_now, existed_now = self._content.read(file_path, allow_missing=True)
            except Exception as exc:
                _record_timing(timings, "reread_under_lock", reread_started)
                timings["commit_total"] = round(time.perf_counter() - commit_started, 6)
                return _result(file_path, f"Cannot read file before commit: {exc}", timings=timings)
            _record_timing(timings, "reread_under_lock", reread_started)

            current_content = current_now if existed_now else None
            current_hash = content_hash(current_now) if existed_now else ""

            if current_content == base_content:
                return self._apply_change(
                    file_path,
                    current_now=current_now,
                    existed_now=existed_now,
                    resolved_content=final_content,
                    agent_id=agent_id,
                    edit_type=edit_type,
                    description=description,
                    success_message=delete_message if final_content is None else write_message,
                    old_hash=current_hash,
                    timings=timings,
                    commit_started=commit_started,
                )

            if final_content is None:
                timings["commit_total"] = round(time.perf_counter() - commit_started, 6)
                return _conflict_result(
                    self._arbiter,
                    file_path,
                    "Write precheck failed: file content changed before delete. "
                    "Re-read the file and retry.",
                    conflict_reason="version_mismatch",
                    timings=timings,
                )

            merge_started = time.perf_counter()
            resolved_content, conflict = self._merge_change_against_base(
                file_path=file_path,
                base_content=base_content,
                final_content=final_content,
                current_content=current_content,
            )
            _record_timing(timings, "merge_against_base", merge_started)
            if conflict is not None:
                conflict.timings.update(timings)
                conflict.timings["commit_total"] = round(time.perf_counter() - commit_started, 6)
                return conflict

            return self._apply_change(
                file_path,
                current_now=current_now,
                existed_now=existed_now,
                resolved_content=resolved_content,
                agent_id=agent_id,
                edit_type=edit_type,
                description=description,
                success_message=write_message,
                old_hash=current_hash,
                timings=timings,
                commit_started=commit_started,
            )
        finally:
            self._arbiter.release_file_lock(file_path)

    def refresh_prepared_write(self, prepared: PreparedWrite) -> PreparedWrite:
        """Refresh a prepared write snapshot, reissuing a token when the file changed."""
        refresh_started = time.perf_counter()
        try:
            current, existed = self._content.read(prepared.file_path, allow_missing=True)
        except Exception:
            return prepared

        current_hash = content_hash(current)
        if current_hash == prepared.current_hash and existed == prepared.existed:
            return prepared

        self.abort_prepared_write(prepared)
        token = self._arbiter.issue_token(prepared.file_path, current_hash, prepared.agent_id)
        return PreparedWrite(
            file_path=prepared.file_path,
            token_id=token.token_id,
            current_content=current,
            current_hash=current_hash,
            agent_id=prepared.agent_id,
            existed=existed,
            line_start=prepared.line_start,
            line_end=prepared.line_end,
            operation_type=prepared.operation_type,
        )

    def abort_prepared_write(self, prepared: PreparedWrite) -> None:
        """Release any reservation still held for *prepared*."""
        ok, _ = self._arbiter.validate_token(prepared.token_id, file_path=prepared.file_path)
        if ok:
            self._arbiter.release_token(prepared.token_id)

    def undo_last_edit(self, file_path: str) -> EditResult:
        """Undo the last edit to *file_path* via TimeMachine."""
        snapshot = self._time_machine.rollback(file_path)
        if snapshot is None:
            return _result(file_path, "No snapshot available for undo")
        try:
            self._content.write(file_path, snapshot.content)
        except Exception as exc:
            return _result(file_path, f"Undo write failed: {exc}")
        self._symbol_index.refresh(file_path, snapshot.content)
        self._lsp_client.invalidate(file_path)
        return _result(file_path, "Reverted to previous snapshot", success=True)

    # -- Internal -------------------------------------------------------------

    def _attempt_patch(self, prepared: PreparedWrite, edit: SearchReplaceEdit) -> Any:
        return self._patcher.apply_edits(prepared.current_content, [edit])

    def _apply_change(
        self,
        file_path: str,
        *,
        current_now: str,
        existed_now: bool,
        resolved_content: str | None,
        agent_id: str,
        edit_type: str,
        description: str,
        success_message: str,
        old_hash: str,
        timings: dict[str, float] | None = None,
        commit_started: float | None = None,
    ) -> EditResult:
        timings = timings if timings is not None else {}
        commit_started = commit_started if commit_started is not None else time.perf_counter()
        snapshot_started = time.perf_counter()
        self._time_machine.save(file_path, current_now)
        _record_timing(timings, "time_machine_save", snapshot_started)
        write_started = time.perf_counter()
        try:
            if resolved_content is None:
                self._content.delete(file_path)
            else:
                self._content.write(file_path, resolved_content)
        except Exception as exc:
            _record_timing(timings, "content_write", write_started)
            action = "Delete" if resolved_content is None else "Write"
            timings["commit_total"] = round(time.perf_counter() - commit_started, 6)
            return _result(file_path, f"{action} failed: {exc}", timings=timings)
        _record_timing(timings, "content_write", write_started)

        new_hash = content_hash(resolved_content) if resolved_content is not None else ""
        record_started = time.perf_counter()
        gen = self._arbiter.record_edit(
            file_path=file_path,
            actor_label=agent_id,
            edit_type=edit_type,
            old_hash=old_hash if existed_now else "",
            new_hash=new_hash,
            description=description,
        )
        _record_timing(timings, "arbiter_record_edit", record_started)
        symbol_started = time.perf_counter()
        self._symbol_index.refresh(file_path, resolved_content or "")
        _record_timing(timings, "symbol_refresh", symbol_started)
        lsp_started = time.perf_counter()
        self._lsp_client.invalidate(file_path)
        _record_timing(timings, "lsp_invalidate", lsp_started)
        timings["commit_total"] = round(time.perf_counter() - commit_started, 6)
        return _result(
            file_path,
            success_message,
            success=True,
            snapshot_id=str(gen),
            timings=timings,
        )

    def _merge_change_against_base(
        self,
        *,
        file_path: str,
        base_content: str | None,
        final_content: str,
        current_content: str | None,
    ) -> tuple[str, EditResult | None]:
        if base_content is None or current_content is None:
            return "", _conflict_result(
                self._arbiter,
                file_path,
                "Write precheck failed: file content changed before commit. "
                "Re-read the file and retry.",
                conflict_reason="version_mismatch",
            )

        line_start, line_end, operation_type = detect_edit_window(base_content, final_content)
        if line_start is None:
            return "", _conflict_result(
                self._arbiter,
                file_path,
                "Write precheck failed: file content changed before commit. "
                "Re-read the file and retry.",
                conflict_reason="version_mismatch",
            )

        merged = merge_non_overlapping_edit(
            original_content=base_content,
            new_content=final_content,
            current_content=current_content,
            line_start=line_start,
            line_end=line_end,
            operation_type=operation_type,
        )
        if merged is not None:
            return merged, None
        return "", _conflict_result(
            self._arbiter,
            file_path,
            "Write precheck failed: file content changed in an overlapping "
            "or unsupported range. Re-read the file and retry.",
            conflict_reason="overlapping_range",
        )

    def _resolve_pending_write(
        self,
        prepared: PreparedWrite,
        current_now: str,
        requested_content: str,
    ) -> tuple[str, str, EditResult | None]:
        """Merge a prepared write with the latest file content when possible."""
        current_hash = content_hash(current_now)
        if current_hash == prepared.current_hash:
            return requested_content, prepared.current_hash, None

        line_start = prepared.line_start
        line_end = prepared.line_end
        operation_type = prepared.operation_type or "replace"
        if line_start is None:
            line_start, line_end, operation_type = detect_edit_window(
                prepared.current_content,
                requested_content,
            )

        if prepared.existed and line_start is not None:
            merged = merge_non_overlapping_edit(
                original_content=prepared.current_content,
                new_content=requested_content,
                current_content=current_now,
                line_start=line_start,
                line_end=line_end,
                operation_type=operation_type,
            )
            if merged is not None:
                return merged, current_hash, None
            return "", current_hash, _conflict_result(
                self._arbiter,
                prepared.file_path,
                "Write precheck failed: file content changed in an overlapping "
                "or unsupported range. Re-read the file and retry.",
                conflict_reason="overlapping_range",
            )

        return "", current_hash, _conflict_result(
            self._arbiter,
            prepared.file_path,
            "Write precheck failed: file content changed before commit. "
            "Re-read the file and retry.",
            conflict_reason="version_mismatch",
        )
