use std::io::Write;
use std::os::unix::net::UnixStream;

use anyhow::{bail, Context, Result};

use crate::runner_cli::{wait_for_start_ack_reader, RunnerCliConfig};

#[test]
fn runner_cli_accepts_explicit_request_and_output_paths() -> Result<()> {
    let _config = RunnerCliConfig::parse(vec![
        "--request".to_owned(),
        "/tmp/request.json".to_owned(),
        "--output".to_owned(),
        "/tmp/result.json".to_owned(),
    ])?;

    Ok(())
}

#[test]
fn runner_cli_rejects_missing_output_path() -> Result<()> {
    let error = match RunnerCliConfig::parse(vec![
        "--request".to_owned(),
        "/tmp/request.json".to_owned(),
    ]) {
        Ok(_) => bail!("missing output path unexpectedly accepted"),
        Err(error) => error,
    };

    assert!(
        error.to_string().contains("requires --output PATH"),
        "{error}"
    );
    Ok(())
}

#[test]
fn runner_cli_rejects_positional_request_path() -> Result<()> {
    let error = match RunnerCliConfig::parse(vec!["/tmp/request.json".to_owned()]) {
        Ok(_) => bail!("positional request path unexpectedly accepted"),
        Err(error) => error,
    };

    assert!(
        error
            .to_string()
            .contains("unexpected ns-runner positional argument"),
        "{error}"
    );
    Ok(())
}

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
