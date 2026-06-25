use std::path::PathBuf;

use crate::cgroup_setup::parse_cgroup_root;

#[test]
fn parse_maps_unified_line_onto_the_cgroup_filesystem() {
    let root = parse_cgroup_root("0::/sandbox/eos-1\n").expect("0:: line parses");
    assert_eq!(root, PathBuf::from("/sys/fs/cgroup/sandbox/eos-1"));
}

#[test]
fn parse_treats_root_slash_as_the_mount_point() {
    let root = parse_cgroup_root("0::/\n").expect("root 0:: line parses");
    assert_eq!(root, PathBuf::from("/sys/fs/cgroup"));
}

#[test]
fn parse_ignores_v1_controller_lines() {
    let proc_self = "12:pids:/foo\n11:memory:/bar\n0::/eos-2\n";
    let root = parse_cgroup_root(proc_self).expect("v2 line found among v1 lines");
    assert_eq!(root, PathBuf::from("/sys/fs/cgroup/eos-2"));
}

#[test]
fn parse_returns_none_without_a_unified_line() {
    assert!(parse_cgroup_root("12:pids:/foo\n").is_none());
}
