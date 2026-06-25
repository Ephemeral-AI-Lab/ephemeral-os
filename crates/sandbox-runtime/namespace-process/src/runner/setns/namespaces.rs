use std::os::fd::RawFd;

use crate::runner::protocol::{Fd, NamespaceRunnerRequest, NsFds};
use crate::runner::RunnerError;

pub(crate) fn join_namespaces(ns_fds: &NsFds) -> Result<(), RunnerError> {
    for (name, fd, nstype) in namespace_fd_order_with_types(ns_fds) {
        setns_fd(name, fd, nstype)?;
    }
    Ok(())
}

pub(crate) fn setns_user_mnt(
    request: &NamespaceRunnerRequest,
    context: &str,
) -> Result<(), RunnerError> {
    let ns_fds = request
        .ns_fds
        .ok_or_else(|| RunnerError::InvalidRequest(format!("{context} requires ns_fds")))?;
    let user = required_namespace_fd(context, "user", ns_fds.user)?;
    let mnt = required_namespace_fd(context, "mnt", ns_fds.mnt)?;
    setns_fd("user", user, libc::CLONE_NEWUSER)?;
    setns_fd("mnt", mnt, libc::CLONE_NEWNS)?;
    Ok(())
}

pub(crate) fn namespace_fd_order_with_types(
    ns_fds: &NsFds,
) -> Vec<(&'static str, RawFd, libc::c_int)> {
    let mut ordered = Vec::with_capacity(4);
    push_namespace(&mut ordered, "user", ns_fds.user, libc::CLONE_NEWUSER);
    push_namespace(&mut ordered, "mnt", ns_fds.mnt, libc::CLONE_NEWNS);
    push_namespace(&mut ordered, "pid", ns_fds.pid, libc::CLONE_NEWPID);
    push_namespace(&mut ordered, "net", ns_fds.net, libc::CLONE_NEWNET);
    ordered
}

fn push_namespace(
    ordered: &mut Vec<(&'static str, RawFd, libc::c_int)>,
    name: &'static str,
    fd: Option<Fd>,
    nstype: libc::c_int,
) {
    if let Some(Fd(fd)) = fd {
        ordered.push((name, fd, nstype));
    }
}

fn required_namespace_fd(context: &str, name: &str, fd: Option<Fd>) -> Result<RawFd, RunnerError> {
    fd.map(|Fd(fd)| fd).ok_or_else(|| {
        RunnerError::InvalidRequest(format!("{context} requires {name} namespace fd"))
    })
}

fn setns_fd(name: &str, fd: RawFd, nstype: libc::c_int) -> Result<(), RunnerError> {
    // SAFETY: `fd` is a borrowed namespace file descriptor supplied by the
    // daemon to this dedicated single-threaded runner process. `nstype` is the
    // matching CLONE_NEW* constant for that descriptor, and no Rust references
    // are invalidated by the kernel changing the current task's namespace.
    let rc = unsafe { libc::setns(fd, nstype) };
    if rc == 0 {
        return Ok(());
    }
    let err = std::io::Error::last_os_error();
    let kind = err.kind();
    Err(RunnerError::Syscall(std::io::Error::new(
        kind,
        format!("setns({name}, fd={fd}, nstype=0x{nstype:x}) failed: {err}"),
    )))
}
