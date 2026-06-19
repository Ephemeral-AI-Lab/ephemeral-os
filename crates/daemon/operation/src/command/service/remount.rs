#[cfg(target_os = "linux")]
use std::collections::BTreeSet;
use std::path::Path;
#[cfg(target_os = "linux")]
use std::path::PathBuf;
#[cfg(target_os = "linux")]
use std::time::{Duration, Instant};

#[cfg(target_os = "linux")]
use nix::sys::signal::{killpg, Signal};
#[cfg(target_os = "linux")]
use nix::unistd::Pid;

use super::CommandOps;
use crate::command::registry::ActiveCommand;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CommandRemountInspection {
    pub active_commands: usize,
    pub remountable_commands: usize,
    pub command_ids: Vec<String>,
    pub process_group_ids: Vec<i32>,
    pub process_count: usize,
    pub quiesced_process_count: usize,
    pub pinned_cwd_count: usize,
    pub pinned_root_count: usize,
    pub pinned_fd_count: usize,
    pub pinned_mapped_file_count: usize,
    pub mountinfo_checked_count: usize,
    pub blocked_reason: Option<&'static str>,
    pub inspected: bool,
    pub quiesce_attempted: bool,
    pub resumed: bool,
    pub detail: Option<String>,
}

impl CommandRemountInspection {
    #[must_use]
    pub fn reason_or_default(&self) -> &'static str {
        self.blocked_reason
            .unwrap_or("session_not_marked_remountable")
    }

    #[must_use]
    pub fn can_live_remount(&self) -> bool {
        self.active_commands > 0
            && self.blocked_reason.is_none()
            && self.remountable_commands == self.active_commands
            && self.inspected
            && self.quiesce_attempted
            && self.quiesced_process_count == self.process_count
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CommandRemountQuiesce {
    inspection: CommandRemountInspection,
    held_process_group_ids: Vec<i32>,
}

impl CommandRemountQuiesce {
    #[must_use]
    pub const fn inspection(&self) -> &CommandRemountInspection {
        &self.inspection
    }

    pub fn finish(mut self) -> CommandRemountInspection {
        self.resume();
        self.inspection.clone()
    }

    pub fn resume(&mut self) -> bool {
        if self.held_process_group_ids.is_empty() {
            return self.inspection.resumed;
        }
        let mut all_resumed = true;
        for pgid in self.held_process_group_ids.drain(..) {
            all_resumed &= resume_process_group_id(pgid);
        }
        self.inspection.resumed = all_resumed;
        all_resumed
    }
}

impl Drop for CommandRemountQuiesce {
    fn drop(&mut self) {
        let _ = self.resume();
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
#[cfg(target_os = "linux")]
struct ProcessSnapshot {
    pids: BTreeSet<i32>,
    stopped: BTreeSet<i32>,
}

impl CommandOps {
    #[must_use]
    pub fn inspect_live_remount_for_caller(&self, caller_id: &str) -> CommandRemountInspection {
        self.begin_live_remount_for_caller(caller_id).finish()
    }

    #[must_use]
    pub fn begin_live_remount_for_caller(&self, caller_id: &str) -> CommandRemountQuiesce {
        let runs = self.registry.caller_commands(caller_id);
        let mut quiesce = CommandRemountQuiesce {
            inspection: CommandRemountInspection {
                active_commands: runs.len(),
                ..CommandRemountInspection::default()
            },
            held_process_group_ids: Vec::new(),
        };
        if runs.is_empty() {
            return quiesce;
        }

        for run in runs {
            let command_id = run.process().id().to_owned();
            quiesce.inspection.command_ids.push(command_id.clone());
            let ActiveCommand::Workspace(isolated) = &*run;
            if isolated.remountable {
                quiesce.inspection.remountable_commands += 1;
            }
            let Some(pgid) = run.process().process_group_id() else {
                quiesce
                    .inspection
                    .blocked_reason
                    .get_or_insert("process_group_unavailable");
                continue;
            };
            quiesce.inspection.process_group_ids.push(pgid);
            let command_report = inspect_isolated_command_process_group(
                pgid,
                &isolated.context.workspace_root,
                isolated.remountable,
            );
            let held = command_report.blocked_reason.is_none() && isolated.remountable;
            merge_report(&mut quiesce.inspection, command_report);
            if held {
                quiesce.held_process_group_ids.push(pgid);
            }
        }

        quiesce.inspection.command_ids.sort();
        quiesce.inspection.command_ids.dedup();
        quiesce.inspection.process_group_ids.sort_unstable();
        quiesce.inspection.process_group_ids.dedup();
        if quiesce.inspection.blocked_reason.is_none()
            && quiesce.inspection.active_commands > 0
            && quiesce.inspection.remountable_commands != quiesce.inspection.active_commands
        {
            quiesce.inspection.blocked_reason = Some("session_not_marked_remountable");
        }
        if !quiesce.inspection.can_live_remount() {
            quiesce.resume();
        }
        quiesce
    }
}

fn merge_report(target: &mut CommandRemountInspection, source: CommandRemountInspection) {
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
    hold_if_unblocked: bool,
) -> CommandRemountInspection {
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (pgid, workspace_root, hold_if_unblocked);
        return CommandRemountInspection {
            active_commands: 1,
            blocked_reason: Some("unsupported_platform"),
            detail: Some("live remount inspection requires Linux /proc".to_owned()),
            ..CommandRemountInspection::default()
        };
    }

    #[cfg(target_os = "linux")]
    inspect_isolated_command_process_group_linux(pgid, workspace_root, hold_if_unblocked)
}

#[cfg(target_os = "linux")]
fn inspect_isolated_command_process_group_linux(
    pgid: i32,
    workspace_root: &Path,
    hold_if_unblocked: bool,
) -> CommandRemountInspection {
    let mut report = CommandRemountInspection {
        active_commands: 1,
        quiesce_attempted: true,
        ..CommandRemountInspection::default()
    };
    let before = process_group_snapshot(pgid);
    report.process_count = before.pids.len();
    if before.pids.is_empty() {
        report.blocked_reason = Some("process_membership_changed");
        report.detail = Some(format!("process group {pgid} had no live members"));
        return report;
    }

    if let Err(error) = killpg(Pid::from_raw(pgid), Signal::SIGSTOP) {
        report.blocked_reason = Some("freeze_failed");
        report.detail = Some(error.to_string());
        return report;
    }

    let Some(stopped) = wait_for_group_stopped(pgid, &before.pids) else {
        report.blocked_reason = Some("freeze_timeout");
        resume_process_group(&mut report, pgid);
        return report;
    };
    report.quiesced_process_count = stopped.stopped.len();
    let after = process_group_snapshot(pgid);
    report.process_count = after.pids.len();
    if after.pids != before.pids {
        report.blocked_reason = Some("process_membership_changed");
        report.detail = Some(format!("before={:?} after={:?}", before.pids, after.pids));
        resume_process_group(&mut report, pgid);
        return report;
    }

    report.inspected = true;
    inspect_pinned_paths(&mut report, &after.pids, workspace_root);
    if report.blocked_reason.is_none() {
        if hold_if_unblocked {
            return report;
        }
        report.blocked_reason = Some("session_not_marked_remountable");
    }
    resume_process_group(&mut report, pgid);
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
        if proc_link_points_inside(*pid, "cwd", workspace_root) {
            report.pinned_cwd_count += 1;
            report.blocked_reason.get_or_insert("cwd_pinned_workspace");
        }
        if proc_link_points_inside(*pid, "root", workspace_root) {
            report.pinned_root_count += 1;
            report.blocked_reason.get_or_insert("root_pinned_workspace");
        }
        match inspect_proc_fds(*pid, workspace_root) {
            Some(count) => {
                report.pinned_fd_count += count;
                if count > 0 {
                    report.blocked_reason.get_or_insert("fd_pinned_workspace");
                }
            }
            None => {
                report.blocked_reason.get_or_insert("fd_pinned_workspace");
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
                    .get_or_insert("mapped_file_pinned_workspace");
            }
        }
        match mountinfo_has_workspace_mount(*pid, workspace_root) {
            Some(true) => report.mountinfo_checked_count += 1,
            Some(false) => {
                report.mountinfo_checked_count += 1;
                report.blocked_reason.get_or_insert("mountinfo_mismatch");
            }
            None => {
                report.blocked_reason.get_or_insert("mountinfo_unavailable");
                report
                    .detail
                    .get_or_insert_with(|| format!("failed to read mountinfo for pid {pid}"));
            }
        }
    }
}

#[cfg(target_os = "linux")]
fn proc_link_points_inside(pid: i32, name: &str, root: &Path) -> bool {
    std::fs::read_link(format!("/proc/{pid}/{name}")).is_ok_and(|path| path_is_inside(&path, root))
}

#[cfg(target_os = "linux")]
fn inspect_proc_fds(pid: i32, root: &Path) -> Option<usize> {
    let entries = std::fs::read_dir(format!("/proc/{pid}/fd")).ok()?;
    let mut count = 0;
    for entry in entries.filter_map(Result::ok) {
        if std::fs::read_link(entry.path()).is_ok_and(|path| path_is_inside(&path, root)) {
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

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;

    #[test]
    fn proc_stat_parser_handles_spaces_in_command_name() {
        let stat = "123 (cmd with spaces) T 1 77 77 0 -1 4194304";
        assert_eq!(parse_proc_stat(stat), Some((77, 'T')));
    }

    #[test]
    fn mountinfo_path_unescapes_octal_sequences() {
        assert_eq!(
            unescape_mountinfo_path("/tmp/with\\040space"),
            PathBuf::from("/tmp/with space")
        );
    }
}
