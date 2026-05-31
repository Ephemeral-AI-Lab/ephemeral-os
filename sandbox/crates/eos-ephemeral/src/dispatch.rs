//! Verb routing: the fast-path vs overlay-path split.
//!
//! INVARIANT (the one this crate owns): `read_file`/`write_file`/`edit_file` are
//! the OCC fast path — they go DIRECTLY to the layer stack / OCC writer, never
//! mounting an overlay or entering a namespace. `shell`/`glob`/`grep` go through
//! the overlay pipeline (mount -> `run_in_namespace` via eos-runner -> capture),
//! and only `WRITE_ALLOWED` ops publish; `glob`/`grep` are `READ_ONLY` so they
//! mount but skip the capture+publish step.
//!
//! This consolidates what the Python splits across the daemon dispatcher
//! (`workspace_tool/dispatch.py`, the fast path) and `EphemeralPipeline`
//! (`pipeline.py`, the overlay path). The per-verb `args`/result models come from
//! `eos_protocol`; the identity (`agent_id`/`invocation_id`) is the thin local
//! request envelope below (protocol deliberately injects identity at the wire
//! `Request` layer, so there is no unified `ToolCallRequest` to reuse).

use eos_protocol::{
    EditFileArgs, GlobArgs, GrepArgs, Intent, ReadFileArgs, ShellArgs, WriteFileArgs,
};

/// The verb being dispatched, carrying its protocol-typed `args`.
///
/// `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:40 — _LAYER_STACK_FILE_VERBS`
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verb {
    /// `read_file` — OCC fast path (direct layer-stack read).
    ReadFile(ReadFileArgs),
    /// `write_file` — OCC fast path (direct changeset apply).
    WriteFile(WriteFileArgs),
    /// `edit_file` — OCC fast path (direct changeset apply).
    EditFile(EditFileArgs),
    /// `shell` — overlay path (mount + namespace + capture/publish).
    Shell(ShellArgs),
    /// `glob` — overlay path, READ_ONLY (mount, no publish).
    Glob(GlobArgs),
    /// `grep` — overlay path, READ_ONLY (mount, no publish).
    Grep(GrepArgs),
}

/// Which execution path a verb routes to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DispatchRoute {
    /// `read_file`/`write_file`/`edit_file` -> direct layer-stack / OCC.
    OccFastPath,
    /// `shell`/`glob`/`grep` -> overlay mount + namespace runner.
    OverlayPipeline,
}

impl Verb {
    /// The wire verb string (matches the Python verb names exactly).
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:469-477 — _api_total_timing_key`
    pub fn name(&self) -> &'static str {
        match self {
            Verb::ReadFile(_) => "read_file",
            Verb::WriteFile(_) => "write_file",
            Verb::EditFile(_) => "edit_file",
            Verb::Shell(_) => "shell",
            Verb::Glob(_) => "glob",
            Verb::Grep(_) => "grep",
        }
    }

    /// Static route for this verb — the load-bearing fast-path/overlay split.
    /// `// PORT backend/src/sandbox/daemon/workspace_tool/dispatch.py:239-257 — verb routing branch`
    pub fn route(&self) -> DispatchRoute {
        match self {
            Verb::ReadFile(_) | Verb::WriteFile(_) | Verb::EditFile(_) => {
                DispatchRoute::OccFastPath
            }
            Verb::Shell(_) | Verb::Glob(_) | Verb::Grep(_) => DispatchRoute::OverlayPipeline,
        }
    }

    /// The verb's intent. Reads/glob/grep are `READ_ONLY`; write/edit are
    /// `WRITE_ALLOWED`; `shell` is `WRITE_ALLOWED` (it may mutate the workspace).
    /// `// PORT backend/src/sandbox/ephemeral_workspace/pipeline.py:147 — req.intent == WRITE_ALLOWED`
    pub fn intent(&self) -> Intent {
        match self {
            Verb::WriteFile(_) | Verb::EditFile(_) | Verb::Shell(_) => Intent::WriteAllowed,
            Verb::ReadFile(_) | Verb::Glob(_) | Verb::Grep(_) => Intent::ReadOnly,
        }
    }
}

/// The thin local request envelope: identity + verb. Faithful to the Python
/// `ToolCallRequest` shape, but kept local because protocol injects identity at
/// the wire `Request` layer rather than minting a unified request type.
/// `// PORT backend/src/sandbox/shared/models.py — ToolCallRequest`
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OperationRequest {
    /// Dispatching agent (defaults to `"default"` at the daemon boundary).
    pub agent_id: String,
    /// Per-call invocation id (uuid hex when the caller omits it).
    pub invocation_id: String,
    /// The bound layer stack root this op targets.
    pub layer_stack_root: String,
    /// The verb + its protocol-typed args.
    pub verb: Verb,
    /// Whether the call runs as a background tool dispatch.
    pub background: bool,
}

impl OperationRequest {
    /// Route this request to its execution path.
    pub fn route(&self) -> DispatchRoute {
        self.verb.route()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn read() -> Verb {
        Verb::ReadFile(ReadFileArgs { path: "a".into() })
    }
    fn write() -> Verb {
        Verb::WriteFile(WriteFileArgs {
            path: "a".into(),
            content: String::new(),
            overwrite: true,
        })
    }
    fn glob() -> Verb {
        Verb::Glob(GlobArgs {
            pattern: "*".into(),
            path: None,
        })
    }

    #[test]
    fn read_write_edit_take_the_occ_fast_path() {
        assert_eq!(read().route(), DispatchRoute::OccFastPath);
        assert_eq!(write().route(), DispatchRoute::OccFastPath);
    }

    #[test]
    fn shell_glob_grep_take_the_overlay_pipeline() {
        assert_eq!(glob().route(), DispatchRoute::OverlayPipeline);
        let shell = Verb::Shell(ShellArgs {
            command: "true".into(),
            cwd: ".".into(),
            timeout_seconds: None,
            background: false,
        });
        assert_eq!(shell.route(), DispatchRoute::OverlayPipeline);
    }

    #[test]
    fn read_glob_grep_are_read_only_write_edit_shell_are_write_allowed() {
        assert_eq!(read().intent(), Intent::ReadOnly);
        assert_eq!(glob().intent(), Intent::ReadOnly);
        assert_eq!(write().intent(), Intent::WriteAllowed);
    }

    #[test]
    fn verb_names_match_the_python_wire_verbs() {
        assert_eq!(read().name(), "read_file");
        assert_eq!(write().name(), "write_file");
        assert_eq!(glob().name(), "glob");
    }
}
