//! Three-way merge unit tests (C3 spec §13/§15 matrix): B7 overlap→conflict,
//! B8 disjoint→clean, B12 identical-edit→inherit-`Active` (no `mixed`),
//! binary→ineligible, byte-exactness, and an origin-tiling round-trip.

use crate::stack::publish::merge::{three_way_merge, LineRange, MergeOutcome, Origin};

fn clean(outcome: MergeOutcome) -> (Vec<u8>, Vec<(LineRange, Origin)>) {
    match outcome {
        MergeOutcome::Clean { bytes, origin } => (bytes, origin),
        other => panic!("expected clean merge, got {other:?}"),
    }
}

/// Resolve each line to an owner-ish label for assertions close to the spec's
/// blame examples: `Active(_)` has no prior audit event here, so it reads as
/// "original"; `Command` is attributed to this publish.
fn owners(origin: &[(LineRange, Origin)], command_owner: &str) -> Vec<String> {
    let mut out = Vec::new();
    for (range, kind) in origin {
        assert_eq!(range.start, out.len() + 1, "ranges must tile with no gaps");
        for _ in 0..range.len {
            out.push(match kind {
                Origin::Command => command_owner.to_owned(),
                Origin::Active(_) => "original".to_owned(),
            });
        }
    }
    out
}

#[test]
fn disjoint_edits_merge_clean_with_both_sides() {
    let base = b"top\nl2\nl3\nl4\nbottom\n";
    let active = b"top-ACTIVE\nl2\nl3\nl4\nbottom\n";
    let command = b"top\nl2\nl3\nl4\nbottom-COMMAND\n";

    let (bytes, origin) = clean(three_way_merge(base, active, command));
    let text = String::from_utf8(bytes).expect("merged text");
    assert!(text.contains("top-ACTIVE"), "active edit survives: {text}");
    assert!(
        text.contains("bottom-COMMAND"),
        "command edit survives: {text}"
    );

    assert_eq!(
        owners(&origin, "command"),
        vec!["original", "original", "original", "original", "command"],
        "top inherits active (resolves original); bottom is this command"
    );
}

#[test]
fn overlapping_edits_conflict() {
    let base = b"l1\nl2\nl3\nl4\nl5\n";
    let active = b"l1\nl2\nl3-ACTIVE\nl4\nl5\n";
    let command = b"l1\nl2\nl3-COMMAND\nl4\nl5\n";
    assert_eq!(
        three_way_merge(base, active, command),
        MergeOutcome::Conflict
    );
}

#[test]
fn identical_edit_inherits_active_not_command() {
    let base = b"one\ntwo\nthree\n";
    let active = b"one\ntwo-EDIT\nthree\n";
    let command = b"one\ntwo-EDIT\nthree\n";

    let (bytes, origin) = clean(three_way_merge(base, active, command));
    assert_eq!(bytes, b"one\ntwo-EDIT\nthree\n");
    let line2 = origin
        .iter()
        .find(|(range, _)| range.start <= 2 && 2 < range.start + range.len)
        .map(|(_, kind)| *kind)
        .expect("line 2 covered");
    assert!(
        matches!(line2, Origin::Active(_)),
        "identical edit inherits Active, got {line2:?}"
    );
}

#[test]
fn command_changed_and_appended_lines_are_command_origin() {
    let base = b"# Project\nSetup\nUsage\n";
    let active = base;
    let command = b"# Project\nInstallation\nUsage\nLicense\n";

    let (bytes, origin) = clean(three_way_merge(base, active, command));
    assert_eq!(bytes, command);
    assert_eq!(
        owners(&origin, "ws-7"),
        vec!["original", "ws-7", "original", "ws-7"],
        "changed line 2 and appended line 4 belong to the command"
    );
}

#[test]
fn new_file_is_wholly_command() {
    // A brand-new file has an empty base and active; every line is this command.
    let (bytes, origin) = clean(three_way_merge(b"", b"", b"hello\nworld\n"));
    assert_eq!(bytes, b"hello\nworld\n");
    assert_eq!(owners(&origin, "ws-7"), vec!["ws-7", "ws-7"]);

    // Both inputs empty is a clean empty merge (no panic, no lines).
    let (empty, ranges) = clean(three_way_merge(b"", b"", b""));
    assert!(empty.is_empty());
    assert!(ranges.is_empty());
}

#[test]
fn binary_inputs_are_ineligible() {
    assert_eq!(
        three_way_merge(&[0u8, 1, 2, 3], &[0u8, 1, 2, 3], &[0u8, 1, 9, 3]),
        MergeOutcome::Ineligible
    );
    assert_eq!(
        three_way_merge(b"a\n", b"a\n", &[b'a', b'\n', 0xff, 0xfe, b'\n']),
        MergeOutcome::Ineligible
    );
}

#[test]
fn preserves_crlf_and_missing_final_newline() {
    let (bytes, _) = clean(three_way_merge(
        b"a\r\nb\r\nc",
        b"a\r\nb\r\nc",
        b"a\r\nB\r\nc",
    ));
    assert_eq!(
        bytes, b"a\r\nB\r\nc",
        "CRLF and missing final newline preserved"
    );
}

#[test]
fn origin_ranges_tile_disjoint_merge_with_no_gaps() {
    let base = b"1\n2\n3\n4\n5\n6\n";
    let active = b"1-A\n2\n3\n4\n5\n6\n";
    let command = b"1\n2\n3\n4\n5\n6-C\n";
    let (bytes, origin) = clean(three_way_merge(base, active, command));
    assert_eq!(bytes, b"1-A\n2\n3\n4\n5\n6-C\n");

    let total: usize = origin.iter().map(|(range, _)| range.len).sum();
    assert_eq!(total, 6, "origin tiles all six lines");
    let mut next = 1;
    for (range, _) in &origin {
        assert_eq!(range.start, next, "no gap/overlap in tiling");
        next += range.len;
    }
}
