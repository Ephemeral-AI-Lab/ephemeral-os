"""Single-threaded subprocess helpers for the isolated_workspace feature.

Every module under this directory MUST keep its module-level import set
minimal so the helper can call ``setns(CLONE_NEWUSER)`` without tripping
EINVAL (libc's setns requires the calling process to be single-threaded;
many stdlib modules silently spin a background thread on import).

The allowed import set is pinned by
``test_setns_exec_discipline``. Deferred imports inside ``main()`` AFTER
the setns calls have completed are safe — that requirement only applies
*before* the syscall.
"""
