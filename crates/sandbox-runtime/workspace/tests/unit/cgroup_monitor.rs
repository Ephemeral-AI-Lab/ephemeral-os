use std::path::{Path, PathBuf};

use sandbox_runtime_workspace::{
    build_cgroup_monitor_sample, session_cgroup_path, CgroupMonitorConfig, CgroupSampleKind,
    CgroupSampleRequest, WorkspaceSessionId,
};

#[test]
fn cgroup_monitor_session_path_uses_session_owned_tree() {
    let path = session_cgroup_path(
        Path::new("/sys/fs/cgroup"),
        &WorkspaceSessionId("wss_123".to_owned()),
    );

    assert_eq!(path, PathBuf::from("/sys/fs/cgroup/eos/sessions/wss_123"));
}

#[test]
fn cgroup_monitor_parses_complete_cgroup_files() -> Result<(), Box<dyn std::error::Error>> {
    let root = temp_root("complete")?;
    let cgroup = root.join("cgroup");
    let upper = root.join("upper");
    std::fs::create_dir_all(&cgroup)?;
    std::fs::create_dir_all(&upper)?;
    std::fs::write(upper.join("file.txt"), b"abcd")?;
    write_complete_cgroup_files(&cgroup)?;

    let sample = build_cgroup_monitor_sample(CgroupSampleRequest {
        cgroup_path: &cgroup,
        upperdir: Some(&upper),
        sample_kind: CgroupSampleKind::Periodic,
        interval_ms: 1000,
        previous: None,
        config: &CgroupMonitorConfig::default(),
    });

    assert_eq!(sample.cpu.usage_usec, Some(1200));
    assert_eq!(sample.memory.current_bytes, Some(4096));
    assert_eq!(sample.io.read_bytes, Some(10));
    assert_eq!(sample.pids.sampled, vec![123, 124]);
    assert_eq!(sample.pressure.memory.full_total_usec, Some(7));
    assert_eq!(sample.disk.upperdir_bytes, 4);
    assert_eq!(sample.state.cgroup_populated, Some(true));
    assert_eq!(sample.state.frozen, Some(false));
    assert!(sample.state.read_error.is_none());

    let _ = std::fs::remove_dir_all(root);
    Ok(())
}

#[test]
fn cgroup_monitor_missing_optional_files_produce_partial_sample(
) -> Result<(), Box<dyn std::error::Error>> {
    let root = temp_root("missing")?;
    let cgroup = root.join("cgroup");
    std::fs::create_dir_all(&cgroup)?;

    let sample = build_cgroup_monitor_sample(CgroupSampleRequest {
        cgroup_path: &cgroup,
        upperdir: None,
        sample_kind: CgroupSampleKind::Periodic,
        interval_ms: 1000,
        previous: None,
        config: &CgroupMonitorConfig::default(),
    });

    assert!(sample.state.cgroup_exists);
    assert_eq!(sample.cpu.usage_usec, None);
    assert_eq!(sample.memory.current_bytes, None);
    assert_eq!(sample.pids.sampled, Vec::<u32>::new());
    assert!(sample
        .state
        .read_error
        .as_deref()
        .is_some_and(|error| error.contains("cpu.stat")));

    let _ = std::fs::remove_dir_all(root);
    Ok(())
}

#[test]
fn cgroup_monitor_malformed_files_report_read_error() -> Result<(), Box<dyn std::error::Error>> {
    let root = temp_root("malformed")?;
    let cgroup = root.join("cgroup");
    std::fs::create_dir_all(&cgroup)?;
    std::fs::write(cgroup.join("cpu.stat"), "usage_usec nope\n")?;
    std::fs::write(cgroup.join("memory.current"), "not-a-number\n")?;
    std::fs::write(cgroup.join("memory.stat"), "anon 10\n")?;
    std::fs::write(cgroup.join("memory.events"), "oom bad\n")?;
    std::fs::write(cgroup.join("io.stat"), "8:0 rbytes=bad\n")?;
    std::fs::write(cgroup.join("pids.current"), "1\n")?;
    std::fs::write(cgroup.join("pids.peak"), "2\n")?;
    std::fs::write(cgroup.join("cgroup.procs"), "123\n")?;
    std::fs::write(cgroup.join("cpu.pressure"), "some avg10=bad total=3\n")?;
    std::fs::write(cgroup.join("memory.pressure"), "some avg10=0.00 total=4\n")?;
    std::fs::write(cgroup.join("io.pressure"), "some avg10=0.00 total=5\n")?;
    std::fs::write(cgroup.join("cgroup.events"), "populated 1\nfrozen 0\n")?;

    let sample = build_cgroup_monitor_sample(CgroupSampleRequest {
        cgroup_path: &cgroup,
        upperdir: None,
        sample_kind: CgroupSampleKind::Periodic,
        interval_ms: 1000,
        previous: None,
        config: &CgroupMonitorConfig::default(),
    });

    let error = sample
        .state
        .read_error
        .as_deref()
        .expect("malformed files are reported");
    assert!(error.contains("cpu.stat malformed value"));
    assert!(error.contains("memory.current malformed integer"));
    assert!(error.contains("io.stat malformed value"));
    assert_eq!(sample.memory.anon_bytes, Some(10));

    let _ = std::fs::remove_dir_all(root);
    Ok(())
}

fn write_complete_cgroup_files(cgroup: &Path) -> Result<(), Box<dyn std::error::Error>> {
    std::fs::write(
        cgroup.join("cpu.stat"),
        "usage_usec 1200\nuser_usec 800\nsystem_usec 400\nnr_periods 10\nnr_throttled 1\nthrottled_usec 5\n",
    )?;
    std::fs::write(cgroup.join("memory.current"), "4096\n")?;
    std::fs::write(cgroup.join("memory.peak"), "8192\n")?;
    std::fs::write(
        cgroup.join("memory.stat"),
        "anon 100\nfile 200\nkernel 300\n",
    )?;
    std::fs::write(
        cgroup.join("memory.events"),
        "low 0\nhigh 1\nmax 2\noom 3\noom_kill 4\n",
    )?;
    std::fs::write(
        cgroup.join("io.stat"),
        "8:0 rbytes=10 wbytes=20 rios=1 wios=2 dbytes=3 dios=4\n",
    )?;
    std::fs::write(cgroup.join("pids.current"), "2\n")?;
    std::fs::write(cgroup.join("pids.peak"), "4\n")?;
    std::fs::write(cgroup.join("cgroup.procs"), "123\n124\n")?;
    std::fs::write(
        cgroup.join("cpu.pressure"),
        "some avg10=0.10 avg60=0.20 avg300=0.30 total=5\n",
    )?;
    std::fs::write(
        cgroup.join("memory.pressure"),
        "some avg10=0.10 avg60=0.20 avg300=0.30 total=6\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=7\n",
    )?;
    std::fs::write(
        cgroup.join("io.pressure"),
        "some avg10=0.10 avg60=0.20 avg300=0.30 total=8\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=9\n",
    )?;
    std::fs::write(cgroup.join("cgroup.events"), "populated 1\nfrozen 0\n")?;
    Ok(())
}

fn temp_root(label: &str) -> Result<PathBuf, Box<dyn std::error::Error>> {
    Ok(std::env::temp_dir().join(format!(
        "sandbox-runtime-workspace-cgroup-monitor-{label}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_nanos()
    )))
}
