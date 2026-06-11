use std::collections::BTreeSet;

use super::*;

#[test]
fn builtin_specs_match_legacy_wire_list() {
    let catalog_wires = BUILTIN_DAEMON_OP_SPECS
        .iter()
        .map(|spec| spec.aliases[0])
        .collect::<Vec<_>>();
    assert_eq!(catalog_wires, BUILTIN_DAEMON_OPS);
}

#[test]
fn builtin_specs_are_returned_by_ops() {
    for spec in BUILTIN_DAEMON_OP_SPECS {
        assert_eq!(*spec, spec.op.spec());
    }
}

#[test]
fn canonical_names_follow_grammar() {
    for spec in BUILTIN_DAEMON_OP_SPECS {
        assert!(
            spec.name.starts_with("sandbox."),
            "daemon op {} must use the sandbox.* grammar",
            spec.name
        );
        assert!(
            !spec.name.split('.').any(|token| token == "v1"),
            "the v1 token is dead in canonical names: {}",
            spec.name
        );
    }
    for spec in HOST_OP_SPECS {
        assert!(
            spec.name.starts_with("sandbox.") && spec.name.split('.').count() == 2,
            "host op {} must be sandbox.<verb>",
            spec.name
        );
    }
}

#[test]
fn no_spelling_is_claimed_twice() {
    let mut spellings = BTreeSet::new();
    let all_names = HOST_OP_SPECS
        .iter()
        .map(|spec| spec.name)
        .chain(BUILTIN_DAEMON_OP_SPECS.iter().map(|spec| spec.name))
        .chain(
            BUILTIN_DAEMON_OP_SPECS
                .iter()
                .flat_map(|spec| spec.aliases.iter().copied()),
        );
    for spelling in all_names {
        assert!(
            spellings.insert(spelling),
            "spelling claimed twice in the catalog: {spelling}"
        );
    }
}

#[test]
fn resolve_accepts_both_spellings() {
    for spec in BUILTIN_DAEMON_OP_SPECS {
        assert_eq!(BuiltinDaemonOp::resolve(spec.name), Some(spec.op));
        for alias in spec.aliases {
            assert_eq!(BuiltinDaemonOp::resolve(alias), Some(spec.op));
        }
    }
    assert_eq!(BuiltinDaemonOp::resolve("api.totally.bogus.op"), None);
}

#[test]
fn fixture_pinned_aliases_are_present() {
    // Pinned by immutable golden fixtures; these aliases are never removed.
    assert_eq!(
        BuiltinDaemonOp::ReadFile.aliases(),
        ["api.v1.read_file"],
        "sandbox.file.read must keep its fixture-pinned alias"
    );
    assert_eq!(
        BuiltinDaemonOp::InvocationHeartbeat.aliases(),
        ["api.v1.heartbeat"],
        "sandbox.call.heartbeat must keep its fixture-pinned alias"
    );
}

#[test]
fn ops_json_document_is_complete_and_stable() {
    let document = ops_json_document();
    let parsed: serde_json::Value = serde_json::from_str(&document).expect("document parses back");
    assert_eq!(parsed["protocol_version"], DAEMON_PROTOCOL_VERSION);
    let ops = parsed["ops"].as_array().expect("ops array");
    assert_eq!(
        ops.len(),
        HOST_OP_SPECS.len() + BUILTIN_DAEMON_OP_SPECS.len()
    );
    assert!(document.ends_with('\n'));
}
