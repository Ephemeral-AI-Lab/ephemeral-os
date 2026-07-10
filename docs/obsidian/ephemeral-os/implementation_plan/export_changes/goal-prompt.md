> **Frozen historical prompt (operation-layout exempt, 2026-07-11):** Do not
> execute this prompt verbatim; its paths and package names describe the tree
> in which the export implementation landed.

/goal Implement the Manager Export Changes spec in ephemeral-os, then build and pass its live e2e catalog.

Design truth - read both, follow exactly:
- docs/obsidian/ephemeral-os/implementation_plan/export_changes/spec.md
- docs/obsidian/ephemeral-os/implementation_plan/export_changes/test-case.md

`sandbox-manager-cli export_changes --sandbox-id ID --dest PATH [--format dir|tar|tar-zst]` folds every published layer above the base (newest-wins, whiteout/opaque aware) into one zstd tar delta, streams it in bounded chunks, and the MANAGER renders it host-side. Read-only on layer storage.

Phase 1 - implementation (on main; touch only what's needed; SOLID/SRP; no inline comments in src; tests in tests/ only). Build order:
1. Winner fold, layerstack projection/delta.rs (new): delta = active layers minus every `B*` (assert >=1 base); newest-first metadata-only fold to BTreeMap<LayerPath,Winner>{File,Symlink,Directory,Delete,OpaqueDir}; reuse apply.rs walk + whiteout helpers; pin to MergedView (inv 2).
2. emit_stream.rs (new): winner map to tar::Builder to zstd spool `<scratch_root>/.export/<nonce>.tar.zst`; entries carry mode + second-granular mtime; Delete->`.wh.<name>`, opaque->`.wh..wh..opq`. zstd is NET-NEW in [workspace.dependencies].
3. operation impls/export.rs (new): export_layerstack + read_export_chunk, BOTH cli:None (squash precedent); singleflight per root; acquire_snapshot lease (fold->spool->release); in-memory {export_id->path,total}; base64 chunks <=2 MiB raw under the 16 MiB MAX_REQUEST_BYTES envelope; unlink-on-eof. services.rs boot step removes `.export/` on start (the session reap never walks scratch). Entries join the layerstack group in operation.rs.
4. manager export_apply.rs (new) - THE host boundary (inv 9, adversarial C1/C2): per-directory THREE-PASS apply (opaque-clear, whiteout, content; mirrors apply.rs) so a dotfile winner is never cleared; reach entries by a dest-rooted O_NOFOLLOW fd walk; REJECT absolute/`..`/hardlink entries and validate whiteout targets AFTER prefix strip; skip-unchanged on (size, second-truncated mtime), stamp on write; cap decompressed bytes + entry count; daemon counts untrusted. dir/tar/tar-zst renders; archive temp+rename.
5. manager export_changes.rs (checkpoint_squash template): dest guard (absolute + deny-list `/`,$HOME,manager state,`.export/`); rebuild sandbox-scoped request reusing request_id; forward start; drive chunk loop; hand stream to applier; merge one result line.
6. EXPORT_CHANGES_SPEC and its management_operations.rs dispatcher land in the SAME commit + a SPECS<->OPERATIONS parity test. LAYERSTACK_EXPORT record. manager gains base64.
Gates: cargo build && cargo test && cargo clippy --all-targets && cargo fmt. tests/manager_export.rs MUST drive a HOSTILE daemon (traversal, hardlink, symlink-then-traverse, bombs, deny-list).

Phase 2 - live e2e (only after Phase 1 gates green):
1. Implement the 30-case catalog exactly as test-case.md defines, under cli-operation-e2e-live-test/manager/management/export/: test_export_easy.py (EZ-01..10), _medium.py (MED-01..10), _hard.py (HRD-01..10); markers `export and easy|medium|hard`; new export/helpers.py owns export_changes(), verdict.json, fault-injection, preconditions P1-P4 (hard-fail).
2. Bring-up `bin/start-sandbox-docker-gateway --rebuild-binary`; run section 5 order P -> EZ -> MED -> HRD-01..08 -> HRD-09/10.
3. Assert structured JSON + on-disk tree only, never logs. Load-bearing: nothing outside dest (HRD-01..05), delta==MergedView, byte-zero re-run. Red-first: HRD-01/02/03 fail on host-safety pre-canonicalization; MED-04 fails vs a blind tar-order applier.
4. On failure fix the code to the spec invariant it traces to (section 4), not the assertion.

Done when: all cargo gates green (incl. hostile-daemon unit tests + parity); 30/30 cases pass on three axes + teardown, P1-P4 asserted; committed to main citing the spec.
