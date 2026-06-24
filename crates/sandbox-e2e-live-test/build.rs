use std::fmt::Write as _;
use std::fs;
use std::path::{Path, PathBuf};

fn main() {
    let manifest_dir =
        std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR set by cargo");
    let out_dir = std::env::var("OUT_DIR").expect("OUT_DIR set by cargo");
    for scope in ["manager", "runtime"] {
        generate_scope_includes(Path::new(&manifest_dir), Path::new(&out_dir), scope);
    }
}

fn generate_scope_includes(manifest_dir: &Path, out_dir: &Path, scope: &str) {
    println!("cargo:rerun-if-changed=tests/{scope}");
    let scope_dir = manifest_dir.join("tests").join(scope);
    let mut leaves = Vec::new();
    collect_leaves(&scope_dir, &mut leaves);
    leaves.sort();

    let mut generated = String::new();
    for leaf in &leaves {
        println!("cargo:rerun-if-changed={}", leaf.display());
        let relative = leaf.strip_prefix(&scope_dir).unwrap_or(leaf);
        let slug = module_slug(relative);
        let leaf_path = leaf.display().to_string();
        let _ = writeln!(generated, "#[path = {leaf_path:?}] mod {slug};");
    }

    let dest = out_dir.join(format!("{scope}_mods.rs"));
    fs::write(&dest, generated).expect("write generated scope include list");
}

fn collect_leaves(dir: &Path, leaves: &mut Vec<PathBuf>) {
    let Ok(entries) = fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            collect_leaves(&path, leaves);
        } else if path.extension().is_some_and(|extension| extension == "rs") {
            leaves.push(path);
        }
    }
}

fn module_slug(scope_relative_path: &Path) -> String {
    let joined = scope_relative_path
        .components()
        .map(|component| component.as_os_str().to_string_lossy())
        .collect::<Vec<_>>()
        .join("_");
    joined.strip_suffix(".rs").unwrap_or(&joined).to_owned()
}
