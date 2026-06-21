use super::allocate_overlay_writable_dirs;

#[test]
fn allocates_upper_and_work_dirs() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let run_dir = std::env::temp_dir().join(format!("overlay-test-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&run_dir);

    let dirs = allocate_overlay_writable_dirs(&run_dir)?;
    assert!(dirs.upperdir.is_dir());
    assert!(dirs.workdir.is_dir());

    let _ = std::fs::remove_dir_all(&run_dir);
    Ok(())
}
