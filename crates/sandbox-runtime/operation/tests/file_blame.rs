//! `file` domain blame unit tests (C3 spec §11/§11.1/§15): blame is a pure store
//! read that tiles `[1..=line_count]` from a hand-written NDJSON event, with no
//! merge, no mount, and no layerstack read.

use std::path::PathBuf;

use sandbox_runtime::file::{BlameRange, FileError, FileService};

fn temp_store_dir(label: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "file-blame-{label}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("create store dir");
    dir
}

/// Append one NDJSON line to the store's active segment, exactly as the runtime
/// would after a commit, then open a fresh service over it.
fn store_with_event(label: &str, event: &str) -> FileService {
    let dir = temp_store_dir(label);
    let segment = dir.join("file_auditability_0.ndjson");
    std::fs::write(&segment, format!("{event}\n")).expect("write event");
    FileService::open(dir, sandbox_runtime::FileRuntimeConfig::default())
        .expect("open file service")
}

fn range(start_line: u64, line_count: u64, owner: &str) -> BlameRange {
    BlameRange {
        start_line,
        line_count,
        owner: owner.to_owned(),
    }
}

#[test]
fn blame_tiles_default_owner_and_sparse_ranges() {
    // Spec §11 example (a): base 3 lines; ws-7 changed line 2 and appended line 4.
    let service = store_with_event(
        "tiles",
        r#"{"path":"README.md","line_count":4,"default_owner":"original","owner_ranges":[{"start_line":2,"line_count":1,"owner":"workspace_session:ws-7"},{"start_line":4,"line_count":1,"owner":"workspace_session:ws-7"}],"content_digest":"sha256:abc"}"#,
    );

    let ranges = service.blame("README.md").expect("blame");
    assert_eq!(
        ranges,
        vec![
            range(1, 1, "original"),
            range(2, 1, "workspace_session:ws-7"),
            range(3, 1, "original"),
            range(4, 1, "workspace_session:ws-7"),
        ],
        "blame must tile [1..=4] with no gaps or overlaps"
    );
    let covered: u64 = ranges.iter().map(|r| r.line_count).sum();
    assert_eq!(covered, 4);
    assert_eq!(ranges.first().map(|r| r.start_line), Some(1));
}

#[test]
fn blame_coalesces_adjacent_equal_owners() {
    let service = store_with_event(
        "coalesce",
        r#"{"path":"src/main.rs","line_count":4,"default_owner":"original","owner_ranges":[{"start_line":3,"line_count":2,"owner":"operation:op-9"}],"content_digest":"sha256:x"}"#,
    );
    assert_eq!(
        service.blame("src/main.rs").expect("blame"),
        vec![range(1, 2, "original"), range(3, 2, "operation:op-9")],
        "lines 1-2 and 3-4 each coalesce into one run"
    );
}

#[test]
fn blame_normalizes_path_like_the_audit_key() {
    let service = store_with_event(
        "normalize",
        r#"{"path":"src/x","line_count":1,"default_owner":"operation:op-1","owner_ranges":[],"content_digest":""}"#,
    );
    // `./src/x` and `src/x` must resolve to the same event (no false NotFound).
    assert_eq!(
        service.blame("./src/x").expect("blame normalized"),
        service.blame("src/x").expect("blame plain"),
    );
}

#[test]
fn blame_of_whole_file_owner_has_one_range() {
    // Non-text / wholesale: one line_count, default_owner, no ranges.
    let service = store_with_event(
        "wholesale",
        r#"{"path":"logo.png","line_count":1,"default_owner":"operation:op-77","owner_ranges":[],"content_digest":"sha256:bin"}"#,
    );
    assert_eq!(
        service.blame("logo.png").expect("blame"),
        vec![range(1, 1, "operation:op-77")],
    );
}

#[test]
fn blame_unaudited_path_is_structured_not_found() {
    let service = store_with_event(
        "notfound",
        r#"{"path":"README.md","line_count":1,"default_owner":"original","owner_ranges":[],"content_digest":""}"#,
    );
    match service.blame("does/not/exist.txt") {
        Err(FileError::NotFound(path)) => assert_eq!(path, "does/not/exist.txt"),
        other => panic!("expected NotFound, got {other:?}"),
    }
}

#[test]
fn blame_latest_event_wins_per_path() {
    // Two events for one path; the last appended is the current blame.
    let dir = temp_store_dir("latest");
    let segment = dir.join("file_auditability_0.ndjson");
    let first = r#"{"path":"a.txt","line_count":1,"default_owner":"original","owner_ranges":[],"content_digest":""}"#;
    let second = r#"{"path":"a.txt","line_count":2,"default_owner":"workspace_session:ws-2","owner_ranges":[],"content_digest":""}"#;
    std::fs::write(&segment, format!("{first}\n{second}\n")).expect("write events");

    let service =
        FileService::open(dir, sandbox_runtime::FileRuntimeConfig::default()).expect("open");
    assert_eq!(
        service.blame("a.txt").expect("blame"),
        vec![range(1, 2, "workspace_session:ws-2")],
        "the latest event (2 lines, ws-2) is the current blame"
    );
}
