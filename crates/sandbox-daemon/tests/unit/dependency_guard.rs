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

#[test]
fn forbidden_runtime_telemetry_infrastructure_is_absent() {
    let workspace_root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .expect("workspace root");

    for forbidden in [
        "crates/sandbox-runtime-trace",
        "crates/sandbox-runtime/operation/src/internal/telemetry.rs",
    ] {
        assert!(
            !workspace_root.join(forbidden).exists(),
            "forbidden telemetry infrastructure exists: {forbidden}"
        );
    }

    for relative in telemetry_boundary_source_and_manifest_files(workspace_root) {
        let text = std::fs::read_to_string(&relative).expect("read source or manifest");
        for forbidden in [
            "TelemetryConfig",
            "TelemetrySink",
            "TelemetryOutputStream",
            "DaemonServeMode",
            "tracing_subscriber",
            "SubscriberInitExt",
            "set_global_default",
            "opentelemetry",
            "otlp",
        ] {
            assert!(
                !text.contains(forbidden),
                "runtime/config boundary must not define telemetry DTOs or subscriber/exporter setup: {} contains {forbidden}",
                relative.display()
            );
        }
    }
}

fn telemetry_boundary_source_and_manifest_files(
    workspace_root: &std::path::Path,
) -> Vec<std::path::PathBuf> {
    let mut files = Vec::new();
    for root in ["crates/sandbox-runtime", "crates/sandbox-config"] {
        collect_telemetry_boundary_files(&workspace_root.join(root), &mut files);
    }
    files
}

fn collect_telemetry_boundary_files(path: &std::path::Path, files: &mut Vec<std::path::PathBuf>) {
    let entries = std::fs::read_dir(path).expect("read telemetry boundary crate directory");
    for entry in entries {
        let entry = entry.expect("read telemetry boundary crate entry");
        let path = entry.path();
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if path.is_dir() {
            if matches!(name.as_ref(), "target" | "tests") {
                continue;
            }
            collect_telemetry_boundary_files(&path, files);
            continue;
        }
        if name == "Cargo.toml" || path.extension().is_some_and(|extension| extension == "rs") {
            files.push(path);
        }
    }
}
