use std::collections::BTreeSet;

use workspace_guard::{
    str_set, Workspace, LEGACY_MIGRATION_CRATES, RETIRED_CRATES, RETIRED_CRATE_RULES, TARGET_CRATES,
};

#[test]
fn workspace_crates_are_target_or_documented_migration_crates() {
    let workspace = Workspace::load();
    let allowed: BTreeSet<String> = str_set(TARGET_CRATES)
        .union(&str_set(LEGACY_MIGRATION_CRATES))
        .cloned()
        .collect();
    let violations = workspace
        .crate_names()
        .difference(&allowed)
        .cloned()
        .collect::<Vec<_>>();

    assert!(
        violations.is_empty(),
        "crate_inventory rule violated: unexpected agent-core crates: {violations:?}; \
         add only target crates or document a migration-only crate in phase 00/01"
    );
}

#[test]
fn retired_crate_names_are_declared_in_guard() {
    let retired = str_set(RETIRED_CRATES);
    for crate_name in LEGACY_MIGRATION_CRATES {
        assert!(
            retired.contains(*crate_name),
            "crate_inventory rule violated: migration crate `{crate_name}` is not declared retired"
        );
    }
    assert!(
        !retired.contains("eos-agent-api"),
        "crate_inventory rule violated: eos-agent-api never existed; retired crate list must name real crates"
    );
}

#[test]
fn retired_crates_cannot_coexist_with_target_successors() {
    let workspace = Workspace::load();
    if !workspace.target_crates_present() {
        return;
    }

    let crate_names = workspace.crate_names();
    let mut violations = Vec::new();

    for rule in RETIRED_CRATE_RULES {
        if crate_names.contains(rule.retired) && crate_names.contains(rule.successor) {
            violations.push(format!(
                "`{}` still exists after successor `{}` was added; target: {}",
                rule.retired, rule.successor, rule.target
            ));
        }
    }

    assert!(
        violations.is_empty(),
        "crate_inventory rule violated:\n{}",
        violations.join("\n")
    );
}

#[test]
fn final_crate_map_is_exact_after_collapse() {
    let workspace = Workspace::load();
    if !workspace.target_crates_present() {
        return;
    }

    let crate_names = workspace.crate_names();
    let target = str_set(TARGET_CRATES);
    let extra = crate_names.difference(&target).cloned().collect::<Vec<_>>();
    let missing = target.difference(&crate_names).cloned().collect::<Vec<_>>();

    assert!(
        extra.is_empty() && missing.is_empty(),
        "crate_inventory rule violated: final crate map drifted; extra={extra:?}, missing={missing:?}"
    );
}
