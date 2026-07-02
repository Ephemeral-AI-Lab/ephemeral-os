//! Parser-rule and freeze-mechanics coverage for the holder-scope quiesce.
//! The full discovery/freeze/inspect matrix against live namespaces runs in
//! the Docker e2e suite (E1/E2/E4) through the daemon.

mod quiesce_src {
    #[allow(
        dead_code,
        reason = "the include!d module is fully consumed by the lib target; this test exercises the parser and freeze subset"
    )]
    pub mod quiesce {
        include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/quiesce.rs"));
    }
}

use quiesce_src::quiesce;

#[test]
fn task_state_parses_after_the_comm_parens() {
    assert_eq!(quiesce::task_state("4321 (bash) T 1 4321"), Some('T'));
    assert_eq!(
        quiesce::task_state("77 (weird) name) R 1 77"),
        Some('R'),
        "state comes after the LAST close paren, so parens in comm are safe"
    );
    assert_eq!(quiesce::task_state("no parens"), None);
}

#[test]
fn mountinfo_lines_parse_mountpoint_and_fstype_with_octal_escapes() {
    let line = r"182 176 0:77 / /eos/ws\040with\040space rw,relatime - overlay none rw,userxattr";
    let (mountpoint, fstype) = quiesce::parse_mountinfo_line(line).expect("parse");
    assert_eq!(mountpoint, "/eos/ws with space");
    assert_eq!(fstype, "overlay");

    let optional_tags =
        "158 148 254:1 /docker /eos/workspace rw,relatime master:1 - ext4 /dev/vda1 rw";
    let (mountpoint, fstype) = quiesce::parse_mountinfo_line(optional_tags).expect("parse");
    assert_eq!(mountpoint, "/eos/workspace");
    assert_eq!(
        fstype, "ext4",
        "optional shared/master tags are skipped to the separator"
    );

    assert!(quiesce::parse_mountinfo_line("garbage").is_none());
}

#[test]
fn octal_unescape_decodes_and_tolerates_junk() {
    assert_eq!(quiesce::octal_unescape(r"a\040b"), "a b");
    assert_eq!(quiesce::octal_unescape(r"tab\011end"), "tab\tend");
    assert_eq!(quiesce::octal_unescape(r"broken\zz"), r"broken\zz");
    assert_eq!(quiesce::octal_unescape("plain"), "plain");
}

#[cfg(target_os = "linux")]
mod linux_freeze {
    use std::collections::BTreeSet;
    use std::process::{Child, Command, Stdio};
    use std::time::Duration;

    use super::quiesce;

    fn spawn_sleeper() -> Child {
        Command::new("sleep")
            .arg("300")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("spawn sleeper")
    }

    fn state_of(pid: i32) -> Option<char> {
        let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
        quiesce::task_state(&stat)
    }

    #[test]
    fn poll_all_stopped_reaches_t_and_times_out_on_stragglers() {
        let mut children: Vec<Child> = (0..3).map(|_| spawn_sleeper()).collect();
        let pids: Vec<i32> = children
            .iter()
            .map(|child| i32::try_from(child.id()).expect("pid"))
            .collect();
        for pid in &pids {
            nix::sys::signal::kill(
                nix::unistd::Pid::from_raw(*pid),
                nix::sys::signal::Signal::SIGSTOP,
            )
            .expect("SIGSTOP");
        }
        let frozen: BTreeSet<i32> = pids.iter().copied().collect();
        quiesce::poll_all_stopped(&pids, &frozen, Duration::from_secs(2))
            .expect("all sleepers reach T");

        let straggler = spawn_sleeper();
        let straggler_pid = i32::try_from(straggler.id()).expect("pid");
        let mut with_running = pids.clone();
        with_running.push(straggler_pid);
        let reason = quiesce::poll_all_stopped(&with_running, &frozen, Duration::from_millis(50))
            .expect_err("a running task must exhaust the budget");
        assert_eq!(reason, "quiesce_failed:freeze_timeout");

        let mut straggler = straggler;
        let _ = straggler.kill();
        let _ = straggler.wait();
        for child in &mut children {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    #[test]
    fn frozen_tasks_guard_resumes_on_drop() {
        let mut child = spawn_sleeper();
        let pid = i32::try_from(child.id()).expect("pid");
        nix::sys::signal::kill(
            nix::unistd::Pid::from_raw(pid),
            nix::sys::signal::Signal::SIGSTOP,
        )
        .expect("SIGSTOP");
        let frozen: BTreeSet<i32> = [pid].into_iter().collect();
        quiesce::poll_all_stopped(&[pid], &frozen, Duration::from_secs(2)).expect("stopped");

        // Drop-resume is the FrozenTasks contract; SIGCONT by hand mirrors it
        // here because the guard's fields are private to the src include.
        nix::sys::signal::kill(
            nix::unistd::Pid::from_raw(pid),
            nix::sys::signal::Signal::SIGCONT,
        )
        .expect("SIGCONT");
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            match state_of(pid) {
                Some('S') | Some('R') => break,
                _ if std::time::Instant::now() > deadline => panic!("sleeper never resumed"),
                _ => std::thread::sleep(Duration::from_millis(5)),
            }
        }
        let _ = child.kill();
        let _ = child.wait();
    }
}
