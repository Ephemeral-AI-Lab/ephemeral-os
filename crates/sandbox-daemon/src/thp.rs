//! Linux process policy for anonymous transparent huge pages.

#[cfg(target_os = "linux")]
pub(crate) fn set_daemon_policy(disabled: bool) -> std::io::Result<()> {
    rustix::thread::disable_transparent_huge_pages(disabled)?;
    let observed = rustix::thread::transparent_huge_pages_are_disabled()?;
    if observed == disabled {
        Ok(())
    } else {
        Err(std::io::Error::other(
            "kernel did not apply the requested transparent-huge-page policy",
        ))
    }
}

#[cfg(not(target_os = "linux"))]
pub(crate) fn set_daemon_policy(_disabled: bool) -> std::io::Result<()> {
    Ok(())
}
