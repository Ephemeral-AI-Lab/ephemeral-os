//! Checkpoint pipeline behavior: pathspec staging policy, committed/no-op
//! outcome semantics, and `.git` refusal. The daemon's wire response shaping
//! over these outcomes is covered by the daemon's adapter tests.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

use eos_checkpoint::{commit_to_git, CheckpointError, CommitRequest};
use eos_layerstack::{LayerChange, LayerStack};
use serde_json::json;

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn commit_to_git_commits_selected_snapshot_paths() -> TestResult {
    let fixture = Fixture::new("selected")?;
    LayerStack::open(fixture.root.clone())?.publish_layer(&[
        LayerChange::Write {
            path: eos_layerstack::LayerPath::parse("checkpoint/included.txt")?,
            content: b"included\n".to_vec(),
        },
        LayerChange::Write {
            path: eos_layerstack::LayerPath::parse("checkpoint/excluded.txt")?,
            content: b"excluded\n".to_vec(),
        },
    ])?;

    let outcome = commit_to_git(&CommitRequest {
        layer_stack_root: &fixture.root,
        workspace_root: &fixture.workspace,
        message: "checkpoint selected path",
        raw_paths: vec!["checkpoint/included.txt".to_owned()],
    })?;

    assert!(outcome.committed);
    let commit_sha = outcome.commit_sha.as_deref().ok_or("commit sha")?;
    assert_eq!(
        git_show(&fixture.workspace, commit_sha, "checkpoint/included.txt")?,
        "included"
    );
    assert!(
        git_show(&fixture.workspace, commit_sha, "checkpoint/excluded.txt").is_err(),
        "excluded path should not be staged into the checkpoint commit"
    );
    Ok(())
}

#[test]
fn commit_to_git_reports_noop_recommit_with_prior_head() -> TestResult {
    let fixture = Fixture::new("noop")?;
    LayerStack::open(fixture.root.clone())?.publish_layer(&[LayerChange::Write {
        path: eos_layerstack::LayerPath::parse("checkpoint/included.txt")?,
        content: b"included\n".to_vec(),
    }])?;
    let request = || CommitRequest {
        layer_stack_root: &fixture.root,
        workspace_root: &fixture.workspace,
        message: "checkpoint shape",
        raw_paths: vec!["checkpoint/included.txt".to_owned()],
    };

    // committed = true: a fresh path projects, stages, and commits.
    let committed = commit_to_git(&request())?;
    assert!(committed.committed);
    assert!(committed.commit_sha.is_some(), "commit_sha present");
    assert_eq!(committed.manifest_version, 2);
    assert_eq!(committed.paths, vec!["checkpoint/included.txt".to_owned()]);
    assert!(
        matches!(committed.worktree_mode, "overlay" | "projection"),
        "worktree mode: {}",
        committed.worktree_mode
    );
    assert!(
        committed.timings.contains_key("api.commit_to_git.total_s"),
        "total timing recorded"
    );

    // committed = false: re-committing the same staged paths is a no-op that
    // still reports the prior HEAD and the full outcome.
    let noop = commit_to_git(&request())?;
    assert!(!noop.committed);
    assert_eq!(noop.commit_sha, committed.commit_sha);
    assert_eq!(noop.manifest_version, 2);
    assert_eq!(noop.manifest_root_hash, committed.manifest_root_hash);
    assert_eq!(noop.paths, vec!["checkpoint/included.txt".to_owned()]);
    Ok(())
}

#[test]
fn commit_to_git_rejects_git_pathspecs() -> TestResult {
    let fixture = Fixture::new("reject-git")?;
    let outcome = commit_to_git(&CommitRequest {
        layer_stack_root: &fixture.root,
        workspace_root: &fixture.workspace,
        message: "bad checkpoint",
        raw_paths: vec![".git/config".to_owned()],
    });

    assert!(matches!(outcome, Err(CheckpointError::Forbidden(_))));
    Ok(())
}

struct Fixture {
    base: PathBuf,
    root: PathBuf,
    workspace: PathBuf,
}

impl Fixture {
    fn new(label: &str) -> TestResult<Self> {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        let base = std::env::temp_dir().join(format!(
            "eos-checkpoint-{label}-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&base);
        let root = base.join("layer-stack");
        let workspace = base.join("workspace");
        let layer = root.join("layers").join("B000001-base");
        std::fs::create_dir_all(&layer)?;
        std::fs::create_dir_all(root.join("staging"))?;
        std::fs::create_dir_all(&workspace)?;
        std::fs::write(layer.join("README.md"), "# README\n")?;
        std::fs::write(
            root.join("manifest.json"),
            serde_json::to_string_pretty(&json!({
                "schema_version": 1,
                "version": 1,
                "layers": [{"layer_id": "B000001-base", "path": "layers/B000001-base"}],
            }))?,
        )?;
        std::fs::write(
            root.join("workspace.json"),
            serde_json::to_string_pretty(&json!({
                "workspace_root": workspace,
                "layer_stack_root": root,
                "active_manifest_version": 1,
                "active_root_hash": "root",
                "base_manifest_version": 1,
                "base_root_hash": "base",
            }))?,
        )?;
        run_git_init(&workspace)?;
        Ok(Self {
            base,
            root,
            workspace,
        })
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.base);
    }
}

fn run_git_init(workspace: &Path) -> TestResult {
    let output = Command::new("git")
        .arg("-C")
        .arg(workspace)
        .arg("init")
        .output()?;
    if output.status.success() {
        Ok(())
    } else {
        Err(format!("git init failed: {}", command_stderr(&output)).into())
    }
}

fn git_show(workspace: &Path, commit_sha: &str, path: &str) -> TestResult<String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(workspace)
        .arg("show")
        .arg(format!("{commit_sha}:{path}"))
        .output()?;
    if output.status.success() {
        Ok(command_stdout(&output))
    } else {
        Err(format!("git show failed: {}", command_stderr(&output)).into())
    }
}

fn command_stdout(output: &std::process::Output) -> String {
    String::from_utf8_lossy(&output.stdout).trim().to_owned()
}

fn command_stderr(output: &std::process::Output) -> String {
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if stderr.is_empty() {
        command_stdout(output)
    } else {
        stderr
    }
}
