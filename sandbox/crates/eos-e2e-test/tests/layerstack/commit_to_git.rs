use anyhow::{Context, Result};
use eos_protocol::ops;
use serde_json::json;

use crate::support::{as_bool, as_str, live_pool_or_skip};

#[test]
fn commit_to_git_commits_overlay_snapshot() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    lease
        .container()
        .exec(&["git", "-C", lease.workspace_root(), "init"])
        .context("git init workspace")?;
    lease.call_ok(
        ops::API_V1_WRITE_FILE,
        json!({
            "path": "git/checkpoint.txt",
            "content": "from layerstack\n",
            "overwrite": true,
        }),
    )?;

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
    assert_eq!(show, "from layerstack");
    Ok(())
}
