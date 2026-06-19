use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::core::request::{
    optional_bool, optional_path, optional_u64, require_command_string, require_nonempty_string,
    ArgProblem, ArgsError,
};
use crate::{CallerId, CommandId, InvocationId, MutationCore, WorkspaceKind};

pub const MAX_PROGRESS_LINES: u64 = 1_000;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ExecCommandInput {
    pub cmd: String,
    pub caller: CallerId,
    pub layer_stack_root: Option<PathBuf>,
    /// Wire alias `timeout` | `timeout_seconds`, resolved here; `timeout` wins.
    pub timeout: Option<u64>,
    pub yield_time_ms: Option<u64>,
    pub cwd: Option<PathBuf>,
    pub remountable: bool,
    pub invocation_id: InvocationId,
}

impl ExecCommandInput {
    pub(crate) fn parse(args: &Value, invocation_id: &str) -> Result<Self, ArgsError> {
        Ok(Self {
            cmd: require_command_string(args, "cmd")?,
            caller: CallerId::from_wire(args),
            layer_stack_root: optional_path(args, "layer_stack_root"),
            timeout: optional_u64(args, "timeout")
                .or_else(|| optional_u64(args, "timeout_seconds")),
            yield_time_ms: optional_u64(args, "yield_time_ms"),
            cwd: optional_path(args, "cwd"),
            remountable: optional_bool(args, "remountable").unwrap_or(false),
            invocation_id: InvocationId::new(invocation_id.to_owned()),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WriteStdinInput {
    pub command_id: CommandId,
    pub chars: String,
    pub yield_time_ms: Option<u64>,
}

impl WriteStdinInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            command_id: CommandId::new(require_command_string(args, "command_id")?),
            chars: require_nonempty_string(args, "chars")?,
            yield_time_ms: optional_u64(args, "yield_time_ms"),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReadProgressInput {
    pub command_id: CommandId,
    pub last_n_lines: usize,
}

impl ReadProgressInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        let command_id = CommandId::new(require_command_string(args, "command_id")?);
        let last_n_lines = optional_u64(args, "last_n_lines").unwrap_or(50);
        if last_n_lines > MAX_PROGRESS_LINES {
            return Err(ArgsError {
                key: "last_n_lines",
                problem: ArgProblem::Invalid(format!(
                    "last_n_lines must be <= {MAX_PROGRESS_LINES}"
                )),
            });
        }
        Ok(Self {
            command_id,
            last_n_lines: last_n_lines.try_into().map_err(|_| ArgsError {
                key: "last_n_lines",
                problem: ArgProblem::Invalid("last_n_lines is too large".to_owned()),
            })?,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CancelCommandInput {
    pub command_id: CommandId,
}

impl CancelCommandInput {
    pub(crate) fn parse(args: &Value) -> Result<Self, ArgsError> {
        Ok(Self {
            command_id: CommandId::new(require_command_string(args, "command_id")?),
        })
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CollectCompletedInput {
    pub command_ids: Option<Vec<CommandId>>,
    pub caller: Option<CallerId>,
}

impl CollectCompletedInput {
    pub(crate) fn parse(args: &Value) -> Self {
        let command_ids = args
            .get("command_ids")
            .and_then(Value::as_array)
            .map(|ids| {
                ids.iter()
                    .filter_map(Value::as_str)
                    .map(CommandId::new)
                    .collect::<Vec<_>>()
            });
        // Optional caller filter: absent means "collect across all callers",
        // so no default-caller fallback applies here.
        let caller = args
            .get("caller_id")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|caller| !caller.is_empty())
            .map(CallerId::new);
        Self {
            command_ids,
            caller,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CommandStatus {
    Running,
    Ok,
    Cancelled,
    Error,
    TimedOut,
}

impl CommandStatus {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Running => "running",
            Self::Ok => "ok",
            Self::Cancelled => "cancelled",
            Self::Error => "error",
            Self::TimedOut => "timed_out",
        }
    }

    #[must_use]
    pub fn from_wire_str(raw: &str) -> Option<Self> {
        match raw {
            "running" => Some(Self::Running),
            "ok" => Some(Self::Ok),
            "cancelled" => Some(Self::Cancelled),
            "error" => Some(Self::Error),
            "timed_out" => Some(Self::TimedOut),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommandMetadata {
    #[serde(flatten)]
    pub core: MutationCore,
    pub workspace: WorkspaceKind,
    #[serde(flatten)]
    pub extras: Map<String, Value>,
}

pub(crate) const PUBLISH_LANES_METADATA_KEY: &str = "publish_lanes";
pub(crate) const PUBLISH_REJECTION_DETAILS_METADATA_KEY: &str = "publish_rejection_details";

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct PublishLanesMetadata {
    pub source: SourcePublishLaneMetadata,
    pub ignored: IgnoredPublishLaneMetadata,
    pub routing: PublishLaneRoutingMetadata,
}

impl PublishLanesMetadata {
    pub(crate) fn new(
        source: SourcePublishLaneMetadata,
        ignored: IgnoredPublishLaneMetadata,
        route_manifest_version: i64,
    ) -> Self {
        Self {
            source,
            ignored,
            routing: PublishLaneRoutingMetadata {
                ignore_route_source: "command_snapshot".to_owned(),
                route_manifest_version,
                dropped_path_count: 0,
                drop_reason_counts: BTreeMap::new(),
            },
        }
    }

    pub(crate) fn empty(route_manifest_version: i64) -> Self {
        Self::new(
            SourcePublishLaneMetadata::new(0, "empty", None::<String>),
            IgnoredPublishLaneMetadata::new(0, 0, 0, "empty", None::<String>, None::<String>),
            route_manifest_version,
        )
    }

    pub(crate) fn dropped_command_failed(route_manifest_version: i64) -> Self {
        Self::new(
            SourcePublishLaneMetadata::new(0, "dropped_command_failed", None::<String>),
            IgnoredPublishLaneMetadata::new(
                0,
                0,
                0,
                "dropped_command_failed",
                None::<String>,
                None::<String>,
            ),
            route_manifest_version,
        )
    }

    pub(crate) fn insert_into(self, extras: &mut Map<String, Value>) {
        extras.insert(PUBLISH_LANES_METADATA_KEY.to_owned(), self.to_value());
    }

    #[must_use]
    pub(crate) fn to_value(&self) -> Value {
        serde_json::to_value(self).expect("serialize publish lanes metadata")
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct SourcePublishLaneMetadata {
    pub path_count: usize,
    pub publish_status: String,
    pub drop_reason: Option<String>,
}

impl SourcePublishLaneMetadata {
    pub(crate) fn new(
        path_count: usize,
        publish_status: impl Into<String>,
        drop_reason: Option<impl Into<String>>,
    ) -> Self {
        Self {
            path_count,
            publish_status: publish_status.into(),
            drop_reason: drop_reason.map(Into::into),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct IgnoredPublishLaneMetadata {
    pub path_count: usize,
    pub bytes: u64,
    pub spooled_bytes: u64,
    pub publish_status: String,
    pub publish_mode: Option<String>,
    pub drop_reason: Option<String>,
}

impl IgnoredPublishLaneMetadata {
    pub(crate) fn new(
        path_count: usize,
        bytes: u64,
        spooled_bytes: u64,
        publish_status: impl Into<String>,
        publish_mode: Option<impl Into<String>>,
        drop_reason: Option<impl Into<String>>,
    ) -> Self {
        Self {
            path_count,
            bytes,
            spooled_bytes,
            publish_status: publish_status.into(),
            publish_mode: publish_mode.map(Into::into),
            drop_reason: drop_reason.map(Into::into),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub(crate) struct PublishLaneRoutingMetadata {
    pub ignore_route_source: String,
    pub route_manifest_version: i64,
    pub dropped_path_count: usize,
    pub drop_reason_counts: BTreeMap<String, usize>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommandResponse {
    pub status: CommandStatus,
    pub exit_code: Option<i64>,
    pub stdout: String,
    pub stderr: String,
    pub command_id: Option<CommandId>,
    pub finalized: Option<CommandMetadata>,
}

impl CommandResponse {
    #[must_use]
    pub fn running(command_id: String, stdout: String) -> Self {
        Self {
            status: CommandStatus::Running,
            exit_code: None,
            stdout,
            stderr: String::new(),
            command_id: Some(CommandId::new(command_id)),
            finalized: None,
        }
    }

    #[must_use]
    pub fn cancelled(stdout: String) -> Self {
        Self {
            status: CommandStatus::Cancelled,
            exit_code: None,
            stdout,
            stderr: String::new(),
            command_id: None,
            finalized: None,
        }
    }

    #[must_use]
    pub fn error(stderr: impl Into<String>) -> Self {
        Self {
            status: CommandStatus::Error,
            exit_code: None,
            stdout: String::new(),
            stderr: stderr.into(),
            command_id: None,
            finalized: None,
        }
    }

    #[must_use]
    pub fn with_last_lines(mut self, last_n_lines: usize) -> Self {
        self.stdout = command::tail_lines(&self.stdout, last_n_lines);
        self
    }

    #[must_use]
    pub fn to_wire_value(&self) -> Value {
        let mut response = json!({
            "status": self.status.as_str(),
            "exit_code": self.exit_code,
            "output": {
                "stdout": self.stdout,
                "stderr": self.stderr,
            },
        });
        if let Some(command_id) = self.command_id.as_ref() {
            response["command_id"] = json!(command_id.as_str());
        }
        let Some(finalized) = self.finalized.as_ref() else {
            return response;
        };
        let Value::Object(core) =
            serde_json::to_value(&finalized.core).expect("serialize command mutation core")
        else {
            unreachable!("MutationCore serializes to a JSON object");
        };
        let object = response
            .as_object_mut()
            .expect("command response starts as a JSON object");
        for (key, value) in core {
            if key == "timings" {
                continue;
            }
            object.insert(key, value);
        }
        object.insert("workspace".to_owned(), json!(finalized.workspace.as_str()));
        for (key, value) in &finalized.extras {
            object.insert(key.clone(), value.clone());
        }
        response
    }
}

pub(crate) fn u64_to_f64_saturating(value: u64) -> f64 {
    const U32_FACTOR: f64 = 4_294_967_296.0;
    let high = u32::try_from(value >> 32).unwrap_or(u32::MAX);
    let low = u32::try_from(value & u64::from(u32::MAX)).unwrap_or(u32::MAX);
    f64::from(high).mul_add(U32_FACTOR, f64::from(low))
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommandCompletion {
    pub command_id: String,
    pub caller_id: String,
    pub command: String,
    pub result: CommandResponse,
}

impl CommandCompletion {
    #[must_use]
    pub fn to_wire_value(&self) -> Value {
        json!({
            "command_id": self.command_id,
            "caller_id": self.caller_id,
            "command": self.command,
            "result": self.result.to_wire_value(),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CollectCompletedOutput {
    pub success: bool,
    pub completions: Vec<CommandCompletion>,
    pub has_more: bool,
    pub max_completions: usize,
}

impl CollectCompletedOutput {
    #[must_use]
    pub fn to_wire_value(&self) -> Value {
        json!({
            "success": self.success,
            "has_more": self.has_more,
            "max_completions": self.max_completions,
            "completions": self.completions
                .iter()
                .map(CommandCompletion::to_wire_value)
                .collect::<Vec<_>>(),
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CommandCountOutput {
    pub success: bool,
    pub caller_id: String,
    pub count: usize,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ChangedPathKind, MutationSource, WorkspaceConflict};

    #[test]
    fn read_progress_rejects_unbounded_line_counts() {
        let parsed = ReadProgressInput::parse(&json!({
            "command_id": "cmd-1",
            "last_n_lines": MAX_PROGRESS_LINES,
        }))
        .expect("max line count accepted");
        assert_eq!(parsed.last_n_lines, MAX_PROGRESS_LINES as usize);

        let err = ReadProgressInput::parse(&json!({
            "command_id": "cmd-1",
            "last_n_lines": MAX_PROGRESS_LINES + 1,
        }))
        .expect_err("over cap rejected");
        assert_eq!(err.key, "last_n_lines");
        assert!(err.message().contains("must be <="));
    }

    #[test]
    fn exec_parse_accepts_remountable_cwd_fields() {
        let parsed = ExecCommandInput::parse(
            &json!({
                "cmd": "sleep 30",
                "caller_id": "caller",
                "cwd": "/tmp",
                "remountable": true,
                "yield_time_ms": 250,
            }),
            "invoke-remountable",
        )
        .expect("parse exec");

        assert_eq!(parsed.cwd.as_deref(), Some(std::path::Path::new("/tmp")));
        assert!(parsed.remountable);
        assert_eq!(parsed.yield_time_ms, Some(250));
    }

    #[test]
    fn finalized_response_splices_typed_metadata_extras() {
        let mut changed_path_kinds = crate::ChangedPathKinds::default();
        changed_path_kinds.insert("src/main.rs".to_owned(), ChangedPathKind::Write);
        let mut extras = Map::new();
        extras.insert(
            "isolated_network".to_owned(),
            json!({"caller_id": "caller", "published": false}),
        );
        PublishLanesMetadata::new(
            SourcePublishLaneMetadata::new(1, "committed", None::<String>),
            IgnoredPublishLaneMetadata::new(0, 0, 0, "empty", None::<String>, None::<String>),
            1,
        )
        .insert_into(&mut extras);
        extras.insert("warnings".to_owned(), json!([]));

        let response = CommandResponse {
            status: CommandStatus::Ok,
            exit_code: Some(0),
            stdout: "done\n".to_owned(),
            stderr: String::new(),
            command_id: Some(CommandId::new("cmd_1")),
            finalized: Some(CommandMetadata {
                core: MutationCore {
                    success: true,
                    changed_paths: vec!["src/main.rs".to_owned()],
                    changed_path_kinds,
                    mutation_source: Some(MutationSource::IsolatedNetwork),
                    conflict: None,
                    conflict_reason: None,
                    timings: crate::WorkspaceTimings::default(),
                },
                workspace: WorkspaceKind::IsolatedNetwork,
                extras,
            }),
        }
        .to_wire_value();

        assert_eq!(response["status"], "ok");
        assert_eq!(response["command_id"], "cmd_1");
        assert_eq!(response["workspace"], "isolated_network");
        assert_eq!(response["success"], true);
        assert!(response.get("timings").is_none());
        assert_eq!(response["changed_paths"], json!(["src/main.rs"]));
        assert_eq!(
            response["changed_path_kinds"],
            json!({"src/main.rs": "write"})
        );
        assert_eq!(response["mutation_source"], "isolated_network");
        assert_eq!(response["isolated_network"]["caller_id"], "caller");
        assert_eq!(
            response[PUBLISH_LANES_METADATA_KEY],
            json!({
                "source": {
                    "path_count": 1,
                    "publish_status": "committed",
                    "drop_reason": null,
                },
                "ignored": {
                    "path_count": 0,
                    "bytes": 0,
                    "spooled_bytes": 0,
                    "publish_status": "empty",
                    "publish_mode": null,
                    "drop_reason": null,
                },
                "routing": {
                    "ignore_route_source": "command_snapshot",
                    "route_manifest_version": 1,
                    "dropped_path_count": 0,
                    "drop_reason_counts": {},
                },
            })
        );
        assert_eq!(response["warnings"], json!([]));
    }

    #[test]
    fn discarded_response_omits_mutation_source() {
        let response = CommandResponse {
            status: CommandStatus::Cancelled,
            exit_code: Some(130),
            stdout: String::new(),
            stderr: String::new(),
            command_id: None,
            finalized: Some(CommandMetadata {
                core: MutationCore {
                    success: false,
                    mutation_source: None,
                    ..MutationCore::default()
                },
                workspace: WorkspaceKind::Host,
                extras: {
                    let mut extras = Map::new();
                    extras.insert(
                        "publish_lanes".to_owned(),
                        json!({
                            "source": {
                                "path_count": 0,
                                "publish_status": "dropped_command_failed",
                                "drop_reason": null,
                            },
                            "ignored": {
                                "path_count": 0,
                                "bytes": 0,
                                "spooled_bytes": 0,
                                "publish_status": "dropped_command_failed",
                                "publish_mode": null,
                                "drop_reason": null,
                            },
                            "routing": {
                                "ignore_route_source": "command_snapshot",
                                "route_manifest_version": 0,
                                "dropped_path_count": 0,
                                "drop_reason_counts": {},
                            },
                        }),
                    );
                    extras
                },
            }),
        }
        .to_wire_value();

        assert_eq!(response["status"], "cancelled");
        assert_eq!(response["workspace"], "host");
        assert!(response.get("mutation_source").is_none());
        assert!(response.get("timings").is_none());
        assert_eq!(
            response["publish_lanes"]["source"]["publish_status"],
            "dropped_command_failed"
        );
        assert_eq!(
            response["publish_lanes"]["ignored"]["publish_status"],
            "dropped_command_failed"
        );
    }

    #[test]
    fn conflict_finalization_matches_contract_fixture() {
        let mut changed_path_kinds = crate::ChangedPathKinds::default();
        changed_path_kinds.insert("src/main.rs".to_owned(), ChangedPathKind::Write);
        let mut timings = crate::WorkspaceTimings::default();
        timings.insert("command_exec.total_s".to_owned(), json!(1.25));

        let response = CommandResponse {
            status: CommandStatus::Ok,
            exit_code: Some(0),
            stdout: String::new(),
            stderr: String::new(),
            command_id: Some(CommandId::new("cmd_conflict")),
            finalized: Some(CommandMetadata {
                core: MutationCore {
                    success: false,
                    changed_paths: vec!["src/main.rs".to_owned()],
                    changed_path_kinds,
                    mutation_source: Some(MutationSource::OverlayCapture),
                    conflict: Some(WorkspaceConflict::path(
                        "aborted_overlap",
                        "src/main.rs",
                        "conflict on src/main.rs",
                    )),
                    conflict_reason: Some("conflict on src/main.rs".to_owned()),
                    timings,
                },
                workspace: WorkspaceKind::Host,
                extras: {
                    let mut extras = Map::new();
                    extras.insert(
                        "publish_lanes".to_owned(),
                        json!({
                            "source": {
                                "path_count": 1,
                                "publish_status": "conflict",
                                "drop_reason": null,
                            },
                            "ignored": {
                                "path_count": 0,
                                "bytes": 0,
                                "spooled_bytes": 0,
                                "publish_status": "empty",
                                "publish_mode": null,
                                "drop_reason": null,
                            },
                            "routing": {
                                "ignore_route_source": "command_snapshot",
                                "route_manifest_version": 0,
                                "dropped_path_count": 0,
                                "drop_reason_counts": {},
                            },
                        }),
                    );
                    extras
                },
            }),
        }
        .to_wire_value();
        let fixture: Value = serde_json::from_str(include_str!(
            "../../fixtures/command_finalize_conflict_response.json"
        ))
        .expect("valid command finalize conflict fixture");

        assert_eq!(response, fixture);
        assert!(response.get("timings").is_none());
    }
}
