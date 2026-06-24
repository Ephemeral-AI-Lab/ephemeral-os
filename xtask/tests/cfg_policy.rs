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

fn run_check(root: &Path) -> Output {
    Command::new(env!("CARGO_BIN_EXE_xtask"))
        .args(["check-cfg", "--root"])
        .arg(root)
        .output()
        .expect("xtask command should run")
}

#[test]
fn rejects_platform_cfg_attribute_in_source() {
    let root = temp_root("source-platform-cfg");
    let src = root.join("crate/src");
    fs::create_dir_all(&src).expect("create source dir");
    fs::write(
        src.join("lib.rs"),
        r#"
#[cfg(unix)]
fn unix_only() {}
"#,
    )
    .expect("write source file");

    let output = run_check(&root);

    fs::remove_dir_all(&root).expect("remove temp root");
    assert!(!output.status.success(), "#[cfg(unix)] should fail");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("#[cfg(unix)]"), "{stderr}");
}

#[test]
fn rejects_target_os_cfg_attribute_in_source() {
    let root = temp_root("source-target-os-cfg");
    let src = root.join("crate/src");
    fs::create_dir_all(&src).expect("create source dir");
    fs::write(
        src.join("lib.rs"),
        r#"
#[cfg(not(target_os = "linux"))]
fn elsewhere() {}
"#,
    )
    .expect("write source file");

    let output = run_check(&root);

    fs::remove_dir_all(&root).expect("remove temp root");
    assert!(!output.status.success(), "target_os cfg should fail");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("target_os"), "{stderr}");
}

#[test]
fn rejects_feature_cfg_attribute_in_source() {
    let root = temp_root("source-feature-cfg");
    let src = root.join("crate/src");
    fs::create_dir_all(&src).expect("create source dir");
    fs::write(
        src.join("lib.rs"),
        r#"
#[cfg(feature = "test-support")]
pub fn helper_for_test() {}
"#,
    )
    .expect("write source file");

    let output = run_check(&root);

    fs::remove_dir_all(&root).expect("remove temp root");
    assert!(!output.status.success(), "feature cfg gate should fail");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("test-support"), "{stderr}");
}

#[test]
fn rejects_inner_cfg_attribute_in_source() {
    let root = temp_root("source-inner-cfg");
    let src = root.join("crate/src");
    fs::create_dir_all(&src).expect("create source dir");
    fs::write(
        src.join("lib.rs"),
        r#"
#![cfg(feature = "experimental")]
"#,
    )
    .expect("write source file");

    let output = run_check(&root);

    fs::remove_dir_all(&root).expect("remove temp root");
    assert!(!output.status.success(), "inner #![cfg(...)] should fail");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("experimental"), "{stderr}");
}

#[test]
fn rejects_cfg_attr_attribute_in_source() {
    let root = temp_root("source-cfg-attr");
    let src = root.join("crate/src");
    fs::create_dir_all(&src).expect("create source dir");
    fs::write(
        src.join("lib.rs"),
        r#"
#[cfg_attr(target_os = "linux", repr(C))]
struct Packet([u8; 4]);
"#,
    )
    .expect("write source file");

    let output = run_check(&root);

    fs::remove_dir_all(&root).expect("remove temp root");
    assert!(!output.status.success(), "#[cfg_attr(...)] should fail");
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("cfg_attr"), "{stderr}");
}

#[test]
fn rejects_multiline_cfg_attribute_in_source() {
    let root = temp_root("source-multiline-cfg");
    let src = root.join("crate/src");
    fs::create_dir_all(&src).expect("create source dir");
    fs::write(
        src.join("lib.rs"),
        r#"
#[cfg(any(
    target_os = "linux",
    target_os = "macos"
))]
fn portable() {}
"#,
    )
    .expect("write source file");

    let output = run_check(&root);

    fs::remove_dir_all(&root).expect("remove temp root");
    assert!(
        !output.status.success(),
        "cfg attributes spanning multiple lines should fail"
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("cfg"), "{stderr}");
}

#[test]
fn ignores_cfg_macro_invocations() {
    let root = temp_root("source-cfg-macro");
    let src = root.join("crate/src");
    fs::create_dir_all(&src).expect("create source dir");
    fs::write(
        src.join("lib.rs"),
        r#"
fn target() -> bool {
    cfg!(target_os = "linux")
}
"#,
    )
    .expect("write source file");

    let output = run_check(&root);

    fs::remove_dir_all(&root).expect("remove temp root");
    assert!(
        output.status.success(),
        "the cfg!() macro is not a #[cfg] attribute and should be allowed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn allows_sources_without_cfg() {
    let root = temp_root("source-no-cfg");
    let src = root.join("crate/src");
    fs::create_dir_all(&src).expect("create source dir");
    fs::write(
        src.join("lib.rs"),
        r#"
pub fn add(a: u32, b: u32) -> u32 {
    a + b
}
"#,
    )
    .expect("write source file");

    let output = run_check(&root);

    fs::remove_dir_all(&root).expect("remove temp root");
    assert!(
        output.status.success(),
        "cfg-free sources should pass: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn allows_cfg_in_crate_root_tests_directory() {
    let root = temp_root("crate-tests-cfg");
    let crate_root = root.join("crate");
    let tests = crate_root.join("tests");
    fs::create_dir_all(&tests).expect("create tests dir");
    fs::write(
        crate_root.join("Cargo.toml"),
        "[package]\nname = \"fake\"\n",
    )
    .expect("write manifest");
    fs::write(
        tests.join("unit.rs"),
        r#"
#[cfg(unix)]
#[test]
fn unix_only_test() {}
"#,
    )
    .expect("write test file");

    let output = run_check(&root);

    fs::remove_dir_all(&root).expect("remove temp root");
    assert!(
        output.status.success(),
        "crate-root tests/ directories may use #[cfg]: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn sandbox_daemon_sources_are_free_of_cfg() {
    let output = Command::new(env!("CARGO_BIN_EXE_xtask"))
        .arg("check-cfg")
        .output()
        .expect("xtask command should run");

    assert!(
        output.status.success(),
        "sandbox-daemon production sources must stay free of #[cfg]/#[cfg_attr]; \
move platform- and feature-specific code into focused helpers or out of src:\n{}",
        String::from_utf8_lossy(&output.stderr)
    );
}
