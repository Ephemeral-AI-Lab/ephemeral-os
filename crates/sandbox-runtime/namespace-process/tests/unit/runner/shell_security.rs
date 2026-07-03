use crate::runner::shell_security::{
    build_seccomp_programs, syscall_number, CLONE_NEW_FLAGS, KEEP_CAPABILITIES,
};
use crate::runner::protocol::ShellSecurityMode;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SeccompDecision {
    Allow,
    Errno(i32),
}

const S_IFCHR: u64 = 0o020000;
const S_IFBLK: u64 = 0o060000;
const S_IFMT: u64 = 0o170000;
const S_IFREG: u64 = 0o100000;

const COMMON_DENIED_SYSCALLS: &[&str] = &[
    "mount",
    "umount2",
    "pivot_root",
    "move_mount",
    "open_tree",
    "fsopen",
    "fsconfig",
    "fsmount",
    "fspick",
    "mount_setattr",
    "init_module",
    "finit_module",
    "delete_module",
    "kexec_load",
    "kexec_file_load",
    "reboot",
    "bpf",
    "perf_event_open",
    "userfaultfd",
    "fanotify_init",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "open_by_handle_at",
    "add_key",
    "request_key",
    "keyctl",
    "swapon",
    "swapoff",
    "quotactl",
];

fn syscall(name: &str) -> i64 {
    syscall_number(name).unwrap_or_else(|| panic!("missing syscall {name}"))
}

fn decision(mode: ShellSecurityMode, name: &str, args: [u64; 6]) -> SeccompDecision {
    decision_by_syscall(mode, syscall(name), args)
}

fn decision_by_syscall(
    mode: ShellSecurityMode,
    syscall: i64,
    args: [u64; 6],
) -> SeccompDecision {
    if mode == ShellSecurityMode::Off {
        return SeccompDecision::Allow;
    }
    if namespace_creation_syscall(syscall, args) {
        return SeccompDecision::Errno(libc::EPERM);
    }
    if common_denied_syscall(syscall, args) {
        return SeccompDecision::Errno(libc::EPERM);
    }
    if syscall_number("clone3") == Some(syscall) {
        return SeccompDecision::Errno(libc::ENOSYS);
    }
    SeccompDecision::Allow
}

fn common_denied_syscall(syscall: i64, args: [u64; 6]) -> bool {
    COMMON_DENIED_SYSCALLS
        .iter()
        .any(|name| syscall_number(name) == Some(syscall))
        || (["mknodat", "mknod"]
            .iter()
            .any(|name| syscall_number(name) == Some(syscall))
            && (!is_mknod(syscall) || device_mknod(syscall, args)))
}

fn namespace_creation_syscall(syscall: i64, args: [u64; 6]) -> bool {
    ["setns", "unshare"]
        .iter()
        .any(|name| syscall_number(name) == Some(syscall))
        || (syscall_number("clone") == Some(syscall)
            && CLONE_NEW_FLAGS.iter().any(|flag| (args[0] & flag) != 0))
}

fn is_mknod(syscall: i64) -> bool {
    ["mknod", "mknodat"]
        .iter()
        .any(|name| syscall_number(name) == Some(syscall))
}

fn device_mknod(syscall: i64, args: [u64; 6]) -> bool {
    let mode = if syscall_number("mknod") == Some(syscall) {
        args[1]
    } else {
        args[2]
    };
    matches!(mode & S_IFMT, S_IFCHR | S_IFBLK)
}

fn clone_namespace_mask() -> u64 {
    CLONE_NEW_FLAGS
        .iter()
        .copied()
        .fold(0, |mask, flag| mask | flag)
}

#[test]
fn kept_capabilities_match_command_policy() {
    assert_eq!(
        KEEP_CAPABILITIES,
        &[0, 1, 2, 3, 4, 7, 6, 31, 5, 10, 13, 27]
    );
}

#[test]
fn seccomp_programs_build_with_arch_guard_first() {
    let programs = build_seccomp_programs().expect("programs build");
    assert_eq!(programs.filters.len(), 2);
    for program in programs.filters {
        assert!(!program.is_empty());
        assert_eq!(program[0].code, 0x20);
        assert_eq!(program[0].k, 4);
        #[cfg(target_arch = "x86_64")]
        assert!(program.iter().any(|instruction| instruction.k == 0x4000_0000));
    }
}

#[test]
fn explicit_denies_return_eperm_in_enforce() {
    for name in COMMON_DENIED_SYSCALLS {
        assert_eq!(
            decision(ShellSecurityMode::Enforce, name, [0; 6]),
            SeccompDecision::Errno(libc::EPERM),
            "{name} should be denied"
        );
    }
}

#[test]
fn clone3_returns_enosys() {
    assert_eq!(
        decision(ShellSecurityMode::Enforce, "clone3", [0; 6]),
        SeccompDecision::Errno(libc::ENOSYS)
    );
}

#[test]
fn exec_syscalls_remain_allowed() {
    for name in [
        "execve",
        "execveat",
        "fchmodat2",
        "getrlimit",
        "renameat",
        "renameat2",
        "setrlimit",
    ] {
        assert_eq!(
            decision(ShellSecurityMode::Enforce, name, [0; 6]),
            SeccompDecision::Allow
        );
    }
}

#[test]
fn namespace_creation_is_denied_in_enforce() {
    let clone_args = [clone_namespace_mask(), 0, 0, 0, 0, 0];
    assert_eq!(
        decision(ShellSecurityMode::Enforce, "clone", clone_args),
        SeccompDecision::Errno(libc::EPERM)
    );
    assert_eq!(
        decision(ShellSecurityMode::Enforce, "clone", [libc::SIGCHLD as u64, 0, 0, 0, 0, 0]),
        SeccompDecision::Allow
    );
    for name in ["setns", "unshare"] {
        assert_eq!(
            decision(ShellSecurityMode::Enforce, name, [0; 6]),
            SeccompDecision::Errno(libc::EPERM)
        );
    }
}

#[test]
fn each_clone_namespace_flag_is_denied_in_enforce() {
    for flag in CLONE_NEW_FLAGS {
        let clone_args = [*flag, 0, 0, 0, 0, 0];
        assert_eq!(
            decision(ShellSecurityMode::Enforce, "clone", clone_args),
            SeccompDecision::Errno(libc::EPERM),
            "clone flag {flag:#x} should be denied"
        );
    }
}

#[test]
fn off_mode_skips_seccomp_denials() {
    assert_eq!(
        decision(ShellSecurityMode::Off, "mount", [0; 6]),
        SeccompDecision::Allow
    );
}

#[test]
fn device_mknod_is_denied_but_regular_nodes_are_not() {
    assert_eq!(
        decision(ShellSecurityMode::Enforce, "mknodat", [0, 0, S_IFCHR, 0, 0, 0]),
        SeccompDecision::Errno(libc::EPERM)
    );
    assert_eq!(
        decision(ShellSecurityMode::Enforce, "mknodat", [0, 0, S_IFBLK, 0, 0, 0]),
        SeccompDecision::Errno(libc::EPERM)
    );
    assert_eq!(
        decision(ShellSecurityMode::Enforce, "mknodat", [0, 0, S_IFREG, 0, 0, 0]),
        SeccompDecision::Allow
    );
    if let Some(mknod) = syscall_number("mknod") {
        assert_eq!(
            decision_by_syscall(ShellSecurityMode::Enforce, mknod, [0, S_IFCHR, 0, 0, 0, 0]),
            SeccompDecision::Errno(libc::EPERM)
        );
    }
}
