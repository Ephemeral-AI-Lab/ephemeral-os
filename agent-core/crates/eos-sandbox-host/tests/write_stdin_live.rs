use std::error::Error;
use std::path::PathBuf;
use std::process::Command;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use eos_sandbox_host::{
    CreateSandboxSpec, DaemonClient, DockerProviderAdapter, ExecOpts, Labels, ProviderAdapter,
    ProviderRegistry, SandboxHostError, DEFAULT_LAYER_STACK_ROOT,
};
use eos_types::{JsonObject, SandboxId};
use serde_json::{json, Value};

const WRAPPER: &str = r#"#!/bin/sh
set -eu

if [ "${1:-}" = "daemon" ] && [ "${2:-}" = "--spawn" ]; then
  n="$(cat /tmp/eos-spawn-calls 2>/dev/null || echo 0)"
  echo "$((n + 1))" > /tmp/eos-spawn-calls
  exec /eos/runtime/daemon/eosd.real "$@"
fi

if [ "${1:-}" = "daemon" ] && [ "${2:-}" = "--client" ]; then
  envelope="${4:-}"
  case "$envelope" in
    *'"op":"api.v1.write_stdin"'*|*'"op": "api.v1.write_stdin"'*)
      n="$(cat /tmp/eos-write-stdin-client-calls 2>/dev/null || echo 0)"
      n="$((n + 1))"
      echo "$n" > /tmp/eos-write-stdin-client-calls
      if [ "$n" = "1" ]; then
        /eos/runtime/daemon/eosd.real "$@" >/tmp/eos-first-write-stdout 2>/tmp/eos-first-write-stderr || true
        printf '%s\n' 'no response from daemon' >&2
        exit 98
      fi
      ;;
  esac
fi

exec /eos/runtime/daemon/eosd.real "$@"
"#;

#[tokio::test]
#[ignore = "requires Docker and EOS_LIVE_E2E_IMAGE"]
async fn write_stdin_empty_response_from_real_eosd_is_not_replayed() -> Result<(), Box<dyn Error>> {
    let Some(image) = live_image() else {
        eprintln!("skipping: EOS_LIVE_E2E_IMAGE is not set");
        return Ok(());
    };
    std::env::set_var("EOS_DOCKER_DAEMON_TCP", "0");

    let adapter: Arc<dyn ProviderAdapter> = Arc::new(DockerProviderAdapter::connect()?);
    let registry = Arc::new(ProviderRegistry::new());
    registry.set_default(Arc::clone(&adapter));
    let daemon = DaemonClient::new(Arc::clone(&registry));

    let mut labels = Labels::new();
    labels.insert("project_dir".to_owned(), "/testbed".to_owned());
    labels.insert("purpose".to_owned(), "write-stdin-live-proof".to_owned());
    let info = adapter
        .create(&CreateSandboxSpec {
            name: unique_name(),
            image: Some(image),
            labels,
            ..CreateSandboxSpec::default()
        })
        .await?;
    registry.register(&info.id, Arc::clone(&adapter));

    let result = run_proof(&daemon, &adapter, &info.id).await;
    let cleanup = adapter.delete(&info.id).await;
    if let Err(err) = cleanup {
        eprintln!("cleanup failed for {}: {err}", info.id.as_str());
    }
    result
}

async fn run_proof(
    daemon: &DaemonClient,
    adapter: &Arc<dyn ProviderAdapter>,
    sandbox_id: &SandboxId,
) -> Result<(), Box<dyn Error>> {
    install_wrapped_eosd(adapter, sandbox_id).await?;
    ensure_workspace_base(daemon, sandbox_id).await?;

    let before_spawn = read_counter(adapter, sandbox_id, "/tmp/eos-spawn-calls").await?;
    let response = daemon
        .call_daemon_api(
            sandbox_id,
            "api.v1.exec_command",
            obj(&[
                ("cmd", json!(stdin_recorder_command())),
                ("yield_time_ms", json!(20)),
                ("timeout", json!(30)),
                ("agent_id", json!("live-write-stdin-proof")),
            ]),
            60,
            DEFAULT_LAYER_STACK_ROOT,
        )
        .await?;
    let command_session_id = response
        .get("command_session_id")
        .and_then(Value::as_str)
        .ok_or("exec_command did not return command_session_id")?
        .to_owned();

    let write_result = daemon
        .call_daemon_api(
            sandbox_id,
            "api.v1.write_stdin",
            obj(&[
                ("command_session_id", json!(command_session_id)),
                ("chars", json!("payload\n")),
                ("yield_time_ms", json!(100)),
                ("max_output_tokens", json!(2000)),
            ]),
            60,
            DEFAULT_LAYER_STACK_ROOT,
        )
        .await;
    match write_result {
        Err(SandboxHostError::ExecFailed { exit_code: 98, .. }) => {}
        Err(err) => {
            return Err(format!("write_stdin returned the wrong error: {err:?}").into());
        }
        Ok(response) => {
            return Err(format!(
                "write_stdin empty response must fail closed, got success: {response:?}"
            )
            .into());
        }
    }

    tokio::time::sleep(Duration::from_millis(750)).await;

    let write_calls =
        read_counter(adapter, sandbox_id, "/tmp/eos-write-stdin-client-calls").await?;
    if write_calls != 1 {
        return Err(format!("write_stdin client call was replayed {write_calls} times").into());
    }
    let after_spawn = read_counter(adapter, sandbox_id, "/tmp/eos-spawn-calls").await?;
    if after_spawn != before_spawn {
        return Err(
            "write_stdin empty response spawned the daemon instead of failing closed".into(),
        );
    }
    let payloads = read_file(adapter, sandbox_id, "/tmp/eos-stdin-payloads")
        .await?
        .replace("\r\n", "\n");
    if payloads != "payload\n" {
        return Err(format!("stdin payload was applied more than once: {payloads:?}").into());
    }
    Ok(())
}

async fn install_wrapped_eosd(
    adapter: &Arc<dyn ProviderAdapter>,
    sandbox_id: &SandboxId,
) -> Result<(), Box<dyn Error>> {
    exec(
        adapter,
        sandbox_id,
        "mkdir -p /eos/runtime/daemon /testbed /tmp",
    )
    .await?;
    let artifact = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../sandbox/dist/eosd-linux-amd64")
        .canonicalize()?;
    let target = format!("{}:/tmp/eosd.real", sandbox_id.as_str());
    let output = Command::new("docker")
        .arg("cp")
        .arg(&artifact)
        .arg(&target)
        .output()?;
    if !output.status.success() {
        return Err(format!(
            "docker cp failed: {}{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        )
        .into());
    }

    let script = format!(
        "cp /tmp/eosd.real /eos/runtime/daemon/eosd.real\n\
         cat > /eos/runtime/daemon/eosd <<'EOS_WRAPPER'\n{WRAPPER}\nEOS_WRAPPER\n\
         chmod 755 /eos/runtime/daemon/eosd /eos/runtime/daemon/eosd.real\n\
         rm -f /tmp/eos-spawn-calls /tmp/eos-write-stdin-client-calls \
         /tmp/eos-stdin-payloads /tmp/eos-first-write-stdout /tmp/eos-first-write-stderr"
    );
    exec(adapter, sandbox_id, &script).await
}

async fn ensure_workspace_base(
    daemon: &DaemonClient,
    sandbox_id: &SandboxId,
) -> Result<(), Box<dyn Error>> {
    daemon
        .call_daemon_api(
            sandbox_id,
            "api.ensure_workspace_base",
            obj(&[("workspace_root", json!("/testbed"))]),
            180,
            DEFAULT_LAYER_STACK_ROOT,
        )
        .await?;
    let ready = daemon
        .call_daemon_api(
            sandbox_id,
            "api.runtime.ready",
            JsonObject::new(),
            60,
            DEFAULT_LAYER_STACK_ROOT,
        )
        .await?;
    if ready.get("ready") != Some(&Value::Bool(true)) {
        return Err(format!("daemon readiness failed: {ready:?}").into());
    }
    Ok(())
}

async fn exec(
    adapter: &Arc<dyn ProviderAdapter>,
    sandbox_id: &SandboxId,
    command: &str,
) -> Result<(), Box<dyn Error>> {
    let result = adapter.exec(sandbox_id, command, &exec_opts(60)).await?;
    if result.exit_code != 0 {
        return Err(format!(
            "container exec failed with {}: stdout={} stderr={}",
            result.exit_code, result.stdout, result.stderr
        )
        .into());
    }
    Ok(())
}

async fn read_counter(
    adapter: &Arc<dyn ProviderAdapter>,
    sandbox_id: &SandboxId,
    path: &str,
) -> Result<u64, Box<dyn Error>> {
    let command = format!("cat {path} 2>/dev/null || echo 0");
    let result = adapter.exec(sandbox_id, &command, &exec_opts(10)).await?;
    if result.exit_code != 0 {
        return Err(format!("counter read failed: {}", result.stderr).into());
    }
    Ok(result.stdout.trim().parse::<u64>()?)
}

async fn read_file(
    adapter: &Arc<dyn ProviderAdapter>,
    sandbox_id: &SandboxId,
    path: &str,
) -> Result<String, Box<dyn Error>> {
    let result = adapter
        .exec(sandbox_id, &format!("cat {path}"), &exec_opts(10))
        .await?;
    if result.exit_code != 0 {
        return Err(format!("file read failed: {}", result.stderr).into());
    }
    Ok(result.stdout)
}

fn exec_opts(timeout_s: u64) -> ExecOpts {
    ExecOpts {
        cwd: Some("/".to_owned()),
        timeout: Some(Duration::from_secs(timeout_s)),
    }
}

fn obj(fields: &[(&str, Value)]) -> JsonObject {
    fields
        .iter()
        .map(|(key, value)| ((*key).to_owned(), value.clone()))
        .collect()
}

fn stdin_recorder_command() -> &'static str {
    "python3 -u -c 'import sys,time; from pathlib import Path; p=Path(\"/tmp/eos-stdin-payloads\");\nfor line in sys.stdin:\n    f=p.open(\"a\"); f.write(line); f.close(); time.sleep(0.2)'"
}

fn live_image() -> Option<String> {
    std::env::var("EOS_LIVE_E2E_IMAGE")
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
}

fn unique_name() -> String {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or_default();
    format!("eos-write-stdin-live-{millis}-{}", std::process::id())
}
