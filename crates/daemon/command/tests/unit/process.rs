use super::*;

use serde_json::json;

#[test]
fn process_exposes_identity_and_expiry() {
    let process = CommandProcess::inactive_for_test(CommandProcessSpec {
        id: "cmd_1".to_owned(),
        caller_id: "caller".to_owned(),
        command: "echo ok".to_owned(),
        timeout_seconds: Some(0.001),
    });

    assert_eq!(process.id(), "cmd_1");
    assert_eq!(process.caller_id(), "caller");
    assert_eq!(process.command(), "echo ok");
    assert!(process.is_past_deadline(process.started_at() + Duration::from_millis(2), 3600));
}

#[test]
fn take_exit_reads_transcript_and_persist_removes_it() -> Result<(), Box<dyn std::error::Error>> {
    let root = std::env::temp_dir().join(format!(
        "command-take-exit-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_nanos()
    ));
    std::fs::create_dir_all(&root)?;
    let transcript_path = root.join("transcript.log");
    let final_path = root.join("final.json");
    std::fs::write(&transcript_path, b"captured transcript output")?;

    let writer = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open("/dev/null")?;
    let process = CommandProcess::with_runtime(
        CommandProcessSpec {
            id: "cmd_1".to_owned(),
            caller_id: "caller".to_owned(),
            command: "echo ok".to_owned(),
            timeout_seconds: None,
        },
        CommandProcessRuntime::new(
            crate::pty::PtyProcess::inactive(writer),
            root.join("runner-result.json"),
            final_path.clone(),
            transcript_path.clone(),
            0,
        ),
    );

    let exit = process.take_exit().expect("inactive process has an exit");
    assert_eq!(exit.stdout, "captured transcript output");
    assert!(exit.kill.is_none());
    assert!(process.take_exit().is_none());

    let response = json!({
        "status": "ok",
        "exit_code": 0,
        "output": {
            "stdout": exit.stdout,
            "stderr": "",
        },
        "command_id": "cmd_1",
        "workspace": "host",
    });
    let persistence = process.persist_final(&response);

    assert!(final_path.exists());
    assert_eq!(
        persistence.final_response,
        Some(CommandFinalResponsePersistence::Persisted {
            path: final_path.clone(),
            bytes: std::fs::metadata(&final_path)?.len().try_into()?,
        })
    );
    assert_eq!(persistence.transcript_error, None);
    let final_response: serde_json::Value = serde_json::from_slice(&std::fs::read(&final_path)?)?;
    assert_eq!(
        final_response
            .get("output")
            .and_then(|output| output.get("stdout"))
            .and_then(serde_json::Value::as_str),
        Some("captured transcript output")
    );
    assert!(!transcript_path.exists());

    let _ = std::fs::remove_dir_all(root);
    Ok(())
}

#[test]
fn spawn_reports_command_request_artifact_write_failure() -> Result<(), Box<dyn std::error::Error>>
{
    let root = std::env::temp_dir().join(format!(
        "command-spawn-artifact-failure-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_nanos()
    ));
    let request_path = root.join("missing-parent").join("command-request.json");
    let error = match CommandProcess::spawn(
        CommandProcessSpec {
            id: "cmd_1".to_owned(),
            caller_id: "caller".to_owned(),
            command: "echo ok".to_owned(),
            timeout_seconds: None,
        },
        CommandProcessSpawn {
            command_request: json!({"invocation_id": "cmd_1"}),
            request_path: request_path.clone(),
            output_path: root.join("runner-result.json"),
            final_path: root.join("final.json"),
            transcript_path: root.join("transcript.log"),
            transcript_timestamp_timezone: "UTC",
            output_drain_grace_ms: 0,
        },
    ) {
        Ok(_) => panic!("spawn should fail before opening a PTY"),
        Err(error) => error,
    };

    match error {
        CommandError::ArtifactWrite {
            artifact,
            path,
            error,
        } => {
            assert_eq!(artifact, "command_request");
            assert_eq!(path, request_path);
            assert!(!error.is_empty());
        }
        other => panic!("expected artifact write failure, got {other:?}"),
    }

    let _ = std::fs::remove_dir_all(root);
    Ok(())
}

#[test]
fn write_process_metadata_records_process_group_id() -> Result<(), Box<dyn std::error::Error>> {
    let root = std::env::temp_dir().join(format!(
        "command-process-metadata-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_nanos()
    ));
    std::fs::create_dir_all(&root)?;
    let path = root.join(PROCESS_METADATA_FILE);

    write_process_metadata(&path, Some(12345))?;

    let metadata = CommandProcessMetadata::from_slice(&std::fs::read(&path)?)?;
    assert_eq!(metadata, CommandProcessMetadata::new(Some(12345)));

    let _ = std::fs::remove_dir_all(root);
    Ok(())
}

#[test]
fn persist_final_reports_final_and_transcript_failures() -> Result<(), Box<dyn std::error::Error>> {
    let root = std::env::temp_dir().join(format!(
        "command-persist-failures-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_nanos()
    ));
    std::fs::create_dir_all(&root)?;
    let final_path = root.join("final-as-dir");
    let transcript_path = root.join("transcript-as-dir");
    std::fs::create_dir_all(&final_path)?;
    std::fs::create_dir_all(&transcript_path)?;

    let writer = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open("/dev/null")?;
    let process = CommandProcess::with_runtime(
        CommandProcessSpec {
            id: "cmd_1".to_owned(),
            caller_id: "caller".to_owned(),
            command: "echo ok".to_owned(),
            timeout_seconds: None,
        },
        CommandProcessRuntime::new(
            crate::pty::PtyProcess::inactive(writer),
            root.join("runner-result.json"),
            final_path.clone(),
            transcript_path.clone(),
            0,
        ),
    );

    let persistence = process.persist_final(&json!({"status": "ok"}));

    match persistence.final_response {
        Some(CommandFinalResponsePersistence::Failed { path, error }) => {
            assert_eq!(path, final_path);
            assert!(!error.is_empty());
        }
        other => panic!("expected final persistence failure, got {other:?}"),
    }
    let transcript_error = persistence
        .transcript_error
        .expect("directory transcript removal reports failure");
    assert_eq!(transcript_error.path, transcript_path);
    assert!(!transcript_error.error.is_empty());

    let _ = std::fs::remove_dir_all(root);
    Ok(())
}
