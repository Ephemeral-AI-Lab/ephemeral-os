# Web Console — Manual E2E Journey

The Phase 9 acceptance journey: create → exec → stdin → preview → blame →
squash → destroy, exercised through the exact console endpoints the browser
uses. Run it against a fresh gateway to validate a build end to end.

Prereqs: Docker running, `bin/start-sandbox-docker-gateway` done (it
repackages the in-container daemon when sources changed), and the console
up via `bin/start-sandbox-console` (default `127.0.0.1:7880`).

In the browser the same journey is: Fleet Board `[+ New Sandbox]` → detail
Terminal tab (create session, run the server, type into stdin, Ctrl-C) →
`Preview` on the running command card → Files tab (blame toggle on a
published file) → LayerStack `Squash` → header `Destroy` (type the id).

The scripted equivalent:

```sh
#!/bin/sh
set -eu
RPC=http://127.0.0.1:7880/api/rpc
post() { curl -s -X POST "$RPC" -H 'content-type: application/json' -d "$1"; }
sse()  { curl -sN -X POST "$RPC" -H 'content-type: application/json' \
              -H 'accept: text/event-stream' -d "$1"; }
jqf()  { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

WSROOT=$(mktemp -d /tmp/eos-journey.XXXXXX)
echo "console journey" > "$WSROOT/hello.txt"

# 1. create (streams progress like CreateSandboxModal)
ID=$(sse "{\"op\":\"create_sandbox\",\"scope\":{\"kind\":\"system\"},\
\"args\":{\"image\":\"ubuntu:24.04\",\"workspace_root\":\"$WSROOT\"}}" \
  | grep '^data: ' | tail -1 | sed 's/^data: //' | jqf "['id']")
echo "created $ID"

# 2. exec a server in an explicit session (yield_time_ms pinned to 0)
WS=$(post "{\"op\":\"create_workspace_session\",\"scope\":{\"kind\":\"sandbox\",\
\"sandbox_id\":\"$ID\"},\"args\":{}}" | jqf "['workspace_session_id']")
SRV='perl -MIO::Socket::INET -e '"'"'$s=IO::Socket::INET->new(LocalPort=>8000,Listen=>5,ReuseAddr=>1) or die; while($c=$s->accept()){while(<$c>){last if /^\r?$/;} print $c "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"; close $c;}'"'"''
CSID=$(post "{\"op\":\"exec_command\",\"scope\":{\"kind\":\"sandbox\",\"sandbox_id\":\"$ID\"},\
\"args\":{\"cmd\":$(python3 -c "import json,sys;print(json.dumps(sys.argv[1]))" "$SRV"),\
\"workspace_session_id\":\"$WS\",\"yield_time_ms\":0}}" | jqf "['command_session_id']")
echo "server running as $CSID"

# 3. stdin + transcript (StdinBar semantics: write, then read-nudge)
post "{\"op\":\"write_command_stdin\",\"scope\":{\"kind\":\"sandbox\",\"sandbox_id\":\"$ID\"},\
\"args\":{\"command_session_id\":\"$CSID\",\"stdin\":\"hello\\n\",\"yield_time_ms\":0}}" >/dev/null
post "{\"op\":\"read_command_lines\",\"scope\":{\"kind\":\"sandbox\",\"sandbox_id\":\"$ID\"},\
\"args\":{\"command_session_id\":\"$CSID\",\"start_offset\":0,\"limit\":1000}}" | jqf "['status']"

# 4. preview through the /s proxy (the Preview tab's iframe URL)
sleep 1
curl -s -w " <- %{http_code}\n" "http://127.0.0.1:7880/s/$ID/shared/8000/"

# 5. Ctrl-C the server, destroy the session
post "{\"op\":\"write_command_stdin\",\"scope\":{\"kind\":\"sandbox\",\"sandbox_id\":\"$ID\"},\
\"args\":{\"command_session_id\":\"$CSID\",\"stdin\":\"\\u0003\",\"yield_time_ms\":0}}" | jqf "['status']"
post "{\"op\":\"destroy_workspace_session\",\"scope\":{\"kind\":\"sandbox\",\"sandbox_id\":\"$ID\"},\
\"args\":{\"workspace_session_id\":\"$WS\"}}" | jqf "['destroyed']"

# 6. publish a write, blame it (BlameGutter's data), list the tree
post "{\"op\":\"file_write\",\"scope\":{\"kind\":\"sandbox\",\"sandbox_id\":\"$ID\"},\
\"args\":{\"path\":\"hello.txt\",\"content\":\"edited from the console\\n\"}}" >/dev/null
post "{\"op\":\"file_blame\",\"scope\":{\"kind\":\"sandbox\",\"sandbox_id\":\"$ID\"},\
\"args\":{\"path\":\"hello.txt\"}}" | jqf "['ranges'][0]['owner']"
post "{\"op\":\"file_list\",\"scope\":{\"kind\":\"sandbox\",\"sandbox_id\":\"$ID\"},\"args\":{}}" \
  | jqf "['entries'][0]['name']"

# 7. squash (SquashButton), then 8. destroy (ConfirmDestroyDialog)
sse "{\"op\":\"checkpoint_squash\",\"scope\":{\"kind\":\"system\"},\
\"args\":{\"sandbox_id\":\"$ID\"}}" | grep '^data: ' | tail -1
sse "{\"op\":\"destroy_sandbox\",\"scope\":{\"kind\":\"system\"},\
\"args\":{\"sandbox_id\":\"$ID\"}}" | grep '^data: ' | tail -1 | sed 's/^data: //' | jqf "['state']"
echo "journey complete"
```

Expected: create streams `log` events then a ready record; the transcript
answers `running` and echoes stdin; the preview returns `ok <- 200`; Ctrl-C
yields `cancelled`; blame reports an `operation:<request-id>` owner for the
edited line range; squash streams and reports `squashed_blocks`; destroy
returns a `stopped` record and the fleet list no longer contains the id.
