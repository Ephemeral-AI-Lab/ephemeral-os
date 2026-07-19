fn main() {
    #[cfg(target_os = "linux")]
    if let Err(error) = linux::main() {
        eprintln!("holder_lifecycle failed: {error}");
        std::process::exit(1);
    }

    #[cfg(not(target_os = "linux"))]
    println!("holder_lifecycle: ignored on non-Linux runner");
}

#[cfg(target_os = "linux")]
mod linux {
    use std::fs::{File, OpenOptions};
    use std::io::Write;
    use std::os::fd::RawFd;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::{Arc, Barrier};
    use std::time::{Duration, Instant};

    use nix::sys::signal::{kill, Signal};
    use nix::unistd::Pid;
    use sandbox_observability_telemetry::Observer;
    use sandbox_runtime_namespace_process::holder::{NamespaceNetwork, NsHolderError};
    use sandbox_runtime_namespace_process::runner::file_op::FileRunnerOp;
    use sandbox_runtime_namespace_process::runner::protocol::{NamespaceRunnerRequest, RunResult};
    use sandbox_runtime_workspace::model::{
        CaptureChangesRequest, CreateWorkspaceRequest, DestroyWorkspaceRequest, NetworkProfile,
        WorkspaceHandle, WorkspaceHolderIdentity,
    };
    use sandbox_runtime_workspace::session::{ResourceCaps, WorkspaceManager};
    use sandbox_runtime_workspace::{
        HolderExitWait, WorkspaceError, WorkspaceOwnershipSnapshot, WorkspaceRuntimeService,
    };
    use serde_json::json;

    type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

    pub(super) fn main() -> TestResult {
        let mut args = std::env::args();
        let _program = args.next();
        let args = args.collect::<Vec<_>>();
        match args.first().map(String::as_str) {
            Some("ns-holder") => run_holder(args.into_iter().skip(1)),
            Some("ns-runner") => run_runner(args.into_iter().skip(1)),
            _ => run_cases(&args),
        }
    }

    fn run_cases(args: &[String]) -> TestResult {
        let filter = args.iter().find(|arg| !arg.starts_with('-'));
        let cases: [(&str, fn() -> TestResult); 3] = [
            (
                "unexpected_holder_exit_is_detected_reaped_and_isolated",
                unexpected_holder_exit_is_detected_reaped_and_isolated,
            ),
            (
                "concurrent_holder_exit_and_destroy_join_one_teardown",
                concurrent_holder_exit_and_destroy_join_one_teardown,
            ),
            (
                "stale_holder_generation_cannot_reach_reused_workspace_id",
                stale_holder_generation_cannot_reach_reused_workspace_id,
            ),
        ];
        let selected = cases
            .into_iter()
            .filter(|(name, _)| filter.is_none_or(|filter| name.contains(filter)))
            .collect::<Vec<_>>();
        println!("running {} tests", selected.len());
        let mut capability_limited = 0;
        for (name, case) in selected {
            match case() {
                Ok(()) => println!("test {name} ... ok"),
                Err(error) if namespace_capability_error(error.as_ref()) => {
                    println!(
                        "test {name} ... ignored: Linux runner lacks required namespace capability: {error}"
                    );
                    capability_limited += 1;
                }
                Err(error) => return Err(format!("{name}: {error}").into()),
            }
        }
        if capability_limited > 0 {
            println!(
                "holder_lifecycle runner limitation: {capability_limited} selected tests lacked required Linux namespace capability"
            );
        }
        Ok(())
    }

    fn unexpected_holder_exit_is_detected_reaped_and_isolated() -> TestResult {
        let fixture = Fixture::new("unexpected-exit")?;
        let service = fixture.service();
        let subscription = service
            .take_holder_exit_subscription()?
            .expect("real runtime has one holder-exit subscription");
        let (listener, shutdown) = subscription.into_parts();
        assert_eq!(
            listener.wait_for_retry(Duration::ZERO),
            HolderExitWait::RetryDeadline
        );
        let failed = create_workspace(&service)?;
        let peer = create_workspace(&service)?;
        let failed_identity = failed.holder_identity();
        let peer_identity = peer.holder_identity();
        validate_holder_identity(&failed_identity)?;
        validate_holder_identity(&peer_identity)?;

        kill_exact_holder(&failed_identity)?;
        assert_eq!(
            listener.wait_for_retry(Duration::from_secs(1)),
            HolderExitWait::Wake
        );
        wait_for_holder_exit(&failed, Duration::from_secs(1))?;
        assert_eq!(failed.holder_exit_reason().as_deref(), Some("signal:9"));
        assert_eq!(
            service.run_file_op(
                &failed,
                None,
                FileRunnerOp::ReadFile {
                    rel: "README.md".to_owned(),
                    max_bytes: 1024,
                },
            ),
            Err(WorkspaceError::NotOpen)
        );
        assert_eq!(
            service.capture_changes(
                &failed,
                CaptureChangesRequest {
                    include_stats: false,
                },
            ),
            Err(WorkspaceError::NotOpen)
        );
        assert!(peer.holder_is_live());
        validate_holder_identity(&peer_identity)?;
        assert_reaped_without_zombie(&failed_identity, Duration::from_secs(1))?;

        service.destroy_workspace(failed, DestroyWorkspaceRequest::default())?;
        assert!(peer.holder_is_live());
        validate_holder_identity(&peer_identity)?;
        service.destroy_workspace(peer, DestroyWorkspaceRequest::default())?;
        assert_eq!(
            service.ownership_snapshot()?,
            WorkspaceOwnershipSnapshot::default()
        );
        shutdown.stop();
        Ok(())
    }

    fn concurrent_holder_exit_and_destroy_join_one_teardown() -> TestResult {
        let fixture = Fixture::new("exit-destroy-race")?;
        let service = fixture.service();
        let handle = create_workspace(&service)?;
        let identity = handle.holder_identity();
        let barrier = Arc::new(Barrier::new(3));
        let left_service = Arc::clone(&service);
        let left_handle = handle.clone();
        let left_barrier = Arc::clone(&barrier);
        let left = std::thread::spawn(move || {
            left_barrier.wait();
            left_service.destroy_workspace(left_handle, DestroyWorkspaceRequest::default())
        });
        let right_service = Arc::clone(&service);
        let right_handle = handle.clone();
        let right_barrier = Arc::clone(&barrier);
        let right = std::thread::spawn(move || {
            right_barrier.wait();
            right_service.destroy_workspace(right_handle, DestroyWorkspaceRequest::default())
        });

        kill_exact_holder(&identity)?;
        barrier.wait();
        let results = [
            left.join().map_err(|_| "left destroy thread panicked")?,
            right.join().map_err(|_| "right destroy thread panicked")?,
        ];
        assert!(results.iter().all(Result::is_ok));
        assert_eq!(results[0], results[1]);
        assert_reaped_without_zombie(&identity, Duration::from_secs(1))?;
        assert_eq!(
            service.destroy_workspace(handle, DestroyWorkspaceRequest::default()),
            results[0]
        );
        assert_eq!(
            service.ownership_snapshot()?,
            WorkspaceOwnershipSnapshot::default()
        );
        Ok(())
    }

    fn stale_holder_generation_cannot_reach_reused_workspace_id() -> TestResult {
        let fixture = Fixture::new("reused-workspace-id")?;
        let service = fixture.service();
        let id = service.allocate_workspace_session_id(NetworkProfile::Shared)?;
        let request = CreateWorkspaceRequest {
            workspace_session_id: id.clone(),
            network: NetworkProfile::Shared,
        };
        let stale = service.create_workspace(request.clone())?;
        let stale_identity = stale.holder_identity();
        service.destroy_workspace(stale.clone(), DestroyWorkspaceRequest::default())?;
        assert_reaped_without_zombie(&stale_identity, Duration::from_secs(1))?;

        let current = service.create_workspace(request)?;
        let current_identity = current.holder_identity();
        assert_ne!(stale_identity.generation, current_identity.generation);
        assert_eq!(
            service.capture_changes(
                &stale,
                CaptureChangesRequest {
                    include_stats: false,
                },
            ),
            Err(WorkspaceError::NotOpen)
        );
        assert_eq!(
            service.run_file_op(
                &stale,
                None,
                FileRunnerOp::ReadFile {
                    rel: "README.md".to_owned(),
                    max_bytes: 1024,
                },
            ),
            Err(WorkspaceError::NotOpen)
        );
        assert_eq!(
            service.destroy_workspace(stale, DestroyWorkspaceRequest::default()),
            Err(WorkspaceError::NotOpen)
        );
        assert!(current.holder_is_live());
        validate_holder_identity(&current_identity)?;

        service.destroy_workspace(current, DestroyWorkspaceRequest::default())?;
        assert_reaped_without_zombie(&current_identity, Duration::from_secs(1))?;
        assert_eq!(
            service.ownership_snapshot()?,
            WorkspaceOwnershipSnapshot::default()
        );
        Ok(())
    }

    fn create_workspace(
        service: &WorkspaceRuntimeService,
    ) -> Result<WorkspaceHandle, WorkspaceError> {
        let workspace_session_id = service.allocate_workspace_session_id(NetworkProfile::Shared)?;
        service.create_workspace(CreateWorkspaceRequest {
            workspace_session_id,
            network: NetworkProfile::Shared,
        })
    }

    fn wait_for_holder_exit(handle: &WorkspaceHandle, timeout: Duration) -> TestResult {
        let started = Instant::now();
        while handle.holder_is_live() && started.elapsed() < timeout {
            std::thread::sleep(Duration::from_millis(2));
        }
        if handle.holder_is_live() {
            return Err(format!(
                "holder {} remained live after {timeout:?}",
                handle.holder_pid
            )
            .into());
        }
        if started.elapsed() > timeout {
            return Err(format!("holder exit detection exceeded {timeout:?}").into());
        }
        Ok(())
    }

    fn kill_exact_holder(expected: &WorkspaceHolderIdentity) -> TestResult {
        validate_holder_identity(expected)?;
        kill(Pid::from_raw(expected.pid), Signal::SIGKILL)?;
        Ok(())
    }

    fn validate_holder_identity(expected: &WorkspaceHolderIdentity) -> TestResult<ProcIdentity> {
        let observed = read_proc_identity(expected.pid)?;
        if observed.parent_pid != expected.parent_pid
            || observed.start_time_ticks != expected.start_time_ticks
            || observed.executable != expected.executable
        {
            return Err(format!(
                "holder identity changed before signal: expected {expected:?}, observed {observed:?}"
            )
            .into());
        }
        Ok(observed)
    }

    fn assert_reaped_without_zombie(
        expected: &WorkspaceHolderIdentity,
        timeout: Duration,
    ) -> TestResult {
        let started = Instant::now();
        loop {
            match read_proc_identity(expected.pid) {
                Ok(observed) if observed.start_time_ticks != expected.start_time_ticks => {
                    return Ok(())
                }
                Ok(observed) if started.elapsed() < timeout => {
                    if observed.state != 'Z' {
                        std::thread::sleep(Duration::from_millis(2));
                    } else {
                        std::thread::yield_now();
                    }
                }
                Ok(observed) => {
                    return Err(format!(
                        "holder identity remained in /proc after {timeout:?}: {observed:?}"
                    )
                    .into())
                }
                Err(error) if proc_entry_missing(error.as_ref()) => return Ok(()),
                Err(error) => return Err(error),
            }
        }
    }

    #[derive(Debug)]
    struct ProcIdentity {
        state: char,
        parent_pid: i32,
        start_time_ticks: u64,
        executable: PathBuf,
    }

    fn read_proc_identity(pid: i32) -> TestResult<ProcIdentity> {
        let stat_path = PathBuf::from(format!("/proc/{pid}/stat"));
        let stat = std::fs::read_to_string(&stat_path)?;
        let close = stat
            .rfind(')')
            .ok_or_else(|| format!("malformed {}", stat_path.display()))?;
        let fields = stat[close + 1..].split_whitespace().collect::<Vec<_>>();
        let state = fields
            .first()
            .and_then(|value| value.chars().next())
            .ok_or_else(|| format!("missing state in {}", stat_path.display()))?;
        let parent_pid = fields
            .get(1)
            .ok_or_else(|| format!("missing parent pid in {}", stat_path.display()))?
            .parse()?;
        let start_time_ticks = fields
            .get(19)
            .ok_or_else(|| format!("missing start time in {}", stat_path.display()))?
            .parse()?;
        let executable = std::fs::read_link(format!("/proc/{pid}/exe"))?;
        Ok(ProcIdentity {
            state,
            parent_pid,
            start_time_ticks,
            executable,
        })
    }

    fn proc_entry_missing(error: &(dyn std::error::Error + Send + Sync)) -> bool {
        error
            .downcast_ref::<std::io::Error>()
            .is_some_and(|error| error.kind() == std::io::ErrorKind::NotFound)
    }

    fn namespace_capability_error(error: &(dyn std::error::Error + Send + Sync)) -> bool {
        let message = error.to_string();
        message.contains("Operation not permitted")
            || message.contains("os error 1")
            || message.contains("EPERM")
    }

    fn run_holder(mut args: impl Iterator<Item = String>) -> TestResult {
        let readiness_fd = parse_fd(args.next(), "readiness fd")?;
        let control_fd = parse_fd(args.next(), "control fd")?;
        let network = match args.next().as_deref() {
            Some("shared") => NamespaceNetwork::Shared,
            Some("isolated") => NamespaceNetwork::Isolated,
            value => return Err(format!("invalid holder network mode: {value:?}").into()),
        };
        match sandbox_runtime_namespace_process::holder::run(readiness_fd, control_fd, network) {
            Ok(()) => Ok(()),
            Err(NsHolderError::ControlPipeClosed) => {
                std::process::exit(NsHolderError::CONTROL_CLOSED_EXIT)
            }
            Err(NsHolderError::UnexpectedToken) => {
                std::process::exit(NsHolderError::UNEXPECTED_TOKEN_EXIT)
            }
            Err(error) => Err(error.into()),
        }
    }

    fn run_runner(args: impl Iterator<Item = String>) -> TestResult {
        let mut request_fd = None;
        let mut result_fd = None;
        let mut mode = None;
        let mut args = args.peekable();
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--mount-overlay" | "--remount-overlay" | "--file-op" | "--shell" => {
                    mode = Some(arg)
                }
                "--request-fd" => request_fd = Some(parse_fd(args.next(), "request fd")?),
                "--result-fd" => result_fd = Some(parse_fd(args.next(), "result fd")?),
                other => return Err(format!("unexpected ns-runner argument {other:?}").into()),
            }
        }
        let request_fd = request_fd.ok_or_else(|| std::io::Error::other("missing request fd"))?;
        let result_fd = result_fd.ok_or_else(|| std::io::Error::other("missing result fd"))?;
        let request: NamespaceRunnerRequest = serde_json::from_reader(open_fd(request_fd)?)?;
        let result = match mode.as_deref() {
            Some("--mount-overlay") => {
                match sandbox_runtime_namespace_process::runner::setns::setns_overlay_mount(
                    &request,
                    &[],
                ) {
                    Ok(()) => RunResult {
                        exit_code: 0,
                        payload: json!({"success": true, "status": "ok"}),
                    },
                    Err(error) => runner_error("overlay mount", error),
                }
            }
            Some("--remount-overlay") => {
                sandbox_runtime_namespace_process::runner::setns::setns_remount_overlay(
                    &request,
                    &[],
                )
                .unwrap_or_else(|error| runner_error("overlay remount", error))
            }
            Some("--file-op") => {
                sandbox_runtime_namespace_process::runner::file_op::run_file_op(&request)
            }
            Some("--shell") => sandbox_runtime_namespace_process::runner::run(&request)?,
            mode => return Err(format!("invalid ns-runner mode {mode:?}").into()),
        };
        let mut output = open_fd_for_write(result_fd)?;
        output.write_all(&serde_json::to_vec(&result)?)?;
        Ok(())
    }

    fn runner_error(step: &str, error: impl std::fmt::Display) -> RunResult {
        RunResult {
            exit_code: 1,
            payload: json!({"error": format!("ns-runner {step} failed: {error}")}),
        }
    }

    fn parse_fd(value: Option<String>, name: &str) -> TestResult<RawFd> {
        Ok(value.ok_or_else(|| format!("missing {name}"))?.parse()?)
    }

    fn open_fd(fd: RawFd) -> std::io::Result<File> {
        File::open(format!("/proc/self/fd/{fd}"))
    }

    fn open_fd_for_write(fd: RawFd) -> std::io::Result<File> {
        OpenOptions::new()
            .write(true)
            .open(format!("/proc/self/fd/{fd}"))
    }

    struct Fixture {
        service: Option<Arc<WorkspaceRuntimeService>>,
        base: PathBuf,
    }

    impl Fixture {
        fn new(label: &str) -> TestResult<Self> {
            let base = std::env::temp_dir().join(format!(
                "workspace-holder-lifecycle-{label}-{}-{}",
                std::process::id(),
                unique_suffix()
            ));
            let layer_stack_root = base.join("layer-stack");
            let workspace_root = base.join("workspace");
            let scratch_root = base.join("scratch");
            let layer = layer_stack_root.join("layers").join("B000001-base");
            std::fs::create_dir_all(&layer)?;
            std::fs::create_dir_all(layer_stack_root.join("staging"))?;
            std::fs::create_dir_all(&workspace_root)?;
            std::fs::write(layer.join("README.md"), "# README\n")?;
            std::fs::write(
                layer_stack_root.join("manifest.json"),
                serde_json::to_vec_pretty(&json!({
                    "schema_version": 1,
                    "version": 1,
                    "layers": [{"layer_id": "B000001-base", "path": "layers/B000001-base"}],
                }))?,
            )?;
            let service = Arc::new(WorkspaceRuntimeService::new(
                WorkspaceManager::new(
                    workspace_root.to_string_lossy().into_owned(),
                    ResourceCaps {
                        setup_timeout_s: 10.0,
                        exit_grace_s: 0.05,
                        ..ResourceCaps::default()
                    },
                    scratch_root,
                    Observer::disabled(),
                ),
                layer_stack_root,
            ));
            Ok(Self {
                service: Some(service),
                base,
            })
        }

        fn service(&self) -> Arc<WorkspaceRuntimeService> {
            Arc::clone(
                self.service
                    .as_ref()
                    .expect("fixture service remains available"),
            )
        }
    }

    impl Drop for Fixture {
        fn drop(&mut self) {
            self.service.take();
            let _ = std::fs::remove_dir_all(&self.base);
        }
    }

    fn unique_suffix() -> u64 {
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        COUNTER.fetch_add(1, Ordering::Relaxed)
    }
}
