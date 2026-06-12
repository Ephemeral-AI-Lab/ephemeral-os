use super::*;

use serde_json::json;

#[test]
fn process_exposes_identity_and_expiry() {
    let process = CommandProcess::new(CommandProcessSpec {
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
fn reap_reads_transcript_and_persist_removes_it() -> Result<(), Box<dyn std::error::Error>> {
    let root = std::env::temp_dir().join(format!(
        "eos-command-reap-{}-{}",
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
    let process = CommandProcess::new_running(
        CommandProcessSpec {
            id: "cmd_1".to_owned(),
            caller_id: "caller".to_owned(),
            command: "echo ok".to_owned(),
            timeout_seconds: None,
        },
        RunningCommandProcessParts {
            process: crate::pty::PtyProcess::inactive(writer),
            output_path: root.join("runner-result.json"),
            final_path: final_path.clone(),
            transcript_path: transcript_path.clone(),
            output_drain_grace_ms: 0,
        },
    );

    let reaped = process.reap().expect("inactive process reaps");
    assert_eq!(reaped.stdout, "captured transcript output");
    assert!(reaped.kill.is_none());
    assert!(process.reap().is_none());

    let response = json!({
        "status": "ok",
        "exit_code": 0,
        "output": {
            "stdout": reaped.stdout,
            "stderr": "",
        },
        "command_id": "cmd_1",
        "workspace": "ephemeral",
    });
    process.persist_final(&response);

    assert!(final_path.exists());
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
