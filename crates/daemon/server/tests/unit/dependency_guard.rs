#[test]
fn daemon_manifest_excludes_host_store_and_sqlite_dependencies() {
    let manifest = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml"),
    )
    .expect("read daemon manifest");
    for forbidden in ["rusqlite", "host"] {
        assert!(
            !manifest.contains(forbidden),
            "daemon hot path must not depend on {forbidden}"
        );
    }
}
