//! The async RPC server: AF_UNIX + loopback-TCP listeners, framing, shutdown.
//!
//! This is the ONLY tokio surface in the workspace. It listens on an AF_UNIX
//! socket AND (optionally) a 127.0.0.1 TCP port, reads ONE newline-delimited
//! compact-JSON request per connection (capped at [`eos_protocol::MAX_REQUEST_BYTES`],
//! read-timed at [`eos_protocol::REQUEST_READ_TIMEOUT_S`]), pops the TCP-only
//! auth token before dispatch, routes through the [`crate::dispatcher::OpTable`],
//! and writes back one framed response.
//!
//! # The two async invariants (§5)
//!
//! 1. **Never hold a lock across `.await`.** Connection handlers clone the data
//!    they need out of any guarded state, drop the guard, THEN await. The audit
//!    ring + in-flight registry use synchronous mutexes held only across
//!    non-await sections.
//! 2. **One OCC writer per root via an mpsc work queue.** The single-writer
//!    publish path is reached NOT by locking shared OCC state across awaits but
//!    by sending a [`OccWork`] item down an [`tokio::sync::mpsc`] channel to a
//!    dedicated consumer task, which replies on a [`tokio::sync::oneshot`]. This
//!    serializes publishes without a long-held lock.
//!
//! Shutdown is driven by a [`tokio_util::sync::CancellationToken`]: a SIGTERM /
//! SIGINT cancels it, the serve loops select on it, in-flight pipelines are
//! drained, and (per the Python `start_new_session=True`) the cancel path kills
//! the full child process group.
//! `// PORT backend/src/sandbox/daemon/rpc/server.py:58,62,116-143,183,193 — caps/timeout/auth/listeners`

use std::path::PathBuf;

use tokio::sync::{mpsc, oneshot};
use tokio_util::sync::CancellationToken;

use eos_protocol::LayerChange;

use crate::audit_buffer::AuditBuffer;
use crate::dispatcher::OpTable;
use crate::error::DaemonError;
use crate::in_flight::InFlightRegistry;

/// Maximum bytes read for a single request line (re-exported for the listener
/// buffer cap). `// PORT backend/src/sandbox/daemon/rpc/server.py:58 — MAX_REQUEST_BYTES`
pub const MAX_REQUEST_BYTES: usize = eos_protocol::MAX_REQUEST_BYTES;

/// Per-request read timeout in seconds. `// PORT server.py:62 — REQUEST_READ_TIMEOUT_S`
pub const REQUEST_READ_TIMEOUT_S: f64 = eos_protocol::REQUEST_READ_TIMEOUT_S;

/// Where the daemon binds + writes its pid, plus the optional TCP listener.
/// `// PORT backend/src/sandbox/daemon/rpc/server.py:148-205 — serve(socket_path, pid_path, tcp_host, tcp_port, auth_token)`
#[derive(Debug, Clone)]
pub struct ServerConfig {
    /// AF_UNIX socket path (chmod 0o600 after bind).
    pub socket_path: PathBuf,
    /// Pid file path written after the listeners bind.
    pub pid_path: PathBuf,
    /// Optional loopback TCP host (e.g. `127.0.0.1`).
    pub tcp_host: Option<String>,
    /// Optional loopback TCP port; both host+port enable the TCP listener.
    pub tcp_port: Option<u16>,
    /// TCP-only auth token; popped from each TCP request before dispatch.
    pub auth_token: Option<String>,
}

/// The running daemon: the op table, audit ring, in-flight registry, the OCC
/// work-queue sender, and the shutdown token.
///
/// It ORCHESTRATES but NEVER enters a namespace: namespace work is delegated to
/// the `eosd ns-holder` / `eosd ns-runner` children it spawns; the daemon stays
/// multi-threaded (tokio) and would fail `unshare(CLONE_NEWUSER)` / `setns` into
/// a userns itself.
pub struct DaemonServer {
    config: ServerConfig,
    op_table: OpTable,
    audit: AuditBuffer,
    in_flight: InFlightRegistry,
    occ_tx: mpsc::Sender<OccWork>,
    shutdown: CancellationToken,
}

impl DaemonServer {
    /// Assemble a daemon over `config`, wiring the op table, audit ring, the
    /// in-flight registry, and the OCC single-writer queue. The returned
    /// [`OccWriterQueue`] consumer must be driven by [`Self::serve`].
    pub fn new(config: ServerConfig) -> (Self, OccWriterQueue) {
        let (occ_tx, occ_rx) = mpsc::channel(MAX_OCC_QUEUE_DEPTH);
        let shutdown = CancellationToken::new();
        let server = Self {
            config,
            op_table: OpTable::with_builtins(),
            audit: AuditBuffer::new(),
            in_flight: InFlightRegistry::from_env(),
            occ_tx,
            shutdown: shutdown.clone(),
        };
        (
            server,
            OccWriterQueue {
                rx: occ_rx,
                shutdown,
            },
        )
    }

    /// The shutdown token; cancel it to drain + tear down the serve loops.
    pub fn shutdown_token(&self) -> CancellationToken {
        self.shutdown.clone()
    }

    /// Bind the AF_UNIX (and optional TCP) listeners, write the pid file, install
    /// the SIGTERM/SIGINT handlers, and serve until the shutdown token fires.
    ///
    /// On shutdown: cancel the serve tasks, drain in-flight ephemeral pipelines,
    /// remove the pid file, and unlink the socket.
    // PORT backend/src/sandbox/daemon/rpc/server.py:148-249 — serve(): start_unix_server + start_server, signal handlers, AsyncExitStack, stop_all_ephemeral_pipelines + pid/socket cleanup
    pub async fn serve(self, occ_queue: OccWriterQueue) -> Result<(), DaemonError> {
        let _ = (
            &self.config,
            &self.op_table,
            &self.audit,
            &self.in_flight,
            &self.occ_tx,
            &self.shutdown,
            occ_queue,
        );
        todo!("PORT server.py:148-249 — bind AF_UNIX (chmod 0o600) + optional 127.0.0.1 TCP (limit=MAX_REQUEST_BYTES), write pid, add SIGTERM/SIGINT -> cancel token, select serve loops vs token, drain pipelines + cleanup")
    }

    /// Handle one accepted connection: read one capped, timed request line, pop
    /// the TCP-only auth token, decode the envelope, dispatch, write one framed
    /// response. Per-connection; never holds a lock across the await points.
    // PORT backend/src/sandbox/daemon/rpc/server.py:64-143 — _handle_connection(): readline(timeout), LimitOverrun/Value -> request_too_large, auth pop (TCP), bad_json/invalid_envelope, dispatch, frame + drain
    async fn handle_connection(&self, _is_tcp: bool) -> Result<(), DaemonError> {
        let _ = (
            &self.config.auth_token,
            &self.op_table,
            REQUEST_READ_TIMEOUT_S,
            MAX_REQUEST_BYTES,
        );
        todo!("PORT server.py:64-143 — readline w/ REQUEST_READ_TIMEOUT_S + MAX_REQUEST_BYTES cap, pop _eos_daemon_auth_token on TCP, decode + dispatch + frame, never hold a lock across .await")
    }
}

/// Bound on the OCC work-queue depth (back-pressures publishers onto the single
/// writer). `// PORT backend/src/sandbox/occ/commit_queue.py:66 — max_batch_size headroom`
pub const MAX_OCC_QUEUE_DEPTH: usize = 1024;

/// One unit of OCC publish work plus its reply channel.
///
/// The single-writer guarantee is reached by SENDING this down the mpsc queue to
/// the one consumer task — never by holding a shared OCC lock across an `.await`
/// (§5). The consumer replies on `reply`.
/// `// PORT backend/src/sandbox/occ/commit_queue.py:90-91 — single "occ-commit-queue" writer thread`
pub struct OccWork {
    /// The `layer_stack_root` whose single writer this work targets.
    pub layer_stack_root: String,
    /// The changeset to publish through that one writer.
    pub changes: Vec<LayerChange>,
    /// Whether the changeset must publish atomically (all-or-nothing).
    pub atomic: bool,
    /// Reply channel: the consumer sends the publish outcome back here.
    pub reply: oneshot::Sender<Result<eos_occ::ChangesetResult, DaemonError>>,
}

/// The receive side of the OCC single-writer queue, driven by one consumer task.
///
/// Owning the single `mpsc::Receiver` is what makes the writer single: exactly
/// one consumer task drains it, so all publishes for all roots serialize through
/// this one task (which dispatches per-root to the matching [`OccService`]).
/// `// PORT backend/src/sandbox/occ/commit_queue.py:120-160 — drain loop (batch window, single thread)`
///
/// [`OccService`]: eos_occ::OccService
pub struct OccWriterQueue {
    rx: mpsc::Receiver<OccWork>,
    shutdown: CancellationToken,
}

impl OccWriterQueue {
    /// Run the single consumer: receive [`OccWork`], drive the per-root OCC
    /// writer, reply on the oneshot, until the queue closes or shutdown fires.
    // PORT backend/src/sandbox/occ/commit_queue.py:120-160 — _drain_loop: recv batch, commit_prepared, reply, honor batch_window
    pub async fn run(mut self) {
        let _ = (&mut self.rx, &self.shutdown);
        todo!("PORT commit_queue.py:120-160 — single consumer: recv OccWork, apply through per-root OccService, reply on oneshot, stop on close/cancel")
    }
}
