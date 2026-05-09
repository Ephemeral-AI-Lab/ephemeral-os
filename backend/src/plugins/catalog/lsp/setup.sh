#!/usr/bin/env bash
# Idempotent Node 22 + Pyright install for the LSP plugin.
#
# Installs Node from the official tarball instead of conda/nodeenv. This has
# been reliable on Daytona sandboxes and works with Node 22+.

set -eu

PLUGIN_DIR="${EOS_PLUGIN_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)}"
NODE_HOME="${EOS_NODE_HOME:-/tmp/eos-node22}"
NODE_VERSION="${EOS_NODE_VERSION:-22.13.1}"
MARKER="$PLUGIN_DIR/.pyright_installed"

export PATH="$NODE_HOME/bin:$PATH"

if [ -f "$MARKER" ] && command -v pyright-langserver >/dev/null 2>&1; then
    exit 0
fi

if ! command -v node >/dev/null 2>&1; then
    arch="$(uname -m)"
    case "$arch" in
        x86_64) node_arch=x64 ;;
        aarch64|arm64) node_arch=arm64 ;;
        *) echo "unsupported arch: $arch" >&2; exit 2 ;;
    esac
    mkdir -p "$NODE_HOME"
    cd "$NODE_HOME"
    curl -fL --retry 3 --connect-timeout 20 --max-time 180 \
        "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
        -o node.tar.xz
    tar -xJf node.tar.xz --strip-components=1
fi

export PATH="$NODE_HOME/bin:$PATH"
npm config set prefix "$NODE_HOME"
if ! command -v pyright-langserver >/dev/null 2>&1; then
    npm install -g pyright
fi

node -v
npm -v
pyright --version
command -v pyright-langserver >/dev/null

mkdir -p "$(dirname "$MARKER")"
: > "$MARKER"
