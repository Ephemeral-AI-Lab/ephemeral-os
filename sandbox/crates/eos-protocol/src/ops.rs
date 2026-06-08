//! Daemon operation names owned by the protocol crate.
//!
//! The live `eosd` dispatcher registers these exact strings, and protocol
//! clients should import them from here instead of duplicating string literals.

/// Functional owner for a built-in daemon op.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum OpFamily {
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

macro_rules! declare_builtin_daemon_ops {
    (
        $(
            $(#[$variant_meta:meta])*
            $variant:ident, $const_name:ident, $wire:literal, $family:ident,
            $mutates_state:literal, $test_only:literal, [
                $(#[$const_meta:meta])*
            ];
        )+
    ) => {
        /// One built-in daemon operation in the wire protocol catalog.
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
        #[non_exhaustive]
        pub enum BuiltinDaemonOp {
            $(
                $(#[$variant_meta])*
                $variant,
            )+
        }

        /// Protocol metadata for one built-in daemon op.
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
        pub struct BuiltinOpSpec {
            /// Typed op identity.
            pub op: BuiltinDaemonOp,
            /// Verbatim wire string.
            pub wire: &'static str,
            /// Functional owner.
            pub family: OpFamily,
            /// Whether the op may change daemon, workspace, or process state.
            pub mutates_state: bool,
            /// Whether the op is a daemon-side test hook.
            pub test_only: bool,
        }

        impl BuiltinOpSpec {
            const fn new(
                op: BuiltinDaemonOp,
                wire: &'static str,
                family: OpFamily,
                mutates_state: bool,
                test_only: bool,
            ) -> Self {
                Self {
                    op,
                    wire,
                    family,
                    mutates_state,
                    test_only,
                }
            }
        }

        impl BuiltinDaemonOp {
            /// Verbatim wire string for this op.
            #[must_use]
            pub const fn wire(self) -> &'static str {
                self.spec().wire
            }

            /// Functional owner for this op.
            #[must_use]
            pub const fn family(self) -> OpFamily {
                self.spec().family
            }

            /// Whether this op may change daemon, workspace, or process state.
            #[must_use]
            pub const fn mutates_state(self) -> bool {
                self.spec().mutates_state
            }

            /// Whether this op is a daemon-side test hook.
            #[must_use]
            pub const fn test_only(self) -> bool {
                self.spec().test_only
            }

            /// Protocol catalog entry for this op.
            #[must_use]
            pub const fn spec(self) -> BuiltinOpSpec {
                match self {
                    $(
                        Self::$variant => BuiltinOpSpec::new(
                            Self::$variant,
                            $wire,
                            OpFamily::$family,
                            $mutates_state,
                            $test_only,
                        ),
                    )+
                }
            }
        }

        $(
            $(#[$const_meta])*
            pub const $const_name: &str = BuiltinDaemonOp::$variant.wire();
        )+

        /// Built-in daemon op metadata expected to be available over the wire.
        pub const BUILTIN_DAEMON_OP_SPECS: &[BuiltinOpSpec] = &[
            $(
                BuiltinDaemonOp::$variant.spec(),
            )+
        ];

        /// Built-in daemon ops expected to be available over the wire.
        pub const BUILTIN_DAEMON_OPS: &[&str] = &[
            $(
                $const_name,
            )+
        ];
    };
}

declare_builtin_daemon_ops! {
    /// `api.runtime.ready`
    RuntimeReady, API_RUNTIME_READY, "api.runtime.ready", Control, false, false, [
        /// Runtime readiness probe.
    ];
    /// `api.v1.heartbeat`
    InvocationHeartbeat, API_V1_HEARTBEAT, "api.v1.heartbeat", Control, true, false, [
        /// Invocation heartbeat.
    ];
    /// `api.v1.cancel`
    InvocationCancel, API_V1_CANCEL, "api.v1.cancel", Control, true, false, [
        /// Cancel an in-flight invocation.
    ];
    /// `api.v1.inflight_count`
    InflightCount, API_V1_INFLIGHT_COUNT, "api.v1.inflight_count", Control, false, false, [
        /// Count in-flight invocations.
    ];
    /// `api.layer_metrics`
    LayerMetrics, API_LAYER_METRICS, "api.layer_metrics", Checkpoint, false, false, [
        /// LayerStack/storage metrics.
    ];
    /// `api.ensure_workspace_base`
    EnsureWorkspaceBase, API_ENSURE_WORKSPACE_BASE, "api.ensure_workspace_base", Checkpoint, true, false, [
        /// Ensure a workspace base binding exists.
    ];
    /// `api.build_workspace_base`
    BuildWorkspaceBase, API_BUILD_WORKSPACE_BASE, "api.build_workspace_base", Checkpoint, true, false, [
        /// Build or rebuild a workspace base binding.
    ];
    /// `api.commit_to_workspace`
    CommitToWorkspace, API_COMMIT_TO_WORKSPACE, "api.commit_to_workspace", Checkpoint, true, false, [
        /// Materialize LayerStack state into the bound workspace.
    ];
    /// `api.commit_to_git`
    CommitToGit, API_COMMIT_TO_GIT, "api.commit_to_git", Checkpoint, true, false, [
        /// Commit a LayerStack snapshot into the bound workspace's durable Git repo.
    ];
    /// `api.workspace_binding`
    WorkspaceBinding, API_WORKSPACE_BINDING, "api.workspace_binding", Checkpoint, false, false, [
        /// Inspect the workspace binding for a layer stack root.
    ];
    /// `api.audit.pull`
    AuditPull, API_AUDIT_PULL, "api.audit.pull", Audit, false, false, [
        /// Pull audit events after a cursor.
    ];
    /// `api.audit.snapshot`
    AuditSnapshot, API_AUDIT_SNAPSHOT, "api.audit.snapshot", Audit, false, false, [
        /// Snapshot audit ring metadata.
    ];
    /// `api.audit.reset_floor`
    AuditResetFloor, API_AUDIT_RESET_FLOOR, "api.audit.reset_floor", Audit, true, false, [
        /// Reset the audit floor when daemon-side test gate allows it.
    ];
    /// `api.v1.read_file`
    ReadFile, API_V1_READ_FILE, "api.v1.read_file", Files, false, false, [
        /// Direct LayerStack read.
    ];
    /// `api.v1.write_file`
    WriteFile, API_V1_WRITE_FILE, "api.v1.write_file", Files, true, false, [
        /// Direct OCC-gated write.
    ];
    /// `api.v1.edit_file`
    EditFile, API_V1_EDIT_FILE, "api.v1.edit_file", Files, true, false, [
        /// Direct OCC-gated edit.
    ];
    /// `api.plugin.ensure`
    PluginEnsure, API_PLUGIN_ENSURE, "api.plugin.ensure", Plugins, true, false, [
        /// Ensure a plugin service is available.
    ];
    /// `api.plugin.status`
    PluginStatus, API_PLUGIN_STATUS, "api.plugin.status", Plugins, false, false, [
        /// Inspect plugin service status.
    ];
    /// `api.isolated_workspace.enter`
    IsolatedWorkspaceEnter, API_ISOLATED_WORKSPACE_ENTER, "api.isolated_workspace.enter", IsolatedWorkspace, true, false, [
        /// Enter isolated workspace mode.
    ];
    /// `api.isolated_workspace.exit`
    IsolatedWorkspaceExit, API_ISOLATED_WORKSPACE_EXIT, "api.isolated_workspace.exit", IsolatedWorkspace, true, false, [
        /// Exit isolated workspace mode.
    ];
    /// `api.isolated_workspace.status`
    IsolatedWorkspaceStatus, API_ISOLATED_WORKSPACE_STATUS, "api.isolated_workspace.status", IsolatedWorkspace, false, false, [
        /// Inspect isolated workspace status.
    ];
    /// `api.isolated_workspace.list_open`
    IsolatedWorkspaceListOpen, API_ISOLATED_WORKSPACE_LIST_OPEN, "api.isolated_workspace.list_open", IsolatedWorkspace, false, false, [
        /// List open isolated workspaces.
    ];
    /// `api.isolated_workspace.test_reset`
    IsolatedWorkspaceTestReset, API_ISOLATED_WORKSPACE_TEST_RESET, "api.isolated_workspace.test_reset", IsolatedWorkspace, true, true, [
        /// Test-only isolated workspace reset hook.
    ];
    /// `api.v1.exec_command`
    ExecCommand, API_V1_EXEC_COMMAND, "api.v1.exec_command", CommandSession, true, false, [
        /// Start or poll a command session.
    ];
    /// `api.v1.write_stdin`
    WriteStdin, API_V1_WRITE_STDIN, "api.v1.write_stdin", CommandSession, true, false, [
        /// Write stdin to a command session.
    ];
    /// `api.v1.command.read_progress`
    CommandReadProgress, API_V1_COMMAND_READ_PROGRESS, "api.v1.command.read_progress", CommandSession, false, false, [
        /// Read command-session progress without writing stdin.
    ];
    /// `api.v1.command.cancel`
    CommandCancel, API_V1_COMMAND_CANCEL, "api.v1.command.cancel", CommandSession, true, false, [
        /// Cancel a command session.
    ];
    /// `api.v1.command.collect_completed`
    CommandCollectCompleted, API_V1_COMMAND_COLLECT_COMPLETED, "api.v1.command.collect_completed", CommandSession, true, false, [
        /// Collect completed command-session notifications.
    ];
    /// `api.v1.command_session_count`
    CommandSessionCount, API_V1_COMMAND_SESSION_COUNT, "api.v1.command_session_count", CommandSession, false, false, [
        /// Count live command sessions.
    ];
    /// `api.v1.cancel_workspace_runs_by_caller_id`
    CancelWorkspaceRunsByCaller, API_V1_CANCEL_WORKSPACE_RUNS_BY_CALLER, "api.v1.cancel_workspace_runs_by_caller_id", WorkspaceRun, true, false, [
        /// Cancel every workspace run owned by one caller (`caller_id == agent_run_id`):
        /// discards the caller's command session(s) and exits its isolated workspace if
        /// open. The agent-core per-run cancellation RPC.
    ];
    /// `api.v1.cancel_workspace_runs`
    CancelWorkspaceRuns, API_V1_CANCEL_WORKSPACE_RUNS, "api.v1.cancel_workspace_runs", WorkspaceRun, true, false, [
        /// Cancel every workspace run in the sandbox (whole-sandbox sweep backstop):
        /// discards all command sessions, exits all isolated callers, reaps orphans.
    ];
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use super::*;

    #[test]
    fn builtin_specs_match_wire_list() {
        let catalog_wires = BUILTIN_DAEMON_OP_SPECS
            .iter()
            .map(|spec| spec.wire)
            .collect::<Vec<_>>();
        assert_eq!(catalog_wires, BUILTIN_DAEMON_OPS);
    }

    #[test]
    fn builtin_specs_have_no_duplicate_ops_or_wires() {
        let unique_ops = BUILTIN_DAEMON_OP_SPECS
            .iter()
            .map(|spec| spec.op)
            .collect::<BTreeSet<_>>();
        let unique_wires = BUILTIN_DAEMON_OP_SPECS
            .iter()
            .map(|spec| spec.wire)
            .collect::<BTreeSet<_>>();
        assert_eq!(unique_ops.len(), BUILTIN_DAEMON_OP_SPECS.len());
        assert_eq!(unique_wires.len(), BUILTIN_DAEMON_OP_SPECS.len());
    }

    #[test]
    fn builtin_specs_are_returned_by_ops() {
        for spec in BUILTIN_DAEMON_OP_SPECS {
            assert_eq!(*spec, spec.op.spec());
        }
    }
}
