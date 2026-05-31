//! Process-local advisory writer lock for a layer-stack storage root.
//!
//! # The DUAL-LAYER lease (both layers MUST be reproduced)
//!
//! 1. **Cross-process advisory lease** — `flock(fd, LOCK_EX | LOCK_NB)` on the
//!    `.storage-writer.lock` file (`O_RDWR | O_CREAT, 0o644`). Prevents a second
//!    daemon *process* from owning the same root; a contended acquire returns
//!    [`crate::LayerStackError::StorageRootOwned`] rather than blocking. Released
//!    with `LOCK_UN` + `close` once the in-process refcount hits zero.
//!    `// PORT backend/src/sandbox/layer_stack/storage_lock.py:71,55,69`
//! 2. **In-process reentrant mutex + refcount** — a per-root registry keyed by
//!    the canonical absolute path serializes multiple in-process `LayerStack`
//!    managers that may coexist after cache drops / overlay resets. The mutex is
//!    a **reentrant** `threading.RLock` in Python.
//!    `// PORT backend/src/sandbox/layer_stack/storage_lock.py:13,14,22,78`
//!
//! # ⚠ THE REENTRANT-RLock → non-reentrant-Mutex DEADLOCK TRAP
//!
//! Python holds a `threading.RLock` (REENTRANT). The same thread re-acquires it
//! via `.exclusive()` while it already holds it — e.g. `LayerStack.squash`
//! takes `_storage_write_guard()` (the RLock) and then `self._lock` (a SECOND
//! RLock), and `release_lease` is called *inside* `squash`'s `finally` while the
//! write guard is still held. A naive 1:1 port to `std::sync::Mutex`
//! (NON-reentrant) **DEADLOCKS** on the second same-thread acquire.
//!
//! Do NOT 1:1-port. The future implementer must either (a) restructure the
//! re-entrant sections so re-entry is impossible (thread the already-acquired
//! guard through the call graph instead of re-locking), or (b) use a reentrant
//! guard type. This is a `todo!()` skeleton for now.
//! `// PORT backend/src/sandbox/layer_stack/transaction.py:45`
//! `// PORT backend/src/sandbox/layer_stack/stack.py:365`

use std::path::Path;

use crate::error::LayerStackError;

/// Lock-file name placed at the root of every storage root.
/// `// PORT backend/src/sandbox/layer_stack/storage_lock.py:13`
pub const STORAGE_WRITER_LOCK_FILE: &str = ".storage-writer.lock";

/// A held cross-process + in-process writer lease for one storage root.
///
/// RAII: dropping the last lease for a root releases the `flock` and closes the
/// fd (refcount-gated). `exclusive()` returns the reentrant in-process guard.
/// `// PORT backend/src/sandbox/layer_stack/storage_lock.py:25-43 — StorageWriterLockLease`
#[derive(Debug)]
pub struct StorageWriterLockLease {
    _key: String,
}

impl StorageWriterLockLease {
    /// Acquire (or refcount-bump) the dual-layer writer lease for `storage_root`.
    ///
    /// Fails with [`LayerStackError::StorageRootOwned`] if another process holds
    /// the `flock`. The registry key is the canonicalized absolute path.
    /// `// PORT backend/src/sandbox/layer_stack/storage_lock.py:59-84 — acquire_storage_writer_lock`
    pub fn acquire(storage_root: &Path) -> Result<Self, LayerStackError> {
        let _ = storage_root;
        // PORT backend/src/sandbox/layer_stack/storage_lock.py:59-84 — flock(LOCK_EX|LOCK_NB) + per-root RLock registry, refcount-bumped
        todo!("PORT: acquire_storage_writer_lock — dual-layer (flock cross-process + reentrant in-process mutex)")
    }

    /// Enter the in-process exclusive (reentrant) write guard for this root.
    ///
    /// See the module-level DEADLOCK TRAP: the returned guard must tolerate
    /// same-thread re-entry (Python `threading.RLock`).
    /// `// PORT backend/src/sandbox/layer_stack/storage_lock.py:33-40 — exclusive`
    pub fn exclusive(&self) -> Result<ExclusiveGuard<'_>, LayerStackError> {
        // PORT backend/src/sandbox/layer_stack/storage_lock.py:33-40 — return the reentrant per-root mutex (NOT a plain Mutex lock — see TRAP)
        todo!("PORT: StorageWriterLockLease.exclusive — reentrant write guard")
    }
}

/// In-process exclusive write guard. Reentrant on the same thread (see TRAP).
/// `// PORT backend/src/sandbox/layer_stack/storage_lock.py:33-40`
#[derive(Debug)]
pub struct ExclusiveGuard<'lease> {
    _lease: &'lease StorageWriterLockLease,
}
