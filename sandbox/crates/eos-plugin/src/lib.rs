//! Plugin dispatch (PPC) — warm per-session plugin server + a bidirectional,
//! message-id'd plugin-process channel.
//!
//! # Invariant this crate owns
//!
//! The Python path loaded a plugin handler via `importlib.import_module(
//! "plugins.catalog.<plugin>.runtime.server")` IN-PROCESS, per call
//! (`overlay_child.py:129`). A Rust daemon cannot reproduce that. This crate
//! replaces it with a WARM per-session plugin server (one server process per
//! `layer_stack_root`) plus a BIDIRECTIONAL, message-id'd PPC channel over an
//! AF_UNIX socket using the daemon's newline-delimited compact-JSON framing
//! (reusing [`eos_protocol`]'s envelope encode/decode — no second wire format).
//!
//! AV-3: the warm server is keyed per `layer_stack_root` and TORN DOWN on session
//! end. The [`WarmServerRegistry`] owns the handles; [`WarmServer`] reaps its
//! process group on `Drop` (best-effort, non-panicking — the real teardown is
//! [`WarmServer::teardown`]).
//!
//! Three dispatch modes, selected from `(intent, auto_workspace_overlay)`:
//! * READ_ONLY -> the op runs OUT-OF-PROCESS in the warm server (no overlay, no
//!   namespace child, no publish).
//! * WRITE_ALLOWED -> `eosd` owns the per-op overlay + the OCC publish around the
//!   warm-server call.
//! * self-managed (`auto_workspace_overlay = false`) -> the plugin owns its
//!   overlay and calls BACK over the PPC channel to commit.
//!
//! Isolated mode BLOCKS all plugin ops ([`PluginError::ForbiddenInIsolatedWorkspace`]).
//!
//! # MF-1 — ONE single writer per `layer_stack_root` (STATE LOUDLY)
//!
//! The self-managed OCC commit callback MUST route through the SAME
//! per-`layer_stack_root` single `occ-commit-queue` writer + storage lease as the
//! primary WRITE_ALLOWED path — NEVER a second writer instance. This crate
//! enforces that structurally: it CONSUMES the existing
//! [`eos_ephemeral::OccRuntimeServicesPort`] (the per-root single-writer port) for
//! BOTH the WRITE_ALLOWED and self-managed paths, and NEVER defines a parallel OCC
//! services trait. See [`dispatch`].
//!
//! # Build-time guarantee — NOT `eos-occ`
//!
//! Parallel to `eos-isolated`, this crate does NOT depend on `eos-occ`. Its only
//! occ touch in Python (`projection.py:10`) was the snapshot/lease/projection
//! HINGE — which lives in `eos-layerstack` ([`eos_layerstack::SnapshotLeasePort`]),
//! used for snapshot/lease/projection, NEVER publish. So this crate links
//! `eos-layerstack`, never `eos-occ`. If `eos-occ` ever appears in this crate's
//! `Cargo.toml`, the no-second-writer guarantee silently breaks — guard that edge.
//!
//! # Runtime payload (NOT a Cargo dependency)
//!
//! The plugin payload runtime is Node 22.13.1 + Pyright, provisioned into the
//! warm server via `put_archive` — it is a RUNTIME artifact, not a core dep of
//! this crate.
#![forbid(unsafe_code)]

pub mod context;
pub mod dispatch;
pub mod error;
pub mod ppc;
pub mod registry;
pub mod warm_server;

pub use context::{PluginCaller, PluginOpContext};
pub use dispatch::{
    dispatch_read_only, dispatch_self_managed, dispatch_write_allowed, ensure_not_isolated,
    DispatchMode,
};
pub use error::{PluginError, Result};
pub use ppc::{PpcDirection, PpcEnvelope};
pub use registry::{
    public_op_name, OpRegistry, PluginOpRegistration, DEFAULT_AUTO_WORKSPACE_OVERLAY,
};
pub use warm_server::{WarmServer, WarmServerRegistry, MAX_WARM_SERVERS};
