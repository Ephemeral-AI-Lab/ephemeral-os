use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc;
use std::time::Duration;

use super::*;
use crate::fs::{clear_storage_root_preserving_lock_and_names, remove_path, write_manifest};
use crate::workspace_base::{
    build_workspace_base, build_workspace_base_from_snapshot, read_workspace_binding,
};

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn commit_workspace_recovery_installs_workspace_replaced_journal() -> TestResult {
    let fixture = CommitFixture::new("recover-install")?;
    std::fs::write(fixture.workspace.join("tracked.txt"), "base\n")?;
    build_workspace_base(&fixture.root, &fixture.workspace, false)?;
    std::fs::create_dir_all(&fixture.snapshot)?;
    std::fs::write(fixture.snapshot.join("tracked.txt"), "committed\n")?;
    let stack = LayerStack::open(fixture.root.clone())?;
    let staged = stack.commit_staged_storage_dir()?;
    build_workspace_base_from_snapshot(
        &staged,
        &fixture.root,
        &fixture.workspace,
        &fixture.snapshot,
        false,
    )?;
    write_commit_workspace_journal(
        &fixture.root,
        CommitWorkspacePhase::WorkspaceReplaced,
        &staged,
    )?;
    clear_storage_root_preserving_lock_and_names(&fixture.root, &[COMMIT_WORKSPACE_JOURNAL_FILE])?;
    drop(stack);

    let recovered = LayerStack::open(fixture.root.clone())?;

    assert_eq!(
        recovered.read_text("tracked.txt")?,
        ("committed\n".to_owned(), true)
    );
    let binding = read_workspace_binding(&fixture.root)?.expect("binding is recovered");
    assert_eq!(binding.workspace_root, fixture.workspace.to_string_lossy());
    assert_eq!(binding.layer_stack_root, fixture.root.to_string_lossy());
    assert!(!staged.exists(), "staged storage is removed after recovery");
    assert!(
        !fixture.root.join(COMMIT_WORKSPACE_JOURNAL_FILE).exists(),
        "commit journal is removed after recovery"
    );
    Ok(())
}

#[test]
fn commit_workspace_recovery_discards_unreplaced_staged_journal() -> TestResult {
    let fixture = CommitFixture::new("recover-staged")?;
    std::fs::write(fixture.workspace.join("tracked.txt"), "base\n")?;
    build_workspace_base(&fixture.root, &fixture.workspace, false)?;
    std::fs::create_dir_all(&fixture.snapshot)?;
    std::fs::write(fixture.snapshot.join("tracked.txt"), "committed\n")?;
    let stack = LayerStack::open(fixture.root.clone())?;
    let staged = stack.commit_staged_storage_dir()?;
    build_workspace_base_from_snapshot(
        &staged,
        &fixture.root,
        &fixture.workspace,
        &fixture.snapshot,
        false,
    )?;
    write_commit_workspace_journal(&fixture.root, CommitWorkspacePhase::Staged, &staged)?;
    drop(stack);

    let recovered = LayerStack::open(fixture.root.clone())?;

    assert_eq!(
        recovered.read_text("tracked.txt")?,
        ("base\n".to_owned(), true)
    );
    assert!(
        !staged.exists(),
        "pre-replace staged storage is discarded during recovery"
    );
    assert!(
        !fixture.root.join(COMMIT_WORKSPACE_JOURNAL_FILE).exists(),
        "pre-replace journal is removed during recovery"
    );
    Ok(())
}

#[test]
fn commit_workspace_recovery_retries_mid_replacement_journal() -> TestResult {
    let fixture = CommitFixture::new("recover-replacing")?;
    std::fs::write(fixture.workspace.join("tracked.txt"), "base\n")?;
    build_workspace_base(&fixture.root, &fixture.workspace, false)?;
    std::fs::create_dir_all(&fixture.snapshot)?;
    std::fs::write(fixture.snapshot.join("tracked.txt"), "committed\n")?;
    std::fs::write(fixture.snapshot.join("new.txt"), "new\n")?;
    let stack = LayerStack::open(fixture.root.clone())?;
    let staged = stack.commit_staged_storage_dir()?;
    build_workspace_base_from_snapshot(
        &staged,
        &fixture.root,
        &fixture.workspace,
        &fixture.snapshot,
        false,
    )?;
    write_commit_workspace_journal(
        &fixture.root,
        CommitWorkspacePhase::ReplacingWorkspace {
            workspace_root: fixture.workspace.to_string_lossy().into_owned(),
        },
        &staged,
    )?;
    std::fs::write(
        fixture.workspace.join("tracked.txt"),
        "partially replaced\n",
    )?;
    drop(stack);

    let recovered = LayerStack::open(fixture.root.clone())?;

    assert_eq!(
        std::fs::read_to_string(fixture.workspace.join("tracked.txt"))?,
        "committed\n"
    );
    assert_eq!(
        std::fs::read_to_string(fixture.workspace.join("new.txt"))?,
        "new\n"
    );
    assert_eq!(
        recovered.read_text("tracked.txt")?,
        ("committed\n".to_owned(), true)
    );
    assert!(!staged.exists(), "staged storage is removed after recovery");
    assert!(
        !fixture.root.join(COMMIT_WORKSPACE_JOURNAL_FILE).exists(),
        "commit journal is removed after recovery"
    );
    Ok(())
}

#[test]
fn active_manifest_reads_wait_for_exclusive_storage_replacement() -> TestResult {
    let fixture = CommitFixture::new("read-blocks-replace")?;
    std::fs::write(fixture.workspace.join("tracked.txt"), "base\n")?;
    build_workspace_base(&fixture.root, &fixture.workspace, false)?;
    let stack = LayerStack::open(fixture.root.clone())?;
    let exclusive = stack.writer_lock.exclusive()?;
    remove_path(&fixture.root.join(ACTIVE_MANIFEST_FILE))?;

    let (version_tx, version_rx) = mpsc::channel();
    let root = fixture.root.clone();
    let reader = std::thread::spawn(move || -> TestResult {
        let version = LayerStack::open(root)?.read_active_manifest()?.version;
        version_tx.send(version)?;
        Ok(())
    });

    assert!(
        version_rx.recv_timeout(Duration::from_millis(50)).is_err(),
        "active manifest read observed transient storage state while exclusive replacement was held"
    );
    let manifest = Manifest::new(7, Vec::new(), crate::model::MANIFEST_SCHEMA_VERSION)?;
    write_manifest(fixture.root.join(ACTIVE_MANIFEST_FILE), &manifest)?;
    drop(exclusive);

    assert_eq!(version_rx.recv_timeout(Duration::from_secs(1))?, 7);
    reader
        .join()
        .map_err(|_| std::io::Error::other("reader thread panicked"))??;
    Ok(())
}

struct CommitFixture {
    root: PathBuf,
    workspace: PathBuf,
    snapshot: PathBuf,
}

impl CommitFixture {
    fn new(label: &str) -> TestResult<Self> {
        let base = std::env::temp_dir().join(format!(
            "layerstack-commit-{label}-{}-{}",
            std::process::id(),
            NEXT_COMMIT_TEST.fetch_add(1, Ordering::Relaxed)
        ));
        let root = base.join("layer-stack");
        let workspace = base.join("workspace");
        let snapshot = base.join("snapshot");
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&workspace)?;
        Ok(Self {
            root,
            workspace,
            snapshot,
        })
    }
}

impl Drop for CommitFixture {
    fn drop(&mut self) {
        if let Some(base) = self.root.parent() {
            let _ = std::fs::remove_dir_all(base);
        }
    }
}

static NEXT_COMMIT_TEST: AtomicU64 = AtomicU64::new(0);
