//! PtyMaster I/O over a real `openpt` pair — exercised on darwin with no child.

use std::io::{Read, Write};
use std::thread;
use std::time::{Duration, Instant};

use sandbox_runtime_namespace_execution::test_support::{open_pty_pair, PtyMaster};

#[test]
fn reader_drains_slave_output_into_the_transcript() {
    let (master, mut slave) = open_pty_pair().expect("openpt pair");
    let pty = PtyMaster::spawn(master, None, Box::new(|| {})).expect("pty master");

    slave.write_all(b"hello pty\n").expect("write to slave");

    let observed = wait_for_output(&pty, "hello pty");
    assert!(observed.contains("hello pty"), "got {observed:?}");
    assert!(pty.output_len() >= 10);
}

#[test]
fn write_stdin_reaches_the_slave() {
    let (master, mut slave) = open_pty_pair().expect("openpt pair");
    let pty = PtyMaster::spawn(master, None, Box::new(|| {})).expect("pty master");

    pty.write_stdin(b"to-slave\n").expect("write stdin");

    let mut buf = [0_u8; 64];
    let read = slave.read(&mut buf).expect("read slave");
    assert!(String::from_utf8_lossy(&buf[..read]).contains("to-slave"));
}

fn wait_for_output(pty: &PtyMaster, needle: &str) -> String {
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        let output = pty.read_output_since(0);
        if output.contains(needle) || Instant::now() >= deadline {
            return output;
        }
        thread::sleep(Duration::from_millis(10));
    }
}
