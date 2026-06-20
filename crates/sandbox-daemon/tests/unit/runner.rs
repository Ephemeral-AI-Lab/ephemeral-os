use std::io::Write;
use std::os::unix::net::UnixStream;

use anyhow::{Context, Result};

use crate::runner_cli::wait_for_start_ack_reader;

#[test]
fn wait_for_start_ack_returns_after_parent_byte() -> Result<()> {
    let (read_end, mut write_end) = UnixStream::pair().context("create ack pair")?;
    write_end.write_all(b"1").context("write ack")?;

    wait_for_start_ack_reader(read_end)?;

    Ok(())
}

#[test]
fn wait_for_start_ack_errors_on_eof_before_ack() -> Result<()> {
    let (read_end, write_end) = UnixStream::pair().context("create ack pair")?;
    drop(write_end);

    let error = wait_for_start_ack_reader(read_end)
        .expect_err("closed ack fd should fail before runner start");

    assert!(
        error
            .to_string()
            .contains("start ack closed before command start"),
        "{error}"
    );
    Ok(())
}
