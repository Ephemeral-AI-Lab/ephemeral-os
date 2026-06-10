use anyhow::{bail, Context, Result};
use eos_daemon::wire::ops;
use eos_e2e_test::audit::section;
use serde_json::{json, Value};

use crate::support::{as_bool, as_i64, as_str, live_pool_or_skip};

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
    let mut audit = lease.audit_tap()?;
    for version in 0..POST_SQUASH_WRITES {
        lease.call_ok(
            ops::API_V1_WRITE_FILE,
            json!({
                "path": "git/checkpoint.txt",
                "content": format!("from layerstack after squash {version}\n"),
                "overwrite": true,
            }),
        )?;
        lease.call_ok(
            ops::API_V1_WRITE_FILE,
            json!({
                "path": format!("git/noise-{version}.txt"),
                "content": format!("noise {version}\n"),
                "overwrite": true,
            }),
        )?;
    }
    audit.collect()?;
    let squash_completed = audit.count("layer_stack.squash_completed");
    assert!(
        squash_completed >= 2,
        "commit_to_git e2e should run after repeated auto-squash; observed {squash_completed}: {:?}",
        audit.events()
    );
    assert_squash_events_reduced_depth(&audit)?;

    let commit = lease.call_ok(
        ops::API_COMMIT_TO_GIT,
        json!({
            "workspace_root": lease.workspace_root(),
            "paths": ["git/checkpoint.txt"],
            "message": "checkpoint live overlay snapshot",
        }),
    )?;

    assert!(as_bool(&commit, "success")?);
    assert!(as_bool(&commit, "committed")?);
    assert_eq!(
        as_str(&commit, "worktree_mode")?,
        "overlay",
        "live e2e must exercise the overlay-mounted worktree path: {commit}"
    );
    let total_s = timing_s(&commit, "api.commit_to_git.total_s")?;
    let git_add_s = timing_s(&commit, "api.commit_to_git.git_add_s")?;
    let git_commit_s = timing_s(&commit, "api.commit_to_git.git_commit_s")?;
    let overlay_mount_s = timing_s(&commit, "api.commit_to_git.overlay_mount_s")?;
    assert!(
        total_s >= git_add_s + git_commit_s,
        "total commit_to_git timing should cover git phases: {commit}"
    );
    assert!(
        overlay_mount_s >= 0.0,
        "overlay mount timing should be reported: {commit}"
    );
    let committed_depth = timing_s(&commit, "resource.layer_stack.manifest_depth")?;
    let metrics = lease.call_ok(ops::API_LAYER_METRICS, json!({}))?;
    assert_eq!(
        committed_depth as i64,
        as_i64(&metrics, "manifest_depth")?,
        "commit_to_git should report the snapshot depth it committed: commit={commit} metrics={metrics}"
    );
    let commit_sha = as_str(&commit, "commit_sha")?;
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
        "commit_to_git timing: {} squashes={squash_completed} depth={committed_depth}",
        timing_report(&commit)?
    );
    Ok(())
}

fn assert_squash_events_reduced_depth(audit: &eos_e2e_test::audit::AuditTap) -> Result<()> {
    for event in audit.all("layer_stack.squash_completed") {
        let layer_stack = section(event, "layer_stack").context("layer_stack section")?;
        let input = layer_stack
            .get("squash_input_layers")
            .and_then(Value::as_i64)
            .context("squash_input_layers")?;
        let output = layer_stack
            .get("squash_result_layers")
            .and_then(Value::as_i64)
            .context("squash_result_layers")?;
        if output >= input {
            bail!("squash should reduce layer count: {event}");
        }
    }
    Ok(())
}

fn timing_s(value: &Value, key: &str) -> Result<f64> {
    value
        .get("timings")
        .and_then(|timings| timings.get(key))
        .and_then(Value::as_f64)
        .with_context(|| format!("timing {key} missing in {value}"))
}

fn timing_report(value: &Value) -> Result<String> {
    let timings = value
        .get("timings")
        .and_then(Value::as_object)
        .with_context(|| format!("timings missing in {value}"))?;
    let total_s = timing_s(value, "api.commit_to_git.total_s")?;
    let mut commit_phase_entries = Vec::new();
    let mut runtime_entries = Vec::new();
    let mut resource_entries = Vec::new();
    for (key, value) in timings {
        let Some(number) = value.as_f64() else {
            continue;
        };
        if key.starts_with("api.commit_to_git.")
            && key.ends_with("_s")
            && key != "api.commit_to_git.total_s"
        {
            commit_phase_entries.push((key.as_str(), number));
        } else if key.starts_with("runtime.") && key.ends_with("_s") {
            runtime_entries.push((key.as_str(), number));
        } else if key != "api.commit_to_git.total_s" {
            resource_entries.push((key.as_str(), number));
        }
    }
    commit_phase_entries.sort_by_key(|(key, _)| *key);
    runtime_entries.sort_by_key(|(key, _)| *key);
    resource_entries.sort_by_key(|(key, _)| *key);
    let recorded_phase_sum_s = commit_phase_entries
        .iter()
        .map(|(_, value)| *value)
        .sum::<f64>();
    let remainder_s = (total_s - recorded_phase_sum_s).max(0.0);
    Ok(format!(
        "total_s={total_s:.6} commit_phase_sum_s={recorded_phase_sum_s:.6} remainder_s={remainder_s:.6} commit_phases=[{}] outer_runtime=[{}] resources=[{}]",
        format_entries(&commit_phase_entries),
        format_entries(&runtime_entries),
        format_entries(&resource_entries)
    ))
}

fn format_entries(entries: &[(&str, f64)]) -> String {
    entries
        .iter()
        .map(|(key, value)| format!("{key}={value:.6}"))
        .collect::<Vec<_>>()
        .join(", ")
}
