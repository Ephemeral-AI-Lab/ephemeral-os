"""Compatibility module for the private namespace helper entrypoint."""

from __future__ import annotations

from sandbox.command_exec.entrypoints import namespace_helper as _impl

execute = _impl.execute
main = _impl.main
subprocess = _impl.subprocess

_MountInputs = _impl._MountInputs
_NamespaceRequest = _impl._NamespaceRequest
_assert_same_dir = _impl._assert_same_dir
_called_process_message = _impl._called_process_message
_fallback_ref = _impl._fallback_ref
_fd_path = _impl._fd_path
_fail = _impl._fail
_fail_bad_payload = _impl._fail_bad_payload
_json_error_line = _impl._json_error_line
_mount_overlay = _impl._mount_overlay
_open_dir_no_follow = _impl._open_dir_no_follow
_payload_request = _impl._payload_request
_umount = _impl._umount
_validate_mount_inputs = _impl._validate_mount_inputs
_validate_overlay_path_text = _impl._validate_overlay_path_text
_write_control = _impl._write_control
_write_error = _impl._write_error
_write_timings = _impl._write_timings

__all__ = [
    "execute",
    "main",
]
