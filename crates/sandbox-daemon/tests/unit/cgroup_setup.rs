use std::collections::VecDeque;
use std::path::{Path, PathBuf};

#[cfg(target_os = "linux")]
use crate::cgroup_setup::prepare_root;
use crate::cgroup_setup::{
    enabled_controller_directives, evacuate_direct_root_processes, parse_cgroup_root,
    prepare_root_with_process_ops, read_direct_root_pids_file, RootProcessOps,
    WorkloadCgroupSettings, DIRECT_ROOT_PROCS_MAX_BYTES, DIRECT_ROOT_PROCS_MAX_PIDS,
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
    std::fs::write(root.join("memory.high"), "402653184").unwrap();

    let workloads = root.join("_workloads");
    std::fs::create_dir_all(&workloads).unwrap();
    std::fs::write(workloads.join("cgroup.controllers"), "cpu memory pids").unwrap();

    let prepared =
        prepare_test_root(&root, TEST_WORKLOAD_LIMITS).expect("delegated root is prepared");

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
        "402653184",
        "Docker owns the outer cgroup limit; delegated setup must not mutate it"
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
        "0"
    );
    assert_eq!(
        std::fs::read_to_string(workloads.join("pids.max")).unwrap(),
        "70"
    );
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn aggregate_workloads_cgroup_reserves_daemon_headroom_without_group_kill() {
    let root = test_root("peer-isolation");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    let workloads = root.join("_workloads");
    std::fs::create_dir_all(&workloads).unwrap();
    std::fs::write(workloads.join("cgroup.controllers"), "cpu memory pids").unwrap();
    std::fs::write(
        workloads.join("memory.max"),
        TEST_WORKLOAD_LIMITS.memory_max_bytes.to_string(),
    )
    .unwrap();
    std::fs::write(workloads.join("memory.oom.group"), "1").unwrap();

    prepare_test_root(&root, TEST_WORKLOAD_LIMITS).expect("delegated root is prepared");

    assert_eq!(
        std::fs::read_to_string(workloads.join("memory.max")).unwrap(),
        TEST_WORKLOAD_LIMITS.memory_max_bytes.to_string(),
        "the aggregate hard cap must leave the independently protected daemon outside the OOM domain"
    );
    assert_eq!(
        std::fs::read_to_string(workloads.join("memory.oom.group")).unwrap(),
        "0",
        "ancestor group OOM would terminate both workspace leaves"
    );
    assert_eq!(
        std::fs::read_to_string(workloads.join("memory.high")).unwrap(),
        TEST_WORKLOAD_LIMITS.memory_high_bytes.to_string(),
        "the soft aggregate pressure boundary must retain daemon headroom"
    );

    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn subtree_enable_failure_is_not_silently_accepted() {
    let root = test_root("subtree-failure");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    std::fs::create_dir(root.join("cgroup.subtree_control")).unwrap();

    let error = prepare_test_root(&root, TEST_WORKLOAD_LIMITS)
        .expect_err("subtree failure disables delegation");

    assert!(error.contains("enable controllers"));
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn daemon_memory_protection_failure_is_not_silently_accepted() {
    let root = test_root("memory-protection-failure");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    std::fs::create_dir_all(root.join("_daemon/memory.min")).unwrap();

    let error = prepare_test_root(&root, TEST_WORKLOAD_LIMITS)
        .expect_err("memory protection failure is visible");

    assert!(error.contains("memory.min"));
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn delegated_setup_does_not_write_the_outer_memory_high() {
    let root = test_root("outer-memory-high-owned-by-docker");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    std::fs::write(root.join("memory.high"), "503316480").unwrap();
    let workloads = root.join("_workloads");
    std::fs::create_dir_all(&workloads).unwrap();
    std::fs::write(workloads.join("cgroup.controllers"), "cpu memory pids").unwrap();

    prepare_test_root(&root, TEST_WORKLOAD_LIMITS).expect("delegated root is prepared");

    assert_eq!(
        std::fs::read_to_string(root.join("memory.high")).unwrap(),
        "503316480"
    );
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn daemon_cpu_weight_failure_is_not_silently_accepted() {
    let root = test_root("daemon-cpu-weight-failure");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    std::fs::create_dir_all(root.join("_daemon/cpu.weight")).unwrap();

    let error = prepare_test_root(&root, TEST_WORKLOAD_LIMITS)
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

    let error = prepare_test_root(&root, TEST_WORKLOAD_LIMITS)
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

    let error = prepare_test_root(&root, TEST_WORKLOAD_LIMITS)
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
        prepare_test_root(&root, TEST_WORKLOAD_LIMITS).expect_err("memory controller is required");

    assert!(error.contains("memory"));
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn every_direct_root_pid_is_pinned_before_one_pid_per_move() {
    let root = PathBuf::from("/sys/fs/cgroup/eos-test");
    let daemon = root.join("_daemon");
    let mut ops = ScriptedRootProcessOps::new([vec![1, 42], Vec::new()]);

    evacuate_direct_root_processes(&root, &daemon, &mut ops)
        .expect("all direct processes move into the daemon leaf");

    assert_eq!(
        ops.events,
        vec![
            ProcessEvent::Read {
                root: root.clone(),
                max_bytes: DIRECT_ROOT_PROCS_MAX_BYTES,
                max_pids: DIRECT_ROOT_PROCS_MAX_PIDS,
            },
            ProcessEvent::Pin {
                pid: 1,
                expected: root.clone(),
            },
            ProcessEvent::Pin {
                pid: 42,
                expected: root.clone(),
            },
            ProcessEvent::Move {
                pid: 1,
                expected: root.clone(),
                target: daemon.clone(),
            },
            ProcessEvent::Move {
                pid: 42,
                expected: root.clone(),
                target: daemon.clone(),
            },
            ProcessEvent::Read {
                root,
                max_bytes: DIRECT_ROOT_PROCS_MAX_BYTES,
                max_pids: DIRECT_ROOT_PROCS_MAX_PIDS,
            },
            ProcessEvent::ValidateCurrent { expected: daemon },
        ]
    );
}

#[test]
fn identity_pin_failure_moves_nothing_and_enables_no_controllers() {
    let root = test_root("root-pid-pin-race");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    let mut ops = ScriptedRootProcessOps::new([vec![1, 42], Vec::new()]);
    ops.fail_pin = Some(42);

    let error = prepare_root_with_process_ops(&root, TEST_WORKLOAD_LIMITS, &mut ops)
        .expect_err("a PID identity race must fail closed");

    assert!(error.contains("pin direct cgroup process 42"), "{error}");
    assert!(
        ops.events
            .iter()
            .all(|event| !matches!(event, ProcessEvent::Move { .. })),
        "no process may move until every direct PID identity is pinned"
    );
    assert!(!root.join("cgroup.subtree_control").exists());
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn pid_identity_change_immediately_before_move_fails_closed() {
    let root = test_root("root-pid-identity-change");
    std::fs::write(root.join("cgroup.controllers"), "cpu memory pids").unwrap();
    let mut ops = ScriptedRootProcessOps::new([vec![42], Vec::new()]);
    ops.fail_move = Some(42);

    let error = prepare_root_with_process_ops(&root, TEST_WORKLOAD_LIMITS, &mut ops)
        .expect_err("a changed PID identity must disable delegation");

    assert!(
        error.contains("identity changed before migration"),
        "{error}"
    );
    assert!(!root.join("cgroup.subtree_control").exists());
    std::fs::remove_dir_all(root).unwrap();
}

#[test]
fn residual_or_new_root_pid_after_moves_fails_closed() {
    let root = PathBuf::from("/sys/fs/cgroup/eos-test");
    let daemon = root.join("_daemon");
    let mut ops = ScriptedRootProcessOps::new([vec![42], vec![77]]);

    let error = evacuate_direct_root_processes(&root, &daemon, &mut ops)
        .expect_err("a residual process must keep controllers disabled");

    assert!(error.contains("still has direct processes"), "{error}");
    assert!(error.contains("77"), "{error}");
    assert!(
        ops.events
            .iter()
            .all(|event| !matches!(event, ProcessEvent::ValidateCurrent { .. })),
        "self validation only follows an empty root"
    );
}

#[test]
fn direct_root_pid_file_read_is_size_and_count_bounded() {
    let root = test_root("root-procs-bounds");
    let procs = root.join("cgroup.procs");
    std::fs::write(&procs, vec![b'7'; DIRECT_ROOT_PROCS_MAX_BYTES + 1]).unwrap();

    let size_error = read_direct_root_pids_file(
        &procs,
        DIRECT_ROOT_PROCS_MAX_BYTES,
        DIRECT_ROOT_PROCS_MAX_PIDS,
    )
    .expect_err("oversized cgroup.procs must fail closed");
    assert!(size_error.contains("byte read bound"), "{size_error}");

    let too_many = (0..=DIRECT_ROOT_PROCS_MAX_PIDS)
        .map(|index| format!("{}\n", index + 1))
        .collect::<String>();
    std::fs::write(&procs, too_many).unwrap();
    let count_error = read_direct_root_pids_file(
        &procs,
        DIRECT_ROOT_PROCS_MAX_BYTES,
        DIRECT_ROOT_PROCS_MAX_PIDS,
    )
    .expect_err("too many direct PIDs must fail closed");
    assert!(count_error.contains("PID count bound"), "{count_error}");

    std::fs::write(&procs, "1\nnot-a-pid\n").unwrap();
    let malformed_error = read_direct_root_pids_file(
        &procs,
        DIRECT_ROOT_PROCS_MAX_BYTES,
        DIRECT_ROOT_PROCS_MAX_PIDS,
    )
    .expect_err("malformed direct PID must fail closed");
    assert!(malformed_error.contains("invalid PID"), "{malformed_error}");
    std::fs::remove_dir_all(root).unwrap();
}

#[cfg(target_os = "linux")]
#[test]
fn real_cgroup_v2_vacates_a_direct_process_before_subtree_activation() {
    const ROOT_ENV: &str = "EOS_REAL_CGROUP_TEST_ROOT";
    const GATE_ENV: &str = "EOS_REAL_CGROUP_TEST_GATE";

    if let (Ok(root), Ok(gate)) = (std::env::var(ROOT_ENV), std::env::var(GATE_ENV)) {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while !Path::new(&gate).exists() {
            assert!(
                std::time::Instant::now() < deadline,
                "timed out waiting for parent cgroup placement"
            );
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        let root = PathBuf::from(root);
        let outer_high = std::fs::read_to_string(root.join("memory.high")).unwrap();
        let workloads = prepare_root(&root, TEST_WORKLOAD_LIMITS)
            .expect("real delegated cgroup root is prepared");
        assert_eq!(
            std::fs::read_to_string(root.join("memory.high")).unwrap(),
            outer_high,
            "daemon setup must not mutate the outer limit"
        );
        assert_eq!(
            std::fs::read_to_string(workloads.join("memory.max"))
                .unwrap()
                .trim(),
            TEST_WORKLOAD_LIMITS.memory_max_bytes.to_string()
        );
        return;
    }

    let Some(parent) = writable_cgroup_v2_parent() else {
        return;
    };
    let run_id = format!(
        "eos-cgroup-real-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let root = parent.join(&run_id);
    if let Err(error) = std::fs::create_dir(&root) {
        if error.kind() == std::io::ErrorKind::PermissionDenied || error.raw_os_error() == Some(30)
        {
            return;
        }
        panic!("create run-owned real cgroup {}: {error}", root.display());
    }
    if !has_required_controllers(&root) {
        std::fs::remove_dir(&root).unwrap();
        return;
    }

    let gate = std::env::temp_dir().join(format!("{run_id}.gate"));
    let mut child = std::process::Command::new(std::env::current_exe().unwrap())
        .args([
            "--exact",
            "cgroup_setup_tests::real_cgroup_v2_vacates_a_direct_process_before_subtree_activation",
            "--nocapture",
        ])
        .env(ROOT_ENV, &root)
        .env(GATE_ENV, &gate)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .expect("spawn isolated cgroup test helper");
    let child_pid = child.id();
    if let Err(error) = std::fs::write(root.join("cgroup.procs"), child_pid.to_string()) {
        let _ = child.kill();
        let _ = child.wait();
        let _ = std::fs::remove_dir(&root);
        if error.kind() == std::io::ErrorKind::PermissionDenied || error.raw_os_error() == Some(30)
        {
            return;
        }
        panic!(
            "place helper PID {child_pid} in {}: {error}",
            root.display()
        );
    }
    std::fs::write(&gate, b"go").expect("release real cgroup helper");
    let output = child.wait_with_output().expect("wait for cgroup helper");

    let subtree = std::fs::read_to_string(root.join("cgroup.subtree_control"))
        .unwrap_or_else(|error| format!("unreadable: {error}"));
    let aggregate_max = std::fs::read_to_string(root.join("_workloads/memory.max"))
        .unwrap_or_else(|error| format!("unreadable: {error}"));
    let cleanup_errors = cleanup_real_cgroup(&root);
    let _ = std::fs::remove_file(&gate);

    assert!(
        output.status.success(),
        "real cgroup helper failed: stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(has_controller_names(&subtree), "subtree={subtree:?}");
    assert_eq!(
        aggregate_max.trim(),
        TEST_WORKLOAD_LIMITS.memory_max_bytes.to_string()
    );
    assert!(cleanup_errors.is_empty(), "{cleanup_errors:?}");
}

#[cfg(target_os = "linux")]
fn writable_cgroup_v2_parent() -> Option<PathBuf> {
    let membership = std::fs::read_to_string("/proc/self/cgroup").ok()?;
    let parent = parse_cgroup_root(&membership)?;
    has_required_controllers(&parent).then_some(parent)
}

#[cfg(target_os = "linux")]
fn has_required_controllers(root: &Path) -> bool {
    std::fs::read_to_string(root.join("cgroup.controllers"))
        .is_ok_and(|controllers| has_controller_names(&controllers))
}

#[cfg(target_os = "linux")]
fn has_controller_names(controllers: &str) -> bool {
    ["cpu", "memory", "pids"]
        .into_iter()
        .all(|required| controllers.split_whitespace().any(|name| name == required))
}

#[cfg(target_os = "linux")]
fn cleanup_real_cgroup(root: &Path) -> Vec<String> {
    let mut errors = Vec::new();
    for child in [root.join("_workloads"), root.join("_daemon")] {
        match std::fs::remove_dir(&child) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => errors.push(format!("remove {}: {error}", child.display())),
        }
    }
    if let Err(error) = std::fs::remove_dir(root) {
        errors.push(format!("remove {}: {error}", root.display()));
    }
    errors
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum ProcessEvent {
    Read {
        root: PathBuf,
        max_bytes: usize,
        max_pids: usize,
    },
    Pin {
        pid: u32,
        expected: PathBuf,
    },
    Move {
        pid: u32,
        expected: PathBuf,
        target: PathBuf,
    },
    ValidateCurrent {
        expected: PathBuf,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct FakePinnedProcess {
    pid: u32,
    generation: u64,
}

struct ScriptedRootProcessOps {
    reads: VecDeque<Vec<u32>>,
    events: Vec<ProcessEvent>,
    fail_pin: Option<u32>,
    fail_move: Option<u32>,
}

impl ScriptedRootProcessOps {
    fn new(reads: impl IntoIterator<Item = Vec<u32>>) -> Self {
        Self {
            reads: reads.into_iter().collect(),
            events: Vec::new(),
            fail_pin: None,
            fail_move: None,
        }
    }
}

impl RootProcessOps for ScriptedRootProcessOps {
    type PinnedProcess = FakePinnedProcess;

    fn read_direct_pids(
        &mut self,
        root: &Path,
        max_bytes: usize,
        max_pids: usize,
    ) -> Result<Vec<u32>, String> {
        self.events.push(ProcessEvent::Read {
            root: root.to_path_buf(),
            max_bytes,
            max_pids,
        });
        self.reads
            .pop_front()
            .ok_or_else(|| "unexpected direct PID read".to_owned())
    }

    fn pin_process(
        &mut self,
        pid: u32,
        expected_cgroup: &Path,
    ) -> Result<Self::PinnedProcess, String> {
        self.events.push(ProcessEvent::Pin {
            pid,
            expected: expected_cgroup.to_path_buf(),
        });
        if self.fail_pin == Some(pid) {
            return Err("synthetic PID disappeared while pinning".to_owned());
        }
        Ok(FakePinnedProcess {
            pid,
            generation: u64::from(pid) * 10,
        })
    }

    fn move_process(
        &mut self,
        process: &Self::PinnedProcess,
        expected_cgroup: &Path,
        target_cgroup: &Path,
    ) -> Result<(), String> {
        self.events.push(ProcessEvent::Move {
            pid: process.pid,
            expected: expected_cgroup.to_path_buf(),
            target: target_cgroup.to_path_buf(),
        });
        assert_eq!(process.generation, u64::from(process.pid) * 10);
        if self.fail_move == Some(process.pid) {
            return Err("identity changed before migration".to_owned());
        }
        Ok(())
    }

    fn validate_current_process(&mut self, expected_cgroup: &Path) -> Result<(), String> {
        self.events.push(ProcessEvent::ValidateCurrent {
            expected: expected_cgroup.to_path_buf(),
        });
        Ok(())
    }
}

fn prepare_test_root(root: &Path, settings: WorkloadCgroupSettings) -> Result<PathBuf, String> {
    let mut ops = ScriptedRootProcessOps::new([vec![std::process::id()], Vec::new()]);
    prepare_root_with_process_ops(root, settings, &mut ops)
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
