use std::os::fd::AsRawFd;

use super::{Handshake, HandshakeState};
use crate::holder::namespace::HeldNamespaces;
use crate::holder::{NsHolderError, NS_UP, READY};

type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;

#[test]
fn signal_ns_up_writes_readiness_token() -> TestResult {
    let (readiness_read, readiness_write) = nix::unistd::pipe()?;
    let (_control_read, control_write) = nix::unistd::pipe()?;
    let mut handshake = Handshake::new(
        readiness_write.as_raw_fd(),
        control_write.as_raw_fd(),
        HeldNamespaces::for_test()?,
    );

    handshake.signal_ns_up()?;

    let mut buf = [0_u8; 16];
    let read = nix::unistd::read(readiness_read.as_raw_fd(), &mut buf)?;
    assert_eq!(&buf[..read], NS_UP);
    assert_eq!(handshake.state(), HandshakeState::NsUpSent);
    Ok(())
}

#[test]
fn await_net_ready_accepts_prefixed_line() -> TestResult {
    let (_readiness_read, readiness_write) = nix::unistd::pipe()?;
    let (control_read, control_write) = nix::unistd::pipe()?;
    nix::unistd::write(&control_write, b"net-ready extra\n")?;
    let mut handshake = Handshake::new(
        readiness_write.as_raw_fd(),
        control_read.as_raw_fd(),
        HeldNamespaces::for_test()?,
    );

    handshake.await_net_ready()?;

    assert_eq!(handshake.state(), HandshakeState::NetReadyReceived);
    Ok(())
}

#[test]
fn await_net_ready_rejects_wrong_token() -> TestResult {
    let (_readiness_read, readiness_write) = nix::unistd::pipe()?;
    let (control_read, control_write) = nix::unistd::pipe()?;
    nix::unistd::write(&control_write, b"wrong\n")?;
    let mut handshake = Handshake::new(
        readiness_write.as_raw_fd(),
        control_read.as_raw_fd(),
        HeldNamespaces::for_test()?,
    );

    let error = match handshake.await_net_ready() {
        Ok(()) => return Err(std::io::Error::other("wrong token was accepted").into()),
        Err(error) => error,
    };

    assert!(matches!(error, NsHolderError::UnexpectedToken));
    Ok(())
}

#[test]
fn finish_ready_writes_ready_token() -> TestResult {
    let (readiness_read, readiness_write) = nix::unistd::pipe()?;
    let (_control_read, control_write) = nix::unistd::pipe()?;
    let mut handshake = Handshake::new(
        readiness_write.as_raw_fd(),
        control_write.as_raw_fd(),
        HeldNamespaces::for_test()?,
    );

    handshake.finish_ready()?;

    let mut buf = [0_u8; 16];
    let read = nix::unistd::read(readiness_read.as_raw_fd(), &mut buf)?;
    assert_eq!(&buf[..read], READY);
    assert_eq!(handshake.state(), HandshakeState::Ready);
    Ok(())
}
