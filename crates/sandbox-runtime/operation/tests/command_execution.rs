//! Black-box coverage of `CommandExecValue`'s retained transcript-window and
//! snapshot-offset accessors over a fake interactive execution. The engine
//! forwards (`is_finished`/`output_len`/`resolved`/...) live on
//! `InteractiveExecution` and are covered by the namespace-execution suite.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

use sandbox_runtime::command::{CommandExecValue, CommandTerminalResult};
use sandbox_runtime::WorkspaceSessionId;
use sandbox_runtime_namespace_execution::{
    open_pty_pair, CompletionPromise, ExecutionHandle, InteractiveExecution, NamespaceExecutionId,
    PtyMaster,
};

struct Fixture {
    command: CommandExecValue,
    transcript_path: PathBuf,
}

fn fixture(suffix: &str) -> Fixture {
    let dir = std::env::temp_dir().join(format!(
        "command-exec-value-{}-{suffix}",
        std::process::id()
    ));
    std::fs::create_dir_all(&dir).expect("create transcript dir");
    let transcript_path = dir.join("transcript.log");
    let _ = std::fs::remove_file(&transcript_path);

    let promise = Arc::new(CompletionPromise::<CommandTerminalResult>::new());
    let handle = ExecutionHandle::new(
        NamespaceExecutionId("namespace_execution_1".to_owned()),
        promise,
    );
    let (master, _slave) = open_pty_pair().expect("openpt pair");
    let pty = PtyMaster::spawn(master, None, Some(transcript_path.clone()), Box::new(|| {}))
        .expect("pty master");
    let exec = InteractiveExecution::new(handle, pty);
    let command = CommandExecValue::new(
        exec,
        transcript_path.clone(),
        WorkspaceSessionId("workspace-session".to_owned()),
        Instant::now(),
        "exec_command",
    );
    Fixture {
        command,
        transcript_path,
    }
}

#[test]
fn transcript_window_reads_the_file_window() {
    let fixture = fixture("window");
    std::fs::write(&fixture.transcript_path, b"alpha\nbeta\n").expect("write transcript");

    let window = fixture.command.transcript_window(0, usize::MAX);
    let rows = window
        .output
        .iter()
        .map(|row| row.text.as_str())
        .collect::<Vec<_>>();
    assert_eq!(rows, vec!["alpha", "beta"]);
}

#[test]
fn snapshot_offset_accessors_round_trip() {
    let fixture = fixture("offset");
    assert_eq!(fixture.command.take_snapshot_offset(), 0);
    fixture.command.advance_snapshot_offset(42);
    assert_eq!(fixture.command.take_snapshot_offset(), 42);
}

#[test]
fn elapsed_seconds_is_non_negative() {
    let fixture = fixture("elapsed");
    assert!(fixture.command.elapsed_seconds() >= 0.0);
}
