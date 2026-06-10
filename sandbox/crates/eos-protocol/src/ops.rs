//! The sandbox op catalog: canonical names, legacy aliases, and routing metadata.
//!
//! Canonical grammar: `sandbox.<verb>` for host ops, `sandbox.<service>.<verb>`
//! for daemon ops, `plugin.<id>.<op>` for dynamic plugin ops. Every `api.*`
//! spelling is a legacy alias of a canonical daemon op. The token `v1` is dead:
//! protocol versioning lives in `args`/`ops.json`, never in names.
//!
//! The live `eosd` dispatcher registers these exact strings, and protocol
//! clients should import them from here instead of duplicating string literals.
//! `eosd dump-ops` renders this catalog as `contract/ops.json`; the
//! `cargo xtask check-contract` gate keeps the committed artifact in sync.

use serde::Serialize;

use crate::version::DAEMON_PROTOCOL_VERSION;

/// Functional owner for a catalog op.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum OpFamily {
    /// Host-side sandbox lifecycle (acquire/release/status/list).
    Sandbox,
    /// Runtime readiness, heartbeat, cancellation, and in-flight accounting.
    Control,
    /// LayerStack base, metrics, and checkpoint materialization.
    Checkpoint,
    /// Audit ring pull/snapshot/reset operations.
    Audit,
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
            Self::Audit => "Audit",
            Self::Files => "Files",
            Self::Plugins => "Plugins",
            Self::IsolatedWorkspace => "IsolatedWorkspace",
            Self::CommandSession => "CommandSession",
            Self::WorkspaceRun => "WorkspaceRun",
        }
    }
}

/// Caller surface allowed to invoke an op; `eos-api` enforces it at the
/// client socket (`visibility != public` → `forbidden`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum OpVisibility {
    /// Part of the public client vocabulary.
    Public,
    /// `eos-api admin <op>` CLI only; never the client socket.
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
            $variant:ident, $const_name:ident, $name:literal, $legacy:literal,
            $family:ident, $visibility:ident, $mutates_state:literal, $summary:literal;
        )+
    ) => {
        /// One built-in daemon operation in the wire protocol catalog.
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
        #[non_exhaustive]
        pub enum BuiltinDaemonOp {
            $(
                #[doc = concat!("`", $name, "` (alias `", $legacy, "`): ", $summary)]
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
            /// Legacy wire spellings the dispatcher keeps accepting.
            pub aliases: &'static [&'static str],
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
            /// Canonical `sandbox.*` wire spelling.
            #[must_use]
            pub const fn name(self) -> &'static str {
                self.spec().name
            }

            /// Legacy wire spellings the dispatcher keeps accepting.
            #[must_use]
            pub const fn aliases(self) -> &'static [&'static str] {
                self.spec().aliases
            }

            /// The pre-canonical `api.*` spelling (sole legacy alias).
            #[must_use]
            pub const fn legacy_wire(self) -> &'static str {
                self.spec().aliases[0]
            }

            /// Functional owner for this op.
            #[must_use]
            pub const fn family(self) -> OpFamily {
                self.spec().family
            }

            /// Caller surface that may invoke the op.
            #[must_use]
            pub const fn visibility(self) -> OpVisibility {
                self.spec().visibility
            }

            /// Whether this op may change daemon, workspace, or process state.
            #[must_use]
            pub const fn mutates_state(self) -> bool {
                self.spec().mutates_state
            }

            /// Protocol catalog entry for this op.
            #[must_use]
            pub const fn spec(self) -> BuiltinOpSpec {
                match self {
                    $(
                        Self::$variant => BuiltinOpSpec {
                            op: Self::$variant,
                            name: $name,
                            aliases: &[$legacy],
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
            #[doc = concat!("Legacy alias of `", $name, "`: ", $summary)]
            pub const $const_name: &str = $legacy;
        )+

        /// Built-in daemon op metadata expected to be available over the wire.
        pub const BUILTIN_DAEMON_OP_SPECS: &[BuiltinOpSpec] = &[
            $(
                BuiltinDaemonOp::$variant.spec(),
            )+
        ];

        /// Legacy dispatch spellings the daemon has registered since before
        /// canonical names existed.
        pub const BUILTIN_DAEMON_OPS: &[&str] = &[
            $(
                $const_name,
            )+
        ];
    };
}

declare_builtin_daemon_ops! {
    RuntimeReady, API_RUNTIME_READY, "sandbox.runtime.ready", "api.runtime.ready",
        Control, Internal, false, "Daemon readiness probe used by the host recovery machine.";
    InvocationHeartbeat, API_V1_HEARTBEAT, "sandbox.call.heartbeat", "api.v1.heartbeat",
        Control, Public, true, "Extend the lease on an in-flight invocation.";
    InvocationCancel, API_V1_CANCEL, "sandbox.call.cancel", "api.v1.cancel",
        Control, Public, true, "Request cooperative cancellation of an in-flight invocation.";
    InflightCount, API_V1_INFLIGHT_COUNT, "sandbox.call.count", "api.v1.inflight_count",
        Control, Public, false, "Count in-flight invocations.";
    LayerMetrics, API_LAYER_METRICS, "sandbox.checkpoint.layer_metrics", "api.layer_metrics",
        Checkpoint, Operator, false, "Report LayerStack and storage metrics for the sandbox.";
    EnsureWorkspaceBase, API_ENSURE_WORKSPACE_BASE, "sandbox.checkpoint.ensure_base", "api.ensure_workspace_base",
        Checkpoint, Operator, true, "Ensure a workspace base binding exists.";
    BuildWorkspaceBase, API_BUILD_WORKSPACE_BASE, "sandbox.checkpoint.build_base", "api.build_workspace_base",
        Checkpoint, Operator, true, "Build or rebuild a workspace base binding.";
    CommitToWorkspace, API_COMMIT_TO_WORKSPACE, "sandbox.checkpoint.commit_to_workspace", "api.commit_to_workspace",
        Checkpoint, Operator, true, "Materialize LayerStack state into the bound workspace.";
    CommitToGit, API_COMMIT_TO_GIT, "sandbox.checkpoint.commit_to_git", "api.commit_to_git",
        Checkpoint, Operator, true, "Commit a LayerStack snapshot into the bound workspace's durable Git repo.";
    WorkspaceBinding, API_WORKSPACE_BINDING, "sandbox.checkpoint.binding", "api.workspace_binding",
        Checkpoint, Operator, false, "Inspect the workspace binding for a layer stack root.";
    AuditPull, API_AUDIT_PULL, "sandbox.audit.pull", "api.audit.pull",
        Audit, Operator, false, "Pull audit events after a cursor.";
    AuditSnapshot, API_AUDIT_SNAPSHOT, "sandbox.audit.snapshot", "api.audit.snapshot",
        Audit, Operator, false, "Snapshot audit ring metadata.";
    AuditResetFloor, API_AUDIT_RESET_FLOOR, "sandbox.audit.reset_floor", "api.audit.reset_floor",
        Audit, Operator, true, "Reset the audit floor when the daemon-side test gate allows it.";
    ReadFile, API_V1_READ_FILE, "sandbox.file.read", "api.v1.read_file",
        Files, Public, false, "Read one file from the layer stack or isolated workspace.";
    WriteFile, API_V1_WRITE_FILE, "sandbox.file.write", "api.v1.write_file",
        Files, Public, true, "Write one file through the OCC gate.";
    EditFile, API_V1_EDIT_FILE, "sandbox.file.edit", "api.v1.edit_file",
        Files, Public, true, "Edit one file through the OCC gate.";
    PluginEnsure, API_PLUGIN_ENSURE, "sandbox.plugin.ensure", "api.plugin.ensure",
        Plugins, Public, true, "Ensure a plugin service is installed and running.";
    PluginStatus, API_PLUGIN_STATUS, "sandbox.plugin.status", "api.plugin.status",
        Plugins, Public, false, "Inspect plugin service status.";
    IsolatedWorkspaceEnter, API_ISOLATED_WORKSPACE_ENTER, "sandbox.isolation.enter", "api.isolated_workspace.enter",
        IsolatedWorkspace, Public, true, "Enter isolated workspace mode for a caller.";
    IsolatedWorkspaceExit, API_ISOLATED_WORKSPACE_EXIT, "sandbox.isolation.exit", "api.isolated_workspace.exit",
        IsolatedWorkspace, Public, true, "Exit isolated workspace mode for a caller.";
    IsolatedWorkspaceStatus, API_ISOLATED_WORKSPACE_STATUS, "sandbox.isolation.status", "api.isolated_workspace.status",
        IsolatedWorkspace, Public, false, "Inspect isolated workspace status.";
    IsolatedWorkspaceListOpen, API_ISOLATED_WORKSPACE_LIST_OPEN, "sandbox.isolation.list_open", "api.isolated_workspace.list_open",
        IsolatedWorkspace, Operator, false, "List open isolated workspaces.";
    IsolatedWorkspaceTestReset, API_ISOLATED_WORKSPACE_TEST_RESET, "sandbox.isolation.test_reset", "api.isolated_workspace.test_reset",
        IsolatedWorkspace, Test, true, "Test-only isolated workspace reset hook.";
    ExecCommand, API_V1_EXEC_COMMAND, "sandbox.command.exec", "api.v1.exec_command",
        CommandSession, Public, true, "Run a foreground command or start a command session.";
    WriteStdin, API_V1_WRITE_STDIN, "sandbox.command.write_stdin", "api.v1.write_stdin",
        CommandSession, Public, true, "Write stdin to a command session.";
    CommandReadProgress, API_V1_COMMAND_READ_PROGRESS, "sandbox.command.poll", "api.v1.command.read_progress",
        CommandSession, Public, false, "Poll command-session progress without writing stdin.";
    CommandCancel, API_V1_COMMAND_CANCEL, "sandbox.command.cancel", "api.v1.command.cancel",
        CommandSession, Public, true, "Cancel a command session.";
    CommandCollectCompleted, API_V1_COMMAND_COLLECT_COMPLETED, "sandbox.command.collect_completed", "api.v1.command.collect_completed",
        CommandSession, Public, true, "Collect completed command-session notifications.";
    CommandSessionCount, API_V1_COMMAND_SESSION_COUNT, "sandbox.command.count", "api.v1.command_session_count",
        CommandSession, Public, false, "Count live command sessions.";
    CancelWorkspaceRunsByCaller, API_V1_CANCEL_WORKSPACE_RUNS_BY_CALLER, "sandbox.run.end", "api.v1.cancel_workspace_runs_by_caller_id",
        WorkspaceRun, Public, true, "End a run: cancel every workspace run owned by one caller (caller_id == agent_run_id), discarding its command sessions and exiting its isolated workspace.";
    CancelWorkspaceRuns, API_V1_CANCEL_WORKSPACE_RUNS, "sandbox.run.cancel_all", "api.v1.cancel_workspace_runs",
        WorkspaceRun, Operator, true, "Cancel every workspace run in the sandbox: the whole-sandbox sweep backstop.";
}

impl BuiltinDaemonOp {
    /// Resolve a wire spelling — canonical or legacy alias — to its typed op.
    #[must_use]
    pub fn resolve(spelling: &str) -> Option<Self> {
        BUILTIN_DAEMON_OP_SPECS
            .iter()
            .find(|spec| spec.name == spelling || spec.aliases.contains(&spelling))
            .map(|spec| spec.op)
    }
}

/// One host-served sandbox lifecycle op (`served_by: host`).
///
/// The daemon never serves these; the entries are review-owned by `eos-api`
/// and live here only so `eosd dump-ops` can render the complete catalog.
/// All host ops are `visibility: public`, family `Sandbox`, and alias-free.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct HostOpSpec {
    /// Canonical `sandbox.<verb>` wire spelling.
    pub name: &'static str,
    /// Whether the op changes fleet state.
    pub mutates_state: bool,
    /// One-line summary rendered into `contract/ops.json`.
    pub summary: &'static str,
}

/// Host-served sandbox lifecycle ops.
pub const HOST_OP_SPECS: &[HostOpSpec] = &[
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
    aliases: &'static [&'static str],
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
        aliases: &[],
        served_by: "host",
        visibility: OpVisibility::Public.as_str(),
        family: OpFamily::Sandbox.as_str(),
        mutates_state: spec.mutates_state,
        summary: spec.summary,
    });
    let daemon = BUILTIN_DAEMON_OP_SPECS.iter().map(|spec| CatalogOp {
        name: spec.name,
        aliases: spec.aliases,
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
mod tests {
    use std::collections::BTreeSet;

    use super::*;

    #[test]
    fn builtin_specs_match_legacy_wire_list() {
        let catalog_wires = BUILTIN_DAEMON_OP_SPECS
            .iter()
            .map(|spec| spec.aliases[0])
            .collect::<Vec<_>>();
        assert_eq!(catalog_wires, BUILTIN_DAEMON_OPS);
    }

    #[test]
    fn builtin_specs_are_returned_by_ops() {
        for spec in BUILTIN_DAEMON_OP_SPECS {
            assert_eq!(*spec, spec.op.spec());
        }
    }

    #[test]
    fn canonical_names_follow_grammar() {
        for spec in BUILTIN_DAEMON_OP_SPECS {
            assert!(
                spec.name.starts_with("sandbox."),
                "daemon op {} must use the sandbox.* grammar",
                spec.name
            );
            assert!(
                !spec.name.split('.').any(|token| token == "v1"),
                "the v1 token is dead in canonical names: {}",
                spec.name
            );
        }
        for spec in HOST_OP_SPECS {
            assert!(
                spec.name.starts_with("sandbox.") && spec.name.split('.').count() == 2,
                "host op {} must be sandbox.<verb>",
                spec.name
            );
        }
    }

    #[test]
    fn no_spelling_is_claimed_twice() {
        let mut spellings = BTreeSet::new();
        let all_names = HOST_OP_SPECS
            .iter()
            .map(|spec| spec.name)
            .chain(BUILTIN_DAEMON_OP_SPECS.iter().map(|spec| spec.name))
            .chain(
                BUILTIN_DAEMON_OP_SPECS
                    .iter()
                    .flat_map(|spec| spec.aliases.iter().copied()),
            );
        for spelling in all_names {
            assert!(
                spellings.insert(spelling),
                "spelling claimed twice in the catalog: {spelling}"
            );
        }
    }

    #[test]
    fn resolve_accepts_both_spellings() {
        for spec in BUILTIN_DAEMON_OP_SPECS {
            assert_eq!(BuiltinDaemonOp::resolve(spec.name), Some(spec.op));
            for alias in spec.aliases {
                assert_eq!(BuiltinDaemonOp::resolve(alias), Some(spec.op));
            }
        }
        assert_eq!(BuiltinDaemonOp::resolve("api.totally.bogus.op"), None);
    }

    #[test]
    fn fixture_pinned_aliases_are_present() {
        // Pinned by immutable golden fixtures; these aliases are never removed.
        assert_eq!(
            BuiltinDaemonOp::ReadFile.aliases(),
            ["api.v1.read_file"],
            "sandbox.file.read must keep its fixture-pinned alias"
        );
        assert_eq!(
            BuiltinDaemonOp::InvocationHeartbeat.aliases(),
            ["api.v1.heartbeat"],
            "sandbox.call.heartbeat must keep its fixture-pinned alias"
        );
    }

    #[test]
    fn ops_json_document_is_complete_and_stable() {
        let document = ops_json_document();
        let parsed: serde_json::Value =
            serde_json::from_str(&document).expect("document parses back");
        assert_eq!(parsed["protocol_version"], DAEMON_PROTOCOL_VERSION);
        let ops = parsed["ops"].as_array().expect("ops array");
        assert_eq!(
            ops.len(),
            HOST_OP_SPECS.len() + BUILTIN_DAEMON_OP_SPECS.len()
        );
        assert!(document.ends_with('\n'));
    }
}
