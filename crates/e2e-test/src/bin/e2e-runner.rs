use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitCode, Stdio};
use std::sync::{Arc, Mutex, PoisonError};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{bail, Context, Result};
use serde_json::json;

const DEFAULT_MAX_PARALLEL: usize = 5;
const DEFAULT_CONTAINER_WEIGHT_CAP: usize = 10;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RunnerMode {
    Parallel,
    Serial,
}

#[derive(Debug, Clone)]
struct RunnerConfig {
    mode: RunnerMode,
    root_run_id: String,
    report_root: PathBuf,
    target_dir: PathBuf,
    artifacts: String,
    max_parallel: usize,
    container_weight_cap: usize,
    heavy_test_threads: usize,
    selected_suites: Option<Vec<String>>,
    cleanup: bool,
}

#[derive(Debug, Clone)]
struct Suite {
    name: &'static str,
    binary_prefix: &'static str,
    weight: usize,
    test_threads: usize,
    wave: usize,
}

#[derive(Debug, Clone)]
struct SuiteResult {
    name: String,
    run_id: String,
    command: Vec<String>,
    log_path: PathBuf,
    status: i32,
    duration_ms: u128,
    daemon_logs_copied: usize,
    removed_containers: usize,
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("{err:#}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<()> {
    let config = RunnerConfig::parse()?;
    fs::create_dir_all(config.report_root.join("suites"))
        .with_context(|| format!("create {}", config.report_root.display()))?;
    fs::create_dir_all(log_dir()).with_context(|| format!("create {}", log_dir().display()))?;

    let mut results = Vec::new();
    let started = Instant::now();
    let prebuild = cargo_command(&["test", "-p", "e2e-test", "--features", "e2e", "--no-run"]);
    let prebuild_result = run_step(
        "prebuild",
        &config.root_run_id,
        &prebuild,
        &config,
        &config.report_root.join("prebuild.log"),
    )?;
    if prebuild_result.status != 0 {
        write_summary(&config, &[prebuild_result], started.elapsed(), false)?;
        bail!("prebuild failed");
    }
    results.push(prebuild_result);

    let suites = selected_suites(&config)?;
    let passed = match config.mode {
        RunnerMode::Serial => run_serial(&config, &suites, &mut results)?,
        RunnerMode::Parallel => run_parallel(&config, &suites, &mut results)?,
    };
    write_summary(&config, &results, started.elapsed(), passed)?;
    if !passed {
        bail!("one or more E2E suites failed");
    }
    println!(
        "[e2e-runner] all selected suites passed in {:.2}s; report root {}",
        started.elapsed().as_secs_f64(),
        config.report_root.display()
    );
    Ok(())
}

impl RunnerConfig {
    fn parse() -> Result<Self> {
        let mut mode = RunnerMode::Parallel;
        let mut root_run_id = None;
        let mut report_root = None;
        let mut target_dir = None;
        let mut artifacts = std::env::var("EOS_E2E_ARTIFACTS").unwrap_or_else(|_| "always".into());
        let mut max_parallel = DEFAULT_MAX_PARALLEL;
        let mut container_weight_cap = DEFAULT_CONTAINER_WEIGHT_CAP;
        let mut heavy_test_threads = 4;
        let mut selected_suites = None;
        let mut cleanup = true;

        let mut args = std::env::args().skip(1);
        while let Some(arg) = args.next() {
            match arg.as_str() {
                "--serial" => mode = RunnerMode::Serial,
                "--parallel" => mode = RunnerMode::Parallel,
                "--run-id" => root_run_id = Some(required_value(&mut args, "--run-id")?),
                "--report-root" => {
                    report_root = Some(PathBuf::from(required_value(&mut args, "--report-root")?));
                }
                "--target-dir" => {
                    target_dir = Some(PathBuf::from(required_value(&mut args, "--target-dir")?));
                }
                "--artifacts" => artifacts = required_value(&mut args, "--artifacts")?,
                "--max-parallel" => {
                    max_parallel = parse_usize(&required_value(&mut args, "--max-parallel")?)?;
                }
                "--container-weight-cap" => {
                    container_weight_cap =
                        parse_usize(&required_value(&mut args, "--container-weight-cap")?)?;
                }
                "--heavy-test-threads" => {
                    heavy_test_threads =
                        parse_usize(&required_value(&mut args, "--heavy-test-threads")?)?;
                }
                "--suites" => {
                    selected_suites = Some(
                        required_value(&mut args, "--suites")?
                            .split(',')
                            .map(str::trim)
                            .filter(|suite| !suite.is_empty())
                            .map(ToOwned::to_owned)
                            .collect::<Vec<_>>(),
                    );
                }
                "--no-cleanup" => cleanup = false,
                "--help" | "-h" => {
                    print_help();
                    std::process::exit(0);
                }
                other => bail!("unknown argument {other}; use --help"),
            }
        }

        if max_parallel == 0 {
            bail!("--max-parallel must be greater than 0");
        }
        if container_weight_cap == 0 {
            bail!("--container-weight-cap must be greater than 0");
        }
        if heavy_test_threads == 0 {
            bail!("--heavy-test-threads must be greater than 0");
        }
        let root_run_id = root_run_id.unwrap_or_else(default_run_id);
        let report_root = report_root.unwrap_or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("test-reports")
                .join("runs")
                .join(&root_run_id)
        });
        let target_dir = target_dir
            .or_else(|| std::env::var_os("CARGO_TARGET_DIR").map(PathBuf::from))
            .unwrap_or_else(|| PathBuf::from("target/e2e-live-target"));
        Ok(Self {
            mode,
            root_run_id,
            report_root,
            target_dir,
            artifacts,
            max_parallel,
            container_weight_cap,
            heavy_test_threads,
            selected_suites,
            cleanup,
        })
    }
}

fn selected_suites(config: &RunnerConfig) -> Result<Vec<Suite>> {
    let mut suites = suite_plan(config.heavy_test_threads);
    assert_suite_plan_covers_cargo_tests(&suites)?;
    if let Some(selected) = &config.selected_suites {
        let selected = selected
            .iter()
            .map(String::as_str)
            .collect::<std::collections::HashSet<_>>();
        suites.retain(|suite| selected.contains(suite.name));
        if suites.len() != selected.len() {
            let known = suite_plan(config.heavy_test_threads)
                .into_iter()
                .map(|suite| suite.name)
                .collect::<Vec<_>>()
                .join(", ");
            bail!("unknown suite in --suites; known suites: {known}");
        }
    }
    Ok(suites)
}

fn assert_suite_plan_covers_cargo_tests(suites: &[Suite]) -> Result<()> {
    let suite_names = suites
        .iter()
        .map(|suite| suite.name)
        .collect::<std::collections::BTreeSet<_>>();
    let missing = cargo_test_targets()?
        .into_iter()
        .filter(|target| !suite_names.contains(target.as_str()))
        .collect::<Vec<_>>();
    if missing.is_empty() {
        return Ok(());
    }
    bail!(
        "suite_plan is missing Cargo [[test]] target(s): {}",
        missing.join(", ")
    )
}

fn cargo_test_targets() -> Result<Vec<String>> {
    let mut targets = Vec::new();
    let mut in_test_target = false;
    for line in include_str!(concat!(env!("CARGO_MANIFEST_DIR"), "/Cargo.toml")).lines() {
        let line = line.trim();
        if line == "[[test]]" {
            in_test_target = true;
            continue;
        }
        if line.starts_with('[') {
            in_test_target = false;
            continue;
        }
        if !in_test_target || !line.starts_with("name") {
            continue;
        }
        let Some((_, value)) = line.split_once('=') else {
            bail!("invalid [[test]] name entry in Cargo.toml: {line}");
        };
        let value = value.trim();
        let Some(name) = value
            .strip_prefix('"')
            .and_then(|value| value.strip_suffix('"'))
        else {
            bail!("invalid [[test]] name value in Cargo.toml: {line}");
        };
        targets.push(name.to_owned());
    }
    Ok(targets)
}

fn suite_plan(heavy_test_threads: usize) -> Vec<Suite> {
    vec![
        Suite {
            name: "lib",
            binary_prefix: "e2e_test",
            weight: 0,
            test_threads: 1,
            wave: 0,
        },
        Suite {
            name: "core",
            binary_prefix: "core",
            weight: 1,
            test_threads: 1,
            wave: 1,
        },
        Suite {
            name: "workspace-publish-gate",
            binary_prefix: "workspace_publish_gate",
            weight: 1,
            test_threads: 1,
            wave: 1,
        },
        Suite {
            name: "layerstack",
            binary_prefix: "layerstack",
            weight: 1,
            test_threads: 1,
            wave: 1,
        },
        Suite {
            name: "ephemeral_workspace",
            binary_prefix: "ephemeral_workspace",
            weight: 1,
            test_threads: 1,
            wave: 1,
        },
        Suite {
            name: "daemon",
            binary_prefix: "daemon",
            weight: 2,
            test_threads: 1,
            wave: 2,
        },
        Suite {
            name: "daemon-config",
            binary_prefix: "daemon_config",
            weight: 1,
            test_threads: 1,
            wave: 2,
        },
        Suite {
            name: "workspace-runtime-isolated",
            binary_prefix: "workspace_runtime_isolated",
            weight: 2,
            test_threads: 1,
            wave: 2,
        },
        Suite {
            name: "workspace-runtime-command",
            binary_prefix: "workspace_runtime_command",
            weight: 4,
            test_threads: heavy_test_threads,
            wave: 3,
        },
        Suite {
            name: "pressure",
            binary_prefix: "pressure",
            weight: 4,
            test_threads: heavy_test_threads,
            wave: 4,
        },
        Suite {
            name: "plugin",
            binary_prefix: "plugin",
            weight: 5,
            test_threads: 1,
            wave: 5,
        },
        Suite {
            name: "plugin-disabled",
            binary_prefix: "plugin_disabled",
            weight: 1,
            test_threads: 1,
            wave: 5,
        },
    ]
}

fn run_serial(
    config: &RunnerConfig,
    suites: &[Suite],
    results: &mut Vec<SuiteResult>,
) -> Result<bool> {
    let mut passed = true;
    for suite in suites {
        let result = run_suite(config, suite)?;
        passed &= result.status == 0;
        results.push(result);
        if !passed {
            break;
        }
    }
    Ok(passed)
}

fn run_parallel(
    config: &RunnerConfig,
    suites: &[Suite],
    results: &mut Vec<SuiteResult>,
) -> Result<bool> {
    let mut passed = true;
    let mut remaining = suites.to_vec();
    remaining.sort_by_key(|suite| (suite.wave, suite.name));
    let waves = remaining
        .iter()
        .map(|suite| suite.wave)
        .collect::<std::collections::BTreeSet<_>>();

    for wave in waves {
        let mut pending = remaining
            .iter()
            .filter(|suite| suite.wave == wave)
            .cloned()
            .collect::<Vec<_>>();
        let mut active = Vec::new();
        while !pending.is_empty() || !active.is_empty() {
            while active.len() < config.max_parallel {
                let active_weight: usize = active
                    .iter()
                    .map(|handle: &SuiteHandle| handle.weight)
                    .sum();
                let Some(index) = pending.iter().position(|suite| {
                    active_weight + suite.weight <= config.container_weight_cap || active.is_empty()
                }) else {
                    break;
                };
                let suite = pending.remove(index);
                let runner_config = config.clone();
                let weight = suite.weight;
                let name = suite.name.to_owned();
                let handle = thread::spawn(move || run_suite(&runner_config, &suite));
                active.push(SuiteHandle {
                    name,
                    weight,
                    handle,
                });
                let active_weight: usize = active
                    .iter()
                    .map(|handle: &SuiteHandle| handle.weight)
                    .sum();
                if active_weight >= config.container_weight_cap {
                    break;
                }
            }
            if active.is_empty() {
                bail!("scheduler could not start a suite in wave {wave}");
            }
            let handle = active.remove(0);
            let result = handle
                .handle
                .join()
                .map_err(|_| anyhow::anyhow!("suite {} runner thread panicked", handle.name))??;
            passed &= result.status == 0;
            results.push(result);
            if !passed {
                for handle in active {
                    let result = handle.handle.join().map_err(|_| {
                        anyhow::anyhow!("suite {} runner thread panicked", handle.name)
                    })??;
                    results.push(result);
                }
                return Ok(false);
            }
        }
    }
    Ok(passed)
}

struct SuiteHandle {
    name: String,
    weight: usize,
    handle: thread::JoinHandle<Result<SuiteResult>>,
}

fn run_suite(config: &RunnerConfig, suite: &Suite) -> Result<SuiteResult> {
    let run_id = format!("{}-{}", config.root_run_id, suite.name);
    let command = suite_command(config, suite)?;
    let log_path = config
        .report_root
        .join("suites")
        .join(suite.name)
        .join("stdout.log");
    let result = run_step(suite.name, &run_id, &command, config, &log_path)?;
    let daemon_logs_copied = copy_daemon_logs(config, suite, &run_id)?;
    let removed_containers = if config.cleanup {
        e2e_test::container::reap_e2e_containers_for_run(&run_id).unwrap_or_default()
    } else {
        0
    };
    Ok(SuiteResult {
        daemon_logs_copied,
        removed_containers,
        ..result
    })
}

fn copy_daemon_logs(config: &RunnerConfig, suite: &Suite, run_id: &str) -> Result<usize> {
    if config.artifacts.eq_ignore_ascii_case("off") {
        return Ok(0);
    }
    let dest_dir = config
        .report_root
        .join("suites")
        .join(suite.name)
        .join("containers");
    e2e_test::container::copy_daemon_logs_for_run(run_id, &dest_dir)
}

fn run_step(
    label: &str,
    run_id: &str,
    command: &[String],
    config: &RunnerConfig,
    log_path: &Path,
) -> Result<SuiteResult> {
    let started = Instant::now();
    if let Some(parent) = log_path.parent() {
        fs::create_dir_all(parent).with_context(|| format!("create {}", parent.display()))?;
    }
    let log = Arc::new(Mutex::new(
        OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(log_path)
            .with_context(|| format!("open suite log {}", log_path.display()))?,
    ));
    write_log_line(
        &log,
        &format!("[e2e-runner] {label}: {}", command.join(" ")),
    )?;
    println!("\n[e2e-runner] {label}");
    println!("[e2e-runner] command: {}", command.join(" "));

    let mut child = Command::new(&command[0])
        .args(&command[1..])
        .current_dir(workspace_root())
        .env("EOS_E2E_RUN_ID", run_id)
        .env("EOS_E2E_ROOT_RUN_ID", &config.root_run_id)
        .env(
            "EOS_E2E_CONTAINER_WEIGHT_CAP",
            config.container_weight_cap.to_string(),
        )
        .env("EOS_E2E_REPORT_ROOT", &config.report_root)
        .env("EOS_E2E_ARTIFACTS", &config.artifacts)
        .env("CARGO_TARGET_DIR", &config.target_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .with_context(|| format!("spawn {}", command.join(" ")))?;
    let stdout = child.stdout.take().context("child stdout missing")?;
    let stderr = child.stderr.take().context("child stderr missing")?;
    let stdout_log = Arc::clone(&log);
    let stderr_log = Arc::clone(&log);
    let stdout_thread = thread::spawn(move || stream_output(stdout, stdout_log, false));
    let stderr_thread = thread::spawn(move || stream_output(stderr, stderr_log, true));
    let status = child.wait().context("wait for child command")?;
    stdout_thread
        .join()
        .map_err(|_| anyhow::anyhow!("stdout copy thread panicked"))??;
    stderr_thread
        .join()
        .map_err(|_| anyhow::anyhow!("stderr copy thread panicked"))??;
    let code = status.code().unwrap_or(1);
    write_log_line(&log, &format!("[e2e-runner] exit {code}: {label}"))?;
    println!("[e2e-runner] exit {code}: {label}");
    Ok(SuiteResult {
        name: label.to_owned(),
        run_id: run_id.to_owned(),
        command: command.to_vec(),
        log_path: log_path.to_path_buf(),
        status: code,
        duration_ms: started.elapsed().as_millis(),
        daemon_logs_copied: 0,
        removed_containers: 0,
    })
}

fn suite_command(config: &RunnerConfig, suite: &Suite) -> Result<Vec<String>> {
    let mut command = vec![compiled_test_binary(config, suite)?
        .to_string_lossy()
        .into_owned()];
    command.extend([
        "--test-threads".to_owned(),
        suite.test_threads.to_string(),
        "--nocapture".to_owned(),
    ]);
    Ok(command)
}

fn compiled_test_binary(config: &RunnerConfig, suite: &Suite) -> Result<PathBuf> {
    let deps_dir = config.target_dir.join("debug").join("deps");
    let prefix = format!("{}-", suite.binary_prefix);
    let mut matches = fs::read_dir(&deps_dir)
        .with_context(|| format!("read {}", deps_dir.display()))?
        .filter_map(Result::ok)
        .filter_map(|entry| {
            let path = entry.path();
            let name = path.file_name()?.to_str()?;
            (name.starts_with(&prefix) && path.extension().is_none() && is_executable(&path))
                .then_some(path)
        })
        .collect::<Vec<_>>();
    matches.sort_by_key(|path| {
        fs::metadata(path)
            .and_then(|metadata| metadata.modified())
            .unwrap_or(SystemTime::UNIX_EPOCH)
    });
    matches
        .pop()
        .with_context(|| format!("compiled test binary for suite {}", suite.name))
}

fn cargo_command(args: &[&str]) -> Vec<String> {
    let mut command = vec!["cargo".to_owned()];
    command.extend(args.iter().map(|arg| (*arg).to_owned()));
    command
}

fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

#[cfg(unix)]
fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;

    fs::metadata(path)
        .map(|metadata| metadata.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

#[cfg(not(unix))]
fn is_executable(path: &Path) -> bool {
    path.is_file()
}

fn stream_output<R: std::io::Read>(reader: R, log: Arc<Mutex<File>>, stderr: bool) -> Result<()> {
    let mut reader = BufReader::new(reader);
    let mut line = Vec::new();
    loop {
        line.clear();
        let read = reader.read_until(b'\n', &mut line)?;
        if read == 0 {
            break;
        }
        {
            let mut log = log.lock().unwrap_or_else(PoisonError::into_inner);
            log.write_all(&line)?;
        }
        if stderr {
            std::io::stderr().write_all(&line)?;
            std::io::stderr().flush()?;
        } else {
            std::io::stdout().write_all(&line)?;
            std::io::stdout().flush()?;
        }
    }
    Ok(())
}

fn write_log_line(log: &Arc<Mutex<File>>, line: &str) -> Result<()> {
    let mut log = log.lock().unwrap_or_else(PoisonError::into_inner);
    writeln!(log, "{line}")?;
    Ok(())
}

fn write_summary(
    config: &RunnerConfig,
    results: &[SuiteResult],
    duration: Duration,
    passed: bool,
) -> Result<()> {
    let summary_path = config.report_root.join("summary.json");
    let summary = json!({
        "schema": "eos.e2e.runner_report.v1",
        "root_run_id": config.root_run_id,
        "mode": match config.mode {
            RunnerMode::Parallel => "parallel",
            RunnerMode::Serial => "serial",
        },
        "passed": passed,
        "duration_ms": duration.as_millis(),
        "report_root": config.report_root.display().to_string(),
        "target_dir": config.target_dir.display().to_string(),
        "max_parallel": config.max_parallel,
        "container_weight_cap": config.container_weight_cap,
        "heavy_test_threads": config.heavy_test_threads,
        "artifacts": config.artifacts,
        "suites": results.iter().map(|result| {
            json!({
                "name": result.name,
                "run_id": result.run_id,
                "command": result.command,
                "status": result.status,
                "duration_ms": result.duration_ms,
                "log_path": result.log_path.display().to_string(),
                "daemon_logs_copied": result.daemon_logs_copied,
                "removed_containers": result.removed_containers,
            })
        }).collect::<Vec<_>>(),
    });
    fs::write(&summary_path, serde_json::to_vec_pretty(&summary)?)
        .with_context(|| format!("write {}", summary_path.display()))
}

fn log_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("test-reports")
        .join("logs")
}

fn required_value(args: &mut impl Iterator<Item = String>, name: &str) -> Result<String> {
    args.next()
        .filter(|value| !value.trim().is_empty())
        .with_context(|| format!("{name} requires a value"))
}

fn parse_usize(value: &str) -> Result<usize> {
    value
        .parse()
        .with_context(|| format!("parse positive integer {value:?}"))
}

fn default_run_id() -> String {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |duration| duration.as_millis());
    format!("e2e-run-{millis}")
}

fn print_help() {
    println!(
        "Usage: e2e-runner [--parallel|--serial] [--run-id ID] [--report-root PATH] \\
         [--target-dir PATH] [--artifacts always|failure|off] [--suites a,b] \\
         [--max-parallel N] [--container-weight-cap N] [--heavy-test-threads N] [--no-cleanup]"
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cargo_test_targets_include_daemon_config() -> Result<()> {
        let targets = cargo_test_targets()?;
        assert!(
            targets.iter().any(|target| target == "daemon-config"),
            "{targets:?}"
        );
        Ok(())
    }

    #[test]
    fn suite_plan_covers_all_cargo_test_targets() -> Result<()> {
        assert_suite_plan_covers_cargo_tests(&suite_plan(4))
    }
}
