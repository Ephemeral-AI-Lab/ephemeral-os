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
fn workspace_run(id: &str, caller: &str) -> Arc<ActiveCommand> {
    use command::process::CommandProcessSpec;

    let process = CommandProcess::inactive_for_test(CommandProcessSpec {
        id: id.to_owned(),
        caller_id: caller.to_owned(),
        command: "sleep 1".to_owned(),
        timeout_seconds: None,
    });
    let scratch = std::env::temp_dir().join(format!(
        "operation-command-registry-{}-{id}-{caller}",
        std::process::id()
    ));
    let layer_stack_root = scratch.join("layers");
    let workspace_root = scratch.join("workspace");
    let scratch_dir = scratch.join("scratch");
    let upperdir = scratch.join("upper");
    let workdir = scratch.join("work");
    for path in [
        &layer_stack_root,
        &workspace_root,
        &scratch_dir,
        &upperdir,
        &workdir,
    ] {
        std::fs::create_dir_all(path).expect("scaffold workspace");
    }
    Arc::new(ActiveCommand::Workspace(WorkspaceRun {
        process,
        trace_origin: CommandTraceOrigin::default(),
        context: WorkspaceModeContext {
            caller_id: caller.to_owned(),
            workspace_handle_id: "workspace-handle".to_owned(),
            profile: workspace::WorkspaceProfile::Isolated,
            layer_stack_root,
            manifest_version: 1,
            manifest_root_hash: "hash".to_owned(),
            workspace_root,
            scratch_dir,
            upperdir,
            workdir,
            layer_paths: Vec::new(),
            ns_fds: std::collections::HashMap::new(),
            cgroup_path: None,
        },
        remountable: false,
    }))
}

#[cfg(not(target_os = "linux"))]
#[test]
fn insert_get_count_remove_track_caller_runs() {
    let registry = CommandRegistry::new();
    registry.insert(workspace_run("cmd_1", "caller"));
    registry.insert(workspace_run("cmd_2", "caller"));
    registry.insert(workspace_run("cmd_3", "other"));

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
fn command_trace_origin_copies_start_request_identity() {
    let request = StartCommand {
        invocation_id: "invoke-command".to_owned(),
        caller_id: "caller".to_owned(),
        cmd: "echo ok".to_owned(),
        trace_id: Some("trace-command".to_owned()),
        request_id: Some("request-command".to_owned()),
        timeout_seconds: None,
        yield_time_ms: 0,
        cwd: None,
        remountable: false,
    };

    let origin = CommandTraceOrigin::from_start(&request);

    assert_eq!(origin.trace_id.as_deref(), Some("trace-command"));
    assert_eq!(origin.request_id.as_deref(), Some("request-command"));
}

#[test]
fn command_reservations_reject_at_cap_and_release_unactivated_slots() {
    let registry = Arc::new(CommandRegistry::new());
    let mut reservations = Vec::new();
    for _ in 0..MAX_ACTIVE_COMMANDS {
        reservations.push(
            registry
                .try_reserve()
                .expect("reservation below active cap succeeds"),
        );
    }

    match registry.try_reserve() {
        Ok(_) => panic!("reservation at active cap must be rejected"),
        Err(error) => {
            assert_eq!(error.active, MAX_ACTIVE_COMMANDS);
            assert_eq!(error.max, MAX_ACTIVE_COMMANDS);
        }
    }
    reservations.pop();
    assert!(
        registry.try_reserve().is_ok(),
        "dropping an unactivated reservation releases capacity"
    );
}

#[test]
fn push_completed_evicts_oldest_beyond_cap() {
    let registry = CommandRegistry::new();
    let overflow = 5;
    let mut evictions = Vec::new();
    for index in 0..(MAX_COMPLETED_ENTRIES + overflow) {
        evictions.extend(registry.push_completed(sample_completion(&format!("cmd_{index}"))));
    }

    assert_eq!(lock(&registry.completed).len(), MAX_COMPLETED_ENTRIES);
    assert_eq!(evictions.len(), overflow);
    for (index, eviction) in evictions.iter().enumerate().take(overflow) {
        assert_eq!(
            eviction.command_id,
            format!("cmd_{index}"),
            "eviction marker names the lost command"
        );
        assert_eq!(
            eviction.seq,
            u64::try_from(index + 1).expect("eviction seq fits u64"),
            "eviction marker preserves completion seq"
        );
        assert_eq!(
            eviction.max_entries, MAX_COMPLETED_ENTRIES,
            "eviction marker records the cap"
        );
        assert!(registry
            .take_completed_result(&format!("cmd_{index}"))
            .is_none());
    }
    let newest = format!("cmd_{}", MAX_COMPLETED_ENTRIES + overflow - 1);
    assert!(registry.take_completed_result(&newest).is_some());
}

#[test]
fn collect_completed_delivers_oldest_bounded_batch() {
    let registry = CommandRegistry::new();
    for index in 0..(MAX_COLLECT_COMPLETED_BATCH + 2) {
        registry.push_completed(sample_completion(&format!("cmd_{index}")));
    }

    let output = registry.collect_completed(&CollectCompleted {
        command_ids: None,
        caller_id: Some("caller".to_owned()),
    });

    assert_eq!(output.completions.len(), MAX_COLLECT_COMPLETED_BATCH);
    assert!(output.has_more);
    assert_eq!(output.max_completions, MAX_COLLECT_COMPLETED_BATCH);
    for (index, completion) in output.completions.iter().enumerate() {
        assert_eq!(completion.command_id, format!("cmd_{index}"));
    }

    let next = registry.collect_completed(&CollectCompleted {
        command_ids: None,
        caller_id: Some("caller".to_owned()),
    });
    assert_eq!(next.completions.len(), 2);
    assert!(!next.has_more);
}
