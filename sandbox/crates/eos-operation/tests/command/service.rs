use eos_command::process::{
    CommandFinalResponsePersistence, CommandPersistenceOutcome, KillReason,
};
use eos_trace::{SpanStatus, TraceKind, TraceLinkKind};
use std::path::PathBuf;

use super::*;

#[test]
fn write_stdin_with_trace_reports_bytes_wait_and_status() {
    let ops = command_ops_with_inactive_isolated_run("cmd_stdin_trace", "caller");

    let outcome = ops
        .write_stdin_with_trace(WriteStdin {
            command_id: "cmd_stdin_trace".to_owned(),
            chars: "abc".to_owned(),
            yield_time_ms: 0,
        })
        .expect("stdin write reaches inactive process scaffold");

    let trace = outcome.trace.expect("stdin trace facts");
    assert_eq!(trace.command_id, "cmd_stdin_trace");
    assert_eq!(trace.bytes, 3);
    assert!(!trace.waited_for_output);
    assert_eq!(trace.status, outcome.response.status);
}

#[test]
fn write_stdin_teardown_control_does_not_emit_stdin_written_fact() {
    let ops = command_ops_with_inactive_isolated_run("cmd_stdin_control", "caller");

    let outcome = ops
        .write_stdin_with_trace(WriteStdin {
            command_id: "cmd_stdin_control".to_owned(),
            chars: "\u{3}".to_owned(),
            yield_time_ms: 0,
        })
        .expect("teardown control routes through cancel");

    assert!(outcome.trace.is_none());
}

#[test]
fn command_finalize_trace_record_carries_origin_and_eviction_markers() {
    let facts = CommandFinalizeTraceFacts {
        trace_origin: CommandTraceOrigin {
            trace_id: Some("trace-command-finalize".to_owned()),
            request_id: Some("request-command-finalize".to_owned()),
        },
        command_id: "cmd_finalized".to_owned(),
        caller_id: "caller".to_owned(),
        status: CommandStatus::TimedOut,
        exit_code: Some(124),
        signal: Some(15),
        kill: Some(KillReason::TimedOut),
        command_elapsed_s: 12.5,
        persistence: CommandPersistenceOutcome {
            final_response: Some(CommandFinalResponsePersistence::Persisted {
                path: PathBuf::from("/tmp/final.json"),
                bytes: 42,
            }),
            transcript_error: Some(eos_command::process::CommandTranscriptPersistenceError {
                path: PathBuf::from("/tmp/transcript.log"),
                error: "permission denied".to_owned(),
            }),
        },
        publish_completion: true,
        evictions: vec![CompletionBufferEviction {
            command_id: "cmd_evicted".to_owned(),
            seq: 7,
            max_entries: 1024,
        }],
    };

    let record = command_finalize_trace_record(&facts);

    assert_eq!(record.kind, TraceKind::CommandFinalize);
    assert_eq!(record.trace_id.as_str(), "trace-command-finalize");
    assert_eq!(
        record.request_id.as_ref().map(eos_trace::RequestId::as_str),
        Some("request-command-finalize")
    );
    assert_eq!(
        record
            .links
            .first()
            .map(|link| (&link.kind, link.value.as_str())),
        Some((&TraceLinkKind::Command, "cmd_finalized"))
    );
    let span = record.spans.first().expect("root finalize span");
    assert_eq!(span.name, "command.finalize");
    assert_eq!(span.status, Some(SpanStatus::TimedOut));

    let finalized = record
        .events
        .iter()
        .find(|event| event.module == "command" && event.name == "finalized")
        .expect("finalized event");
    assert_eq!(finalized.details.value["command_id"], "cmd_finalized");
    assert_eq!(finalized.details.value["signal"], 15);
    assert_eq!(finalized.details.value["kill_reason"], "timed_out");
    assert_eq!(finalized.details.value["publish_completion"], true);

    let exit_taken = record
        .events
        .iter()
        .find(|event| event.module == "command" && event.name == "exit_taken")
        .expect("exit-taken event");
    assert_eq!(exit_taken.details.value["command_id"], "cmd_finalized");
    assert_eq!(exit_taken.details.value["exit_code"], 124);
    assert_eq!(exit_taken.details.value["signal"], 15);
    assert_eq!(exit_taken.details.value["kill_reason"], "timed_out");

    let evicted = record
        .events
        .iter()
        .find(|event| event.module == "command" && event.name == "completion_buffer_evicted")
        .expect("eviction event");
    assert_eq!(evicted.details.value["command_id"], "cmd_evicted");
    assert_eq!(evicted.details.value["seq"], 7);
    assert_eq!(evicted.details.value["max_entries"], 1024);

    let persisted = record
        .events
        .iter()
        .find(|event| event.module == "command" && event.name == "final_persisted")
        .expect("final persist success event");
    assert_eq!(persisted.details.value["path"], "/tmp/final.json");
    assert_eq!(persisted.details.value["bytes"], 42);

    let transcript_failed = record
        .events
        .iter()
        .find(|event| event.module == "command" && event.name == "transcript_failed")
        .expect("transcript failure event");
    assert_eq!(
        transcript_failed.details.value["path"],
        "/tmp/transcript.log"
    );
    assert_eq!(
        transcript_failed.details.value["error"],
        "permission denied"
    );
}

#[test]
fn command_finalize_trace_record_carries_final_persist_failures() {
    let facts = CommandFinalizeTraceFacts {
        trace_origin: CommandTraceOrigin::default(),
        command_id: "cmd_final_failed".to_owned(),
        caller_id: "caller".to_owned(),
        status: CommandStatus::Error,
        exit_code: Some(1),
        signal: None,
        kill: None,
        command_elapsed_s: 0.1,
        persistence: CommandPersistenceOutcome {
            final_response: Some(CommandFinalResponsePersistence::Failed {
                path: PathBuf::from("/tmp/final.json"),
                error: "disk full".to_owned(),
            }),
            transcript_error: None,
        },
        publish_completion: false,
        evictions: Vec::new(),
    };

    let record = command_finalize_trace_record(&facts);
    let failed = record
        .events
        .iter()
        .find(|event| event.module == "command" && event.name == "final_persist_failed")
        .expect("final persist failure event");
    assert_eq!(failed.details.value["path"], "/tmp/final.json");
    assert_eq!(failed.details.value["error"], "disk full");
}

fn command_ops_with_inactive_isolated_run(id: &str, caller_id: &str) -> CommandOps {
    let ops = CommandOps::new(eos_command::CommandConfig::default());
    let root = std::env::temp_dir().join(format!(
        "eos-operation-command-service-{}-{id}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&root);
    let layer_stack_root = root.join("layers");
    let workspace_root = root.join("workspace");
    let scratch_dir = root.join("scratch");
    let upperdir = root.join("upper");
    let workdir = root.join("work");
    for path in [
        &layer_stack_root,
        &workspace_root,
        &scratch_dir,
        &upperdir,
        &workdir,
    ] {
        std::fs::create_dir_all(path).expect("create command test scaffold");
    }
    let process = CommandProcess::new(CommandProcessSpec {
        id: id.to_owned(),
        caller_id: caller_id.to_owned(),
        command: "cat".to_owned(),
        timeout_seconds: None,
    });
    ops.registry
        .insert(Arc::new(ActiveCommand::Isolated(IsolatedRun {
            process,
            trace_origin: CommandTraceOrigin::default(),
            binding: IsolatedWorkspaceBinding {
                caller_id: caller_id.to_owned(),
                workspace_handle_id: "workspace-handle".to_owned(),
                layer_stack_root,
                manifest_version: 1,
                manifest_root_hash: "root".to_owned(),
                workspace_root,
                scratch_dir,
                upperdir,
                workdir,
                layer_paths: Vec::new(),
                ns_fds: std::collections::HashMap::new(),
                cgroup_path: None,
            },
        })));
    ops
}
