//! Merged one-level directory listing (`LayerStack::list_dir`) semantics:
//! upper layers win per name, delete layers hide lower entries, non-directory
//! and absent paths classify, and the entry cap reports truncation.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use sandbox_runtime_layerstack::{
    LayerChange, LayerPath, LayerStack, ManifestDirEntryKind, ManifestDirList,
};

static NEXT_TMP: AtomicU64 = AtomicU64::new(0);

struct Fixture {
    root: PathBuf,
}

impl Fixture {
    fn new(label: &str) -> Self {
        let root = std::env::temp_dir().join(format!(
            "layerstack-list-{label}-{}-{}",
            std::process::id(),
            NEXT_TMP.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&root);
        Self { root }
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

fn publish_text(stack: &mut LayerStack, path: &str, content: &str) {
    stack
        .publish_layer(&[LayerChange::Write {
            path: LayerPath::parse(path).expect("parse path"),
            content: content.as_bytes().to_vec(),
        }])
        .expect("publish layer");
}

fn entries_of(list: ManifestDirList) -> Vec<(String, ManifestDirEntryKind, Option<u64>)> {
    match list {
        ManifestDirList::Entries { entries, .. } => entries
            .into_iter()
            .map(|entry| (entry.name, entry.kind, entry.size))
            .collect(),
        other => panic!("expected entries, got {other:?}"),
    }
}

#[test]
fn lists_merged_root_and_subdirectory_across_layers() {
    let fixture = Fixture::new("merged");
    let mut stack = LayerStack::open(fixture.root.clone()).expect("open stack");
    publish_text(&mut stack, "readme.txt", "hello\n");
    publish_text(&mut stack, "dir/a.txt", "one\n");
    publish_text(&mut stack, "dir/b.txt", "two-longer\n");

    let root = entries_of(stack.list_dir(None, 100).expect("list root"));
    assert_eq!(
        root.iter()
            .map(|(name, ..)| name.as_str())
            .collect::<Vec<_>>(),
        vec!["dir", "readme.txt"],
        "root merges every layer, sorted by name"
    );
    assert_eq!(root[0].1, ManifestDirEntryKind::Directory);
    assert_eq!(root[1].1, ManifestDirEntryKind::File);
    assert_eq!(root[1].2, Some(6));

    let sub = entries_of(
        stack
            .list_dir(Some(&LayerPath::parse("dir").expect("parse")), 100)
            .expect("list dir"),
    );
    assert_eq!(
        sub.iter()
            .map(|(name, _, size)| (name.as_str(), *size))
            .collect::<Vec<_>>(),
        vec![("a.txt", Some(4)), ("b.txt", Some(11))]
    );
}

#[test]
fn upper_layer_wins_for_size_and_delete_hides_lower_entries() {
    let fixture = Fixture::new("winner");
    let mut stack = LayerStack::open(fixture.root.clone()).expect("open stack");
    publish_text(&mut stack, "dir/a.txt", "original content\n");
    publish_text(&mut stack, "dir/b.txt", "keep\n");
    publish_text(&mut stack, "dir/a.txt", "new\n");

    let sub = entries_of(
        stack
            .list_dir(Some(&LayerPath::parse("dir").expect("parse")), 100)
            .expect("list dir"),
    );
    assert_eq!(
        sub.iter()
            .map(|(name, _, size)| (name.as_str(), *size))
            .collect::<Vec<_>>(),
        vec![("a.txt", Some(4)), ("b.txt", Some(5))],
        "the upper layer's a.txt wins"
    );

    stack
        .publish_layer(&[LayerChange::Delete {
            path: LayerPath::parse("dir/a.txt").expect("parse"),
        }])
        .expect("publish delete");
    let sub = entries_of(
        stack
            .list_dir(Some(&LayerPath::parse("dir").expect("parse")), 100)
            .expect("list dir"),
    );
    assert_eq!(
        sub.iter()
            .map(|(name, ..)| name.as_str())
            .collect::<Vec<_>>(),
        vec!["b.txt"],
        "the whiteout hides every lower a.txt"
    );
}

#[test]
fn absent_and_non_directory_paths_classify() {
    let fixture = Fixture::new("classify");
    let mut stack = LayerStack::open(fixture.root.clone()).expect("open stack");
    publish_text(&mut stack, "file.txt", "x\n");

    assert!(matches!(
        stack
            .list_dir(Some(&LayerPath::parse("missing").expect("parse")), 100)
            .expect("list"),
        ManifestDirList::Absent
    ));
    assert!(matches!(
        stack
            .list_dir(Some(&LayerPath::parse("file.txt").expect("parse")), 100)
            .expect("list"),
        ManifestDirList::NotDirectory
    ));
}

#[test]
fn listing_caps_entries_and_reports_truncation() {
    let fixture = Fixture::new("cap");
    let mut stack = LayerStack::open(fixture.root.clone()).expect("open stack");
    for index in 0..5 {
        publish_text(&mut stack, &format!("dir/f{index}.txt"), "x\n");
    }

    match stack
        .list_dir(Some(&LayerPath::parse("dir").expect("parse")), 3)
        .expect("list dir")
    {
        ManifestDirList::Entries { entries, truncated } => {
            assert_eq!(entries.len(), 3);
            assert!(truncated, "cap reached reports truncation");
        }
        other => panic!("expected entries, got {other:?}"),
    }
}
