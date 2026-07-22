# macOS Setup

This guide runs the Ephemeral Sandbox core gateway and Docker-backed sandboxes
on Apple silicon Macs. Docker Desktop must already be installed and running.

The current macOS release supports arm64 (`uname -m` reports `arm64`). The host
gateway and CLIs run natively on macOS, while the sandbox daemon runs in
Linux arm64 containers managed by Docker Desktop.

Binary releases do not require Rust, Cargo, Zig, or `cargo-zigbuild`. Source
builds require Rust/Cargo; the launcher prepares the Linux musl daemon toolchain
when a rebuild is requested.

## Binary Release

Download and start the macOS arm64 release:

```sh
curl -LO https://github.com/Ephemeral-AI-Lab/ephemeral-sandbox/releases/latest/download/ephemeral-sandbox-macos-arm64.tar.gz
tar -xzf ephemeral-sandbox-macos-arm64.tar.gz
cd ephemeral-sandbox-macos-arm64

export PATH="$PWD/bin:$PATH"
bin/start-sandbox-macos-docker-gateway
tail -f /tmp/eos-gateway-macos.log
```

The launcher starts the gateway in the background and writes its token to:

```text
$HOME/.ephemeral-sandbox/gateway.token
```

## Source Checkout

From a cloned checkout, start the macOS Docker gateway and rebuild the native
host binaries plus the packaged Linux arm64 daemon:

```sh
bin/start-sandbox-macos-docker-gateway --rebuild-binary
tail -f /tmp/eos-gateway-macos.log
```

Use source builds for development or when testing local changes. The release
archive is the recommended path for users who only want to run sandboxes.

## Verify The CLIs

Use a small smoke workspace first. Binding a checkout with a large `target`
directory can make shared-base creation slow.

```sh
export PATH="$PWD/bin:$PATH"
export SANDBOX_GATEWAY_SOCKET=127.0.0.1:7878
export SANDBOX_GATEWAY_AUTH_TOKEN="$(cat "$HOME/.ephemeral-sandbox/gateway.token")"
export SANDBOX_IMAGE="${SANDBOX_IMAGE:-alpine:3.20}"

docker pull --platform linux/arm64 "$SANDBOX_IMAGE"

smoke=/tmp/ephemeral-sandbox-macos-smoke
rm -rf "$smoke"
mkdir -p "$smoke/src"
printf 'hello from macOS smoke workspace\n' >"$smoke/README.txt"
printf 'sandbox smoke file\n' >"$smoke/src/main.txt"

sandbox-manager-cli list_docker_images

created=$(
  sandbox-manager-cli create_sandbox \
    --image "$SANDBOX_IMAGE" \
    --workspace-bind-root "$smoke"
)
sandbox_id=$(printf '%s\n' "$created" | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

sandbox-runtime-cli --sandbox-id "$sandbox_id" exec_command 'pwd && cat README.txt && ls src'
sandbox-observability-cli snapshot --sandbox-id "$sandbox_id"
sandbox-manager-cli destroy_sandbox --sandbox-id "$sandbox_id"
```

Expected command output includes `/workspace`, `hello from macOS smoke
workspace`, and `main.txt`.

## Maintainer Release Package

Maintainers can build the macOS arm64 release archive from an Apple silicon
source checkout:

```sh
bin/package-macos-arm64-release
```

The package script writes:

```text
dist/release/ephemeral-sandbox-macos-arm64.tar.gz
dist/release/ephemeral-sandbox-macos-arm64.tar.gz.sha256
```

## Troubleshooting

- `this launcher supports Apple silicon Macs only`: use a Mac where `uname -m`
  reports `arm64`. An Intel macOS release is not currently published.
- `docker daemon is not reachable`: start Docker Desktop and wait until its
  engine reports that it is running.
- `no matching manifest for linux/arm64`: choose a multi-architecture image or
  pull an image that publishes a Linux arm64 variant.
- Docker reports that a workspace path is not shared: add its parent directory
  to Docker Desktop's file-sharing settings.
- `cargo not found`: use the binary release, or install Rust through rustup for
  source builds.
- Gateway startup is slow on the first source build: the rebuild path compiles
  native host binaries and prepares the Linux arm64 musl daemon package.
