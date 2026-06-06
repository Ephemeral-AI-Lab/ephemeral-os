//! AC-llm-client-09: the crate must not port the dropped legacy surface.
//!
//! Asserts (a) no `src/**/*.rs` source mentions a `class_path` or
//! credential-store symbol, and (b) `Cargo.toml` pulls in no provider SDK. These
//! are the absence guarantees behind GC-llm-client-05 (anchor §2): no
//! `class_path` importlib dispatch, no OAuth credential-store strategy, and
//! direct `reqwest` only.

use std::fs;
use std::path::Path;

/// Lowercase symbols that must never appear in this crate's source.
const FORBIDDEN_SYMBOLS: &[&str] = &["class_path", "keychain"];

/// Provider SDK crate names that must never appear as dependencies.
const FORBIDDEN_DEPS: &[&str] = &["anthropic", "async-openai", "async_openai"];

fn read_rust_sources(dir: &Path) -> Vec<(String, String)> {
    let mut out = Vec::new();
    read_rust_sources_into(dir, dir, &mut out);
    out
}

fn read_rust_sources_into(root: &Path, dir: &Path, out: &mut Vec<(String, String)>) {
    for entry in fs::read_dir(dir).expect("read src dir") {
        let path = entry.expect("dir entry").path();
        if path.is_dir() {
            read_rust_sources_into(root, &path, out);
        } else if path.extension().is_some_and(|ext| ext == "rs") {
            let name = path
                .strip_prefix(root)
                .expect("source is under src root")
                .display()
                .to_string();
            out.push((name, fs::read_to_string(&path).expect("read source")));
        }
    }
}

#[test]
fn source_has_no_legacy_symbols() {
    let src = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    for (name, contents) in read_rust_sources(&src) {
        let lower = contents.to_ascii_lowercase();
        for symbol in FORBIDDEN_SYMBOLS {
            assert!(
                !lower.contains(symbol),
                "forbidden legacy symbol `{symbol}` found in src/{name}"
            );
        }
    }
}

#[test]
fn cargo_toml_has_no_provider_sdk() {
    let manifest = fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml"))
        .expect("read Cargo.toml");
    for dep in FORBIDDEN_DEPS {
        let needle = format!("{dep}.workspace");
        assert!(
            !manifest.contains(&needle),
            "forbidden provider SDK dependency `{dep}` declared in Cargo.toml"
        );
        // Also reject a direct `dep = ...` line.
        for line in manifest.lines() {
            let trimmed = line.trim_start();
            assert!(
                !trimmed.starts_with(&format!("{dep} "))
                    && !trimmed.starts_with(&format!("{dep}=")),
                "forbidden provider SDK dependency `{dep}` declared in Cargo.toml"
            );
        }
    }
}
