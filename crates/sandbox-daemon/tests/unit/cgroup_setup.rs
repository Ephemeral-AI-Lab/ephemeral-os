use std::path::PathBuf;

use crate::cgroup_setup::{
    enabled_controller_directives, parse_cgroup_root, prepare_root, WorkloadCgroupSettings,
};

const TEST_WORKLOAD_LIMITS: WorkloadCgroupSettings = WorkloadCgroupSettings {
    nano_cpus: 500_000_000,
    memory_high_bytes: 64 * 1024 * 1024,
    memory_max_bytes: 96 * 1024 * 1024,
    pids_max: 70,
};

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

#[test]
fn workload_leaf_enables_cpu_memory_and_pid_controllers_only_when_delegated() {
    assert_eq!(
        enabled_controller_directives("cpuset cpu io memory hugetlb pids rdma"),
        "+cpu +memory +pids"
    );
    assert_eq!(enabled_controller_directives("cpu io"), "+cpu");
    assert_eq!(enabled_controller_directives("io hugetlb"), "");
}

#[test]
fn daemon_leaf_reserves_memory_for_control_plane_cleanup() {
    let root = std::env::temp_dir().join(format!(
        "eos-cgroup-setup-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(&root).unwrap();
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();

    let workloads = root.join("_workloads");
    std::fs::create_dir_all(&workloads).unwrap();
    std::fs::write(workloads.join("cgroup.controllers"), "cpu memory pids").unwrap();

    let prepared = prepare_root(&root, TEST_WORKLOAD_LIMITS).expect("delegated root is prepared");

    let daemon = root.join("_daemon");
    assert_eq!(prepared, workloads);
    assert_eq!(
        std::fs::read_to_string(daemon.join("memory.min")).unwrap(),
        "33554432"
    );
    assert_eq!(
        std::fs::read_to_string(daemon.join("memory.low")).unwrap(),
        "33554432"
    );
    assert_eq!(
        std::fs::read_to_string(root.join("memory.high")).unwrap(),
        (64 * 1024 * 1024).to_string()
    );
    assert_eq!(
        std::fs::read_to_string(daemon.join("cpu.weight")).unwrap(),
        "10000"
    );
    assert_eq!(
        std::fs::read_to_string(workloads.join("cpu.weight")).unwrap(),
        "100"
    );
    assert_eq!(
        std::fs::read_to_string(root.join("cgroup.subtree_control")).unwrap(),
        "+cpu +memory +pids"
    );
    assert_eq!(
        std::fs::read_to_string(workloads.join("cgroup.subtree_control")).unwrap(),
        "+cpu +memory +pids"
    );
    assert_eq!(
        std::fs::read_to_string(workloads.join("cpu.max")).unwrap(),
        "50000 100000"
    );
    assert_eq!(
        std::fs::read_to_string(workloads.join("memory.high")).unwrap(),
        (64 * 1024 * 1024).to_string()
    );
    assert_eq!(
        std::fs::read_to_string(workloads.join("memory.max")).unwrap(),
        (96 * 1024 * 1024).to_string()
    );
    assert_eq!(
        std::fs::read_to_string(workloads.join("memory.oom.group")).unwrap(),
        "1"
    );
    assert_eq!(
        std::fs::read_to_string(workloads.join("pids.max")).unwrap(),
        "70"
    );
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn subtree_enable_failure_is_not_silently_accepted() {
    let root = test_root("subtree-failure");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    std::fs::create_dir(root.join("cgroup.subtree_control")).unwrap();

    let error =
        prepare_root(&root, TEST_WORKLOAD_LIMITS).expect_err("subtree failure disables delegation");

    assert!(error.contains("enable controllers"));
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn daemon_memory_protection_failure_is_not_silently_accepted() {
    let root = test_root("memory-protection-failure");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    std::fs::create_dir_all(root.join("_daemon/memory.min")).unwrap();

    let error = prepare_root(&root, TEST_WORKLOAD_LIMITS)
        .expect_err("memory protection failure is visible");

    assert!(error.contains("memory.min"));
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn outer_memory_high_failure_is_not_silently_accepted() {
    let root = test_root("outer-memory-high-failure");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    std::fs::create_dir_all(root.join("memory.high")).unwrap();

    let error = prepare_root(&root, TEST_WORKLOAD_LIMITS)
        .expect_err("outer memory.high failure must disable workload delegation");

    assert!(error.contains("outer memory.high"), "{error}");
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn daemon_cpu_weight_failure_is_not_silently_accepted() {
    let root = test_root("daemon-cpu-weight-failure");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    std::fs::create_dir_all(root.join("_daemon/cpu.weight")).unwrap();

    let error = prepare_root(&root, TEST_WORKLOAD_LIMITS)
        .expect_err("daemon CPU priority failure must disable workload delegation");

    assert!(error.contains("daemon cpu.weight"), "{error}");
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn aggregate_cpu_weight_failure_is_not_silently_accepted() {
    let root = test_root("aggregate-cpu-weight-failure");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    let workloads = root.join("_workloads");
    std::fs::create_dir_all(workloads.join("cpu.weight")).unwrap();

    let error = prepare_root(&root, TEST_WORKLOAD_LIMITS)
        .expect_err("workload CPU priority failure must disable workload delegation");

    assert!(error.contains("aggregate workload cpu.weight"), "{error}");
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn aggregate_workload_limit_failure_disables_delegation() {
    let root = test_root("aggregate-limit-failure");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    let workloads = root.join("_workloads");
    std::fs::create_dir_all(workloads.join("memory.max")).unwrap();
    std::fs::write(workloads.join("cgroup.controllers"), "cpu memory pids").unwrap();

    let error = prepare_root(&root, TEST_WORKLOAD_LIMITS)
        .expect_err("an incomplete aggregate workload cap must fail closed");

    assert!(error.contains("aggregate workload"), "{error}");
    assert!(error.contains("memory.max"), "{error}");
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn missing_required_controller_disables_workload_delegation() {
    let root = test_root("missing-controller");
    std::fs::write(root.join("cgroup.controllers"), "cpu pids").unwrap();

    let error =
        prepare_root(&root, TEST_WORKLOAD_LIMITS).expect_err("memory controller is required");

    assert!(error.contains("memory"));
    std::fs::remove_dir_all(root).unwrap();
}

fn test_root(name: &str) -> PathBuf {
    let root = std::env::temp_dir().join(format!(
        "eos-cgroup-setup-{name}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(&root).unwrap();
    root
}
