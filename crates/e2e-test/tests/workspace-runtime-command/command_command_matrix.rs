use std::sync::{Arc, Barrier};
use std::thread;
use std::time::{Duration, Instant};

use anyhow::{anyhow, bail, ensure, Context, Result};
use e2e_test::{next_invocation_id, unique_suffix, NodePool};
use protocol::catalog;
use serde_json::{json, Value};

use crate::support::{
    array, as_i64, as_str, command_transcript_logs, finalize_foreground_command, live_pool_or_skip,
    stdout, unwrap_operation_result, wait_for_active_leases, wait_for_command_count,
    wait_for_command_transcript_recycled,
};

struct CommandFamily {
    name: &'static str,
    variants: Vec<CommandVariant>,
}

struct CommandVariant {
    name: &'static str,
    cmd: String,
    stdout_contains: String,
    changed_paths: Vec<String>,
}

#[test]
fn command_matrix_covers_shell_families() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let dir = format!("command-matrix/{}", unique_suffix().replace('-', "_"));
    let timeout_s = workload_timeout_s(&pool);
    let families = command_families(&dir);
    ensure!(
        families.len() >= 10,
        "command matrix should cover at least ten command families"
    );
    for family in &families {
        ensure!(
            family.variants.len() >= 2,
            "command family {} should have multiple variants",
            family.name
        );
    }

    let mut executed = 0;
    let before_transcripts = command_transcript_logs(&lease)?;
    for family in &families {
        for variant in &family.variants {
            let call_started = Instant::now();
            let response = lease.call_ok(
                catalog::SANDBOX_COMMAND_EXEC,
                json!({
                    "cmd": variant.cmd,
                    "yield_time_ms": 1000,
                    "timeout_seconds": timeout_s,}),
            )?;
            // Under emulation a quick variant can outlast the yield and return
            // "running"; finalize to its terminal foreground outcome before asserting.
            let response = finalize_foreground_command(
                &lease,
                response,
                Instant::now() + Duration::from_secs(timeout_s + 5),
            )?;
            let elapsed = call_started.elapsed();
            assert_command_ok(&response, family.name, variant.name)?;
            ensure!(
                output_contains(&response, &variant.stdout_contains),
                "{}:{} stdout should contain {:?}: {}",
                family.name,
                variant.name,
                variant.stdout_contains,
                response
            );
            assert_changed_paths(&response, &variant.changed_paths)?;
            assert_command_wall_time_bounded(&response, elapsed, timeout_s)?;
            executed += 1;
        }
    }

    ensure!(
        executed >= families.len() * 2,
        "command matrix should run at least two variants per family, got {executed}"
    );
    wait_for_command_count(&lease, 0)?;
    wait_for_active_leases(&lease, 0)?;
    let after_transcripts = command_transcript_logs(&lease)?;
    ensure!(
        after_transcripts == before_transcripts,
        "foreground command family should recycle transient transcripts; before={before_transcripts:?} after={after_transcripts:?}"
    );
    Ok(())
}

#[test]
fn stdin_prompt_progress_collect_and_cancel_variants() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let lease = pool.acquire()?;
    let prompt_cmd = concat!(
        "python3 -u -c '",
        "import sys,time; ",
        "print(\"prompt:one\", flush=True); ",
        "first=sys.stdin.readline().strip(); ",
        "print(\"reply:one:\" + first, flush=True); ",
        "print(\"prompt:two\", flush=True); ",
        "second=sys.stdin.readline().strip(); ",
        "print(\"reply:two:\" + second, flush=True); ",
        "time.sleep(60)'"
    );
    let started = lease.call_ok(
        catalog::SANDBOX_COMMAND_EXEC,
        json!({
            "cmd": prompt_cmd,
            "yield_time_ms": 500,
            "timeout_seconds": workload_timeout_s(&pool) + 60,}),
    )?;
    ensure!(
        output_contains(&started, "prompt:one"),
        "prompt command should expose the first prompt: {started}"
    );
    let command_id = as_str(&started, "command_id")?.to_owned();

    let body = (|| -> Result<()> {
        let first = lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({
                "command_id": &command_id,
                "chars": "alpha payload\n",
                "yield_time_ms": 1500,}),
        )?;
        ensure!(
            output_contains(&first, "reply:one:alpha payload")
                && output_contains(&first, "prompt:two"),
            "first stdin write should answer prompt one and surface prompt two: {first}"
        );
        ensure!(
            !output_contains(&first, "prompt:one"),
            "stdin output should be scoped to text produced after the write: {first}"
        );

        let progress = lease.call_ok(
            catalog::SANDBOX_COMMAND_POLL,
            json!({
                "command_id": &command_id,
                "last_n_lines": 4,
            }),
        )?;
        ensure!(
            output_contains(&progress, "prompt:two"),
            "read_progress should expose the current transcript tail: {progress}"
        );

        let second = lease.call_ok(
            catalog::SANDBOX_COMMAND_WRITE_STDIN,
            json!({
                "command_id": &command_id,
                "chars": "beta payload\n",
                "yield_time_ms": 1500,}),
        )?;
        ensure!(
            output_contains(&second, "reply:two:beta payload"),
            "second stdin write should answer prompt two: {second}"
        );
        ensure!(
            !output_contains(&second, "reply:one:alpha payload"),
            "second stdin write must not replay the first answer: {second}"
        );

        let not_done = lease.call_ok(
            catalog::SANDBOX_COMMAND_COLLECT_COMPLETED,
            json!({"command_ids": [command_id.clone()]}),
        )?;
        ensure!(
            array(&not_done, "completions")?.is_empty(),
            "sleeping prompt command must not produce a completion before cancellation: {not_done}"
        );

        let cancel = unwrap_operation_result(lease.call(
            catalog::SANDBOX_COMMAND_CANCEL,
            json!({"command_id": &command_id}),
        )?)?;
        ensure_terminalish_status(&cancel)?;
        wait_for_command_count(&lease, 0)?;
        wait_for_active_leases(&lease, 0)?;
        wait_for_command_transcript_recycled(&lease, &command_id)?;
        Ok(())
    })();

    if body.is_err() {
        let _ = lease.call(
            catalog::SANDBOX_COMMAND_CANCEL,
            json!({"command_id": &command_id}),
        );
        let _ = wait_for_command_count(&lease, 0);
    }
    body
}

#[test]
fn parallel_command_matrix_load_stays_bounded() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let levels = pool.workload().concurrency_levels.clone();
    ensure!(
        levels == [1, 3, 6, 12],
        "command workload.concurrency_levels should use [1, 3, 6, 12], got {levels:?}"
    );
    let lease = pool.acquire()?;
    let timeout_s = workload_timeout_s(&pool);

    for level in levels {
        let dir = format!(
            "command-load/level-{level}/{}",
            unique_suffix().replace('-', "_")
        );
        let before_transcripts = command_transcript_logs(&lease)?;
        let barrier = Arc::new(Barrier::new(level));
        let handles: Vec<_> = (0..level)
            .map(|index| {
                let client = lease.recorded_client();
                let root = lease.root().to_owned();
                let caller_id = lease.caller_id().to_owned();
                let barrier = Arc::clone(&barrier);
                let cmd = parallel_command(&dir, level, index);
                thread::spawn(move || -> Result<(usize, Value, Duration)> {
                    barrier.wait();
                    let started = Instant::now();
                    let response = request_with_identity(
                        &client,
                        catalog::SANDBOX_COMMAND_EXEC,
                        &root,
                        &caller_id,
                        json!({
                            "cmd": cmd,
                            "yield_time_ms": 1000,
                            "timeout_seconds": timeout_s,}),
                    )?;
                    Ok((index, response, started.elapsed()))
                })
            })
            .collect();

        for handle in handles {
            let (index, response, elapsed) = handle
                .join()
                .map_err(|_| anyhow!("parallel command worker panicked"))??;
            // Under emulation a quick worker may not finish inside the 1s yield
            // window, so `exec_command` returns "running"; poll it to its
            // terminal outcome before asserting on the finalized payload.
            let response = finalize_foreground_command(
                &lease,
                response,
                Instant::now() + Duration::from_secs(timeout_s),
            )?;
            assert_command_ok(&response, "parallel-load", "worker")?;
            ensure!(
                output_contains(&response, &format!("worker:{level}:{index}")),
                "parallel worker stdout should include its marker: {response}"
            );
            assert_changed_paths(&response, &[format!("{dir}/worker-{index}/result.txt")])?;
            assert_command_wall_time_bounded(&response, elapsed, timeout_s)?;
        }

        for index in 0..level {
            let read = lease.call_ok(
                catalog::SANDBOX_FILE_READ,
                json!({"path": format!("{dir}/worker-{index}/result.txt")}),
            )?;
            ensure!(
                as_str(&read, "content")?.contains(&format!("worker:{level}:{index}")),
                "parallel worker publish should be durable: {read}"
            );
        }
        let metrics = wait_for_active_leases(&lease, 0)?;
        ensure!(
            as_i64(&metrics, "active_leases")? == 0,
            "parallel command level {level} should not leak leases: {metrics}"
        );
        let after_transcripts = command_transcript_logs(&lease)?;
        ensure!(
            after_transcripts == before_transcripts,
            "parallel foreground command level {level} should recycle transient transcripts; before={before_transcripts:?} after={after_transcripts:?}"
        );
    }
    wait_for_command_count(&lease, 0)?;
    Ok(())
}

#[test]
fn parallel_prompt_sessions_ladder_stays_isolated_and_bounded() -> Result<()> {
    let Some(pool) = live_pool_or_skip()? else {
        return Ok(());
    };
    let levels = pool.workload().concurrency_levels.clone();
    ensure!(
        levels == [1, 3, 6, 12],
        "command workload.concurrency_levels should use [1, 3, 6, 12], got {levels:?}"
    );
    let lease = pool.acquire()?;
    let timeout_s = workload_timeout_s(&pool);

    for level in levels {
        let before_transcripts = command_transcript_logs(&lease)?;
        let barrier = Arc::new(Barrier::new(level));
        let handles: Vec<_> = (0..level)
            .map(|index| {
                let client = lease.recorded_client();
                let root = lease.root().to_owned();
                let caller_id = lease.caller_id().to_owned();
                let barrier = Arc::clone(&barrier);
                thread::spawn(move || -> Result<()> {
                    barrier.wait();
                    let marker = format!("prompt-worker:{level}:{index}");
                    let prompt_cmd = format!(
                        "python3 -u -c 'import sys,time; \
marker={marker:?}; \
print(\"prompt:\" + marker, flush=True); \
payload=sys.stdin.readline().strip(); \
print(\"reply:\" + marker + \":\" + payload, flush=True); \
time.sleep(60)'"
                    );
                    let started = request_with_identity(
                        &client,
                        catalog::SANDBOX_COMMAND_EXEC,
                        &root,
                        &caller_id,
                        json!({
                            "cmd": prompt_cmd,
                            "yield_time_ms": 500,
                            "timeout_seconds": timeout_s + 60,}),
                    )?;
                    ensure!(
                        as_str(&started, "status")? == "running",
                        "parallel prompt worker should stay running: {started}"
                    );
                    let command_id = as_str(&started, "command_id")?.to_owned();
                    let prompt_needle = format!("prompt:{marker}");
                    let reply_needle = format!("reply:{marker}:payload-{level}-{index}");
                    // Under emulation python startup can outlast the 500ms yield,
                    // so the prompt may not be in the start snapshot yet; poll the
                    // transcript for this worker's own prompt before proceeding.
                    let started = if output_contains(&started, &prompt_needle) {
                        started
                    } else {
                        poll_read_progress_until_contains(
                            &client,
                            &root,
                            &caller_id,
                            &command_id,
                            &prompt_needle,
                            Instant::now() + Duration::from_secs(timeout_s.min(15)),
                        )?
                    };
                    ensure!(
                        output_contains(&started, &prompt_needle),
                        "parallel prompt worker should expose its own prompt: {started}"
                    );

                    let answered = request_with_identity(
                        &client,
                        catalog::SANDBOX_COMMAND_WRITE_STDIN,
                        &root,
                        &caller_id,
                        json!({
                            "command_id": &command_id,
                            "chars": format!("payload-{level}-{index}\n"),
                            "yield_time_ms": 1500,}),
                    )?;
                    ensure!(
                        !output_contains(&answered, &prompt_needle),
                        "parallel prompt stdin output should be scoped after the write: {answered}"
                    );
                    let reply = if output_contains(&answered, &reply_needle) {
                        answered
                    } else {
                        poll_read_progress_until_contains(
                            &client,
                            &root,
                            &caller_id,
                            &command_id,
                            &reply_needle,
                            Instant::now() + Duration::from_secs(timeout_s.min(15)),
                        )?
                    };
                    ensure!(
                        output_contains(&reply, &reply_needle),
                        "parallel prompt worker should echo its own payload: {reply}"
                    );

                    let progress = request_with_identity(
                        &client,
                        catalog::SANDBOX_COMMAND_POLL,
                        &root,
                        &caller_id,
                        json!({
                            "command_id": &command_id,
                            "last_n_lines": 4,
                        }),
                    )?;
                    ensure!(
                        output_contains(&progress, &reply_needle),
                        "parallel prompt read_progress should expose the transcript tail: {progress}"
                    );

                    let cancel = request_with_identity(
                        &client,
                        catalog::SANDBOX_COMMAND_CANCEL,
                        &root,
                        &caller_id,
                        json!({"command_id": &command_id}),
                    )?;
                    ensure_terminalish_status(&cancel)?;
                    Ok(())
                })
            })
            .collect();

        for handle in handles {
            handle
                .join()
                .map_err(|_| anyhow!("parallel prompt worker panicked"))??;
        }
        wait_for_command_count(&lease, 0)?;
        let metrics = wait_for_active_leases(&lease, 0)?;
        ensure!(
            as_i64(&metrics, "active_leases")? == 0,
            "parallel prompt level {level} should not leak leases: {metrics}"
        );
        let after_transcripts = command_transcript_logs(&lease)?;
        ensure!(
            after_transcripts == before_transcripts,
            "parallel prompt level {level} should recycle command transcripts; before={before_transcripts:?} after={after_transcripts:?}"
        );
    }
    Ok(())
}

fn poll_read_progress_until_contains(
    client: &e2e_test::RecordedClient,
    root: &str,
    caller_id: &str,
    command_id: &str,
    needle: &str,
    deadline: Instant,
) -> Result<Value> {
    let mut last = None;
    while Instant::now() < deadline {
        let poll = request_with_identity(
            client,
            catalog::SANDBOX_COMMAND_POLL,
            root,
            caller_id,
            json!({
                "command_id": command_id,
                "last_n_lines": 8,
            }),
        )?;
        if output_contains(&poll, needle) {
            return Ok(poll);
        }
        last = Some(poll);
    }
    bail!("read_progress did not surface {needle:?} before deadline; last poll: {last:?}");
}

fn command_families(dir: &str) -> Vec<CommandFamily> {
    vec![
        CommandFamily {
            name: "builtin",
            variants: vec![
                variant("printf", "printf 'builtin-a\n'", "builtin-a", []),
                variant(
                    "sh-argv",
                    "sh -c 'printf \"builtin-b:%s\\n\" \"$0\"' matrix_arg",
                    "builtin-b:matrix_arg",
                    [],
                ),
            ],
        },
        CommandFamily {
            name: "pipeline",
            variants: vec![
                variant("sort", "printf 'b\na\n' | sort", "a\nb", []),
                variant("wc", "printf 'aa\nbb\n' | wc -l", "2", []),
            ],
        },
        CommandFamily {
            name: "redirection",
            variants: vec![
                variant(
                    "write-cat",
                    format!(
                        "mkdir -p {dir}/redir && printf 'redir-a\n' > {dir}/redir/a.txt && cat {dir}/redir/a.txt"
                    ),
                    "redir-a",
                    [format!("{dir}/redir/a.txt")],
                ),
                variant(
                    "empty-file",
                    format!(
                        "mkdir -p {dir}/redir && : > {dir}/redir/empty.txt && test -f {dir}/redir/empty.txt && printf 'empty-ok\n'"
                    ),
                    "empty-ok",
                    [format!("{dir}/redir/empty.txt")],
                ),
            ],
        },
        CommandFamily {
            name: "append",
            variants: vec![
                variant(
                    "append-tail",
                    format!(
                        "mkdir -p {dir}/append && printf 'one\n' > {dir}/append/log.txt && printf 'two\n' >> {dir}/append/log.txt && tail -n 1 {dir}/append/log.txt"
                    ),
                    "two",
                    [format!("{dir}/append/log.txt")],
                ),
                variant(
                    "append-count",
                    format!(
                        "mkdir -p {dir}/append && printf 'a\n' > {dir}/append/count.txt && printf 'b\n' >> {dir}/append/count.txt && wc -l < {dir}/append/count.txt"
                    ),
                    "2",
                    [format!("{dir}/append/count.txt")],
                ),
            ],
        },
        CommandFamily {
            name: "heredoc",
            variants: vec![
                variant("stdout-doc", "cat <<'EOS'\nhere-a\nEOS", "here-a", []),
                variant(
                    "file-doc",
                    format!(
                        "mkdir -p {dir}/here && cat > {dir}/here/doc.txt <<'EOS'\nhere-file\nEOS\ncat {dir}/here/doc.txt"
                    ),
                    "here-file",
                    [format!("{dir}/here/doc.txt")],
                ),
            ],
        },
        CommandFamily {
            name: "filesystem",
            variants: vec![
                variant(
                    "find",
                    format!(
                        "mkdir -p {dir}/fs/nested && touch {dir}/fs/nested/a {dir}/fs/b && find {dir}/fs -type f | sort"
                    ),
                    format!("{dir}/fs/nested/a"),
                    [format!("{dir}/fs/nested/a"), format!("{dir}/fs/b")],
                ),
                variant(
                    "symlink",
                    format!(
                        "mkdir -p {dir}/fs && ln -sf target {dir}/fs/link && readlink {dir}/fs/link"
                    ),
                    "target",
                    [format!("{dir}/fs/link")],
                ),
            ],
        },
        CommandFamily {
            name: "grep",
            variants: vec![
                variant("pipe-grep", "printf 'alpha\nbeta\n' | grep '^beta$'", "beta", []),
                variant(
                    "file-grep",
                    format!(
                        "mkdir -p {dir}/grep && printf 'red\nblue\n' > {dir}/grep/colors.txt && grep blue {dir}/grep/colors.txt"
                    ),
                    "blue",
                    [format!("{dir}/grep/colors.txt")],
                ),
            ],
        },
        CommandFamily {
            name: "sed",
            variants: vec![
                variant("replace", "printf 'red\n' | sed 's/red/blue/'", "blue", []),
                variant(
                    "extract",
                    "printf 'prefix:42\n' | sed -n 's/^prefix://p'",
                    "42",
                    [],
                ),
            ],
        },
        CommandFamily {
            name: "awk",
            variants: vec![
                variant(
                    "sum",
                    "printf '1 2\n3 4\n' | awk '{s+=$1+$2} END {print s}'",
                    "10",
                    [],
                ),
                variant(
                    "split",
                    "printf 'a,b\n' | awk -F, '{print $2 \":\" $1}'",
                    "b:a",
                    [],
                ),
            ],
        },
        CommandFamily {
            name: "python",
            variants: vec![
                variant("stdout", "python3 - <<'PY'\nprint('py-a')\nPY", "py-a", []),
                variant(
                    "file-write",
                    format!(
                        "python3 - <<'PY'\nfrom pathlib import Path\npath = Path({:?})\npath.parent.mkdir(parents=True, exist_ok=True)\npath.write_text('py-file\\n')\nprint('py-b')\nPY",
                        format!("{dir}/python/out.txt")
                    ),
                    "py-b",
                    [format!("{dir}/python/out.txt")],
                ),
            ],
        },
        CommandFamily {
            name: "stderr",
            variants: vec![
                variant(
                    "stderr-stdout",
                    "sh -c 'printf \"stderr-ok\\n\" >&2; printf \"stdout-ok\\n\"'",
                    "stdout-ok",
                    [],
                ),
                variant(
                    "stderr-pipe",
                    "sh -c 'printf \"pipe-ok\\n\"; printf \"noise\\n\" >&2' | grep pipe",
                    "pipe-ok",
                    [],
                ),
            ],
        },
        CommandFamily {
            name: "json-and-bytes",
            variants: vec![
                variant(
                    "json",
                    "python3 - <<'PY'\nimport json\nprint(json.dumps({'n': 3, 'ok': True}, sort_keys=True))\nPY",
                    "\"ok\": true",
                    [],
                ),
                variant("byte-count", "printf 'abc' | wc -c", "3", []),
            ],
        },
    ]
}

fn variant(
    name: &'static str,
    cmd: impl Into<String>,
    stdout_contains: impl Into<String>,
    changed_paths: impl Into<Vec<String>>,
) -> CommandVariant {
    CommandVariant {
        name,
        cmd: cmd.into(),
        stdout_contains: stdout_contains.into(),
        changed_paths: changed_paths.into(),
    }
}

fn parallel_command(dir: &str, level: usize, index: usize) -> String {
    let marker = format!("worker:{level}:{index}");
    let path = format!("{dir}/worker-{index}/result.txt");
    match index % 10 {
        0 => format!("mkdir -p {dir}/worker-{index} && printf '{marker}\n' > {path} && cat {path}"),
        1 => format!("mkdir -p {dir}/worker-{index} && printf '{marker}\n' | tee {path}"),
        2 => format!(
            "mkdir -p {dir}/worker-{index} && printf 'z\n{marker}\n' | sort | grep worker > {path} && cat {path}"
        ),
        3 => format!(
            "mkdir -p {dir}/worker-{index} && python3 - <<'PY'\nfrom pathlib import Path\nPath({path:?}).write_text({marker:?} + '\\n')\nprint({marker:?})\nPY"
        ),
        4 => format!(
            "mkdir -p {dir}/worker-{index} && printf '{level} {index}\n' | awk '{{print \"{marker}\"}}' > {path} && cat {path}"
        ),
        5 => format!(
            "mkdir -p {dir}/worker-{index} && printf 'raw:{marker}\n' | sed 's/^raw://' > {path} && cat {path}"
        ),
        6 => format!(
            "mkdir -p {dir}/worker-{index} && printf '{marker}\n' > {path} && grep 'worker' {path}"
        ),
        7 => format!(
            "mkdir -p {dir}/worker-{index} && cat > {path} <<'EOS'\n{marker}\nEOS\ncat {path}"
        ),
        8 => format!(
            "mkdir -p {dir}/worker-{index} && printf '{marker}\n' > {path} && test -s {path} && cat {path}"
        ),
        _ => format!(
            "mkdir -p {dir}/worker-{index} && sh -c 'printf \"$1\\n\" > \"$2\" && cat \"$2\"' sh {marker:?} {path:?}"
        ),
    }
}

fn request_with_identity(
    client: &e2e_test::RecordedClient,
    op: &str,
    root: &str,
    caller_id: &str,
    args: Value,
) -> Result<Value> {
    let mut args = args
        .as_object()
        .cloned()
        .with_context(|| format!("request args should be an object: {args}"))?;
    args.entry("layer_stack_root".to_owned())
        .or_insert_with(|| json!(root));
    args.entry("caller_id".to_owned())
        .or_insert_with(|| json!(caller_id));
    unwrap_operation_result(client.request(op, &next_invocation_id(), &Value::Object(args))?)
}

fn assert_command_ok(response: &Value, family: &str, variant: &str) -> Result<()> {
    ensure!(
        as_str(response, "status")? == "ok",
        "{family}:{variant} should complete foreground: {response}"
    );
    ensure!(
        as_i64(response, "exit_code")? == 0,
        "{family}:{variant} should exit 0: {response}"
    );
    ensure!(
        response.get("command_id").is_none(),
        "{family}:{variant} should not leave a command handle: {response}"
    );
    Ok(())
}

fn assert_changed_paths(response: &Value, expected: &[String]) -> Result<()> {
    let changed = array(response, "changed_paths")?;
    if expected.is_empty() {
        ensure!(
            changed.is_empty(),
            "read-only command should not publish changed paths: {response}"
        );
        return Ok(());
    }
    for expected_path in expected {
        ensure!(
            changed
                .iter()
                .any(|path| path.as_str() == Some(expected_path.as_str())),
            "command should publish {expected_path}: {response}"
        );
    }
    Ok(())
}

fn assert_command_wall_time_bounded(
    response: &Value,
    elapsed: Duration,
    timeout_s: u64,
) -> Result<()> {
    ensure!(
        elapsed < Duration::from_secs(timeout_s + 10),
        "command exceeded bounded wall time {elapsed:?}: {response}"
    );
    Ok(())
}

fn ensure_terminalish_status(response: &Value) -> Result<()> {
    let status = as_str(response, "status")?;
    if matches!(status, "cancelled" | "ok" | "error") {
        return Ok(());
    }
    bail!("cancel should return a terminal-ish status: {response}");
}

fn output_contains(response: &Value, needle: &str) -> bool {
    // Strip the per-line `[ISO-8601] ` transcript timestamp prefix the daemon
    // prepends before matching on the command's actual output.
    crate::support::strip_transcript_timestamps(stdout(response)).contains(needle)
}

fn workload_timeout_s(pool: &NodePool) -> u64 {
    pool.workload().timeout.as_secs().max(10)
}
