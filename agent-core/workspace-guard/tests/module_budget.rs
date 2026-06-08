use std::collections::BTreeMap;
use std::path::Path;

use workspace_guard::{
    directories_under, nonblank_line_count, relative_to, rust_files_under, Workspace,
};

const PHASE_2_TOTAL_LIMIT: usize = 220;
const PHASE_4_TOTAL_LIMIT: usize = 190;
const FINAL_TOTAL_LIMIT: usize = 170;

const FINAL_CRATE_LIMITS: &[(&str, usize)] = &[
    ("eos-agent-core", 22),
    ("eos-tool", 16),
    ("eos-engine", 22),
    ("eos-workflow", 10),
    ("eos-types", 12),
];

#[test]
fn module_budget_report_is_available() {
    let workspace = Workspace::load();
    let counts = module_counts(&workspace);
    let total = counts.values().sum::<usize>();

    eprintln!("workspace-guard module budget report");
    eprintln!("total modules: {total}");
    eprintln!("phase 2 advisory ceiling: {PHASE_2_TOTAL_LIMIT}");
    eprintln!("phase 4 advisory ceiling: {PHASE_4_TOTAL_LIMIT}");
    eprintln!("final advisory ceiling: {FINAL_TOTAL_LIMIT}");
    for (crate_name, count) in &counts {
        eprintln!("{crate_name}: {count}");
    }
    for (crate_name, depth) in max_folder_depths(&workspace) {
        eprintln!("{crate_name}: max folder depth {depth}");
    }
    for (crate_name, root_file, lines) in root_file_line_counts(&workspace) {
        eprintln!("{crate_name}: {root_file} has {lines} nonblank lines");
    }
    for (crate_name, limit) in FINAL_CRATE_LIMITS {
        if let Some(count) = counts.get(*crate_name) {
            if count > limit {
                eprintln!(
                    "advisory over final per-crate budget: {crate_name} has {count}, limit {limit}"
                );
            }
        }
    }
    if total > FINAL_TOTAL_LIMIT {
        eprintln!(
            "advisory over final total budget: total modules {total}, limit {FINAL_TOTAL_LIMIT}"
        );
    }

    assert!(
        total > 0,
        "module_budget rule violated: no Rust source modules were counted"
    );
}

fn module_counts(workspace: &Workspace) -> BTreeMap<String, usize> {
    workspace
        .crates()
        .iter()
        .map(|(crate_name, crate_info)| {
            (
                crate_name.clone(),
                rust_files_under(&crate_info.src_dir).len(),
            )
        })
        .collect()
}

fn max_folder_depths(workspace: &Workspace) -> BTreeMap<String, usize> {
    workspace
        .crates()
        .iter()
        .map(|(crate_name, crate_info)| {
            let max_depth = directories_under(&crate_info.src_dir)
                .into_iter()
                .filter_map(|path| {
                    path.strip_prefix(&crate_info.src_dir)
                        .ok()
                        .map(Path::to_path_buf)
                })
                .map(|relative| relative.components().count())
                .max()
                .unwrap_or(0);
            (crate_name.clone(), max_depth)
        })
        .collect()
}

fn root_file_line_counts(workspace: &Workspace) -> Vec<(String, String, usize)> {
    let mut counts = workspace
        .crates()
        .iter()
        .flat_map(|(crate_name, crate_info)| {
            rust_files_under(&crate_info.src_dir)
                .into_iter()
                .filter(|path| is_rust_root_file(path, &crate_info.src_dir))
                .map(|path| {
                    (
                        crate_name.clone(),
                        relative_to(&path, workspace.root()),
                        nonblank_line_count(&path),
                    )
                })
        })
        .collect::<Vec<_>>();
    counts.sort();
    counts
}

fn is_rust_root_file(path: &Path, src_dir: &Path) -> bool {
    path.parent() == Some(src_dir)
        && matches!(
            path.file_name().and_then(|name| name.to_str()),
            Some("lib.rs" | "main.rs" | "mod.rs")
        )
}
