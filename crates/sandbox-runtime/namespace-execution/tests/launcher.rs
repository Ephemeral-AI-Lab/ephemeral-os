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
        fn configured_cgroup_placement_failure_terminates_and_reaps_spawned_runner() {
            let (request_read, request_write) = request_pipe().expect("request pipe");
            let (result_read, result_write) = result_pipe().expect("result pipe");
            let mut command = Command::new("sh");
            command
                .arg("-c")
                .arg("while true; do sleep 1; done")
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            install_pgid_leader_hook(&mut command);
            let child = command.spawn().expect("spawn child");
            let pgid = child_pgid(&child).expect("child process group");
            drop(request_read);
            drop(result_write);
            let mut spawned = SpawnedRunner {
                child,
                result_read,
                request_write,
                pgid,
            };
            let missing = std::env::temp_dir()
                .join(format!("eos-missing-cgroup-{}", std::process::id()))
                .join("cgroup.procs");

            let error = place_spawned_child_in_cgroup(&mut spawned, Some(&missing))
                .expect_err("configured placement fails closed");

            assert!(error.to_string().contains("place ns-runner pid"));
            assert!(
                spawned.child.try_wait().expect("child state").is_some(),
                "placement failure waits the runner before returning"
            );
        }

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
            let mut runner = ForkRunnerChild::new(
                child,
                result_read,
                Some("--mount-overlay"),
                0.01,
                crate::caps::ExecutionCaps::default().max_runner_result_bytes,
            )
            .expect("runner");

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
            let mut runner = ForkRunnerChild::new(
                child,
                result_read,
                Some("--file-op"),
                5.0,
                crate::caps::ExecutionCaps::default().max_runner_result_bytes,
            )
            .expect("runner");

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
            let mut runner = ForkRunnerChild::new(
                child,
                result_read,
                None,
                0.0,
                crate::caps::ExecutionCaps::default().max_runner_result_bytes,
            )
            .expect("runner");

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
            let mut runner = ForkRunnerChild::new(
                child,
                result_read,
                None,
                0.0,
                crate::caps::ExecutionCaps::default().max_runner_result_bytes,
            )
            .expect("runner");

            let result = runner
                .wait_completion()
                .expect("nonzero child status yields synthesized result");

            assert_eq!(result.exit_code, 17);
            assert_eq!(result.payload["status"], "error");
        }
    }
}
