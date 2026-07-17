//! Leaf collectors: the cgroup v2 reader and the budgeted upperdir disk walk.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use sandbox_observability_telemetry::collect::cgroup::{CgroupRole, CgroupSample, CgroupTopology};
use sandbox_observability_telemetry::collect::disk::sample_upperdir;
use sandbox_observability_telemetry::WalkBudget;

fn fixture(label: &str) -> PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let dir = std::env::temp_dir().join(format!(
        "sandbox-obs-collect-{label}-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir_all(&dir).expect("create fixture dir");
    dir
}

fn write_file(dir: &Path, name: &str, contents: &str) {
    std::fs::write(dir.join(name), contents).expect("write fixture file");
}

#[test]
fn cgroup_read_parses_cpu_and_memory_counters_with_a_bounded_max() {
    let dir = fixture("counters");
    write_file(
        &dir,
        "cpu.stat",
        "usage_usec 123456\nuser_usec 100000\nsystem_usec 23456\n",
    );
    write_file(&dir, "memory.current", "8192\n");
    write_file(&dir, "memory.max", "16384\n");

    let sample = CgroupSample::read(&dir);

    assert!(sample.cgroup_available);
    assert_eq!(sample.cgroup_error, None);
    assert_eq!(sample.cpu_usage_usec, Some(123_456));
    assert_eq!(sample.memory_current_bytes, Some(8_192));
    assert_eq!(sample.memory_max_bytes, Some(16_384));
    assert_eq!(sample.memory_max_unlimited, Some(false));
    assert_eq!(
        sample.cgroup_path.as_deref(),
        Some(dir.to_string_lossy().as_ref())
    );
}

#[test]
fn cgroup_read_treats_memory_max_literal_as_unlimited() {
    let dir = fixture("unlimited");
    write_file(&dir, "cpu.stat", "usage_usec 0\n");
    write_file(&dir, "memory.current", "0\n");
    write_file(&dir, "memory.max", "max\n");

    let sample = CgroupSample::read(&dir);

    assert!(sample.cgroup_available);
    assert_eq!(sample.memory_max_bytes, None);
    assert_eq!(sample.memory_max_unlimited, Some(true));
}

#[test]
fn cgroup_read_degrades_when_a_required_controller_file_is_missing() {
    let dir = fixture("missing-memory");
    write_file(&dir, "cpu.stat", "usage_usec 10\n");

    let sample = CgroupSample::read(&dir);

    assert!(!sample.cgroup_available);
    assert!(sample.cpu_usage_usec.is_none());
    assert_eq!(
        sample.cgroup_path.as_deref(),
        Some(dir.to_string_lossy().as_ref())
    );
    let error = sample
        .cgroup_error
        .expect("missing controller file yields an error");
    assert!(
        error.contains("memory.current"),
        "unexpected error {error:?}"
    );
}

#[test]
fn cgroup_read_degrades_when_cpu_stat_lacks_usage_usec() {
    let dir = fixture("no-usage");
    write_file(&dir, "cpu.stat", "nr_periods 0\n");
    write_file(&dir, "memory.current", "0\n");
    write_file(&dir, "memory.max", "max\n");

    let sample = CgroupSample::read(&dir);

    assert!(!sample.cgroup_available);
    let error = sample
        .cgroup_error
        .expect("missing usage_usec yields an error");
    assert!(error.contains("usage_usec"), "unexpected error {error:?}");
}

#[test]
fn cgroup_topology_reads_runtime_groups_and_proc_membership() {
    let cgroup_root = fixture("topology-cgroup");
    let proc_root = fixture("topology-proc");
    write_cgroup(&cgroup_root, "100\n", 1_000, 8_192, "16384");
    write_file(&cgroup_root, "cgroup.controllers", "memory cpu io\n");

    let daemon = cgroup_root.join("_daemon");
    std::fs::create_dir_all(&daemon).expect("daemon group");
    write_cgroup(&daemon, "42\n", 600, 4_096, "max");

    let workspace = cgroup_root.join("workspace-ws-1");
    std::fs::create_dir_all(&workspace).expect("workspace group");
    write_cgroup(&workspace, "101\n100\n", 400, 4_096, "8192");

    write_proc(&proc_root, "self", "sandboxd", "0::/_daemon\n");
    write_proc(&proc_root, "42", "sandboxd", "0::/_daemon\n");
    write_proc(&proc_root, "100", "ns-runner", "0::/workspace-ws-1\n");
    write_proc(&proc_root, "101", "sh", "0::/workspace-ws-1\n");

    let topology = CgroupTopology::read(&cgroup_root, &proc_root);

    assert!(topology.available);
    assert_eq!(topology.self_cgroup.as_deref(), Some("0::/_daemon"));
    assert_eq!(topology.controllers, ["cpu", "io", "memory"]);
    assert_eq!(topology.groups.len(), 3);
    assert_eq!(topology.groups[0].role, CgroupRole::Root);
    assert_eq!(topology.groups[1].role, CgroupRole::Daemon);
    assert_eq!(topology.groups[2].role, CgroupRole::Workspace);
    assert_eq!(topology.groups[2].path, "/workspace-ws-1");
    assert_eq!(topology.groups[2].cpu_usage_usec, Some(400));
    assert_eq!(topology.groups[2].processes[0].pid, 100);
    assert_eq!(topology.groups[2].processes[0].name, "ns-runner");
    assert_eq!(
        topology.groups[2].processes[0].membership.as_deref(),
        Some("0::/workspace-ws-1")
    );
}

fn write_cgroup(dir: &Path, procs: &str, cpu: i64, memory: i64, memory_max: &str) {
    write_file(dir, "cgroup.procs", procs);
    write_file(dir, "cpu.stat", &format!("usage_usec {cpu}\n"));
    write_file(dir, "memory.current", &format!("{memory}\n"));
    write_file(dir, "memory.max", &format!("{memory_max}\n"));
}

fn write_proc(proc_root: &Path, pid: &str, name: &str, membership: &str) {
    let dir = proc_root.join(pid);
    std::fs::create_dir_all(&dir).expect("proc pid dir");
    write_file(&dir, "comm", &format!("{name}\n"));
    write_file(&dir, "cgroup", membership);
}

#[test]
fn disk_sample_totals_bytes_and_counts() {
    let dir = fixture("disk");
    write_file(&dir, "one.txt", "abc");
    write_file(&dir, "two.txt", "de");
    std::fs::create_dir_all(dir.join("nested")).expect("nested dir");
    write_file(&dir.join("nested"), "three.txt", "f");

    let sample = sample_upperdir(&dir, WalkBudget::default());

    assert_eq!(sample.upperdir_bytes, Some(6), "3 + 2 + 1 bytes");
    #[cfg(unix)]
    assert_eq!(
        sample.upperdir_allocated_bytes,
        Some(allocated_tree_bytes(&dir)),
        "allocated bytes include files and directory entries"
    );
    #[cfg(not(unix))]
    assert_eq!(sample.upperdir_allocated_bytes, None);
    assert_eq!(sample.file_count, Some(3));
    assert_eq!(sample.dir_count, Some(2), "root + nested");
    assert_eq!(sample.truncated, Some(false));
    assert_eq!(sample.read_error_count, Some(0));
}

#[test]
fn disk_sample_never_reports_a_partial_allocated_total() {
    let dir = fixture("disk-truncated");
    write_file(&dir, "one.txt", "abc");

    let sample = sample_upperdir(
        &dir,
        WalkBudget {
            max_nodes: 1,
            max_depth: 64,
        },
    );

    assert_eq!(sample.truncated, Some(true));
    assert_eq!(sample.upperdir_allocated_bytes, None);
}

#[test]
fn disk_sample_read_failure_never_reports_an_allocated_total() {
    let missing = fixture("disk-missing").join("not-present");

    let sample = sample_upperdir(&missing, WalkBudget::default());

    assert_eq!(sample.read_error_count, Some(1));
    assert!(sample.first_error_path.is_some());
    assert_eq!(sample.upperdir_allocated_bytes, None);
}

#[cfg(unix)]
fn allocated_tree_bytes(root: &Path) -> i64 {
    use std::os::unix::fs::MetadataExt;

    let mut total = 0_i64;
    let mut stack = vec![root.to_path_buf()];
    while let Some(path) = stack.pop() {
        let metadata = std::fs::symlink_metadata(&path).expect("fixture metadata");
        total = total
            .checked_add(i64::try_from(metadata.blocks()).expect("fixture blocks") * 512)
            .expect("fixture allocation total");
        if metadata.is_dir() {
            stack.extend(
                std::fs::read_dir(path)
                    .expect("fixture directory")
                    .map(|entry| entry.expect("fixture entry").path()),
            );
        }
    }
    total
}
