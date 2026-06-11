//! The sandbox op catalog: canonical names and routing metadata.
//!
//! Canonical grammar: `sandbox.<verb>` for host ops, `sandbox.<service>.<verb>`
//! for daemon ops, `plugin.<id>.<op>` for dynamic plugin ops. Each op has
//! exactly one wire spelling — its canonical name. The token `v1` is dead:
//! protocol versioning lives in `args`/`ops.json`, never in names.
//!
//! The live `eosd` dispatcher registers these exact strings, and protocol
//! clients should import them from here instead of duplicating string literals.
//! `eosd dump-ops` renders this catalog as `contract/ops.json`; the
//! `cargo xtask check-contract` gate keeps the committed artifact in sync.

use serde::Serialize;

use super::DAEMON_PROTOCOL_VERSION;

/// Functional owner for a catalog op.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum OpFamily {
    /// Host-side sandbox lifecycle (acquire/release/status/list).
    Sandbox,
    /// Runtime readiness, heartbeat, cancellation, and in-flight accounting.
    Control,
    /// LayerStack base, metrics, and checkpoint materialization.
    Checkpoint,
    /// Shared workspace file read/write/edit operations.
    Files,
    /// Plugin package, service, and dynamic dispatch operations.
    Plugins,
    /// Isolated workspace lifecycle and status operations.
    IsolatedWorkspace,
    /// Command-session lifecycle, IO, and completion operations.
    CommandSession,
    /// Caller-keyed or whole-sandbox workspace-run cleanup operations.
    WorkspaceRun,
}

impl OpFamily {
    /// Stable spelling used in `contract/ops.json`.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Sandbox => "Sandbox",
            Self::Control => "Control",
            Self::Checkpoint => "Checkpoint",
            Self::Files => "Files",
            Self::Plugins => "Plugins",
            Self::IsolatedWorkspace => "IsolatedWorkspace",
            Self::CommandSession => "CommandSession",
            Self::WorkspaceRun => "WorkspaceRun",
        }
    }
}

/// Caller surface allowed to invoke an op; `eos-sandbox-gateway` enforces it at the
/// client socket (`visibility != public` → `forbidden`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum OpVisibility {
    /// Part of the public client vocabulary.
    Public,
    /// Operator socket only; never the client socket.
    Operator,
    /// Host machinery only (recovery ready-gate).
    Internal,
    /// Daemon-side test hook; test builds only.
    Test,
}

impl OpVisibility {
    /// Stable spelling used in `contract/ops.json`.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Public => "public",
            Self::Operator => "operator",
            Self::Internal => "internal",
            Self::Test => "test",
        }
    }
}

macro_rules! declare_builtin_daemon_ops {
    (
        $(
            $variant:ident, $const_name:ident, $name:literal,
            $family:ident, $visibility:ident, $mutates_state:literal, $summary:literal;
        )+
    ) => {
        /// One built-in daemon operation in the wire protocol catalog.
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
        #[non_exhaustive]
        pub enum BuiltinDaemonOp {
            $(
                #[doc = concat!("`", $name, "`: ", $summary)]
                $variant,
            )+
        }

        /// Catalog metadata for one built-in daemon op.
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
        pub struct BuiltinOpSpec {
            /// Typed op identity.
            pub op: BuiltinDaemonOp,
            /// Canonical `sandbox.*` wire spelling.
            pub name: &'static str,
            /// Functional owner.
            pub family: OpFamily,
            /// Caller surface that may invoke the op.
            pub visibility: OpVisibility,
            /// Whether the op may change daemon, workspace, or process state.
            pub mutates_state: bool,
            /// One-line summary rendered into `contract/ops.json`.
            pub summary: &'static str,
        }

        impl BuiltinDaemonOp {
            /// Protocol catalog entry for this op.
            #[must_use]
            pub const fn spec(self) -> BuiltinOpSpec {
                match self {
                    $(
                        Self::$variant => BuiltinOpSpec {
                            op: Self::$variant,
                            name: $name,
                            family: OpFamily::$family,
                            visibility: OpVisibility::$visibility,
                            mutates_state: $mutates_state,
                            summary: $summary,
                        },
                    )+
                }
            }
        }

        $(
            #[doc = concat!("Canonical wire spelling `", $name, "`: ", $summary)]
            pub const $const_name: &str = $name;
        )+

        /// Built-in daemon op metadata expected to be available over the wire.
        pub const BUILTIN_DAEMON_OP_SPECS: &[BuiltinOpSpec] = &[
            $(
                BuiltinDaemonOp::$variant.spec(),
            )+
        ];
    };
}

declare_builtin_daemon_ops! {
    RuntimeReady, SANDBOX_RUNTIME_READY, "sandbox.runtime.ready",
        Control, Internal, false, "Daemon readiness probe used by the host recovery machine.";
    InvocationHeartbeat, SANDBOX_CALL_HEARTBEAT, "sandbox.call.heartbeat",
        Control, Public, true, "Extend the lease on an in-flight invocation.";
    InvocationCancel, SANDBOX_CALL_CANCEL, "sandbox.call.cancel",
        Control, Public, true, "Request cooperative cancellation of an in-flight invocation.";
    InflightCount, SANDBOX_CALL_COUNT, "sandbox.call.count",
        Control, Public, false, "Count in-flight invocations.";
    LayerMetrics, SANDBOX_CHECKPOINT_LAYER_METRICS, "sandbox.checkpoint.layer_metrics",
        Checkpoint, Operator, false, "Report LayerStack and storage metrics for the sandbox.";
    EnsureWorkspaceBase, SANDBOX_CHECKPOINT_ENSURE_BASE, "sandbox.checkpoint.ensure_base",
        Checkpoint, Operator, true, "Ensure a workspace base binding exists.";
    BuildWorkspaceBase, SANDBOX_CHECKPOINT_BUILD_BASE, "sandbox.checkpoint.build_base",
        Checkpoint, Operator, true, "Build or rebuild a workspace base binding.";
    CommitToWorkspace, SANDBOX_CHECKPOINT_COMMIT_TO_WORKSPACE, "sandbox.checkpoint.commit_to_workspace",
        Checkpoint, Operator, true, "Materialize LayerStack state into the bound workspace.";
    CommitToGit, SANDBOX_CHECKPOINT_COMMIT_TO_GIT, "sandbox.checkpoint.commit_to_git",
        Checkpoint, Operator, true, "Commit a LayerStack snapshot into the bound workspace's durable Git repo.";
    WorkspaceBinding, SANDBOX_CHECKPOINT_BINDING, "sandbox.checkpoint.binding",
        Checkpoint, Operator, false, "Inspect the workspace binding for a layer stack root.";
    ReadFile, SANDBOX_FILE_READ, "sandbox.file.read",
        Files, Public, false, "Read one file from the layer stack or isolated workspace.";
    WriteFile, SANDBOX_FILE_WRITE, "sandbox.file.write",
        Files, Public, true, "Write one file through the OCC gate.";
    EditFile, SANDBOX_FILE_EDIT, "sandbox.file.edit",
        Files, Public, true, "Edit one file through the OCC gate.";
    PluginEnsure, SANDBOX_PLUGIN_ENSURE, "sandbox.plugin.ensure",
        Plugins, Public, true, "Ensure a plugin service is installed and running.";
    PluginStatus, SANDBOX_PLUGIN_STATUS, "sandbox.plugin.status",
        Plugins, Public, false, "Inspect plugin service status.";
    IsolatedWorkspaceEnter, SANDBOX_ISOLATION_ENTER, "sandbox.isolation.enter",
        IsolatedWorkspace, Public, true, "Enter isolated workspace mode for a caller.";
    IsolatedWorkspaceExit, SANDBOX_ISOLATION_EXIT, "sandbox.isolation.exit",
        IsolatedWorkspace, Public, true, "Exit isolated workspace mode for a caller.";
    IsolatedWorkspaceStatus, SANDBOX_ISOLATION_STATUS, "sandbox.isolation.status",
        IsolatedWorkspace, Public, false, "Inspect isolated workspace status.";
    IsolatedWorkspaceListOpen, SANDBOX_ISOLATION_LIST_OPEN, "sandbox.isolation.list_open",
        IsolatedWorkspace, Operator, false, "List open isolated workspaces.";
    IsolatedWorkspaceTestReset, SANDBOX_ISOLATION_TEST_RESET, "sandbox.isolation.test_reset",
        IsolatedWorkspace, Test, true, "Test-only isolated workspace reset hook.";
    ExecCommand, SANDBOX_COMMAND_EXEC, "sandbox.command.exec",
        CommandSession, Public, true, "Run a foreground command or start a command session.";
    WriteStdin, SANDBOX_COMMAND_WRITE_STDIN, "sandbox.command.write_stdin",
        CommandSession, Public, true, "Write stdin to a command session.";
    CommandReadProgress, SANDBOX_COMMAND_POLL, "sandbox.command.poll",
        CommandSession, Public, false, "Poll command-session progress without writing stdin.";
    CommandCancel, SANDBOX_COMMAND_CANCEL, "sandbox.command.cancel",
        CommandSession, Public, true, "Cancel a command session.";
    CommandCollectCompleted, SANDBOX_COMMAND_COLLECT_COMPLETED, "sandbox.command.collect_completed",
        CommandSession, Public, true, "Collect completed command-session notifications.";
    CommandSessionCount, SANDBOX_COMMAND_COUNT, "sandbox.command.count",
        CommandSession, Public, false, "Count live command sessions.";
    CancelWorkspaceRunsByCaller, SANDBOX_RUN_END, "sandbox.run.end",
        WorkspaceRun, Public, true, "End a run: cancel every workspace run owned by one caller (caller_id == agent_run_id), discarding its command sessions and exiting its isolated workspace.";
    CancelWorkspaceRuns, SANDBOX_RUN_CANCEL_ALL, "sandbox.run.cancel_all",
        WorkspaceRun, Operator, true, "Cancel every workspace run in the sandbox: the whole-sandbox sweep backstop.";
}

/// One host-served sandbox lifecycle op (`served_by: host`).
///
/// The daemon never serves these; the entries are review-owned by `eos-sandbox-gateway`
/// and live here only so `eosd dump-ops` can render the complete catalog.
/// All host ops are `visibility: public` and family `Sandbox`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct HostOpSpec {
    /// Canonical `sandbox.<verb>` wire spelling.
    name: &'static str,
    /// Whether the op changes fleet state.
    mutates_state: bool,
    /// One-line summary rendered into `contract/ops.json`.
    summary: &'static str,
}

/// Host-served sandbox lifecycle ops.
const HOST_OP_SPECS: &[HostOpSpec] = &[
    HostOpSpec {
        name: "sandbox.acquire",
        mutates_state: true,
        summary: "Provision a sandbox container plus daemon and return its sandbox_id.",
    },
    HostOpSpec {
        name: "sandbox.release",
        mutates_state: true,
        summary: "Destroy the sandbox container and drop its registry entry.",
    },
    HostOpSpec {
        name: "sandbox.status",
        mutates_state: false,
        summary: "Host view of one sandbox (container/endpoint/recovery state) plus embedded daemon readiness.",
    },
    HostOpSpec {
        name: "sandbox.list",
        mutates_state: false,
        summary: "Enumerate the sandbox registry.",
    },
];

#[derive(Serialize)]
struct CatalogOp {
    name: &'static str,
    served_by: &'static str,
    visibility: &'static str,
    family: &'static str,
    mutates_state: bool,
    summary: &'static str,
}

#[derive(Serialize)]
struct CatalogDocument {
    protocol_version: i64,
    ops: Vec<CatalogOp>,
}

/// Render the catalog as the `contract/ops.json` document: host ops first,
/// then daemon ops in catalog order. Pretty-printed with a trailing newline so
/// `eosd dump-ops` output can be committed and diffed byte-for-byte.
#[must_use]
pub fn ops_json_document() -> String {
    let host = HOST_OP_SPECS.iter().map(|spec| CatalogOp {
        name: spec.name,
        served_by: "host",
        visibility: OpVisibility::Public.as_str(),
        family: OpFamily::Sandbox.as_str(),
        mutates_state: spec.mutates_state,
        summary: spec.summary,
    });
    let daemon = BUILTIN_DAEMON_OP_SPECS.iter().map(|spec| CatalogOp {
        name: spec.name,
        served_by: "daemon",
        visibility: spec.visibility.as_str(),
        family: spec.family.as_str(),
        mutates_state: spec.mutates_state,
        summary: spec.summary,
    });
    let document = CatalogDocument {
        protocol_version: DAEMON_PROTOCOL_VERSION,
        ops: host.chain(daemon).collect(),
    };
    let mut body =
        serde_json::to_string_pretty(&document).expect("static catalog always serializes");
    body.push('\n');
    body
}

#[cfg(test)]
#[path = "../../tests/unit/wire/ops.rs"]
mod tests;
