use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use crate::observability::cgroup::CgroupSample;

fn cgroup_fixture(label: &str) -> PathBuf {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let dir = std::env::temp_dir().join(format!(
        "sandbox-daemon-cgroup-{label}-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    std::fs::create_dir_all(&dir).expect("create cgroup fixture dir");
    dir
}

fn write_file(dir: &Path, name: &str, contents: &str) {
    std::fs::write(dir.join(name), contents).expect("write cgroup fixture file");
}

#[test]
fn read_parses_cpu_and_memory_counters_with_a_bounded_max() {
    let dir = cgroup_fixture("counters");
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
fn read_treats_memory_max_literal_as_unlimited() {
    let dir = cgroup_fixture("unlimited");
    write_file(&dir, "cpu.stat", "usage_usec 0\n");
    write_file(&dir, "memory.current", "0\n");
    write_file(&dir, "memory.max", "max\n");

    let sample = CgroupSample::read(&dir);

    assert!(sample.cgroup_available);
    assert_eq!(sample.memory_max_bytes, None);
    assert_eq!(sample.memory_max_unlimited, Some(true));
}

#[test]
fn read_degrades_when_a_required_controller_file_is_missing() {
    let dir = cgroup_fixture("missing-memory");
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
    assert!(error.contains("memory.current"), "unexpected error {error:?}");
}

#[test]
fn read_degrades_when_cpu_stat_lacks_usage_usec() {
    let dir = cgroup_fixture("no-usage");
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
