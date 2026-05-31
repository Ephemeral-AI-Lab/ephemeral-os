//! Ephemeral workspace: the per-operation collaborative pipeline.
//!
//! INVARIANT this crate owns: there are exactly two execution paths, and the
//! verb selects which.
//!
//! * FAST PATH — `read_file`/`write_file`/`edit_file` go DIRECTLY to the layer
//!   stack / single OCC writer. No overlay mount, no namespace.
//! * OVERLAY PATH — `shell`/`glob`/`grep` mount a fresh overlay over a leased
//!   snapshot, run inside a namespace via eos-runner, then (for `WRITE_ALLOWED`
//!   only) CAPTURE the upperdir changes and PUBLISH them. Capture + publish are
//!   ATOMIC: a partial capture is never published. `glob`/`grep` are
//!   `READ_ONLY`, so they mount but skip capture+publish.
//!
//! This is the one workspace flavor that DOES publish — hence it links eos-occ
//! and drives the publish cycle, and it links eos-layerstack as a DIRECT edge
//! (the pipeline reads layer-stack snapshot/manifest types directly, not only
//! through overlay/occ).
//!
//! The orchestration is `async` (it runs inside the daemon's tokio runtime) but
//! this crate adds NO tokio dependency: the `asyncio.Lock` operation locks and
//! the foreign-publish watcher task are daemon-side concerns documented in prose
//! on the future port sites.
//!
//! Port traits invert the upward edges: this crate DEFINES the ports
//! (notably [`ChangesetProjectionPort`]) and `eos-daemon` implements + injects
//! them, keeping the graph leaf->root.
#![forbid(unsafe_code)]

pub mod dispatch;
pub mod error;
pub mod pipeline;
pub mod ports;
pub mod registry;

pub use dispatch::{DispatchRoute, OperationRequest, Verb};
pub use error::{EphemeralError, Result};
pub use pipeline::{
    EphemeralPipeline, OperationOutcome, DEFAULT_FOREIGN_WATCH_INTERVAL_S,
    DEFAULT_SHELL_MOUNT_SQUASH_MAX_DEPTH, DEFAULT_WORKSPACE_ROOT,
};
pub use ports::{
    ChangesetProjectionPort, DispatchSlot, LayerStackSnapshotPort, LeasedSnapshot,
    NamespaceRunnerPort, OccRuntimeServicesPort, OverlayHandle, OverlayLifecyclePort,
    PublishedFile, RawToolPayload, WorkspaceBinding,
};
pub use registry::{
    get_ephemeral_pipeline, reap_stale_runtime_overlay_dirs_once, stop_all_ephemeral_pipelines,
    stop_ephemeral_pipeline, PipelineKey, StopSummary, MAX_PIPELINES,
};
