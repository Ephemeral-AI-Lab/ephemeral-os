#!/bin/sh
set -eu

dep="${EOS_PLUGIN_DEPENDENCY_ROOT:?}"
pkg="${EOS_PLUGIN_PACKAGE_ROOT:?}"

node_root="$dep/node22"
node_bin="$node_root/bin/node"
pyright_root="$node_root/lib/node_modules/pyright"
state_root="$dep/pyright"
cache_root="$dep/npm-cache"

mkdir -p "$node_root/bin" "$pyright_root" "$state_root" "$cache_root"

cat > "$node_bin" <<'NODE'
#!/bin/sh
set -eu
if [ "$#" -gt 0 ] && [ -f "$1" ]; then
    script="$1"
    shift
    exec python3 "$script" "$@"
fi
printf '%s\n' "node22 package shim"
NODE
chmod +x "$node_bin"

cat > "$pyright_root/langserver.index.js" <<'PYRIGHT'
#!/usr/bin/env python3
import sys

def main():
    for line in sys.stdin:
        if not line:
            break
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
PYRIGHT
chmod +x "$pyright_root/langserver.index.js"

printf '%s\n' "$node_bin" > "$state_root/node-path"
printf '%s\n' "$pyright_root/langserver.index.js" > "$state_root/langserver-path"
printf '%s\n' "$pkg" > "$state_root/package-root"
