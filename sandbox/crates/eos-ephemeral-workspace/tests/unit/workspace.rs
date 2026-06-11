use super::*;

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

fn scratch(label: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("eos-ephemeral-ws-{label}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    dir
}

#[test]
fn create_capture_and_drop_cleans_scratch() -> TestResult {
    let scratch = scratch("lifecycle");
    let ws = EphemeralWorkspace::create(
        &scratch,
        "command",
        "inv-1",
        PathBuf::from("/workspace"),
        vec![PathBuf::from("/stack/layers/a")],
    )?;
    let run_dir = ws.dirs().run_dir.clone();
    std::fs::create_dir_all(ws.dirs().upperdir.join("nested"))?;
    std::fs::write(ws.dirs().upperdir.join("nested/new.txt"), b"hello")?;

    let plan = ws.mount_plan();
    assert_eq!(plan.workspace_root, Path::new("/workspace"));
    assert_eq!(plan.layer_paths, &[PathBuf::from("/stack/layers/a")]);

    let captured = ws.capture()?;
    assert_eq!(captured.changes.len(), 1, "one written path captured");
    assert_eq!(captured.path_kinds[0].path, "nested/new.txt");
    assert_eq!(
        captured.path_kinds[0].kind,
        crate::PathChangeKind::Write,
        "regular file classifies as write"
    );
    assert!(captured.stats.bytes >= 5, "stats count written bytes");

    drop(ws);
    assert!(!run_dir.exists(), "drop removes the run dir");
    let _ = std::fs::remove_dir_all(scratch);
    Ok(())
}

#[test]
fn allocator_sanitizes_unsafe_segments() -> TestResult {
    let scratch = scratch("sanitize");
    let dirs = DirAllocator::new(scratch.clone()).allocate("a/b", "../evil")?;
    assert!(
        dirs.run_dir.starts_with(scratch.join("a_b")),
        "kind sanitized"
    );
    let leaf = dirs
        .run_dir
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default();
    assert!(
        leaf.ends_with("-.._evil"),
        "token slash flattened into one safe segment: {leaf}"
    );
    let _ = std::fs::remove_dir_all(scratch);
    Ok(())
}
