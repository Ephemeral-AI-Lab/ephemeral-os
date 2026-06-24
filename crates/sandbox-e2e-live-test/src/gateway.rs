use std::os::unix::net::UnixStream;
use std::path::Path;
use std::thread::sleep;
use std::time::{Duration, Instant};

const READY_TIMEOUT: Duration = Duration::from_secs(5);
const POLL_INTERVAL: Duration = Duration::from_millis(50);

/// Poll until the gateway socket exists and accepts a connection, or the fixed
/// timeout elapses. Attach mode only — never spawns a gateway. Returns `Err`
/// naming the socket if it never becomes ready.
pub fn await_ready(socket: &Path) -> anyhow::Result<()> {
    let deadline = Instant::now() + READY_TIMEOUT;
    loop {
        if socket.exists() && UnixStream::connect(socket).is_ok() {
            return Ok(());
        }
        if Instant::now() >= deadline {
            anyhow::bail!(
                "gateway socket {} did not become ready within {READY_TIMEOUT:?}",
                socket.display()
            );
        }
        sleep(POLL_INTERVAL);
    }
}
