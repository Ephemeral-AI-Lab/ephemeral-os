pub mod error {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/error.rs"));
}

#[allow(dead_code)]
pub mod pty {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/pty.rs"));
}

pub mod caps {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/caps.rs"));
}

#[allow(dead_code)]
pub mod launcher {
    include!(concat!(env!("CARGO_MANIFEST_DIR"), "/src/launcher.rs"));

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn overlay_mount_completion_timeout_terminates_and_reaps_child() {
            let (result_read, result_write) = result_pipe().expect("result pipe");
            drop(result_write);
            let mut command = Command::new("sh");
            command
                .arg("-c")
                .arg("trap 'exit 0' TERM; while true; do sleep 1; done")
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            install_pgid_leader_hook(&mut command);
            let child = command.spawn().expect("spawn child");
            let mut runner = ForkRunnerChild {
                child,
                result_read,
                mode_flag: Some("--mount-overlay"),
                setup_timeout_s: 0.01,
                max_result_bytes: crate::caps::ExecutionCaps::default().max_runner_result_bytes,
            };

            let error = runner.wait_completion().expect_err("timeout");

            assert!(error
                .to_string()
                .contains("ns-runner --mount-overlay timed out"));
            assert!(runner.child.try_wait().expect("child state").is_some());
        }

        #[test]
        fn file_op_result_over_cap_surfaces_as_error() {
            // A file-op runner that emits more than MAX_RUNNER_RESULT_BYTES must be
            // failed, not buffered without bound or deadlocked: the drainer reads
            // past the cap so the writer never blocks, then reports the overflow.
            let (result_read, result_write) = result_pipe().expect("result pipe");
            let writer = thread::spawn(move || {
                let mut file = File::from(result_write);
                let chunk = vec![b'x'; 64 * 1024];
                let mut written = 0;
                while written <= crate::caps::ExecutionCaps::default().max_runner_result_bytes {
                    file.write_all(&chunk).expect("write oversized result");
                    written += chunk.len();
                }
            });
            let child = Command::new("sh")
                .arg("-c")
                .arg("true")
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .expect("spawn child");
            let mut runner = ForkRunnerChild {
                child,
                result_read,
                mode_flag: Some("--file-op"),
                setup_timeout_s: 5.0,
                max_result_bytes: crate::caps::ExecutionCaps::default().max_runner_result_bytes,
            };

            let error = runner
                .wait_completion()
                .expect_err("oversized result envelope must error");
            writer.join().expect("writer thread");

            assert!(
                error.to_string().contains(&format!(
                    "exceeds {} bytes",
                    crate::caps::ExecutionCaps::default().max_runner_result_bytes
                )),
                "expected over-cap error, got {error}"
            );
        }

        #[test]
        fn zero_status_without_valid_result_is_completion_error() {
            let (result_read, result_write) = result_pipe().expect("result pipe");
            drop(result_write);
            let child = Command::new("sh")
                .arg("-c")
                .arg("true")
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .expect("spawn child");
            let mut runner = ForkRunnerChild {
                child,
                result_read,
                mode_flag: None,
                setup_timeout_s: 0.0,
                max_result_bytes: crate::caps::ExecutionCaps::default().max_runner_result_bytes,
            };

            let error = runner
                .wait_completion()
                .expect_err("missing success result is an execution error");

            assert!(matches!(
                error,
                crate::error::NamespaceExecutionError::Completion(_)
            ));
        }

        #[test]
        fn nonzero_status_without_valid_result_synthesizes_failure() {
            let (result_read, result_write) = result_pipe().expect("result pipe");
            drop(result_write);
            let child = Command::new("sh")
                .arg("-c")
                .arg("exit 17")
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn()
                .expect("spawn child");
            let mut runner = ForkRunnerChild {
                child,
                result_read,
                mode_flag: None,
                setup_timeout_s: 0.0,
                max_result_bytes: crate::caps::ExecutionCaps::default().max_runner_result_bytes,
            };

            let result = runner
                .wait_completion()
                .expect("nonzero child status yields synthesized result");

            assert_eq!(result.exit_code, 17);
            assert_eq!(result.payload["status"], "error");
        }
    }
}
