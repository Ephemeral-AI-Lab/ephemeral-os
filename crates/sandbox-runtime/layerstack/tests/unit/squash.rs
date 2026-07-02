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
