#!/usr/bin/env sh
set -eu

repo_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
helper=$repo_root/bin/sandbox-gateway-token
test_root=$(mktemp -d)
trap 'rm -rf "$test_root"' 0 1 2 3 15

HOME=$test_root/home
export HOME
unset SANDBOX_GATEWAY_TOKEN_FILE SANDBOX_GATEWAY_AUTH_TOKEN

expected=$HOME/.ephemeral-sandbox/gateway.token
[ "$("$helper" path)" = "$expected" ]
"$helper" ensure >"$test_root/first" &
first_pid=$!
"$helper" ensure >"$test_root/second" &
second_pid=$!
wait "$first_pid"
wait "$second_pid"
first=$(sed -n '1p' "$test_root/first")
[ "$first" = "$(sed -n '1p' "$test_root/second")" ]
[ "$first" = "$("$helper" ensure)" ]
[ "${#first}" -eq 64 ]
[ "$("$helper" read)" = "$first" ]

case "$(uname -s)" in
    Darwin|*BSD)
        token_mode=$(stat -L -f '%Lp' "$expected")
        directory_mode=$(stat -L -f '%Lp' "$(dirname "$expected")")
        ;;
    *)
        token_mode=$(stat -L -c '%a' "$expected")
        directory_mode=$(stat -L -c '%a' "$(dirname "$expected")")
        ;;
esac
[ "$token_mode" = 600 ]
[ "$directory_mode" = 700 ]

printf 'replacement-token\n' | "$helper" write
[ "$("$helper" read)" = replacement-token ]

. "$helper"
sandbox_gateway_token_load
[ "$SANDBOX_GATEWAY_AUTH_TOKEN" = replacement-token ]

SANDBOX_GATEWAY_TOKEN_FILE=$test_root/service/gateway.token
export SANDBOX_GATEWAY_TOKEN_FILE
[ "$("$helper" path)" = "$SANDBOX_GATEWAY_TOKEN_FILE" ]
[ -n "$("$helper" ensure)" ]
if printf 'conflicting-token\n' | "$helper" write >/dev/null 2>&1; then
    printf 'a configured token file was overwritten\n' >&2
    exit 1
fi

chmod 644 "$SANDBOX_GATEWAY_TOKEN_FILE"
if "$helper" read >/dev/null 2>&1; then
    printf 'insecure token permissions were accepted\n' >&2
    exit 1
fi

printf 'sandbox-gateway-token check passed\n'
