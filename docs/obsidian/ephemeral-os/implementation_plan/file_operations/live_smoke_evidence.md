---
title: File Operations Live Smoke Evidence (M6)
tags:
  - ephemeral-os
  - sandbox
  - runtime
  - file
  - e2e
status: done
updated: 2026-07-02
---

# File Operations Live Smoke Evidence (M6)

Live end-to-end smoke run of `file_read` / `file_write` / `file_edit` against a
real Docker sandbox, exercising **both** backends (sessionless layerstack publish
and session namespace runner). Gateway rebuilt with `--rebuild-binary`
(`cargo run -p xtask -- package --target aarch64-unknown-linux-musl` succeeded,
Linux-compiling the daemon + namespace-process setns file-op body + workspace
`run_file_op`). All operations issued via `sandbox-cli` only.

- Sandbox: `eos-ab5d6b74-b438-4651-a6b2-3f45639c0357` (image `ubuntu:24.04`,
  workspace bind `/tmp/eos-testbed`)
- Workspace session: `00000118be41db9cba4263` (shared network)
- Daemon package: `sandbox-daemon-linux-arm64`
  `sha256=0649114127743ab6d763c56f713836580f9633f31010f2cba263a428ca9e8847`

## Result: 15/15 smoke cases pass

### Read (5/5)

1. Sessionless read of a sessionless-written file → `smoke/created.txt` returns
   `content:"hello\nworld"`, correct window fields. **PASS**
2. Session read of a session-written file → `sess/deep/s.txt` with
   `--workspace-session-id` returns `"in-session-content"`. **PASS**
3. Sessionless read `--offset 2 --limit 2` over `readme.txt` → `"line2\nline3"`,
   `start_line:2`, `total_lines:5`, `next_offset:4`, `truncated:true`. **PASS**
4. Sessionless read of missing file → `not_found`. **PASS**
5. Sessionless read of `/etc/passwd` (absolute outside root) → `invalid_request`
   (`invalid path`). **PASS**

### Write (5/5)

1. Sessionless write creates `smoke/created.txt` (`type:create`), read sees it.
   **PASS**
2. Sessionless write updates it (`type:update`); `file_blame` attributes the
   changed line to `operation:<request_id>` and the unchanged line keeps the
   prior create's `operation:<request_id>`. **PASS**
3. Session write `sess/deep/s.txt` visible with `--workspace-session-id`,
   `not_found` for the sessionless read (never published). **PASS**
4. Session write created the missing `sess/deep/` parent dirs through the mounted
   overlay. **PASS**
5. Sessionless write to `existing_dir` → `invalid_request` (`not a regular file
   (Directory)`). **PASS**

### Edit (5/5)

1. Sessionless edit `HELLO`→`HOWDY` (1 replacement), read sees `"HOWDY\nworld"`.
   **PASS**
2. Sessionless edit `alpha`→`beta` `replace_all:true` on `multi.txt` → 3
   replacements, `"beta beta beta"`. **PASS**
3. Sessionless edit with missing `old_string` → `invalid_request` (`string to
   replace not found`). **PASS**
4. Session edit `in-session`→`EDITED` visible with `--workspace-session-id`
   (`"EDITED-content"`), sessionless read still `not_found` (not published).
   **PASS**
5. Ordered multi-edit `beta beta beta`→`one`→`two` against evolving content → 2
   replacements, `"two"`. **PASS**

Both backends exercised; no unexpected errors (the six `error` responses are the
four intentional negative cases plus the two session-isolation `not_found`
checks).

## Raw transcript

```text
### create_workspace_session
{"workspace_session_id":"00000118be41db9cba4263","network_profile":"shared"}
===== SESSIONLESS =====
### file_write --path smoke/created.txt --content hello\nworld
{"type":"create","path":"smoke/created.txt","bytes_written":11}

### file_read --path smoke/created.txt
{"path":"smoke/created.txt","content":"hello\nworld","start_line":1,"num_lines":2,"total_lines":2,"bytes_read":11,"total_bytes":11,"next_offset":null,"truncated":false}

### file_write --path smoke/created.txt --content HELLO\nworld
{"type":"update","path":"smoke/created.txt","bytes_written":11}

### file_blame --path smoke/created.txt
{"path":"smoke/created.txt","ranges":[{"start_line":1,"line_count":1,"owner":"operation:e8d77535-9a4a-44c4-afe2-d1815ad8ccb2"},{"start_line":2,"line_count":1,"owner":"operation:e7a0279c-cd2f-4741-949a-2c9105dcf804"}]}

### file_read --path readme.txt --offset 2 --limit 2
{"path":"readme.txt","content":"line2\nline3","start_line":2,"num_lines":2,"total_lines":5,"bytes_read":11,"total_bytes":30,"next_offset":4,"truncated":true}

### file_read --path missing.txt
{"error":{"kind":"not_found","message":"file not found: missing.txt","details":{"path":"missing.txt"}}}

### file_read --path /etc/passwd
{"error":{"kind":"invalid_request","message":"invalid path: /etc/passwd","details":{}}}

### file_write --path existing_dir --content nope
{"error":{"kind":"invalid_request","message":"path is not a regular file (Directory): existing_dir","details":{}}}

===== SESSIONLESS EDIT =====
### file_edit --path smoke/created.txt --edits [{"old_string":"HELLO","new_string":"HOWDY"}]
{"type":"edit","path":"smoke/created.txt","edits_applied":1,"replacements":1,"bytes_written":11}

### file_read --path smoke/created.txt
{"path":"smoke/created.txt","content":"HOWDY\nworld","start_line":1,"num_lines":2,"total_lines":2,"bytes_read":11,"total_bytes":11,"next_offset":null,"truncated":false}

### file_edit --path multi.txt --edits [{"old_string":"alpha","new_string":"beta","replace_all":true}]
{"type":"edit","path":"multi.txt","edits_applied":1,"replacements":3,"bytes_written":15}

### file_read --path multi.txt
{"path":"multi.txt","content":"beta beta beta","start_line":1,"num_lines":1,"total_lines":1,"bytes_read":14,"total_bytes":15,"next_offset":null,"truncated":false}

### file_edit --path smoke/created.txt --edits [{"old_string":"zzz-not-present","new_string":"x"}]
{"error":{"kind":"invalid_request","message":"string to replace not found in smoke/created.txt: zzz-not-present","details":{}}}

### file_edit --path multi.txt --edits [{"old_string":"beta beta beta","new_string":"one"},{"old_string":"one","new_string":"two"}]
{"type":"edit","path":"multi.txt","edits_applied":2,"replacements":2,"bytes_written":4}

### file_read --path multi.txt
{"path":"multi.txt","content":"two","start_line":1,"num_lines":1,"total_lines":1,"bytes_read":3,"total_bytes":4,"next_offset":null,"truncated":false}

===== SESSION (live namespace runner) =====
### file_write --workspace-session-id 00000118be41db9cba4263 --path sess/deep/s.txt --content in-session-content
{"type":"create","path":"sess/deep/s.txt","bytes_written":18}

### file_read --workspace-session-id 00000118be41db9cba4263 --path sess/deep/s.txt
{"path":"sess/deep/s.txt","content":"in-session-content","start_line":1,"num_lines":1,"total_lines":1,"bytes_read":18,"total_bytes":18,"next_offset":null,"truncated":false}

### file_read --path sess/deep/s.txt
{"error":{"kind":"not_found","message":"file not found: sess/deep/s.txt","details":{"path":"sess/deep/s.txt"}}}

### file_edit --workspace-session-id 00000118be41db9cba4263 --path sess/deep/s.txt --edits [{"old_string":"in-session","new_string":"EDITED"}]
{"type":"edit","path":"sess/deep/s.txt","edits_applied":1,"replacements":1,"bytes_written":14}

### file_read --workspace-session-id 00000118be41db9cba4263 --path sess/deep/s.txt
{"path":"sess/deep/s.txt","content":"EDITED-content","start_line":1,"num_lines":1,"total_lines":1,"bytes_read":14,"total_bytes":14,"next_offset":null,"truncated":false}

### file_read --path sess/deep/s.txt
{"error":{"kind":"not_found","message":"file not found: sess/deep/s.txt","details":{"path":"sess/deep/s.txt"}}}
```
