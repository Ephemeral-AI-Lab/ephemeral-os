//! PTY allocation for daemon-owned command sessions.

use std::fs::File;
use std::io;

use rustix::pty::{grantpt, ioctl_tiocgptpeer, openpt, unlockpt, OpenptFlags};

pub(super) fn open_pty_pair() -> io::Result<(File, File)> {
    let flags = OpenptFlags::RDWR | OpenptFlags::NOCTTY | OpenptFlags::CLOEXEC;
    let master = openpt(flags).map_err(io::Error::from)?;
    grantpt(&master).map_err(io::Error::from)?;
    unlockpt(&master).map_err(io::Error::from)?;
    let slave = ioctl_tiocgptpeer(&master, flags).map_err(io::Error::from)?;

    Ok((File::from(master), File::from(slave)))
}
