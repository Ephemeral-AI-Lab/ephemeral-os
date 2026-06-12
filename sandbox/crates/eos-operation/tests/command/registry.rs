use super::*;

fn sample_completion(id: &str) -> CommandCompletion {
    CommandCompletion {
        command_id: id.to_owned(),
        caller_id: "caller".to_owned(),
        command: "cmd".to_owned(),
        result: CommandResponse::error(""),
    }
}

#[cfg(not(target_os = "linux"))]
fn ephemeral_run(id: &str, caller: &str) -> Arc<ActiveCommand> {
    use std::path::PathBuf;

    use eos_command::process::CommandProcessSpec;
    use eos_layerstack::service::Snapshot;

    let process = CommandProcess::new(CommandProcessSpec {
        id: id.to_owned(),
        caller_id: caller.to_owned(),
        command: "sleep 1".to_owned(),
        timeout_seconds: None,
    });
    let scratch = std::env::temp_dir().join(format!(
        "eos-operation-command-registry-{}-{id}-{caller}",
        std::process::id()
    ));
    let workspace = EphemeralWorkspace::create(&scratch, "test", id).expect("scaffold workspace");
    Arc::new(ActiveCommand::Ephemeral(EphemeralRun {
        process,
        root: PathBuf::from("/layers"),
        snapshot: Snapshot {
            lease_id: "lease".to_owned(),
            manifest_version: 1,
            root_hash: "hash".to_owned(),
            layer_paths: Vec::new(),
        },
        workspace,
    }))
}

#[cfg(not(target_os = "linux"))]
#[test]
fn insert_get_count_remove_track_caller_runs() {
    let registry = CommandRegistry::new();
    registry.insert(ephemeral_run("cmd_1", "caller"));
    registry.insert(ephemeral_run("cmd_2", "caller"));
    registry.insert(ephemeral_run("cmd_3", "other"));

    assert_eq!(registry.count_by_caller(Some("caller")), 2);
    assert_eq!(registry.count_by_caller(Some("other")), 1);
    assert_eq!(registry.count_by_caller(None), 3);
    assert!(registry.get("cmd_2").is_some());
    assert_eq!(registry.caller_commands("caller").len(), 2);

    assert!(registry.remove("cmd_2").is_some());
    assert_eq!(registry.count_by_caller(Some("caller")), 1);
    assert!(registry.remove("cmd_1").is_some());
    assert_eq!(registry.count_by_caller(Some("caller")), 0);
    assert_eq!(registry.live().len(), 1);
}

#[test]
fn push_completed_evicts_oldest_beyond_cap() {
    let registry = CommandRegistry::new();
    let overflow = 5;
    for index in 0..(MAX_COMPLETED_ENTRIES + overflow) {
        registry.push_completed(sample_completion(&format!("cmd_{index}")));
    }

    assert_eq!(lock(&registry.completed).len(), MAX_COMPLETED_ENTRIES);
    for index in 0..overflow {
        assert!(registry
            .take_completed_result(&format!("cmd_{index}"))
            .is_none());
    }
    let newest = format!("cmd_{}", MAX_COMPLETED_ENTRIES + overflow - 1);
    assert!(registry.take_completed_result(&newest).is_some());
}
