# Linux Multipass Setup

This guide runs the Ephemeral Sandbox Docker gateway, CLIs, and browser console
inside an Ubuntu 22.04 Multipass VM. It was verified with:

- Host: Windows 11
- VM: `ephemeral-sandbox-2204`, Ubuntu 22.04.5 LTS
- Docker: Ubuntu `docker.io`
- Node.js: 24.x
- Rust: stable
- Test image: `alpine:3.20`

The VM is the Linux environment. Docker runs inside the VM, and each sandbox is a
Docker container launched by the gateway.

## Create The VM

Install Multipass on Windows:

```powershell
winget install --id Canonical.Multipass --exact --silent --accept-package-agreements --accept-source-agreements
```

Create a 22.04 VM with enough disk for Cargo, Docker images, and sandbox shared
base caches:

```powershell
multipass launch 22.04 --name ephemeral-sandbox-2204 --cpus 2 --memory 4G --disk 30G
multipass shell ephemeral-sandbox-2204
```

Inside the VM, install the base packages:

```sh
sudo apt-get update
sudo apt-get install -y \
  ca-certificates curl git jq lsof \
  build-essential clang pkg-config libssl-dev musl-tools \
  docker.io

sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
newgrp docker
docker info
```

Install Node.js 24 by your preferred package source, then verify:

```sh
node --version
npm --version
```

Install Rust and the targets used by the daemon package:

```sh
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
. "$HOME/.cargo/env"
rustup target add x86_64-unknown-linux-musl aarch64-unknown-linux-musl
cargo install cargo-zigbuild --locked
```

## Clone The Repositories

From the VM:

```sh
mkdir -p "$HOME/code/Ephemeral-AI-Lab"
cd "$HOME/code/Ephemeral-AI-Lab"
git clone https://github.com/Ephemeral-AI-Lab/ephemeral-sandbox.git
git clone https://github.com/Ephemeral-AI-Lab/ephemeral-sandbox-console.git
```

If the VM has trouble cloning from GitHub, create archives from a clean host
checkout and transfer them with `multipass transfer`. After extracting Windows
archives in Linux, normalize launcher line endings if needed:

```sh
find "$HOME/code/Ephemeral-AI-Lab/ephemeral-sandbox/bin" \
  "$HOME/code/Ephemeral-AI-Lab/ephemeral-sandbox-console/bin" \
  -maxdepth 1 -type f -print0 | xargs -0 perl -pi -e 's/\r\n/\n/g'
```

The repositories include `.gitattributes` entries that keep `bin/*` launcher
scripts checked out with LF line endings.

## Prepare Zig

`bin/setup-musl-cross` installs the Zig version expected by the packaging flow:

```sh
cd "$HOME/code/Ephemeral-AI-Lab/ephemeral-sandbox"
. "$HOME/.cargo/env"
export PATH="$HOME/.cargo/bin:$PWD/bin:$PATH"
bin/setup-musl-cross
zig version
```

If that download stalls in Multipass, download the Linux tarball on Windows and
transfer it:

```powershell
curl.exe -L -o $env:TEMP\zig-x86_64-linux-0.16.0.tar.xz https://ziglang.org/download/0.16.0/zig-x86_64-linux-0.16.0.tar.xz
multipass transfer $env:TEMP\zig-x86_64-linux-0.16.0.tar.xz ephemeral-sandbox-2204:/home/ubuntu/zig-x86_64-linux-0.16.0.tar.xz
```

Then unpack it inside the VM:

```sh
mkdir -p "$HOME/.cache/ephemeral-os"
tar -xf "$HOME/zig-x86_64-linux-0.16.0.tar.xz" -C "$HOME/.cache/ephemeral-os"
ln -sf "$HOME/.cache/ephemeral-os/zig-x86_64-linux-0.16.0/zig" "$HOME/.cargo/bin/zig"
zig version
```

If Cargo caches were copied from Windows, restore executable bits on crate build
scripts before building:

```sh
find "$HOME/.cargo/registry/src" -type f -name "*.sh" -exec chmod +x {} +
```

## Start The Gateway

The Linux config uses Docker directly from the VM:

```sh
cd "$HOME/code/Ephemeral-AI-Lab/ephemeral-sandbox"
. "$HOME/.cargo/env"
export PATH="$HOME/.cargo/bin:$PWD/bin:$PATH"
export SANDBOX_GATEWAY_CONFIG_YAML="$PWD/config/linux-amd64.yml"
export SANDBOX_XTASK_BUILDER=zigbuild
export SANDBOX_GATEWAY_PID_FILE=/tmp/eos-gateway-linux.pid
export SANDBOX_GATEWAY_LOG=/tmp/eos-gateway-linux.log

bin/start-sandbox-docker-gateway --rebuild-binary
```

The gateway listens on `127.0.0.1:7878` inside the VM and writes the auth token
to:

```text
$HOME/.ephemeral-sandbox/gateway.token
```

Keep the gateway running. In a second VM shell, load the token for CLI checks:

```sh
cd "$HOME/code/Ephemeral-AI-Lab/ephemeral-sandbox"
. "$HOME/.cargo/env"
export SANDBOX_GATEWAY_SOCKET=127.0.0.1:7878
export SANDBOX_GATEWAY_AUTH_TOKEN="$(cat "$HOME/.ephemeral-sandbox/gateway.token")"
```

## Prepare A Test Image

With normal Docker Hub access:

```sh
docker pull alpine:3.20
```

If Docker Hub is blocked from the VM, pull or reuse the image on Windows, save
it, transfer it, and load it in the VM:

```powershell
docker pull alpine:3.20
docker save alpine:3.20 -o $env:TEMP\alpine-3.20.tar
multipass transfer $env:TEMP\alpine-3.20.tar ephemeral-sandbox-2204:/home/ubuntu/alpine-3.20.tar
```

```sh
docker load -i "$HOME/alpine-3.20.tar"
docker images alpine:3.20
```

Use a small smoke workspace first. Binding a checkout that already has a large
`target/` directory can copy gigabytes into the shared base cache.

## Verify The CLIs

Create a sandbox from the small `config/` directory:

```sh
created=$(
  target/debug/sandbox-manager-cli create_sandbox \
    --image alpine:3.20 \
    --workspace-bind-root "$PWD/config"
)
sandbox_id=$(printf '%s\n' "$created" | jq -r .id)
printf '%s\n' "$sandbox_id"
```

Inspect it:

```sh
target/debug/sandbox-manager-cli inspect_sandbox --sandbox-id "$sandbox_id"
```

Run basic runtime operations:

```sh
target/debug/sandbox-runtime-cli --sandbox-id "$sandbox_id" exec_command pwd
target/debug/sandbox-runtime-cli --sandbox-id "$sandbox_id" exec_command "ls -1"
target/debug/sandbox-runtime-cli --sandbox-id "$sandbox_id" file_read --path README.md --limit 5
```

Check observability:

```sh
target/debug/sandbox-observability-cli snapshot --sandbox-id "$sandbox_id"
```

Check daemon HTTP health. Use the `daemon_http.port` value from
`inspect_sandbox`:

```sh
curl "http://127.0.0.1:<daemon_http_port>/health"
```

Expected results include:

- sandbox state `ready`
- `pwd` returns `/workspace`
- `ls -1` shows `README.md`, `bench.yml`, `linux-amd64.yml`, and `prd.yml`
- `file_read` returns content from `config/README.md`
- daemon HTTP health returns `{"status":"ok","service":"daemon_http",...}`

## Run The Web Console

Build the SPA in the console repo:

```sh
cd "$HOME/code/Ephemeral-AI-Lab/ephemeral-sandbox-console/web"
npm ci
npm run build
```

Start the Rust console server against the already-running gateway:

```sh
cd "$HOME/code/Ephemeral-AI-Lab/ephemeral-sandbox-console"
. "$HOME/.cargo/env"
export EPHEMERAL_SANDBOX_ROOT="$HOME/code/Ephemeral-AI-Lab/ephemeral-sandbox"
export SANDBOX_GATEWAY_SOCKET=127.0.0.1:7878
export SANDBOX_GATEWAY_AUTH_TOKEN="$(cat "$HOME/.ephemeral-sandbox/gateway.token")"
export SANDBOX_CONSOLE_ASSETS="$PWD/web/dist"
export SANDBOX_CONSOLE_PID_FILE=/tmp/eos-console-linux.pid
export SANDBOX_CONSOLE_LOG=/tmp/eos-console-linux.log

bin/start-sandbox-console-stack --skip-gateway --skip-spa --bind 0.0.0.0:7880
```

Inside the VM, verify the BFF:

```sh
curl -sf http://127.0.0.1:7880/
curl -sf http://127.0.0.1:7880/api/catalog >/tmp/catalog.json
```

If the VM has a host-reachable address, open `http://<vm-ip>:7880` from
Windows. Some Multipass NAT setups do not route the guest IP back to Windows;
that is a network exposure issue, not a console startup failure. In that case,
use bridged networking for the VM or an SSH/user-level local proxy.

In the web console:

1. Confirm the dashboard status says `Connected`.
2. Confirm the ready sandbox appears in the list.
3. Open the sandbox.
4. On the Terminal tab, run:

   ```sh
   printf 'ui-console-ok\n'; pwd; ls -1
   ```

5. Confirm the ledger shows `OK`, `ui-console-ok`, `/workspace`, and the
   workspace files.

## Why Rust And Zig Are Downloaded

The gateway binary alone is not enough when the Docker backend rebuilds the
sandbox daemon package. The gateway runs on the VM host, but every Docker
sandbox also receives a Linux daemon binary from `dist/sandbox-daemon-linux-amd64`.

`--rebuild-binary` asks the launcher to build and package that daemon. The
repository's Rust toolchain file pins the build to the stable Linux toolchain,
so rustup may download `stable-x86_64-unknown-linux-gnu` the first time. The
daemon package targets `x86_64-unknown-linux-musl`, and `cargo-zigbuild` uses
Zig to build C dependencies consistently for that target.

If `dist/sandbox-daemon-linux-amd64` is already present and current, start the
gateway without `--rebuild-binary` to avoid rebuilding the daemon package.

## Troubleshooting

- `curl`, `rustup`, Zig, Cargo, or `docker pull` waits for minutes with no CPU:
  the Multipass VM network is likely stalled. Stop the stale process and seed
  downloads from Windows with `multipass transfer`.
- Docker Hub returns `connect: connection refused`: load the image from a
  Windows `docker save` tarball or configure a registry mirror.
- `/usr/bin/env: 'sh\r': No such file or directory`: launcher scripts have CRLF
  endings. Normalize `bin/*` with the `perl -pi` command above.
- `Permission denied` from a Cargo crate `.sh` script after copying caches from
  Windows: restore executable bits with the `find ... chmod +x` command above.
- `failed to unshare namespace stack`: use `config/linux-amd64.yml` with
  `manager.docker.privileged: true` for Multipass/native Linux Docker.
- `overlay mount syscall failed at fsconfig create: Invalid argument`: Ubuntu
  22.04's 5.15 kernel may reject repeated `lowerdir+` fsconfig entries. Use a
  revision with the legacy colon-joined `lowerdir` fallback.
- Sandbox creation copies multiple gigabytes or the gateway dies during base
  cache creation: do not use a checkout containing `target/` as the first smoke
  workspace. Use a small workspace or set `CARGO_TARGET_DIR` outside the bound
  tree.
