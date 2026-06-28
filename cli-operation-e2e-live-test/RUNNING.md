# How to run the E2E suite

A practical run guide. For architecture and layout, see `README.md`.

## 1. One-time setup

These are checked once per machine, not per run.

- **Docker running** — `docker version` must succeed.
- **pytest installed** — `pytest --version`. If missing: `pip install -r requirements.txt`.
  (Already installed? Then nothing to do — you don't reinstall before each run.)
- **Rust toolchain** — only needed the first time the gateway is built / the
  in-container daemon is packaged.

## 2. Quick start

```sh
cd cli-operation-e2e-live-test
pytest -m smoke      # smallest check: gateway up + structured list_sandboxes
pytest               # the whole suite
```

The gateway is started automatically on the first test that needs it and reused
afterward — you do **not** start it by hand.

## 3. What to run

| Command                                                        | Runs                                          |
|---------------------------------------------------------------|-----------------------------------------------|
| `pytest -m smoke`                                             | smallest end-to-end check                     |
| `pytest manager`                                             | manager `management` family lifecycle         |
| `pytest runtime`                                            | runtime `command` + `workspace_session`       |
| `pytest observability`                                     | placeholder (skipped)                         |
| `pytest`                                                   | everything                                    |
| `pytest runtime/command`                                  | one family                                    |
| `pytest runtime/command/test_command.py::test_exec_one_shot` | one test                                      |
| `pytest -v`                                                | verbose (per-test names)                      |
| `pytest -x`                                                | stop at first failure                         |

Exit code is `0` only when everything that ran passed.

## 4. Customize a run

All knobs are environment variables (defaults in `core/config.py`); set them
inline for one run:

```sh
E2E_IMAGE=debian:12 pytest manager                   # different image
E2E_WORKSPACE_VARIANT=special_case_b pytest manager  # different repo/ workspace variant
E2E_NETWORK_PROFILE=isolated pytest runtime
E2E_REBUILD_BINARY=0 pytest -m smoke     # fastest cold start: skip the forced daemon rebuild
```

Workspace variants live under `repo/` — one host directory per variant
(`repo/testbed`, `repo/special_case_b`, …), bind-mounted into the sandbox as its
workspace root. `repo/testbed` is the default.

| Variable                      | Default             | Controls                                          |
|-------------------------------|---------------------|---------------------------------------------------|
| `E2E_IMAGE`                   | `ubuntu:24.04`      | Docker image for new sandboxes                     |
| `E2E_WORKSPACE_VARIANT`       | `testbed`           | variant subfolder under `repo/` (bind-mounted)     |
| `E2E_WORKSPACE_ROOT`          | `repo/<variant>`    | absolute host workspace root (overrides variant)   |
| `E2E_NETWORK_PROFILE`         | `shared`            | workspace-session profile (`shared`/`isolated`)    |
| `SANDBOX_GATEWAY_CONFIG_YAML` | `../config/prd.yml` | daemon/sandbox config YAML used by the gateway      |
| `E2E_REBUILD_BINARY`          | `1`                 | cold-start gateway with `--rebuild-binary`          |

## 5. Gateway & cleanup

- **First run** (no gateway up) cold-starts it via
  `bin/start-sandbox-docker-gateway` — with `--rebuild-binary` when
  `E2E_REBUILD_BINARY=1`, which may take a while (cargo build + daemon package).
- **Later runs** reuse the running gateway (instant). It is left running between
  runs on purpose; restart it with `bin/start-sandbox-docker-gateway` if needed.
- Every sandbox / workspace session a test creates is destroyed by fixture
  teardown — even when the test fails. Logs are never scraped; results come from
  each operation's JSON.

## 6. Troubleshooting

- **`pytest: command not found`** → `pip install -r requirements.txt`
  (or run `python3 -m pytest ...`).
- **Cannot connect / gateway never ready** → check Docker is running, then look
  at `/tmp/eos-gateway.log` and the pid in `/tmp/eos-gateway.pid`. Force a fresh
  gateway: `bin/start-sandbox-docker-gateway --rebuild-binary`.
- **`create_sandbox` errors with `start_container: expected value at line 1
  column 1`** → that is a backend (Docker provider) failure, not a test bug. The
  suite is reporting it faithfully; the create path must be fixed in
  `crates/sandbox-provider-docker` for the manager/runtime tests to pass.
- **First cold start is slow** → expected (it builds/packages binaries). Use
  `E2E_REBUILD_BINARY=0` once the daemon artifacts in `dist/` are current.
```
