set -e
if command -v git >/dev/null 2>&1; then exit 0; fi
echo "[sandbox] Installing git..."
as_root() {
    if [ "$(id -u)" = "0" ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo -n "$@"
    else
        return 1
    fi
}
if command -v apt-get >/dev/null 2>&1; then
    as_root mkdir -p /var/lib/apt/lists/partial
    as_root apt-get update -qq && as_root apt-get install -y -qq git
elif command -v apk >/dev/null 2>&1; then
    as_root apk add --no-cache git
elif command -v microdnf >/dev/null 2>&1; then
    as_root microdnf install -y git
elif command -v dnf >/dev/null 2>&1; then
    as_root dnf install -y git
elif command -v yum >/dev/null 2>&1; then
    as_root yum install -y git
else
    echo "[sandbox] No package manager found; git not installed" >&2
    exit 1
fi
echo "[sandbox] git installed"
