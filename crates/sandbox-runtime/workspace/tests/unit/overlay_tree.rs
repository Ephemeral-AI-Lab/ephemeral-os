use sandbox_runtime_workspace::overlay::tree::TreeResourceStats;

#[test]
fn collect_with_entry_limit_marks_real_truncation() {
    let root = std::env::temp_dir().join(format!("workspace-tree-limit-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(root.join("dir")).expect("create tree");
    std::fs::write(root.join("a.txt"), b"a").expect("write file");
    std::fs::write(root.join("dir").join("b.txt"), b"b").expect("write nested file");

    let stats = TreeResourceStats::collect_with_entry_limit(&root, 2);

    assert!(stats.truncated);
    assert!(stats.dirs >= 1);
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn collect_records_first_failing_path() {
    let root = std::env::temp_dir().join(format!("workspace-tree-missing-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);

    let stats = TreeResourceStats::collect(&root);

    assert_eq!(stats.read_error_count, 1);
    assert_eq!(stats.first_error_path, Some(root.display().to_string()));
}
