use std::os::unix::fs::{symlink, MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use crate::stack::squash::flatten::flatten_block_into;
use crate::whiteout::{is_kernel_whiteout, logical_whiteout_path_for_target, OPAQUE_MARKER};

struct FlattenFixture {
    base: PathBuf,
}

static NEXT_FLATTEN_TEST: AtomicU64 = AtomicU64::new(0);

impl FlattenFixture {
    fn new(label: &str) -> Self {
        let base = std::env::temp_dir().join(format!(
            "layerstack-flatten-{label}-{}-{}",
            std::process::id(),
            NEXT_FLATTEN_TEST.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).expect("create fixture base");
        Self { base }
    }

    fn layer(&self, name: &str) -> PathBuf {
        let dir = self.base.join(name);
        std::fs::create_dir_all(&dir).expect("create layer dir");
        dir
    }

    fn staging(&self) -> PathBuf {
        self.base.join("S.staging")
    }

    fn flatten(&self, sources_newest_first: &[PathBuf]) -> PathBuf {
        let staging = self.staging();
        flatten_block_into(&staging, sources_newest_first).expect("flatten block");
        staging
    }
}

impl Drop for FlattenFixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.base);
    }
}

fn write(path: &Path, content: &str) {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).expect("mkdir");
    }
    std::fs::write(path, content).expect("write fixture file");
}

// Portable whiteout fixture: a zero-length file carrying the
// user.overlay.whiteout xattr (one of the accepted kernel encodings).
fn write_xattr_whiteout(path: &Path) {
    write(path, "");
    rustix::fs::lsetxattr(
        path,
        "user.overlay.whiteout",
        b"y",
        rustix::fs::XattrFlags::empty(),
    )
    .expect("set whiteout xattr");
}

fn is_whiteout_at(path: &Path) -> bool {
    is_kernel_whiteout(path) || logical_whiteout_path_for_target(path).exists()
}

fn dir_is_opaque(path: &Path) -> bool {
    let marker = path.join(OPAQUE_MARKER);
    let mut value = [0_u8; 1];
    let xattr = matches!(
        rustix::fs::lgetxattr(path, "user.overlay.opaque", &mut value),
        Ok(1) if value[0] == b'y'
    );
    marker.exists() && xattr
}

fn visible_names(dir: &Path) -> Vec<String> {
    let mut names: Vec<String> = std::fs::read_dir(dir)
        .expect("read dir")
        .map(|entry| {
            entry
                .expect("entry")
                .file_name()
                .to_string_lossy()
                .into_owned()
        })
        .filter(|name| name != OPAQUE_MARKER)
        .collect();
    names.sort();
    names
}

#[test]
fn flatten_newest_wins_and_hardlinks_whole_files() {
    let fixture = FlattenFixture::new("newest-wins");
    let l_new = fixture.layer("l-new");
    let l_mid = fixture.layer("l-mid");
    let l_old = fixture.layer("l-old");
    write(&l_new.join("f.txt"), "new");
    write(&l_mid.join("f.txt"), "mid");
    write(&l_old.join("f.txt"), "old");
    write(&l_mid.join("mid-only.txt"), "mid-only");

    let staging = fixture.flatten(&[l_new.clone(), l_mid.clone(), l_old]);

    assert_eq!(
        std::fs::read_to_string(staging.join("f.txt")).expect("read f.txt"),
        "new"
    );
    let source_inode = std::fs::metadata(l_new.join("f.txt"))
        .expect("stat source")
        .ino();
    let flat_inode = std::fs::metadata(staging.join("f.txt"))
        .expect("stat flat")
        .ino();
    assert_eq!(
        flat_inode, source_inode,
        "whole-file winner must be hardlinked"
    );
    let mid_inode = std::fs::metadata(l_mid.join("mid-only.txt"))
        .expect("stat source")
        .ino();
    assert_eq!(
        std::fs::metadata(staging.join("mid-only.txt"))
            .expect("stat flat")
            .ino(),
        mid_inode
    );
}

#[test]
fn flatten_reemits_winning_whiteouts_both_encodings() {
    let fixture = FlattenFixture::new("whiteout-encodings");
    let l_new = fixture.layer("l-new");
    let l_mid = fixture.layer("l-mid");
    let l_old = fixture.layer("l-old");
    write(&l_new.join("keep.txt"), "keep");
    // xattr-file encoding masking a below-layer file.
    write(&l_old.join("gone-xattr.txt"), "doomed");
    write_xattr_whiteout(&l_mid.join("gone-xattr.txt"));
    // logical .wh. encoding masking a below-layer file.
    write(&l_old.join("gone-logical.txt"), "doomed");
    write(&l_mid.join(".wh.gone-logical.txt"), "");

    let staging = fixture.flatten(&[l_new, l_mid, l_old]);

    assert!(is_whiteout_at(&staging.join("gone-xattr.txt")));
    assert!(is_whiteout_at(&staging.join("gone-logical.txt")));
    assert!(!staging.join(".wh..wh..opq").exists());
    assert_eq!(
        std::fs::read_to_string(staging.join("keep.txt")).expect("read keep.txt"),
        "keep"
    );
}

#[test]
fn flatten_drops_whiteout_shadowed_by_newer_file() {
    let fixture = FlattenFixture::new("shadowed-whiteout");
    let l_new = fixture.layer("l-new");
    let l_mid = fixture.layer("l-mid");
    let l_old = fixture.layer("l-old");
    write(&l_old.join("reborn.txt"), "old");
    write_xattr_whiteout(&l_mid.join("reborn.txt"));
    write(&l_new.join("reborn.txt"), "reborn");

    let staging = fixture.flatten(&[l_new, l_mid, l_old]);

    assert!(!is_whiteout_at(&staging.join("reborn.txt")));
    assert_eq!(
        std::fs::read_to_string(staging.join("reborn.txt")).expect("read reborn.txt"),
        "reborn"
    );
}

#[test]
fn flatten_opaque_marker_cuts_block_and_reemits_dual_encoding() {
    let fixture = FlattenFixture::new("opaque-cut");
    let l_new = fixture.layer("l-new");
    let l_mid = fixture.layer("l-mid");
    let l_old = fixture.layer("l-old");
    write(&l_new.join("unrelated.txt"), "x");
    write(&l_mid.join("somedir").join(OPAQUE_MARKER), "");
    write(&l_mid.join("somedir/kept"), "kept");
    write(&l_old.join("somedir/dropped"), "dropped");

    let staging = fixture.flatten(&[l_new, l_mid, l_old]);

    assert_eq!(visible_names(&staging.join("somedir")), vec!["kept"]);
    assert!(
        dir_is_opaque(&staging.join("somedir")),
        "opaque winner must re-emit marker file + user.overlay.opaque xattr"
    );
}

#[test]
fn flatten_dir_over_whiteout_composes_opaque_dir() {
    let fixture = FlattenFixture::new("dir-over-whiteout");
    let l_new = fixture.layer("l-new");
    let l_mid = fixture.layer("l-mid");
    let l_old = fixture.layer("l-old");
    write(&l_new.join("composed/n.txt"), "n");
    write_xattr_whiteout(&l_mid.join("composed"));
    write(&l_old.join("composed/o.txt"), "o");

    let staging = fixture.flatten(&[l_new, l_mid, l_old]);

    assert_eq!(visible_names(&staging.join("composed")), vec!["n.txt"]);
    assert!(
        dir_is_opaque(&staging.join("composed")),
        "a dir whose merge run was cut by an in-block whiteout must mask below-block layers"
    );
}

#[test]
fn flatten_dir_over_file_composes_opaque_dir() {
    let fixture = FlattenFixture::new("dir-over-file");
    let l_new = fixture.layer("l-new");
    let l_mid = fixture.layer("l-mid");
    let l_old = fixture.layer("l-old");
    write(&l_new.join("dof/x"), "x");
    write(&l_mid.join("dof"), "i was a file");
    write(&l_old.join("dof/o"), "o");

    let staging = fixture.flatten(&[l_new, l_mid, l_old]);

    assert_eq!(visible_names(&staging.join("dof")), vec!["x"]);
    assert!(dir_is_opaque(&staging.join("dof")));
}

#[test]
fn flatten_dir_created_then_emptied_survives_plain() {
    let fixture = FlattenFixture::new("dir-emptied");
    let l_new = fixture.layer("l-new");
    let l_old = fixture.layer("l-old");
    std::fs::create_dir_all(l_new.join("emptied")).expect("mkdir emptied");
    write(&l_old.join("other.txt"), "x");

    let staging = fixture.flatten(&[l_new, l_old]);

    assert!(staging.join("emptied").is_dir());
    assert_eq!(
        visible_names(&staging.join("emptied")),
        Vec::<String>::new()
    );
    assert!(
        !dir_is_opaque(&staging.join("emptied")),
        "a run reaching the block bottom must stay plain so below-block merging is preserved"
    );
    assert!(!staging.join("emptied").join(OPAQUE_MARKER).exists());
}

#[test]
fn flatten_merges_dirs_across_all_layers_without_terminators() {
    let fixture = FlattenFixture::new("plain-merge");
    let l_new = fixture.layer("l-new");
    let l_mid = fixture.layer("l-mid");
    let l_old = fixture.layer("l-old");
    write(&l_new.join("merged/from-new"), "n");
    write(&l_mid.join("merged/from-mid"), "m");
    write(&l_old.join("merged/from-old"), "o");

    let staging = fixture.flatten(&[l_new, l_mid, l_old]);

    assert_eq!(
        visible_names(&staging.join("merged")),
        vec!["from-mid", "from-new", "from-old"]
    );
    assert!(!dir_is_opaque(&staging.join("merged")));
    assert!(!staging.join(OPAQUE_MARKER).exists());
}

#[test]
fn flatten_preserves_file_and_dir_modes() {
    let fixture = FlattenFixture::new("modes");
    let l_new = fixture.layer("l-new");
    let l_old = fixture.layer("l-old");
    write(&l_new.join("mode-file"), "m");
    std::fs::set_permissions(
        l_new.join("mode-file"),
        std::fs::Permissions::from_mode(0o640),
    )
    .expect("chmod file");
    std::fs::create_dir_all(l_new.join("mode-dir")).expect("mkdir mode-dir");
    std::fs::set_permissions(
        l_new.join("mode-dir"),
        std::fs::Permissions::from_mode(0o750),
    )
    .expect("chmod dir");
    write(&l_old.join("x"), "x");

    let staging = fixture.flatten(&[l_new, l_old]);

    let file_mode = std::fs::metadata(staging.join("mode-file"))
        .expect("stat mode-file")
        .mode()
        & 0o7777;
    assert_eq!(file_mode, 0o640);
    let dir_mode = std::fs::metadata(staging.join("mode-dir"))
        .expect("stat mode-dir")
        .mode()
        & 0o7777;
    assert_eq!(dir_mode, 0o750);
}

#[test]
fn flatten_never_follows_symlinks() {
    let fixture = FlattenFixture::new("no-follow");
    let l_new = fixture.layer("l-new");
    let l_old = fixture.layer("l-old");
    symlink("/etc", l_new.join("evil-abs")).expect("symlink abs");
    symlink("../../../..", l_new.join("evil-rel")).expect("symlink rel");
    // A symlink shadowing a populated below-layer dir: the subtree is dropped,
    // the symlink is copied verbatim, and nothing is ever resolved through it.
    symlink("elsewhere", l_new.join("swap")).expect("symlink swap");
    write(&l_old.join("swap/secret"), "s");

    let staging = fixture.flatten(&[l_new, l_old]);

    let abs = std::fs::symlink_metadata(staging.join("evil-abs")).expect("lstat evil-abs");
    assert!(abs.file_type().is_symlink());
    assert_eq!(
        std::fs::read_link(staging.join("evil-abs")).expect("readlink abs"),
        PathBuf::from("/etc")
    );
    assert_eq!(
        std::fs::read_link(staging.join("evil-rel")).expect("readlink rel"),
        PathBuf::from("../../../..")
    );
    let swap = std::fs::symlink_metadata(staging.join("swap")).expect("lstat swap");
    assert!(swap.file_type().is_symlink());
}

#[test]
fn flatten_same_layer_logical_whiteout_beats_same_layer_file() {
    let fixture = FlattenFixture::new("tie");
    let l_new = fixture.layer("l-new");
    let l_mid = fixture.layer("l-mid");
    let l_old = fixture.layer("l-old");
    write(&l_new.join("unrelated"), "x");
    write(&l_mid.join("tie.txt"), "contradicted");
    write(&l_mid.join(".wh.tie.txt"), "");
    write(&l_old.join("tie.txt"), "old");

    let staging = fixture.flatten(&[l_new, l_mid, l_old]);

    assert!(
        is_whiteout_at(&staging.join("tie.txt")),
        "MergedView consults whiteouts before entries, so the whiteout wins the tie"
    );
}

#[test]
fn flatten_shadowed_subtree_dropped_under_file_winner() {
    let fixture = FlattenFixture::new("subtree-drop");
    let l_new = fixture.layer("l-new");
    let l_old = fixture.layer("l-old");
    write(&l_new.join("sub"), "i am a file now");
    write(&l_old.join("sub/tree/a"), "a");
    write(&l_old.join("sub/tree/b"), "b");

    let staging = fixture.flatten(&[l_new, l_old]);

    assert!(staging.join("sub").is_file());
    assert_eq!(
        std::fs::read_to_string(staging.join("sub")).expect("read sub"),
        "i am a file now"
    );
}

#[test]
fn flatten_rejects_blocks_smaller_than_two_layers() {
    let fixture = FlattenFixture::new("too-small");
    let l_new = fixture.layer("l-new");
    let error = flatten_block_into(&fixture.staging(), &[l_new])
        .expect_err("flatten must reject the block");
    assert!(error.to_string().contains("at least two source layers"));
}

mod rewrite_tests {
    use std::path::PathBuf;
    use std::sync::atomic::Ordering;

    use crate::stack::RewrittenLease;
    use crate::{LayerRef, LayerStack, Lease, Manifest, MANIFEST_SCHEMA_VERSION};

    use super::NEXT_FLATTEN_TEST;

    struct RewriteFixture {
        root: PathBuf,
    }

    impl RewriteFixture {
        fn new(label: &str) -> Self {
            let root = std::env::temp_dir().join(format!(
                "layerstack-rewrite-{label}-{}-{}",
                std::process::id(),
                NEXT_FLATTEN_TEST.fetch_add(1, Ordering::Relaxed)
            ));
            let _ = std::fs::remove_dir_all(&root);
            std::fs::create_dir_all(root.join("layers")).expect("create layers dir");
            Self { root }
        }

        fn layer(&self, id: &str) -> LayerRef {
            std::fs::create_dir_all(self.root.join("layers").join(id)).expect("create layer");
            LayerRef {
                layer_id: id.to_owned(),
                path: format!("layers/{id}"),
            }
        }

        fn set_manifest(&self, version: i64, layers: &[LayerRef]) {
            let manifest =
                Manifest::new(version, layers.to_vec(), MANIFEST_SCHEMA_VERSION).expect("manifest");
            crate::fs::write_manifest(self.root.join("manifest.json"), &manifest)
                .expect("write manifest");
        }

        fn stack(&self) -> LayerStack {
            LayerStack::open(self.root.clone()).expect("open stack")
        }
    }

    impl Drop for RewriteFixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    fn ids(layers: &[LayerRef]) -> Vec<&str> {
        layers.iter().map(|layer| layer.layer_id.as_str()).collect()
    }

    fn fake_lease(layers: &[LayerRef]) -> Lease {
        Lease {
            lease_id: "hand-built".to_owned(),
            manifest: Manifest::new(4, layers.to_vec(), MANIFEST_SCHEMA_VERSION).expect("manifest"),
            layer_paths: Vec::new(),
        }
    }

    // B4's two-generation world: gen-1 squashes [L7,L6,L5]->Sa and
    // [L3,L2,L1]->Sb; gen-2 re-squashes [L8,Sa]->Sc. Contraction applies map
    // entries in recording order, so raw runs containing earlier S ids
    // compose across generations without any expansion step.
    #[test]
    fn in_memory_substitutions_match_expand_then_contract() {
        let fixture = RewriteFixture::new("b4");
        let l: Vec<LayerRef> = (1..=8)
            .map(|index| fixture.layer(&format!("L00000{index}-0{index}")))
            .collect();
        let newest_first: Vec<LayerRef> = l.iter().rev().cloned().collect();
        fixture.set_manifest(8, &newest_first);
        let stack = fixture.stack();
        let ws1 = stack.acquire_snapshot("ws-1").expect("lease ws-1");

        let sa = fixture.layer("S000009-aa");
        let sb = fixture.layer("S000009-ab");
        fixture.set_manifest(9, &[l[7].clone(), sa.clone(), l[3].clone(), sb.clone()]);
        stack.record_substitution(sa.clone(), vec![l[6].clone(), l[5].clone(), l[4].clone()]);
        stack.record_substitution(sb.clone(), vec![l[2].clone(), l[1].clone(), l[0].clone()]);

        let l10 = fixture.layer("L000010-10");
        fixture.set_manifest(
            10,
            &[
                l10.clone(),
                l[7].clone(),
                sa.clone(),
                l[3].clone(),
                sb.clone(),
            ],
        );
        let ws3 = stack.acquire_snapshot("ws-3").expect("lease ws-3");

        let sc = fixture.layer("S000011-ac");
        fixture.set_manifest(11, &[l10.clone(), sc.clone(), l[3].clone(), sb.clone()]);
        stack.record_substitution(sc.clone(), vec![l[7].clone(), sa.clone()]);

        let ws1_rewritten = stack
            .acquire_rewritten_lease(&ws1, "ws-1-rewrite")
            .expect("rewrite ws-1");
        match ws1_rewritten {
            RewrittenLease::Replaced(lease) => {
                assert_eq!(
                    ids(&lease.manifest.layers),
                    vec!["S000011-ac", "L000004-04", "S000009-ab"],
                    "gen-0 lease crosses both generations in one bounded pass"
                );
                assert_eq!(lease.manifest.version, ws1.manifest.version);
            }
            RewrittenLease::Identity => panic!("ws-1 must contract"),
        }

        let ws3_rewritten = stack
            .acquire_rewritten_lease(&ws3, "ws-3-rewrite")
            .expect("rewrite ws-3");
        match ws3_rewritten {
            RewrittenLease::Replaced(lease) => {
                assert_eq!(
                    ids(&lease.manifest.layers),
                    vec!["L000010-10", "S000011-ac", "L000004-04", "S000009-ab"]
                );
            }
            RewrittenLease::Identity => panic!("ws-3 must contract via the raw [L8,Sa] run"),
        }

        // ws-2's shape: no recorded raw run is present -> identity.
        let ws2 = fake_lease(&[l[3].clone(), sb.clone()]);
        assert!(matches!(
            stack
                .acquire_rewritten_lease(&ws2, "ws-2-rewrite")
                .expect("rewrite ws-2"),
            RewrittenLease::Identity
        ));
    }

    #[test]
    fn rewrite_missing_entry_and_dead_layer_degrade_to_identity() {
        let fixture = RewriteFixture::new("identity");
        let l2 = fixture.layer("L000002-02");
        let l1 = fixture.layer("L000001-01");
        fixture.set_manifest(2, &[l2.clone(), l1.clone()]);
        let stack = fixture.stack();
        let lease = stack.acquire_snapshot("ws").expect("lease");

        // Empty map: identity.
        assert!(matches!(
            stack
                .acquire_rewritten_lease(&lease, "empty-map")
                .expect("rewrite"),
            RewrittenLease::Identity
        ));

        // A substitution whose S dir does not exist on disk: the contraction
        // would apply, but validate-alive degrades it to identity.
        let dead = LayerRef {
            layer_id: "S000003-dead".to_owned(),
            path: "layers/S000003-dead".to_owned(),
        };
        stack.record_substitution(dead, vec![l2.clone(), l1.clone()]);
        assert!(matches!(
            stack
                .acquire_rewritten_lease(&lease, "dead-target")
                .expect("rewrite"),
            RewrittenLease::Identity
        ));
    }

    #[test]
    fn rewrite_is_deterministic_under_adversarial_map_shapes() {
        let fixture = RewriteFixture::new("adversarial");
        let l3 = fixture.layer("L000003-03");
        let l2 = fixture.layer("L000002-02");
        let l1 = fixture.layer("L000001-01");
        fixture.set_manifest(3, &[l3.clone(), l2.clone(), l1.clone()]);
        let stack = fixture.stack();
        let lease = stack.acquire_snapshot("ws").expect("lease");

        // Repeated ids never match a real manifest.
        let sx = fixture.layer("S000004-xx");
        stack.record_substitution(sx, vec![l2.clone(), l2.clone()]);
        // First recorded run wins; the overlapping later run no longer matches.
        let sy = fixture.layer("S000004-yy");
        stack.record_substitution(sy, vec![l3.clone(), l2.clone()]);
        let sz = fixture.layer("S000004-zz");
        stack.record_substitution(sz, vec![l2.clone(), l1.clone()]);

        match stack
            .acquire_rewritten_lease(&lease, "adversarial")
            .expect("rewrite")
        {
            RewrittenLease::Replaced(lease) => {
                assert_eq!(
                    ids(&lease.manifest.layers),
                    vec!["S000004-yy", "L000001-01"]
                );
            }
            RewrittenLease::Identity => panic!("the [L3,L2] run must contract"),
        }
    }

    #[test]
    fn rewrite_never_releases_the_old_lease() {
        let fixture = RewriteFixture::new("pin-overlap");
        let l2 = fixture.layer("L000002-02");
        let l1 = fixture.layer("L000001-01");
        fixture.set_manifest(2, &[l2.clone(), l1.clone()]);
        let mut stack = fixture.stack();
        let old = stack.acquire_snapshot("ws").expect("lease");
        let baseline = stack.active_lease_count();

        let s1 = fixture.layer("S000003-s1");
        fixture.set_manifest(3, std::slice::from_ref(&s1));
        stack.record_substitution(s1, vec![l2.clone(), l1.clone()]);

        let replacement = match stack
            .acquire_rewritten_lease(&old, "replacement")
            .expect("rewrite")
        {
            RewrittenLease::Replaced(lease) => lease,
            RewrittenLease::Identity => panic!("must contract"),
        };
        assert_eq!(
            stack.active_lease_count(),
            baseline + 1,
            "replacement acquired before anything is released"
        );
        assert_ne!(replacement.lease_id, old.lease_id);

        // Clean-abort path: releasing only the replacement returns the
        // registry to baseline and deletes nothing the old lease pins.
        stack
            .release_lease(&replacement.lease_id)
            .expect("release replacement");
        assert_eq!(stack.active_lease_count(), baseline);
        assert!(fixture.root.join("layers/L000002-02").is_dir());
        assert!(fixture.root.join("layers/L000001-01").is_dir());
    }

    #[test]
    fn restart_empties_substitution_map_and_no_rewrite_is_attempted() {
        let _state_guard = crate::process_state_test_lock();
        crate::reset_process_state_for_tests();
        let fixture = RewriteFixture::new("restart");
        let l2 = fixture.layer("L000002-02");
        let l1 = fixture.layer("L000001-01");
        fixture.set_manifest(2, &[l2.clone(), l1.clone()]);
        let stack = fixture.stack();
        let lease = stack.acquire_snapshot("ws").expect("lease");

        let s1 = fixture.layer("S000003-s1");
        stack.record_substitution(s1, vec![l2.clone(), l1.clone()]);

        crate::reset_process_state_for_tests();
        let stack = fixture.stack();
        assert!(matches!(
            stack
                .acquire_rewritten_lease(&lease, "post-restart")
                .expect("rewrite"),
            RewrittenLease::Identity
        ));
    }
}
