use anyhow::{Context, Result};
use protocol::catalog;
use serde_json::{json, Value};

use crate::support::{
    as_bool, as_i64, as_str, envelope_result, has_trace_event, live_pool_or_skip, trace_record,
};

const POST_SQUASH_WRITES: usize = 28;

#[test]
fn commit_to_git_commits_overlay_snapshot_after_repeated_squash() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease
        .container()
        .exec(&["git", "-C", lease.workspace_root(), "init"])
        .context("git init workspace")?;
    for version in 0..POST_SQUASH_WRITES {
        lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": "git/checkpoint.txt",
                "content": format!("from layerstack after squash {version}\n"),
                "overwrite": true,
            }),
        )?;
        lease.call_ok(
            catalog::SANDBOX_FILE_WRITE,
            json!({
                "path": format!("git/noise-{version}.txt"),
                "content": format!("noise {version}\n"),
                "overwrite": true,
            }),
        )?;
    }
    // 56 publishes against `auto_squash_max_depth: 8` only stays this shallow
    // if auto-squash folded the stack repeatedly along the way.
    let metrics = lease.call_ok(catalog::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    assert!(
        as_i64(&metrics, "manifest_depth")? <= 8,
        "commit_to_git e2e should run after repeated auto-squash bounded the depth: {metrics}"
    );

    let commit_wire = lease.call(
        catalog::SANDBOX_CHECKPOINT_COMMIT_TO_GIT,
        json!({
            "workspace_root": lease.workspace_root(),
            "paths": ["git/checkpoint.txt"],
            "message": "checkpoint live overlay snapshot",
        }),
    )?;
    let commit = envelope_result(&commit_wire)?;
    let record = trace_record(&commit_wire)?;

    assert!(as_bool(commit, "success")?);
    assert!(as_bool(commit, "committed")?);
    assert_eq!(
        as_str(commit, "worktree_mode")?,
        "overlay",
        "live e2e must exercise the overlay-mounted worktree path: {commit}"
    );
    assert!(
        has_trace_event(&record, "workspace.route", "route_selected", |details| {
            details["kind"] == "fast_path"
                && details["reason"] == "commit_to_git_uses_layerstack_worktree"
        }),
        "commit_to_git trace should record the fast-path checkpoint route: {record:?}"
    );
    assert!(
        has_trace_event(&record, "checkpoint", "worktree_mode_selected", |details| {
            details["mode"] == "overlay"
        }),
        "commit_to_git trace should record the selected overlay worktree: {record:?}"
    );
    assert_git_command_finished(&record, "git add -A -- <paths>")?;
    assert_git_command_finished(&record, "git commit -m <message>")?;
    assert_checkpoint_phase(&record, "sandbox.checkpoint.commit_to_git.total_s")?;
    assert_checkpoint_phase(&record, "sandbox.checkpoint.commit_to_git.git_add_s")?;
    assert_checkpoint_phase(&record, "sandbox.checkpoint.commit_to_git.git_commit_s")?;
    assert_checkpoint_phase(&record, "sandbox.checkpoint.commit_to_git.overlay_mount_s")?;
    let committed_depth = layer_stack_snapshot_number(&record, "manifest_depth")?;
    let metrics = lease.call_ok(catalog::SANDBOX_CHECKPOINT_LAYER_METRICS, json!({}))?;
    assert_eq!(
        committed_depth as i64,
        as_i64(&metrics, "manifest_depth")?,
        "commit_to_git should report the snapshot depth it committed: commit={commit} metrics={metrics}"
    );
    let commit_sha = as_str(commit, "commit_sha")?;
    let show = lease
        .container()
        .exec(&[
            "git",
            "--git-dir",
            &format!("{}/.git", lease.workspace_root()),
            "show",
            &format!("{commit_sha}:git/checkpoint.txt"),
        ])
        .context("git show checkpoint blob")?;
    assert_eq!(
        show,
        format!("from layerstack after squash {}\n", POST_SQUASH_WRITES - 1).trim_end()
    );
    let excluded = lease.container().exec(&[
        "sh",
        "-lc",
        &format!(
            "git --git-dir {}/.git show {commit_sha}:git/noise-0.txt >/dev/null 2>&1; test $? -ne 0",
            lease.workspace_root()
        ),
    ]);
    assert!(
        excluded.is_ok(),
        "path-filtered commit should not include noise files"
    );
    eprintln!(
        "commit_to_git trace phases: {} depth={committed_depth}",
        checkpoint_phase_report(&record)?
    );
    Ok(())
}

fn assert_git_command_finished(record: &trace::TraceRecord, argv_summary: &str) -> Result<()> {
    assert!(
        has_trace_event(record, "checkpoint", "git_command_finished", |details| {
            details["argv_summary"] == argv_summary && details["exit_code"] == 0
        }),
        "commit_to_git trace should record successful {argv_summary}: {record:?}"
    );
    Ok(())
}

fn assert_checkpoint_phase(record: &trace::TraceRecord, key: &str) -> Result<()> {
    let duration = checkpoint_phase(record, key)?;
    assert!(
        duration >= 0.0,
        "checkpoint phase {key} should be nonnegative: {record:?}"
    );
    Ok(())
}

fn checkpoint_phase(record: &trace::TraceRecord, key: &str) -> Result<f64> {
    record
        .events
        .iter()
        .find(|event| event.module == "checkpoint" && event.name == "commit_to_git_finished")
        .and_then(|event| {
            event
                .details
                .value
                .get("phases")
                .and_then(|phases| phases.get(key))
        })
        .and_then(Value::as_f64)
        .with_context(|| format!("checkpoint phase {key} missing in trace: {record:?}"))
}

fn layer_stack_snapshot_number(record: &trace::TraceRecord, key: &str) -> Result<f64> {
    record
        .events
        .iter()
        .find(|event| event.module == "layer_stack" && event.name == "snapshot_lease_used")
        .and_then(|event| event.details.value.get(key))
        .and_then(Value::as_f64)
        .with_context(|| format!("layer_stack snapshot field {key} missing in trace: {record:?}"))
}

fn checkpoint_phase_report(record: &trace::TraceRecord) -> Result<String> {
    let phases = record
        .events
        .iter()
        .find(|event| event.module == "checkpoint" && event.name == "commit_to_git_finished")
        .and_then(|event| event.details.value.get("phases"))
        .and_then(Value::as_object)
        .with_context(|| format!("commit_to_git phase event missing in trace: {record:?}"))?;
    let total_s = checkpoint_phase(record, "sandbox.checkpoint.commit_to_git.total_s")?;
    let mut entries = Vec::new();
    for (key, value) in phases {
        let Some(number) = value.as_f64() else {
            continue;
        };
        if key != "sandbox.checkpoint.commit_to_git.total_s" {
            entries.push((key.as_str(), number));
        }
    }
    entries.sort_by_key(|(key, _)| *key);
    Ok(format!(
        "total_s={total_s:.6} phases=[{}]",
        format_entries(&entries)
    ))
}

fn format_entries(entries: &[(&str, f64)]) -> String {
    entries
        .iter()
        .map(|(key, value)| format!("{key}={value:.6}"))
        .collect::<Vec<_>>()
        .join(", ")
}
