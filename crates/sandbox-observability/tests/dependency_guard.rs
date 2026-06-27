#[test]
fn observability_leaf_excludes_runtime_and_daemon_dependencies() {
    let manifest = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml"),
    )
    .expect("read observability manifest");
    let dependencies = manifest_section(&manifest, "[dependencies]");
    for forbidden in ["sandbox-runtime", "sandbox-daemon", "sandbox-manager"] {
        assert!(
            !dependencies.contains(forbidden),
            "observability leaf crate must not depend on {forbidden}"
        );
    }
}

fn manifest_section<'a>(manifest: &'a str, section: &str) -> &'a str {
    let Some(start) = manifest.find(section) else {
        return "";
    };
    let body = &manifest[start + section.len()..];
    let end = body.find("\n[").unwrap_or(body.len());
    &body[..end]
}
