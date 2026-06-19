use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::{SystemTime, UNIX_EPOCH};

fn temp_root(name: &str) -> PathBuf {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after Unix epoch")
        .as_nanos();
    std::env::temp_dir().join(format!("eos-xtask-{name}-{}-{nonce}", std::process::id()))
}

fn run_check(root: &Path, max_lines: &str) -> Output {
    Command::new(env!("CARGO_BIN_EXE_xtask"))
        .args(["check-crate-source-size", "--root"])
        .arg(root)
        .args(["--max-lines", max_lines])
        .output()
        .expect("xtask command should run")
}

fn body_with_lines(line_count: usize) -> String {
    (0..line_count)
        .map(|index| format!("pub const LINE_{index}: usize = {index};"))
        .collect::<Vec<_>>()
        .join("\n")
}

fn write_manifest(crate_root: &Path) {
    fs::write(
        crate_root.join("Cargo.toml"),
        "[package]\nname = \"fixture\"\nversion = \"0.0.0\"\nedition = \"2021\"\n",
    )
    .expect("write fixture manifest");
}

#[test]
fn rejects_oversized_rust_file_under_crate_src() {
    let root = temp_root("oversized-crate-src");
    let crate_root = root.join("crates/fixture");
    let src = crate_root.join("src/deep/module");
    fs::create_dir_all(&src).expect("create source dir");
    write_manifest(&crate_root);
    fs::write(src.join("large.rs"), body_with_lines(4)).expect("write source file");

    let output = run_check(&root, "3");

    fs::remove_dir_all(&root).expect("remove temp root");
    assert!(!output.status.success(), "oversized crate src should fail");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("large.rs"), "{stderr}");
    assert!(stderr.contains("4 lines"), "{stderr}");
}

#[test]
fn allows_file_at_line_limit() {
    let root = temp_root("crate-src-at-limit");
    let crate_root = root.join("crates/fixture");
    let src = crate_root.join("src");
    fs::create_dir_all(&src).expect("create source dir");
    write_manifest(&crate_root);
    fs::write(src.join("lib.rs"), body_with_lines(3)).expect("write source file");

    let output = run_check(&root, "3");

    fs::remove_dir_all(&root).expect("remove temp root");
    assert!(
        output.status.success(),
        "files at the limit should pass: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn ignores_large_rust_file_outside_crate_src() {
    let root = temp_root("large-outside-crate-src");
    let tests = root.join("crates/fixture/tests");
    fs::create_dir_all(&tests).expect("create tests dir");
    fs::write(tests.join("large.rs"), body_with_lines(4)).expect("write test file");

    let output = run_check(&root, "3");

    fs::remove_dir_all(&root).expect("remove temp root");
    assert!(
        output.status.success(),
        "files outside crate src should be ignored: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}
