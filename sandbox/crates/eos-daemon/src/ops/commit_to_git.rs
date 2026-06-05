//! Commit a LayerStack snapshot into a durable Git repository.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::Instant;

use eos_layerstack::{LayerStack, MergedView, WorkspaceBinding};
use eos_overlay::{
    allocate_overlay_writable_dirs, mount_overlay, overlay_writable_root, OverlayError,
    OverlayHandle, OverlayMount,
};
use serde_json::{json, Value};

use crate::dispatcher::DispatchContext;
use crate::error::DaemonError;
use crate::request_args::{require_string, timings_to_value_map};

pub(crate) fn op_commit_to_git(
    args: &Value,
    _context: DispatchContext<'_>,
) -> Result<Value, DaemonError> {
    let total_start = Instant::now();
    let root = PathBuf::from(require_string(args, "layer_stack_root")?);
    let workspace_root = PathBuf::from(require_string(args, "workspace_root")?);
    let message = require_string(args, "message")?;
    let mut stack = LayerStack::open(root.clone())?;
    let binding = eos_layerstack::require_workspace_binding(&root)?;
    ensure_bound_workspace(&binding, &workspace_root)?;
    let paths = parse_paths(args, &binding)?;
    let git_dir = resolve_git_dir(&workspace_root)?;

    let lease_owner = format!("commit_to_git:{}", uuid::Uuid::new_v4().simple());
    let lease = stack.acquire_snapshot(&lease_owner)?;
    let manifest_version = lease.manifest_version;
    let manifest_root_hash = lease.root_hash.clone();
    let manifest_depth = lease.manifest.depth();
    let manifest_path_count = lease.layer_paths.len();
    let lease_id = lease.lease_id.clone();
    let mut timings = lease.timings.clone();

    let outcome = (|| {
        let worktree = prepare_worktree(&root, &lease, &mut timings)?;
        let git_add_start = Instant::now();
        git_add(&git_dir, worktree.path(), &paths)?;
        record_elapsed(&mut timings, "api.commit_to_git.git_add_s", git_add_start);

        let diff_start = Instant::now();
        let has_changes = git_index_has_changes(&git_dir, worktree.path())?;
        record_elapsed(
            &mut timings,
            "api.commit_to_git.git_diff_cached_s",
            diff_start,
        );
        if !has_changes {
            let commit_sha = current_head(&git_dir, worktree.path())?;
            return Ok(json!({
                "success": true,
                "committed": false,
                "commit_sha": commit_sha,
                "manifest_version": manifest_version,
                "manifest_root_hash": manifest_root_hash,
                "paths": paths,
                "worktree_mode": worktree.mode(),
            }));
        }

        let commit_start = Instant::now();
        git_commit(&git_dir, worktree.path(), &message)?;
        record_elapsed(&mut timings, "api.commit_to_git.git_commit_s", commit_start);
        let commit_sha = current_head(&git_dir, worktree.path())?;
        Ok(json!({
            "success": true,
            "committed": true,
            "commit_sha": commit_sha,
            "manifest_version": manifest_version,
            "manifest_root_hash": manifest_root_hash,
            "paths": paths,
            "worktree_mode": worktree.mode(),
        }))
    })();

    let release = stack.release_lease(&lease_id);
    match (outcome, release) {
        (Ok(mut response), Ok(_)) => {
            timings.insert(
                "resource.layer_stack.manifest_depth".to_owned(),
                manifest_depth as f64,
            );
            timings.insert(
                "resource.layer_stack.manifest_path_count".to_owned(),
                manifest_path_count as f64,
            );
            record_elapsed(&mut timings, "api.commit_to_git.total_s", total_start);
            response["timings"] = Value::Object(timings_to_value_map(&timings));
            Ok(response)
        }
        (Err(err), Ok(_)) => Err(err),
        (Ok(_), Err(err)) | (Err(_), Err(err)) => Err(err.into()),
    }
}

fn ensure_bound_workspace(
    binding: &WorkspaceBinding,
    workspace_root: &Path,
) -> Result<(), DaemonError> {
    let bound = Path::new(&binding.workspace_root);
    if bound != workspace_root {
        return Err(DaemonError::InvalidEnvelope(format!(
            "workspace_root must match LayerStack binding: expected {}, got {}",
            bound.display(),
            workspace_root.display()
        )));
    }
    Ok(())
}

fn parse_paths(args: &Value, binding: &WorkspaceBinding) -> Result<Vec<String>, DaemonError> {
    let Some(value) = args.get("paths") else {
        return Ok(Vec::new());
    };
    match value {
        Value::Null => Ok(Vec::new()),
        Value::String(path) => normalize_pathspec(path, binding)
            .map(|path| path.map_or_else(Vec::new, |path| vec![path])),
        Value::Array(paths) => paths
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .ok_or_else(|| DaemonError::InvalidEnvelope("paths must be strings".to_owned()))
                    .and_then(|path| normalize_pathspec(path, binding))
            })
            .filter_map(|result| match result {
                Ok(Some(path)) => Some(Ok(path)),
                Ok(None) => None,
                Err(err) => Some(Err(err)),
            })
            .collect(),
        _ => Err(DaemonError::InvalidEnvelope(
            "paths must be a string or array of strings".to_owned(),
        )),
    }
}

fn normalize_pathspec(
    raw: &str,
    binding: &WorkspaceBinding,
) -> Result<Option<String>, DaemonError> {
    let trimmed = raw.trim();
    if trimmed.is_empty() || trimmed == "." {
        return Ok(None);
    }
    let path = if trimmed.starts_with('/') {
        binding.layer_path_from_absolute(trimmed)?
    } else {
        binding.layer_path_from_relative(trimmed)?
    };
    if path == ".git" || path.starts_with(".git/") {
        return Err(DaemonError::Forbidden(
            "commit_to_git cannot stage .git paths".to_owned(),
        ));
    }
    Ok(Some(path))
}

struct PreparedWorktree {
    path: PathBuf,
    mode: &'static str,
    mount: Option<OverlayMount>,
    run_dir: PathBuf,
}

impl PreparedWorktree {
    fn path(&self) -> &Path {
        &self.path
    }

    const fn mode(&self) -> &'static str {
        self.mode
    }
}

impl Drop for PreparedWorktree {
    fn drop(&mut self) {
        drop(self.mount.take());
        let _ = std::fs::remove_dir_all(&self.run_dir);
    }
}

fn prepare_worktree(
    root: &Path,
    lease: &eos_layerstack::Lease,
    timings: &mut BTreeMap<String, f64>,
) -> Result<PreparedWorktree, DaemonError> {
    if let Some(worktree) = try_prepare_overlay_worktree(lease, timings)? {
        return Ok(worktree);
    }
    prepare_projected_worktree(root, lease, timings)
}

fn try_prepare_overlay_worktree(
    lease: &eos_layerstack::Lease,
    timings: &mut BTreeMap<String, f64>,
) -> Result<Option<PreparedWorktree>, DaemonError> {
    let writable_root = match overlay_writable_root() {
        Ok(root) => root,
        Err(OverlayError::WritableRootUnavailable(_)) | Err(OverlayError::Unsupported) => {
            return Ok(None);
        }
        Err(err) => return Err(overlay_error("prepare overlay writable root", err)),
    };
    let run_dir = writable_root
        .join("commit-to-git")
        .join(uuid::Uuid::new_v4().simple().to_string());
    std::fs::create_dir_all(&run_dir)?;
    let dirs = allocate_overlay_writable_dirs(&run_dir)
        .map_err(|err| overlay_error("allocate commit_to_git overlay dirs", err))?;
    let mountpoint = run_dir.join("worktree");
    std::fs::create_dir_all(&mountpoint)?;
    let mount_start = Instant::now();
    let mount = match mount_overlay(
        &mountpoint,
        &OverlayHandle {
            upperdir: dirs.upperdir,
            workdir: dirs.workdir,
            layer_paths: lease.layer_paths.iter().map(PathBuf::from).collect(),
        },
    ) {
        Ok(mount) => mount,
        Err(OverlayError::Unsupported) => {
            let _ = std::fs::remove_dir_all(&run_dir);
            return Ok(None);
        }
        Err(err) => {
            let _ = std::fs::remove_dir_all(&run_dir);
            return Err(overlay_error("mount commit_to_git worktree", err));
        }
    };
    record_elapsed(timings, "api.commit_to_git.overlay_mount_s", mount_start);
    Ok(Some(PreparedWorktree {
        path: mountpoint,
        mode: "overlay",
        mount: Some(mount),
        run_dir,
    }))
}

fn prepare_projected_worktree(
    root: &Path,
    lease: &eos_layerstack::Lease,
    timings: &mut BTreeMap<String, f64>,
) -> Result<PreparedWorktree, DaemonError> {
    let run_dir = std::env::temp_dir().join(format!(
        "eos-commit-to-git-{}-{}",
        std::process::id(),
        uuid::Uuid::new_v4().simple()
    ));
    let worktree = run_dir.join("worktree");
    let project_start = Instant::now();
    MergedView::new(root.to_path_buf()).project(&worktree, &lease.manifest)?;
    record_elapsed(
        timings,
        "api.commit_to_git.project_worktree_s",
        project_start,
    );
    Ok(PreparedWorktree {
        path: worktree,
        mode: "projection",
        mount: None,
        run_dir,
    })
}

fn resolve_git_dir(workspace_root: &Path) -> Result<PathBuf, DaemonError> {
    let output = Command::new("git")
        .arg("-C")
        .arg(workspace_root)
        .arg("-c")
        .arg("safe.directory=*")
        .args(["rev-parse", "--absolute-git-dir"])
        .output()?;
    if !output.status.success() {
        return Err(DaemonError::InvalidEnvelope(format!(
            "workspace_root must be a git repository: {}",
            command_stderr(&output)
        )));
    }
    let path = command_stdout(&output);
    if path.is_empty() {
        return Err(DaemonError::InvalidEnvelope(
            "git rev-parse returned an empty git dir".to_owned(),
        ));
    }
    Ok(PathBuf::from(path))
}

fn git_add(git_dir: &Path, worktree: &Path, paths: &[String]) -> Result<(), DaemonError> {
    let mut args = vec!["add", "-A", "--"];
    if paths.is_empty() {
        args.push(".");
    } else {
        args.extend(paths.iter().map(String::as_str));
    }
    run_git_checked(git_dir, worktree, &args).map(|_| ())
}

fn git_index_has_changes(git_dir: &Path, worktree: &Path) -> Result<bool, DaemonError> {
    let output = run_git(
        git_dir,
        worktree,
        &["diff", "--cached", "--quiet", "--exit-code"],
    )?;
    match output.status.code() {
        Some(0) => Ok(false),
        Some(1) => Ok(true),
        _ => Err(git_error("git diff --cached", &output)),
    }
}

fn git_commit(git_dir: &Path, worktree: &Path, message: &str) -> Result<(), DaemonError> {
    run_git_checked(git_dir, worktree, &["commit", "-m", message]).map(|_| ())
}

fn current_head(git_dir: &Path, worktree: &Path) -> Result<Option<String>, DaemonError> {
    let output = run_git(git_dir, worktree, &["rev-parse", "--verify", "HEAD"])?;
    if output.status.success() {
        return Ok(Some(command_stdout(&output)));
    }
    Ok(None)
}

fn run_git_checked(git_dir: &Path, worktree: &Path, args: &[&str]) -> Result<Output, DaemonError> {
    let output = run_git(git_dir, worktree, args)?;
    if output.status.success() {
        Ok(output)
    } else {
        Err(git_error(&format!("git {}", args.join(" ")), &output))
    }
}

fn run_git(git_dir: &Path, worktree: &Path, args: &[&str]) -> Result<Output, DaemonError> {
    Ok(Command::new("git")
        .arg("-c")
        .arg("safe.directory=*")
        .env("GIT_DIR", git_dir)
        .env("GIT_WORK_TREE", worktree)
        .env("GIT_AUTHOR_NAME", "EphemeralOS")
        .env("GIT_AUTHOR_EMAIL", "ephemeralos@example.invalid")
        .env("GIT_COMMITTER_NAME", "EphemeralOS")
        .env("GIT_COMMITTER_EMAIL", "ephemeralos@example.invalid")
        .args(args)
        .output()?)
}

fn git_error(command: &str, output: &Output) -> DaemonError {
    DaemonError::OverlayPipeline(format!(
        "{command} failed with status {}: {}",
        output.status,
        command_stderr(output)
    ))
}

fn overlay_error(context: &str, error: OverlayError) -> DaemonError {
    DaemonError::OverlayPipeline(format!("{context}: {error}"))
}

fn command_stdout(output: &Output) -> String {
    String::from_utf8_lossy(&output.stdout).trim().to_owned()
}

fn command_stderr(output: &Output) -> String {
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if stderr.is_empty() {
        String::from_utf8_lossy(&output.stdout).trim().to_owned()
    } else {
        stderr
    }
}

fn record_elapsed(timings: &mut BTreeMap<String, f64>, key: &str, start: Instant) {
    timings.insert(key.to_owned(), start.elapsed().as_secs_f64());
}

#[cfg(test)]
mod tests {
    use std::sync::atomic::{AtomicU64, Ordering};

    use eos_layerstack::LayerChange;
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

        let response = op_commit_to_git(
            &json!({
                "layer_stack_root": fixture.root,
                "workspace_root": fixture.workspace,
                "paths": ["checkpoint/included.txt"],
                "message": "checkpoint selected path",
            }),
            DispatchContext::empty(),
        )?;

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
        let response = op_commit_to_git(
            &json!({
                "layer_stack_root": fixture.root,
                "workspace_root": fixture.workspace,
                "paths": [".git/config"],
                "message": "bad checkpoint",
            }),
            DispatchContext::empty(),
        );

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
}
