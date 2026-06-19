use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

#[cfg(target_os = "linux")]
use std::collections::BTreeSet;
#[cfg(target_os = "linux")]
use std::path::PathBuf;
#[cfg(target_os = "linux")]
use std::time::{Duration, Instant};

#[cfg(target_os = "linux")]
use nix::sys::signal::{killpg, Signal};
#[cfg(target_os = "linux")]
use nix::unistd::Pid;

use crate::command::{CommandId, CommandLifecycleState, CommandProcessStore};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CommandRemountInspection {
    pub active_commands: usize,
    pub command_ids: Vec<CommandId>,
    pub process_group_ids: Vec<i32>,
    pub process_count: usize,
    pub quiesced_process_count: usize,
    pub pinned_cwd_count: usize,
    pub pinned_root_count: usize,
    pub pinned_fd_count: usize,
    pub pinned_mapped_file_count: usize,
    pub mountinfo_checked_count: usize,
    pub blocked_reason: Option<String>,
    pub inspected: bool,
    pub quiesce_attempted: bool,
    pub resumed: bool,
    pub detail: Option<String>,
}

impl CommandRemountInspection {
    #[must_use]
    pub fn reason_or_default(&self) -> &str {
        self.blocked_reason
            .as_deref()
            .unwrap_or("remount_inspection_blocked")
    }

    #[must_use]
    pub fn can_live_remount(&self) -> bool {
        self.active_commands > 0
            && self.blocked_reason.is_none()
            && self.inspected
            && self.quiesce_attempted
            && self.quiesced_process_count == self.process_count
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RemountSwitchState {
    Quiescing,
    ReadyToSwitch,
    CriticalSwitch,
    Resuming,
    Finished,
}

#[derive(Debug, Clone, Default)]
pub struct RemountCancellationToken {
    cancelled: Arc<AtomicBool>,
}

impl RemountCancellationToken {
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    pub fn request_cancel(&self) {
        self.cancelled.store(true, Ordering::Release);
    }

    #[must_use]
    pub fn is_cancelled(&self) -> bool {
        self.cancelled.load(Ordering::Acquire)
    }

    #[must_use]
    pub fn same_token(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.cancelled, &other.cancelled)
    }
}

pub struct CommandRemountQuiesce {
    pub(crate) inspection: CommandRemountInspection,
    pub(crate) held_process_group_ids: Vec<i32>,
    pub(crate) command_ids: Vec<CommandId>,
    pub(crate) process_store: Arc<CommandProcessStore>,
    pub(crate) cancellation: RemountCancellationToken,
    pub(crate) switch_state: RemountSwitchState,
    pub(crate) controller: Arc<dyn ProcessGroupController>,
}

impl std::fmt::Debug for CommandRemountQuiesce {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("CommandRemountQuiesce")
            .field("inspection", &self.inspection)
            .field("held_process_group_ids", &self.held_process_group_ids)
            .field("command_ids", &self.command_ids)
            .field("cancellation", &self.cancellation)
            .field("switch_state", &self.switch_state)
            .finish_non_exhaustive()
    }
}

impl CommandRemountQuiesce {
    #[must_use]
    pub const fn inspection(&self) -> &CommandRemountInspection {
        &self.inspection
    }

    #[must_use]
    pub fn cancellation(&self) -> RemountCancellationToken {
        self.cancellation.clone()
    }

    #[must_use]
    pub const fn switch_state(&self) -> RemountSwitchState {
        self.switch_state
    }

    pub fn set_switch_state(&mut self, state: RemountSwitchState) {
        self.switch_state = state;
        for command_id in &self.command_ids {
            let cancellation = self.cancellation.clone();
            self.process_store.update_active(command_id, |active| {
                if active
                    .remount_cancellation
                    .as_ref()
                    .is_some_and(|token| token.same_token(&cancellation))
                {
                    active.remount_switch_state = Some(state);
                }
            });
        }
    }

    #[must_use]
    pub fn cancellation_requested(&self) -> bool {
        self.cancellation.is_cancelled()
    }

    pub fn finish(mut self) -> CommandRemountInspection {
        self.resume();
        self.inspection.clone()
    }

    pub fn resume(&mut self) -> bool {
        if self.switch_state == RemountSwitchState::Finished {
            return self.inspection.resumed;
        }
        self.set_switch_state(RemountSwitchState::Resuming);
        let had_held_process_groups = !self.held_process_group_ids.is_empty();
        let mut all_resumed = true;
        for pgid in self.held_process_group_ids.drain(..) {
            all_resumed &= self.controller.resume_process_group_id(pgid);
        }
        self.resume_command_records();
        self.switch_state = RemountSwitchState::Finished;
        self.inspection.resumed |= had_held_process_groups && all_resumed;
        all_resumed
    }

    fn resume_command_records(&self) {
        for command_id in &self.command_ids {
            let cancellation = self.cancellation.clone();
            self.process_store.update_active(command_id, |active| {
                if !active
                    .remount_cancellation
                    .as_ref()
                    .is_some_and(|token| token.same_token(&cancellation))
                {
                    return;
                }
                active.remount_cancellation = None;
                active.remount_switch_state = None;
                if cancellation.is_cancelled() {
                    active.process.cancel_process();
                    active.lifecycle_state = CommandLifecycleState::Cancelled;
                } else {
                    active.lifecycle_state = CommandLifecycleState::Running;
                }
            });
        }
    }
}

impl Drop for CommandRemountQuiesce {
    fn drop(&mut self) {
        let _ = self.resume();
    }
}

#[doc(hidden)]
pub trait ProcessGroupController: Send + Sync {
    fn inspect_command_process_group(
        &self,
        pgid: i32,
        workspace_root: &Path,
    ) -> CommandRemountInspection;

    fn resume_process_group_id(&self, pgid: i32) -> bool;
}

pub(crate) struct ProcProcessGroupController;

impl ProcessGroupController for ProcProcessGroupController {
    fn inspect_command_process_group(
        &self,
        pgid: i32,
        workspace_root: &Path,
    ) -> CommandRemountInspection {
        inspect_isolated_command_process_group(pgid, workspace_root)
    }

    fn resume_process_group_id(&self, pgid: i32) -> bool {
        resume_process_group_id(pgid)
    }
}

pub(crate) fn merge_report(
    target: &mut CommandRemountInspection,
    source: CommandRemountInspection,
) {
    target.process_count += source.process_count;
    target.quiesced_process_count += source.quiesced_process_count;
    target.pinned_cwd_count += source.pinned_cwd_count;
    target.pinned_root_count += source.pinned_root_count;
    target.pinned_fd_count += source.pinned_fd_count;
    target.pinned_mapped_file_count += source.pinned_mapped_file_count;
    target.mountinfo_checked_count += source.mountinfo_checked_count;
    target.inspected |= source.inspected;
    target.quiesce_attempted |= source.quiesce_attempted;
    target.resumed |= source.resumed;
    if target.blocked_reason.is_none() {
        target.blocked_reason = source.blocked_reason;
    }
    if target.detail.is_none() {
        target.detail = source.detail;
    }
}

fn inspect_isolated_command_process_group(
    pgid: i32,
    workspace_root: &Path,
) -> CommandRemountInspection {
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (pgid, workspace_root);
        CommandRemountInspection {
            active_commands: 1,
            blocked_reason: Some("unsupported_platform".to_owned()),
            detail: Some("live remount inspection requires Linux /proc".to_owned()),
            ..CommandRemountInspection::default()
        }
    }

    #[cfg(target_os = "linux")]
    inspect_isolated_command_process_group_linux(pgid, workspace_root)
}

#[cfg(target_os = "linux")]
#[derive(Debug, Clone, Default, PartialEq, Eq)]
struct ProcessSnapshot {
    pids: BTreeSet<i32>,
    stopped: BTreeSet<i32>,
}

#[cfg(target_os = "linux")]
fn inspect_isolated_command_process_group_linux(
    pgid: i32,
    workspace_root: &Path,
) -> CommandRemountInspection {
    let mut report = CommandRemountInspection {
        active_commands: 1,
        quiesce_attempted: true,
        ..CommandRemountInspection::default()
    };
    let before = process_group_snapshot(pgid);
    report.process_count = before.pids.len();
    if before.pids.is_empty() {
        report.blocked_reason = Some("process_membership_changed".to_owned());
        report.detail = Some(format!("process group {pgid} had no live members"));
        return report;
    }

    if let Err(error) = killpg(Pid::from_raw(pgid), Signal::SIGSTOP) {
        report.blocked_reason = Some("freeze_failed".to_owned());
        report.detail = Some(error.to_string());
        return report;
    }

    let Some(stopped) = wait_for_group_stopped(pgid, &before.pids) else {
        report.blocked_reason = Some("freeze_timeout".to_owned());
        resume_process_group(&mut report, pgid);
        return report;
    };
    report.quiesced_process_count = stopped.stopped.len();
    let after = process_group_snapshot(pgid);
    report.process_count = after.pids.len();
    if after.pids != before.pids {
        report.blocked_reason = Some("process_membership_changed".to_owned());
        report.detail = Some(format!("before={:?} after={:?}", before.pids, after.pids));
        resume_process_group(&mut report, pgid);
        return report;
    }

    report.inspected = true;
    inspect_pinned_paths(&mut report, &after.pids, workspace_root);
    if report.blocked_reason.is_some() {
        resume_process_group(&mut report, pgid);
    }
    report
}

#[cfg(target_os = "linux")]
fn resume_process_group(report: &mut CommandRemountInspection, pgid: i32) {
    report.resumed = resume_process_group_id(pgid);
}

#[cfg(target_os = "linux")]
fn resume_process_group_id(pgid: i32) -> bool {
    killpg(Pid::from_raw(pgid), Signal::SIGCONT).is_ok()
}

#[cfg(not(target_os = "linux"))]
const fn resume_process_group_id(_pgid: i32) -> bool {
    false
}

#[cfg(target_os = "linux")]
fn wait_for_group_stopped(pgid: i32, expected: &BTreeSet<i32>) -> Option<ProcessSnapshot> {
    let deadline = Instant::now() + Duration::from_millis(500);
    loop {
        let snapshot = process_group_snapshot(pgid);
        if snapshot.pids == *expected && snapshot.stopped == *expected {
            return Some(snapshot);
        }
        if Instant::now() >= deadline {
            return None;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[cfg(target_os = "linux")]
fn process_group_snapshot(pgid: i32) -> ProcessSnapshot {
    let Ok(entries) = std::fs::read_dir("/proc") else {
        return ProcessSnapshot::default();
    };
    let mut snapshot = ProcessSnapshot::default();
    for entry in entries.filter_map(Result::ok) {
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|name| name.parse::<i32>().ok())
        else {
            continue;
        };
        if let Some((entry_pgid, state)) = read_proc_stat(pid) {
            if entry_pgid == pgid && state != 'Z' {
                snapshot.pids.insert(pid);
                if matches!(state, 'T' | 't') {
                    snapshot.stopped.insert(pid);
                }
            }
        }
    }
    snapshot
}

#[cfg(target_os = "linux")]
fn read_proc_stat(pid: i32) -> Option<(i32, char)> {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    parse_proc_stat(&stat)
}

#[cfg(target_os = "linux")]
fn parse_proc_stat(stat: &str) -> Option<(i32, char)> {
    let close = stat.rfind(") ")?;
    let fields: Vec<&str> = stat[close + 2..].split_whitespace().collect();
    let state = fields.first()?.chars().next()?;
    let pgrp = fields.get(2)?.parse::<i32>().ok()?;
    Some((pgrp, state))
}

#[cfg(target_os = "linux")]
fn inspect_pinned_paths(
    report: &mut CommandRemountInspection,
    pids: &BTreeSet<i32>,
    workspace_root: &Path,
) {
    for pid in pids {
        match proc_link_points_inside(*pid, "cwd", workspace_root) {
            Some(true) => {
                report.pinned_cwd_count += 1;
                report
                    .blocked_reason
                    .get_or_insert_with(|| "cwd_pinned_workspace".to_owned());
            }
            Some(false) => {}
            None => {
                report
                    .blocked_reason
                    .get_or_insert_with(|| "cwd_unavailable".to_owned());
                report
                    .detail
                    .get_or_insert_with(|| format!("failed to inspect cwd for pid {pid}"));
            }
        }
        match proc_link_points_inside(*pid, "root", workspace_root) {
            Some(true) => {
                report.pinned_root_count += 1;
                report
                    .blocked_reason
                    .get_or_insert_with(|| "root_pinned_workspace".to_owned());
            }
            Some(false) => {}
            None => {
                report
                    .blocked_reason
                    .get_or_insert_with(|| "root_unavailable".to_owned());
                report
                    .detail
                    .get_or_insert_with(|| format!("failed to inspect root for pid {pid}"));
            }
        }
        match inspect_proc_fds(*pid, workspace_root) {
            Some(count) => {
                report.pinned_fd_count += count;
                if count > 0 {
                    report
                        .blocked_reason
                        .get_or_insert_with(|| "fd_pinned_workspace".to_owned());
                }
            }
            None => {
                report
                    .blocked_reason
                    .get_or_insert_with(|| "fd_pinned_workspace".to_owned());
                report.detail.get_or_insert_with(|| {
                    format!("failed to inspect file descriptors for pid {pid}")
                });
            }
        }
        if let Some(count) = inspect_proc_maps(*pid, workspace_root) {
            report.pinned_mapped_file_count += count;
            if count > 0 {
                report
                    .blocked_reason
                    .get_or_insert_with(|| "mapped_file_pinned_workspace".to_owned());
            }
        } else {
            report
                .blocked_reason
                .get_or_insert_with(|| "mapped_file_unavailable".to_owned());
            report
                .detail
                .get_or_insert_with(|| format!("failed to inspect mapped files for pid {pid}"));
        }
        match mountinfo_has_workspace_mount(*pid, workspace_root) {
            Some(true) => report.mountinfo_checked_count += 1,
            Some(false) => {
                report.mountinfo_checked_count += 1;
                report
                    .blocked_reason
                    .get_or_insert_with(|| "mountinfo_mismatch".to_owned());
            }
            None => {
                report
                    .blocked_reason
                    .get_or_insert_with(|| "mountinfo_unavailable".to_owned());
                report
                    .detail
                    .get_or_insert_with(|| format!("failed to read mountinfo for pid {pid}"));
            }
        }
    }
}

#[cfg(target_os = "linux")]
fn proc_link_points_inside(pid: i32, name: &str, root: &Path) -> Option<bool> {
    std::fs::read_link(format!("/proc/{pid}/{name}"))
        .ok()
        .map(|path| path_is_inside(&path, root))
}

#[cfg(target_os = "linux")]
fn inspect_proc_fds(pid: i32, root: &Path) -> Option<usize> {
    let entries = std::fs::read_dir(format!("/proc/{pid}/fd")).ok()?;
    let mut count = 0;
    for entry in entries {
        let entry = entry.ok()?;
        let path = std::fs::read_link(entry.path()).ok()?;
        if path_is_inside(&path, root) {
            count += 1;
        }
    }
    Some(count)
}

#[cfg(target_os = "linux")]
fn inspect_proc_maps(pid: i32, root: &Path) -> Option<usize> {
    let maps = std::fs::read_to_string(format!("/proc/{pid}/maps")).ok()?;
    Some(
        maps.lines()
            .filter_map(|line| line.split_whitespace().last())
            .map(PathBuf::from)
            .filter(|path| path_is_inside(path, root))
            .count(),
    )
}

#[cfg(target_os = "linux")]
fn mountinfo_has_workspace_mount(pid: i32, root: &Path) -> Option<bool> {
    let mountinfo = std::fs::read_to_string(format!("/proc/{pid}/mountinfo")).ok()?;
    Some(mountinfo.lines().any(|line| {
        let mut fields = line.split_whitespace();
        let _id = fields.next();
        let _parent = fields.next();
        let _major_minor = fields.next();
        let _mount_root = fields.next();
        fields
            .next()
            .map(unescape_mountinfo_path)
            .is_some_and(|mountpoint| mountpoint == root)
    }))
}

#[cfg(target_os = "linux")]
fn unescape_mountinfo_path(raw: &str) -> PathBuf {
    let mut out = Vec::with_capacity(raw.len());
    let bytes = raw.as_bytes();
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'\\'
            && index + 3 < bytes.len()
            && bytes[index + 1..index + 4].iter().all(u8::is_ascii_digit)
        {
            if let Ok(value) = u8::from_str_radix(&raw[index + 1..index + 4], 8) {
                out.push(value);
                index += 4;
                continue;
            }
        }
        out.push(bytes[index]);
        index += 1;
    }
    PathBuf::from(String::from_utf8_lossy(&out).into_owned())
}

#[cfg(target_os = "linux")]
fn path_is_inside(path: &Path, root: &Path) -> bool {
    path == root || path.starts_with(root)
}

#[cfg(test)]
mod tests {
    use std::collections::VecDeque;
    use std::path::PathBuf;
    use std::sync::{Arc, Mutex};
    use std::time::Instant;

    use crate::command::{
        ActiveCommandProcess, CancellationState, CommandFinalizePolicy, CommandId,
        CommandLifecycleState, CommandOperationService, CommandProcessStore, CommandTraceOrigin,
        CommandTranscriptStore, FinalizationState, RealCommandLaunchDriver,
    };
    use crate::workspace_crate::{
        CallerId, CaptureChangesRequest, CreateWorkspaceRequest, DestroyWorkspaceRequest,
        LatestSnapshotRequest, RemountWorkspaceRequest, WorkspaceError, WorkspaceHandle,
        WorkspaceId, WorkspaceRuntimeHooks, WorkspaceRuntimeService,
    };
    use crate::workspace_remount::CommandRemountCoordinator;
    use crate::workspace_session::WorkspaceSessionService;

    use super::*;

    #[derive(Default)]
    struct FakeProcessGroupController {
        reports: Mutex<VecDeque<CommandRemountInspection>>,
        resumed: Mutex<Vec<i32>>,
        cancel_during_inspection: Mutex<Option<(Arc<CommandOperationService>, CommandId)>>,
        observed_lifecycle_after_cancel: Mutex<Option<CommandLifecycleState>>,
    }

    fn noop_workspace_runtime() -> Arc<WorkspaceRuntimeService> {
        Arc::new(WorkspaceRuntimeService::from_hooks_for_test(
            WorkspaceRuntimeHooks {
                create_workspace: Box::new(|_request: CreateWorkspaceRequest| {
                    Err(WorkspaceError::Setup {
                        step: "not configured".to_owned(),
                    })
                }),
                capture_changes: Box::new(
                    |_handle: &WorkspaceHandle, _request: CaptureChangesRequest| {
                        Err(WorkspaceError::Capture {
                            message: "not configured".to_owned(),
                        })
                    },
                ),
                remount_workspace: Box::new(
                    |_handle: &WorkspaceHandle, _request: RemountWorkspaceRequest| {
                        Err(WorkspaceError::Setup {
                            step: "not configured".to_owned(),
                        })
                    },
                ),
                destroy_workspace: Box::new(
                    |_handle: WorkspaceHandle, _request: DestroyWorkspaceRequest| {
                        Err(WorkspaceError::Setup {
                            step: "not configured".to_owned(),
                        })
                    },
                ),
                latest_snapshot: Box::new(|_request: LatestSnapshotRequest| {
                    Err(WorkspaceError::SnapshotAcquire {
                        source: "not configured".to_owned(),
                    })
                }),
            },
        ))
    }

    impl FakeProcessGroupController {
        fn push_report(&self, report: CommandRemountInspection) {
            self.reports
                .lock()
                .expect("test operation succeeds")
                .push_back(report);
        }

        fn resumed(&self) -> Vec<i32> {
            self.resumed
                .lock()
                .expect("test operation succeeds")
                .clone()
        }

        fn cancel_during_inspection(
            &self,
            service: Arc<CommandOperationService>,
            command_id: CommandId,
        ) {
            *self
                .cancel_during_inspection
                .lock()
                .expect("test operation succeeds") = Some((service, command_id));
        }

        fn observed_lifecycle_after_cancel(&self) -> Option<CommandLifecycleState> {
            self.observed_lifecycle_after_cancel
                .lock()
                .expect("test operation succeeds")
                .clone()
        }
    }

    impl ProcessGroupController for FakeProcessGroupController {
        fn inspect_command_process_group(
            &self,
            _pgid: i32,
            _workspace_root: &Path,
        ) -> CommandRemountInspection {
            if let Some((service, command_id)) = self
                .cancel_during_inspection
                .lock()
                .expect("test operation succeeds")
                .take()
            {
                service
                    .process_store()
                    .update_active(&command_id, |active| {
                        if let Some(token) = active.remount_cancellation.clone() {
                            token.request_cancel();
                        } else {
                            active.process.cancel_process();
                            active.lifecycle_state = CommandLifecycleState::Cancelled;
                        }
                        active.cancellation = CancellationState::Requested {
                            requested_at: Instant::now(),
                        };
                    })
                    .expect("active command exists");
                let lifecycle = service
                    .process_store()
                    .active(&command_id)
                    .expect("active command exists")
                    .lifecycle_state
                    .clone();
                *self
                    .observed_lifecycle_after_cancel
                    .lock()
                    .expect("test operation succeeds") = Some(lifecycle);
            }
            self.reports
                .lock()
                .expect("test operation succeeds")
                .pop_front()
                .unwrap_or(CommandRemountInspection {
                    active_commands: 1,
                    process_count: 1,
                    quiesced_process_count: 1,
                    inspected: true,
                    quiesce_attempted: true,
                    ..CommandRemountInspection::default()
                })
        }

        fn resume_process_group_id(&self, pgid: i32) -> bool {
            self.resumed
                .lock()
                .expect("test operation succeeds")
                .push(pgid);
            true
        }
    }

    fn command_service() -> CommandOperationService {
        let workspace = Arc::new(WorkspaceSessionService::new(noop_workspace_runtime()));
        CommandOperationService::with_process_store_for_test(
            workspace,
            command::CommandConfig::default(),
            CommandProcessStore::new(),
        )
    }

    fn command_service_with_controller(
        controller: Arc<dyn ProcessGroupController>,
    ) -> CommandOperationService {
        let workspace = Arc::new(WorkspaceSessionService::new(noop_workspace_runtime()));
        CommandOperationService::with_launch_driver_and_remount_controller_for_test(
            workspace,
            command::CommandConfig::default(),
            Arc::new(RealCommandLaunchDriver),
            controller,
        )
    }

    fn command_id(id: &str) -> CommandId {
        CommandId(id.to_owned())
    }

    fn caller_id(id: &str) -> CallerId {
        CallerId(id.to_owned())
    }

    fn workspace_session_id(id: &str) -> WorkspaceId {
        WorkspaceId(id.to_owned())
    }

    fn active_record(
        command_id: CommandId,
        caller_id: CallerId,
        workspace_session_id: WorkspaceId,
        workspace_root: PathBuf,
        pgid: Option<i32>,
    ) -> ActiveCommandProcess {
        let spec = command::CommandProcessSpec {
            id: command_id.0.clone(),
            caller_id: caller_id.0.clone(),
            command: "cat".to_owned(),
            cwd: None,
            timeout_seconds: None,
        };
        let process = match pgid {
            Some(pgid) => command::CommandProcess::inactive_with_process_group_for_test(spec, pgid),
            None => command::CommandProcess::inactive_for_test(spec),
        };
        ActiveCommandProcess {
            command_id: command_id.clone(),
            caller_id,
            workspace_session_id: workspace_session_id.clone(),
            workspace_root,
            process: Arc::new(process),
            transcript: CommandTranscriptStore::default(),
            finalize_policy: CommandFinalizePolicy::Session {
                workspace_session_id,
            },
            lifecycle_state: CommandLifecycleState::Running,
            cancellation: CancellationState::None,
            remount_cancellation: None,
            remount_switch_state: None,
            finalization: FinalizationState::NotStarted,
            trace_origin: CommandTraceOrigin,
            started_at: Instant::now(),
        }
    }

    fn seed_active(
        service: &CommandOperationService,
        command_id: CommandId,
        caller_id: CallerId,
        workspace_session_id: WorkspaceId,
        workspace_root: PathBuf,
        pgid: Option<i32>,
    ) {
        service
            .registry()
            .bind(command_id.clone(), workspace_session_id.clone())
            .expect("registry bind succeeds");
        let reservation = service
            .process_store()
            .try_reserve()
            .expect("reservation succeeds");
        service
            .process_store()
            .insert_active(
                reservation,
                active_record(
                    command_id,
                    caller_id,
                    workspace_session_id,
                    workspace_root,
                    pgid,
                ),
            )
            .expect("active insert succeeds");
    }

    #[test]
    fn command_remount_workspace_scan_finds_multiple_active_command_ids() {
        let controller = Arc::new(FakeProcessGroupController::default());
        let service = command_service_with_controller(controller.clone());
        seed_active(
            &service,
            command_id("cmd_b"),
            caller_id("caller-1"),
            workspace_session_id("workspace-1"),
            PathBuf::from("/workspace"),
            Some(12),
        );
        seed_active(
            &service,
            command_id("cmd_a"),
            caller_id("caller-1"),
            workspace_session_id("workspace-1"),
            PathBuf::from("/workspace"),
            Some(11),
        );

        let quiesce = service.begin_workspace_remount_quiesce(&workspace_session_id("workspace-1"));

        assert_eq!(
            quiesce.inspection().command_ids,
            vec![command_id("cmd_a"), command_id("cmd_b")]
        );
        assert_eq!(quiesce.inspection().process_group_ids, vec![11, 12]);
        assert!(quiesce.inspection().can_live_remount());
    }

    #[test]
    fn command_remount_no_active_commands_yields_unblocked_noop_inspection() {
        let service = command_service();

        let inspection = service
            .begin_workspace_remount_quiesce(&workspace_session_id("workspace-1"))
            .finish();

        assert_eq!(inspection.active_commands, 0);
        assert!(inspection.blocked_reason.is_none());
    }

    #[test]
    fn command_remount_unavailable_process_group_blocks() {
        let service = command_service();
        seed_active(
            &service,
            command_id("cmd_1"),
            caller_id("caller-1"),
            workspace_session_id("workspace-1"),
            PathBuf::from("/workspace"),
            None,
        );

        let inspection = service
            .begin_workspace_remount_quiesce(&workspace_session_id("workspace-1"))
            .finish();

        assert_eq!(
            inspection.blocked_reason.as_deref(),
            Some("process_group_unavailable")
        );
        assert!(!inspection.can_live_remount());
    }

    #[test]
    fn command_remount_blocked_inspection_resumes_stopped_groups() {
        let controller = Arc::new(FakeProcessGroupController::default());
        let service = command_service_with_controller(controller.clone());
        controller.push_report(CommandRemountInspection {
            active_commands: 1,
            process_count: 1,
            quiesced_process_count: 1,
            inspected: true,
            quiesce_attempted: true,
            ..CommandRemountInspection::default()
        });
        controller.push_report(CommandRemountInspection {
            active_commands: 1,
            process_count: 1,
            quiesced_process_count: 1,
            inspected: true,
            quiesce_attempted: true,
            blocked_reason: Some("fd_pinned_workspace".to_owned()),
            ..CommandRemountInspection::default()
        });
        seed_active(
            &service,
            command_id("cmd_1"),
            caller_id("caller-1"),
            workspace_session_id("workspace-1"),
            PathBuf::from("/workspace"),
            Some(101),
        );
        seed_active(
            &service,
            command_id("cmd_2"),
            caller_id("caller-1"),
            workspace_session_id("workspace-1"),
            PathBuf::from("/workspace"),
            Some(102),
        );

        let inspection = service
            .begin_workspace_remount_quiesce(&workspace_session_id("workspace-1"))
            .finish();

        assert_eq!(
            inspection.blocked_reason.as_deref(),
            Some("fd_pinned_workspace")
        );
        assert_eq!(controller.resumed(), vec![101]);
    }

    #[test]
    fn command_remount_drop_resumes_held_groups() {
        let controller = Arc::new(FakeProcessGroupController::default());
        let service = command_service_with_controller(controller.clone());
        seed_active(
            &service,
            command_id("cmd_1"),
            caller_id("caller-1"),
            workspace_session_id("workspace-1"),
            PathBuf::from("/workspace"),
            Some(101),
        );

        {
            let _quiesce =
                service.begin_workspace_remount_quiesce(&workspace_session_id("workspace-1"));
        }

        assert_eq!(controller.resumed(), vec![101]);
    }

    #[test]
    fn command_remount_cancel_while_quiesced_kills_only_after_resume() {
        let controller = Arc::new(FakeProcessGroupController::default());
        let service = command_service_with_controller(controller.clone());
        let command_id = command_id("cmd_1");
        seed_active(
            &service,
            command_id.clone(),
            caller_id("caller-1"),
            workspace_session_id("workspace-1"),
            PathBuf::from("/workspace"),
            Some(101),
        );
        let mut quiesce =
            service.begin_workspace_remount_quiesce(&workspace_session_id("workspace-1"));

        quiesce.cancellation().request_cancel();
        service
            .process_store()
            .update_active(&command_id, |active| {
                active.cancellation = CancellationState::Requested {
                    requested_at: Instant::now(),
                };
            });

        assert_eq!(
            service
                .process_store()
                .active(&command_id)
                .expect("active command exists")
                .lifecycle_state,
            CommandLifecycleState::QuiescedForRemount
        );
        quiesce.resume();
        assert_eq!(controller.resumed(), vec![101]);
        assert_eq!(
            service
                .process_store()
                .active(&command_id)
                .expect("active command exists")
                .lifecycle_state,
            CommandLifecycleState::Cancelled
        );
    }

    #[test]
    fn command_remount_cancel_during_inspection_waits_until_resume() {
        let controller = Arc::new(FakeProcessGroupController::default());
        let service = Arc::new(command_service_with_controller(controller.clone()));
        let command_id = command_id("cmd_1");
        seed_active(
            service.as_ref(),
            command_id.clone(),
            caller_id("caller-1"),
            workspace_session_id("workspace-1"),
            PathBuf::from("/workspace"),
            Some(101),
        );
        controller.cancel_during_inspection(Arc::clone(&service), command_id.clone());

        let mut quiesce =
            service.begin_workspace_remount_quiesce(&workspace_session_id("workspace-1"));

        assert_eq!(controller.resumed(), Vec::<i32>::new());
        assert_eq!(
            controller.observed_lifecycle_after_cancel(),
            Some(CommandLifecycleState::QuiescedForRemount)
        );
        assert_eq!(
            service
                .process_store()
                .active(&command_id)
                .expect("active command exists")
                .lifecycle_state,
            CommandLifecycleState::QuiescedForRemount
        );
        quiesce.resume();
        assert_eq!(controller.resumed(), vec![101]);
        assert_eq!(
            service
                .process_store()
                .active(&command_id)
                .expect("active command exists")
                .lifecycle_state,
            CommandLifecycleState::Cancelled
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn command_remount_proc_stat_parser_handles_spaces_in_command_name() {
        let stat = "123 (cmd with spaces) T 1 77 77 0 -1 4194304";
        assert_eq!(parse_proc_stat(stat), Some((77, 'T')));
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn command_remount_mountinfo_path_unescapes_octal_sequences() {
        assert_eq!(
            unescape_mountinfo_path("/tmp/with\\040space"),
            PathBuf::from("/tmp/with space")
        );
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn command_remount_maps_unavailable_blocks_inspection() {
        let mut report = CommandRemountInspection::default();
        let mut pids = BTreeSet::new();
        pids.insert(-1);

        inspect_pinned_paths(&mut report, &pids, Path::new("/workspace"));

        assert_eq!(report.blocked_reason.as_deref(), Some("cwd_unavailable"));
    }
}
