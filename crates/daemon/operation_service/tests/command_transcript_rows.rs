pub mod support;

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use command::process::{
    CommandProcess, CommandProcessExit, CommandProcessSpawn, CommandProcessSpec,
};
use command::yield_wait_loop::WaitOutcome;
use operation_service::command::{
    CommandCallContext, CommandId, CommandLaunchDriver, CommandServiceError, CommandStatus,
    CommandStream, CommandTranscriptRow, ExecCommandInput, PollCommandInput, ReadCommandLinesInput,
};
use workspace::{CallerId, WorkspaceProfile};

use support::{
    build_services_with_launch_driver, create_request, success_exit, workspace_handle,
    FakeWorkspaceService, TestServices,
};

#[derive(Debug)]
struct TranscriptLaunchDriver {
    transcript: String,
    outcomes: Mutex<VecDeque<WaitOutcome<CommandProcessExit>>>,
}

#[derive(Debug)]
struct MissingTranscriptLaunchDriver {
    outcomes: Mutex<VecDeque<WaitOutcome<CommandProcessExit>>>,
}

impl TranscriptLaunchDriver {
    fn running(transcript: &str) -> Self {
        Self {
            transcript: transcript.to_owned(),
            outcomes: Mutex::new(VecDeque::from([WaitOutcome::Running(String::new())])),
        }
    }

    fn completed(transcript: &str, stdout: &str) -> Self {
        Self {
            transcript: transcript.to_owned(),
            outcomes: Mutex::new(VecDeque::from([WaitOutcome::Completed(success_exit(
                stdout,
            ))])),
        }
    }
}

impl MissingTranscriptLaunchDriver {
    fn running() -> Self {
        Self {
            outcomes: Mutex::new(VecDeque::from([WaitOutcome::Running(String::new())])),
        }
    }

    fn completed(stdout: &str) -> Self {
        Self {
            outcomes: Mutex::new(VecDeque::from([WaitOutcome::Completed(success_exit(
                stdout,
            ))])),
        }
    }
}

impl CommandLaunchDriver for TranscriptLaunchDriver {
    fn spawn(
        &self,
        spec: CommandProcessSpec,
        parts: CommandProcessSpawn<'_>,
    ) -> Result<CommandProcess, CommandServiceError> {
        if let Some(parent) = parts.transcript_path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| CommandServiceError::CommandIo {
                command_id: CommandId(spec.id.clone()),
                error: error.to_string(),
            })?;
        }
        std::fs::write(&parts.transcript_path, &self.transcript).map_err(|error| {
            CommandServiceError::CommandIo {
                command_id: CommandId(spec.id.clone()),
                error: error.to_string(),
            }
        })?;
        Ok(CommandProcess::inactive_for_test(spec))
    }

    fn wait_for_initial_yield(
        &self,
        _process: &CommandProcess,
        _config: &command::CommandConfig,
        _yield_time_ms: u64,
        _start_offset: u64,
    ) -> WaitOutcome<CommandProcessExit> {
        self.outcomes
            .lock()
            .expect("test operation succeeds")
            .pop_front()
            .unwrap_or_else(|| WaitOutcome::Running(String::new()))
    }
}

impl CommandLaunchDriver for MissingTranscriptLaunchDriver {
    fn spawn(
        &self,
        spec: CommandProcessSpec,
        _parts: CommandProcessSpawn<'_>,
    ) -> Result<CommandProcess, CommandServiceError> {
        Ok(CommandProcess::inactive_for_test(spec))
    }

    fn wait_for_initial_yield(
        &self,
        _process: &CommandProcess,
        _config: &command::CommandConfig,
        _yield_time_ms: u64,
        _start_offset: u64,
    ) -> WaitOutcome<CommandProcessExit> {
        self.outcomes
            .lock()
            .expect("test operation succeeds")
            .pop_front()
            .unwrap_or_else(|| WaitOutcome::Running(String::new()))
    }
}

fn session_with_driver(driver: impl CommandLaunchDriver + 'static) -> (TestServices, CommandId) {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/session");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-session",
        "caller-owner",
        "lease-1",
        workspace_root.clone(),
        WorkspaceProfile::HostCompatible,
    )));
    let env = build_services_with_launch_driver(Arc::clone(&fake), Arc::new(driver));
    let handler = env
        .workspace
        .create_workspace_session(create_request("caller-owner", workspace_root.clone()))
        .expect("session create succeeds");

    let output = env
        .command
        .exec_command(
            ExecCommandInput {
                caller_id: CallerId("caller-owner".to_owned()),
                workspace_root,
                workspace_session_id: Some(handler.workspace_session_id.clone()),
                cmd: "printf rows".to_owned(),
                cwd: None,
                timeout_seconds: None,
                yield_time_ms: Some(0),
            },
            context("caller-owner"),
        )
        .expect("command exec succeeds");

    (
        env,
        output.command_id.expect("command id is returned by exec"),
    )
}

fn context(caller_id: &str) -> CommandCallContext {
    CommandCallContext {
        caller_id: CallerId(caller_id.to_owned()),
    }
}

#[test]
fn command_transcript_rows_preserve_offsets_streams_and_window_metadata() {
    let transcript = concat!(
        "{\"offset\":0,\"stream\":\"stdout\",\"text\":\"first\"}\n",
        "{\"offset\":1,\"stream\":\"stderr\",\"text\":\"warning\"}\n",
        "{\"offset\":2,\"stream\":\"stdout\",\"text\":\"third\"}\n",
    );
    let (env, command_id) = session_with_driver(TranscriptLaunchDriver::running(transcript));

    let output = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id: command_id.clone(),
                offset: 1,
                limit: 1,
            },
            context("caller-owner"),
        )
        .expect("owner can read active command rows");

    assert_eq!(output.command_id, command_id);
    assert_eq!(output.status, CommandStatus::Running);
    assert_eq!(output.exit_code, None);
    assert_eq!(output.offset, 1);
    assert_eq!(output.next_offset, 2);
    assert_eq!(output.total_lines, 3);
    assert_eq!(output.truncated_before, 0);
    assert!(output.output_truncated);
    assert_eq!(
        output.output,
        vec![CommandTranscriptRow {
            offset: 1,
            stream: CommandStream::Stderr,
            text: "warning".to_owned(),
        }]
    );
}

#[test]
fn command_transcript_rows_parse_raw_pty_transcript_as_stdout_rows() {
    let transcript = concat!(
        "[2026-06-18T01:02:03.004Z] first\n",
        "[2026-06-18T09:02:03.004+08:00] second\n",
        "third\n",
    );
    let (env, command_id) = session_with_driver(TranscriptLaunchDriver::running(transcript));

    let output = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id,
                offset: 0,
                limit: 10,
            },
            context("caller-owner"),
        )
        .expect("owner can read raw transcript rows");

    assert_eq!(output.next_offset, 3);
    assert_eq!(output.total_lines, 3);
    assert!(!output.output_truncated);
    assert_eq!(
        output.output,
        vec![
            CommandTranscriptRow {
                offset: 0,
                stream: CommandStream::Stdout,
                text: "first".to_owned(),
            },
            CommandTranscriptRow {
                offset: 1,
                stream: CommandStream::Stdout,
                text: "second".to_owned(),
            },
            CommandTranscriptRow {
                offset: 2,
                stream: CommandStream::Stdout,
                text: "third".to_owned(),
            },
        ]
    );
}

#[test]
fn command_transcript_rows_keep_empty_window_next_offset_at_request() {
    let (env, command_id) =
        session_with_driver(TranscriptLaunchDriver::running("one\ntwo\nthree\n"));

    let output = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id,
                offset: 10,
                limit: 5,
            },
            context("caller-owner"),
        )
        .expect("owner can request beyond retained rows");

    assert_eq!(output.offset, 10);
    assert_eq!(output.next_offset, 10);
    assert_eq!(output.total_lines, 3);
    assert_eq!(output.truncated_before, 0);
    assert!(output.output.is_empty());
    assert!(!output.output_truncated);
}

#[test]
fn command_transcript_rows_report_bounded_window_truncation() {
    let mut transcript = String::from("old-one\nold-two\n");
    transcript.push_str(&"x".repeat(1024 * 1024 + 128));
    transcript.push('\n');
    transcript.push_str("kept-one\nkept-two\n");
    let (env, command_id) = session_with_driver(TranscriptLaunchDriver::running(&transcript));

    let output = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id,
                offset: 0,
                limit: 10,
            },
            context("caller-owner"),
        )
        .expect("owner can read bounded row window");

    assert_eq!(output.offset, 0);
    assert_eq!(output.truncated_before, 3);
    assert_eq!(output.total_lines, 5);
    assert_eq!(output.next_offset, 5);
    assert!(output.output_truncated);
    assert_eq!(
        output.output,
        vec![
            CommandTranscriptRow {
                offset: 3,
                stream: CommandStream::Stdout,
                text: "kept-one".to_owned(),
            },
            CommandTranscriptRow {
                offset: 4,
                stream: CommandStream::Stdout,
                text: "kept-two".to_owned(),
            },
        ]
    );
}

#[test]
fn command_transcript_rows_allow_active_missing_transcript_as_empty_pending_window() {
    let (env, command_id) = session_with_driver(MissingTranscriptLaunchDriver::running());

    let output = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id,
                offset: 0,
                limit: 10,
            },
            context("caller-owner"),
        )
        .expect("active command without output yet returns an empty pending window");

    assert_eq!(output.status, CommandStatus::Running);
    assert_eq!(output.exit_code, None);
    assert_eq!(output.next_offset, 0);
    assert_eq!(output.total_lines, 0);
    assert!(!output.output_truncated);
    assert!(output.output.is_empty());
}

#[test]
fn command_transcript_rows_error_when_completed_transcript_is_missing() {
    let (env, command_id) = session_with_driver(MissingTranscriptLaunchDriver::completed(
        "terminal stdout\n",
    ));

    let error = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id: command_id.clone(),
                offset: 0,
                limit: 10,
            },
            context("caller-owner"),
        )
        .expect_err("completed command with missing retained transcript is not empty output");

    assert!(matches!(
        error,
        CommandServiceError::CommandTranscriptUnavailable { command_id: id, path: Some(path), error }
            if id == command_id
                && path.ends_with("transcript.log")
                && error.contains("open transcript")
    ));
}

#[test]
fn command_transcript_rows_authorize_active_reads_by_caller() {
    let (env, command_id) = session_with_driver(TranscriptLaunchDriver::running("owner only\n"));

    let error = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id: command_id.clone(),
                offset: 0,
                limit: 1,
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot read active rows");

    assert!(matches!(
        error,
        CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
            if id == command_id
                && expected == CallerId("caller-owner".to_owned())
                && actual == CallerId("caller-other".to_owned())
    ));
}

#[test]
fn command_transcript_rows_keep_completed_rows_and_authorization() {
    let transcript = "completed one\ncompleted two\ncompleted three\n";
    let (env, command_id) = session_with_driver(TranscriptLaunchDriver::completed(
        transcript,
        "terminal stdout\n",
    ));

    let lines = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id: command_id.clone(),
                offset: 0,
                limit: 10,
            },
            context("caller-owner"),
        )
        .expect("owner can read completed rows");
    let poll = env
        .command
        .poll(
            PollCommandInput {
                command_id: command_id.clone(),
                last_n_lines: Some(10),
            },
            context("caller-owner"),
        )
        .expect("owner can poll completed command");

    assert_eq!(lines.status, CommandStatus::Completed);
    assert_eq!(lines.exit_code, Some(0));
    assert_eq!(poll.status, lines.status);
    assert_eq!(poll.exit_code, lines.exit_code);
    assert_eq!(lines.total_lines, 3);
    assert_eq!(
        lines.output,
        vec![
            CommandTranscriptRow {
                offset: 0,
                stream: CommandStream::Stdout,
                text: "completed one".to_owned(),
            },
            CommandTranscriptRow {
                offset: 1,
                stream: CommandStream::Stdout,
                text: "completed two".to_owned(),
            },
            CommandTranscriptRow {
                offset: 2,
                stream: CommandStream::Stdout,
                text: "completed three".to_owned(),
            },
        ]
    );

    let window = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id: command_id.clone(),
                offset: 1,
                limit: 1,
            },
            context("caller-owner"),
        )
        .expect("owner can read a completed command window");
    assert_eq!(window.status, CommandStatus::Completed);
    assert_eq!(window.exit_code, Some(0));
    assert_eq!(window.total_lines, 3);
    assert_eq!(window.truncated_before, 0);
    assert_eq!(window.next_offset, 2);
    assert!(window.output_truncated);
    assert_eq!(
        window.output,
        vec![CommandTranscriptRow {
            offset: 1,
            stream: CommandStream::Stdout,
            text: "completed two".to_owned(),
        }]
    );

    let error = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id: command_id.clone(),
                offset: 0,
                limit: 1,
            },
            context("caller-other"),
        )
        .expect_err("wrong caller cannot read completed rows");
    assert!(matches!(
        error,
        CommandServiceError::CommandCallerMismatch { command_id: id, expected, actual }
            if id == command_id
                && expected == CallerId("caller-owner".to_owned())
                && actual == CallerId("caller-other".to_owned())
    ));
}

#[test]
fn command_transcript_rows_report_running_status_for_one_shot_active_command() {
    let fake = Arc::new(FakeWorkspaceService::new());
    let workspace_root = PathBuf::from("/workspace/one-shot");
    fake.push_create_result(Ok(workspace_handle(
        "workspace-one-shot",
        "caller-owner",
        "lease-1",
        workspace_root.clone(),
        WorkspaceProfile::HostCompatible,
    )));
    let env = build_services_with_launch_driver(
        fake,
        Arc::new(TranscriptLaunchDriver::running("one-shot row\n")),
    );
    let output = env
        .command
        .exec_command(
            ExecCommandInput {
                caller_id: CallerId("caller-owner".to_owned()),
                workspace_root,
                workspace_session_id: None,
                cmd: "printf rows".to_owned(),
                cwd: None,
                timeout_seconds: None,
                yield_time_ms: Some(0),
            },
            context("caller-owner"),
        )
        .expect("one-shot command starts");
    let command_id = output.command_id.expect("command id is returned");

    let rows = env
        .command
        .read_lines(
            ReadCommandLinesInput {
                command_id,
                offset: 0,
                limit: 1,
            },
            context("caller-owner"),
        )
        .expect("owner can read active one-shot rows");

    assert_eq!(rows.status, CommandStatus::Running);
    assert_eq!(rows.exit_code, None);
    assert_eq!(rows.total_lines, 1);
}
