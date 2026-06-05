use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

use eos_layerstack::{LayerChange, LayerStack};
use serde_json::json;

use super::*;

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

    let response = commit_to_git(&json!({
        "layer_stack_root": fixture.root,
        "workspace_root": fixture.workspace,
        "paths": ["checkpoint/included.txt"],
        "message": "checkpoint selected path",
    }))?;

    assert_eq!(response["success"], json!(true));
    assert_eq!(response["committed"], json!(true));
    let commit_sha = response["commit_sha"].as_str().ok_or("commit sha")?;
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
fn commit_to_git_rejects_git_pathspecs() -> TestResult {
    let fixture = Fixture::new("reject-git")?;
    let response = commit_to_git(&json!({
        "layer_stack_root": fixture.root,
        "workspace_root": fixture.workspace,
        "paths": [".git/config"],
        "message": "bad checkpoint",
    }));

    assert!(matches!(response, Err(DaemonError::Forbidden(_))));
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
            "eosd-commit-to-git-{label}-{}-{}",
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
