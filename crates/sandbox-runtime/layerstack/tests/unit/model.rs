use super::*;
use proptest::prelude::*;

fn lp(s: &str) -> Result<LayerPath, CasError> {
    LayerPath::parse(s)
}

#[test]
fn ascii_escaper_reproduces_documented_literal() {
    // From RUST-GUIDANCE §2a directed test + fixture manifest_unicode_bmp.
    let layers = vec![LayerRef {
        layer_id: "Lunicodé".to_owned(),
        path: "layers/café".to_owned(),
    }];
    assert_eq!(
        manifest_layers_json(&layers),
        "{\"layers\":[{\"layer_id\":\"Lunicod\\u00e9\",\"path\":\"layers/caf\\u00e9\"}]}"
    );
}

#[test]
fn ascii_escaper_surrogate_pair_for_nonbmp() {
    let layers = vec![LayerRef {
        layer_id: "Lrocket".to_owned(),
        path: "layers/🚀".to_owned(),
    }];
    assert_eq!(
        manifest_layers_json(&layers),
        "{\"layers\":[{\"layer_id\":\"Lrocket\",\"path\":\"layers/\\ud83d\\ude80\"}]}"
    );
}

#[test]
fn escaper_short_escapes_and_control_chars() {
    let mut out = String::new();
    push_json_ascii_escaped(&mut out, "\u{0008}\t\n\u{000C}\r\u{0001}\u{007F}\"\\");
    assert_eq!(out, "\\b\\t\\n\\f\\r\\u0001\\u007f\\\"\\\\");
}

#[test]
fn normalize_layer_path_rules() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    assert_eq!(lp("a/b/c")?.as_str(), "a/b/c");
    assert_eq!(lp(" a//b/./c ")?.as_str(), "a/b/c");
    assert_eq!(lp("a\\b")?.as_str(), "a/b");
    assert!(LayerPath::parse("/abs").is_err());
    assert!(LayerPath::parse("a/../b").is_err());
    assert!(LayerPath::parse("").is_err());
    assert!(LayerPath::parse("./").is_err());
    assert!(LayerPath::parse("a\0b").is_err());
    Ok(())
}

#[test]
fn manifest_new_rejects_bad_schema() {
    assert!(Manifest::new(0, vec![], 2).is_err());
    assert!(Manifest::new(0, vec![], 1).is_ok());
}

#[test]
fn aggregate_is_idempotent_and_order_insensitive(
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let changes = vec![
        LayerChange::Write {
            path: lp("z.txt")?,
            content: b"z".to_vec(),
        },
        LayerChange::Delete { path: lp("a.txt")? },
        LayerChange::Symlink {
            path: lp("m")?,
            source_path: "t".to_owned(),
        },
    ];
    let agg = aggregate_layer_changes(&changes);
    assert_eq!(agg, aggregate_layer_changes(&agg));
    let mut reversed = changes;
    reversed.reverse();
    assert_eq!(agg, aggregate_layer_changes(&reversed));
    // sorted by path: a.txt, m, z.txt
    assert_eq!(
        agg.iter().map(|c| c.path().as_str()).collect::<Vec<_>>(),
        vec!["a.txt", "m", "z.txt"]
    );
    Ok(())
}

#[test]
fn aggregate_last_write_wins() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let changes = vec![
        LayerChange::Write {
            path: lp("x")?,
            content: b"first".to_vec(),
        },
        LayerChange::Delete { path: lp("x")? },
    ];
    let agg = aggregate_layer_changes(&changes);
    assert_eq!(agg.len(), 1);
    assert_eq!(agg[0].kind(), "delete");
    Ok(())
}

// A change over a UNIQUE relative path (no collisions, so order-insensitivity
// holds — colliding paths would change the last-write-wins survivor).
fn arb_change_unique() -> impl Strategy<Value = LayerChange> {
    // single-segment lowercase paths keep them unique-able and always valid.
    let path =
        "[a-z]{1,8}".prop_map(|path| LayerPath::parse(&path).expect("generated path is valid"));
    prop_oneof![
        (path.clone(), prop::collection::vec(any::<u8>(), 0..32))
            .prop_map(|(path, content)| LayerChange::Write { path, content }),
        path.clone().prop_map(|path| LayerChange::Delete { path }),
        (path.clone(), "[a-z/]{0,16}")
            .prop_map(|(path, source_path)| LayerChange::Symlink { path, source_path }),
        path.clone()
            .prop_map(|path| LayerChange::Directory { path }),
        path.prop_map(|path| LayerChange::OpaqueDir { path }),
    ]
}

proptest! {
    #[test]
    fn aggregate_idempotent_and_order_insensitive(changes in prop::collection::vec(arb_change_unique(), 0..12)) {
        // Dedup by path so the property's order-insensitivity precondition holds.
        let mut seen = std::collections::HashSet::new();
        let unique: Vec<LayerChange> = changes
            .into_iter()
            .filter(|c| seen.insert(c.path().as_str().to_owned()))
            .collect();
        let agg = aggregate_layer_changes(&unique);
        // idempotent
        prop_assert_eq!(agg.clone(), aggregate_layer_changes(&agg));
        // input-order-insensitive
        let mut shuffled = unique;
        shuffled.reverse();
        prop_assert_eq!(&agg, &aggregate_layer_changes(&shuffled));
        // emitted sorted by path
        let paths: Vec<&str> = agg.iter().map(|c| c.path().as_str()).collect();
        let mut sorted = paths.clone();
        sorted.sort_unstable();
        prop_assert_eq!(paths, sorted);
    }

    #[test]
    fn escaper_output_is_pure_ascii(s in ".*") {
        let mut out = String::new();
        push_json_ascii_escaped(&mut out, &s);
        prop_assert!(out.is_ascii(), "escaper leaked a non-ASCII byte for input {:?}", s);
    }
}
