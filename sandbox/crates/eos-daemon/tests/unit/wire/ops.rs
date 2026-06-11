use std::collections::BTreeSet;

use super::*;

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
        .chain(BUILTIN_DAEMON_OP_SPECS.iter().map(|spec| spec.name));
    for spelling in all_names {
        assert!(
            spellings.insert(spelling),
            "spelling claimed twice in the catalog: {spelling}"
        );
    }
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
