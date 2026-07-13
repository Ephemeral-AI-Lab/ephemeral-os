//! OCC merge-publish experiment harness.
//!
//! This is an experiment harness, not a correctness test. It drives the real
//! layerstack public API (publish / snapshot / read / project) to measure the
//! implemented full-file designs (C1/C2/C3) and to collect the measured
//! primitives (patch bytes, copy/project cost) used to MODEL the unimplemented
//! patch-backed / hybrid / compaction designs (C4/C5/C6).
//!
//! It is `#[ignore]`-gated so `cargo test` stays fast. Run it with:
//!   cargo test -p sandbox-runtime-layerstack --test occ_merge_bench \
//!     -- --ignored --nocapture
//!
//! Output: a markdown-ish summary to stdout and a JSON dump at
//! `$TMPDIR/occ_merge_bench_results.json`. Numbers labelled "(modeled)" are
//! derived from measured primitives, not produced by a running patch backend.

#![allow(clippy::unwrap_used)]

use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use sandbox_runtime_layerstack::{
    build_workspace_base, manifest_root_hash, LayerChange, LayerPath, LayerStack, LayerStackError,
    Manifest, MergedView, PublishBase, PublishBaseRevision, PublishRejectReason,
    PublishValidatedChangesRequest,
};

// ----------------------------------------------------------------------------
// Fixture
// ----------------------------------------------------------------------------

static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

struct Fixture {
    base: PathBuf,
    root: PathBuf,
    workspace: PathBuf,
}

impl Fixture {
    fn new(label: &str) -> Self {
        let base = std::env::temp_dir().join(format!(
            "occ-bench-{label}-{}-{}",
            std::process::id(),
            NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = std::fs::remove_dir_all(&base);
        let workspace = base.join("workspace");
        std::fs::create_dir_all(&workspace).expect("create workspace dir");
        Self {
            base: base.clone(),
            root: base.join("layer-stack"),
            workspace,
        }
    }

    /// Seed a workspace file then build the layerstack base layer from it.
    fn build_base_with(&self, files: &[(&str, &[u8])]) -> Manifest {
        for (path, bytes) in files {
            let target = self.workspace.join(path);
            if let Some(parent) = target.parent() {
                std::fs::create_dir_all(parent).expect("mk parent");
            }
            std::fs::write(target, bytes).expect("seed workspace file");
        }
        build_workspace_base(&self.root, &self.workspace, false).expect("build base");
        self.stack()
            .read_active_manifest()
            .expect("read base manifest")
    }

    fn stack(&self) -> LayerStack {
        LayerStack::open(self.root.clone()).expect("open stack")
    }

    fn layers_bytes(&self) -> u64 {
        dir_bytes(&self.root.join("layers"))
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.base);
    }
}

fn lp(path: &str) -> LayerPath {
    LayerPath::parse(path).expect("valid layer path")
}

fn req(base: Manifest, changes: Vec<LayerChange>) -> PublishValidatedChangesRequest {
    PublishValidatedChangesRequest {
        base: PublishBase {
            revision: PublishBaseRevision {
                manifest_version: base.version,
                root_hash: manifest_root_hash(&base),
                layer_count: base.layers.len(),
            },
            manifest: base,
        },
        changes,
        protected_drops: Vec::new(),
    }
}

fn dir_bytes(path: &Path) -> u64 {
    let mut total = 0;
    let Ok(entries) = std::fs::read_dir(path) else {
        return 0;
    };
    for entry in entries.flatten() {
        let p = entry.path();
        let Ok(meta) = std::fs::symlink_metadata(&p) else {
            continue;
        };
        if meta.is_dir() {
            total += dir_bytes(&p);
        } else if meta.is_file() {
            total += meta.len();
        }
    }
    total
}

fn median_us(mut samples: Vec<u128>) -> f64 {
    samples.sort_unstable();
    let n = samples.len();
    if n == 0 {
        return 0.0;
    }
    samples[n / 2] as f64 / 1000.0
}

// ----------------------------------------------------------------------------
// Line model (exact byte preservation: keep trailing '\n' attached)
// ----------------------------------------------------------------------------

fn split_lines(data: &[u8]) -> Vec<&[u8]> {
    let mut lines = Vec::new();
    let mut start = 0;
    for (i, b) in data.iter().enumerate() {
        if *b == b'\n' {
            lines.push(&data[start..=i]);
            start = i + 1;
        }
    }
    if start < data.len() {
        lines.push(&data[start..]);
    }
    lines
}

fn join_lines(lines: &[&[u8]]) -> Vec<u8> {
    let mut out = Vec::new();
    for l in lines {
        out.extend_from_slice(l);
    }
    out
}

fn is_text(data: &[u8]) -> bool {
    !data.contains(&0) && std::str::from_utf8(data).is_ok()
}

// ----------------------------------------------------------------------------
// Myers O(ND) line diff
// ----------------------------------------------------------------------------

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Kind {
    Eq,
    Del,
    Ins,
}

#[derive(Clone, Copy, Debug)]
struct EditOp {
    kind: Kind,
    a: usize,
    b: usize,
}

const MYERS_MAX_D: usize = 200_000;

fn diff<'a>(a: &[&'a [u8]], b: &[&'a [u8]]) -> Vec<EditOp> {
    let n = a.len() as isize;
    let m = b.len() as isize;
    let max = (a.len() + b.len()).min(MYERS_MAX_D);
    let off = max as isize;
    let mut v = vec![0_isize; 2 * max + 1];
    let mut trace: Vec<Vec<isize>> = Vec::new();
    let mut reached = false;
    for d in 0..=(max as isize) {
        trace.push(v.clone());
        let mut k = -d;
        while k <= d {
            let mut x =
                if k == -d || (k != d && v[(k - 1 + off) as usize] < v[(k + 1 + off) as usize]) {
                    v[(k + 1 + off) as usize]
                } else {
                    v[(k - 1 + off) as usize] + 1
                };
            let mut y = x - k;
            while x < n && y < m && a[x as usize] == b[y as usize] {
                x += 1;
                y += 1;
            }
            v[(k + off) as usize] = x;
            if x >= n && y >= m {
                reached = true;
                break;
            }
            k += 2;
        }
        if reached {
            break;
        }
    }
    if !reached {
        // Edit distance exceeded the cap: fall back to delete-all + insert-all.
        let mut ops = Vec::with_capacity(a.len() + b.len());
        for i in 0..a.len() {
            ops.push(EditOp {
                kind: Kind::Del,
                a: i,
                b: 0,
            });
        }
        for j in 0..b.len() {
            ops.push(EditOp {
                kind: Kind::Ins,
                a: 0,
                b: j,
            });
        }
        return ops;
    }
    backtrack(a, b, &trace, off)
}

fn backtrack(a: &[&[u8]], b: &[&[u8]], trace: &[Vec<isize>], off: isize) -> Vec<EditOp> {
    let mut x = a.len() as isize;
    let mut y = b.len() as isize;
    let mut ops: Vec<EditOp> = Vec::new();
    for d in (0..trace.len()).rev() {
        let v = &trace[d];
        let d = d as isize;
        let k = x - y;
        let prev_k = if k == -d || (k != d && v[(k - 1 + off) as usize] < v[(k + 1 + off) as usize])
        {
            k + 1
        } else {
            k - 1
        };
        let prev_x = v[(prev_k + off) as usize];
        let prev_y = prev_x - prev_k;
        while x > prev_x && y > prev_y {
            ops.push(EditOp {
                kind: Kind::Eq,
                a: (x - 1) as usize,
                b: (y - 1) as usize,
            });
            x -= 1;
            y -= 1;
        }
        if d > 0 {
            if x == prev_x {
                ops.push(EditOp {
                    kind: Kind::Ins,
                    a: 0,
                    b: (y - 1) as usize,
                });
            } else {
                ops.push(EditOp {
                    kind: Kind::Del,
                    a: (x - 1) as usize,
                    b: 0,
                });
            }
        }
        x = prev_x;
        y = prev_y;
    }
    ops.reverse();
    ops
}

/// Self-check: applying the edit script to `a` must reproduce `b`.
fn diff_roundtrips(a: &[&[u8]], b: &[&[u8]], ops: &[EditOp]) -> bool {
    let mut got: Vec<&[u8]> = Vec::new();
    for op in ops {
        match op.kind {
            Kind::Eq | Kind::Ins => got.push(b[op.b]),
            Kind::Del => {}
        }
    }
    got == b && {
        // also confirm Eq/Del cover a exactly
        let mut ai = 0;
        for op in ops {
            match op.kind {
                Kind::Eq | Kind::Del => {
                    if op.a != ai {
                        return false;
                    }
                    ai += 1;
                }
                Kind::Ins => {}
            }
        }
        ai == a.len()
    }
}

// ----------------------------------------------------------------------------
// Change regions on base coordinates + compact patch codec
// ----------------------------------------------------------------------------

#[derive(Clone, Debug)]
struct Region {
    b0: usize,
    b1: usize,
    repl: Vec<usize>, // indices into the side's line vec
}

fn regions(ops: &[EditOp]) -> Vec<Region> {
    let mut out: Vec<Region> = Vec::new();
    let mut bi = 0;
    let mut open: Option<Region> = None;
    for op in ops {
        match op.kind {
            Kind::Eq => {
                if let Some(r) = open.take() {
                    out.push(r);
                }
                bi += 1;
            }
            Kind::Del => {
                let r = open.get_or_insert(Region {
                    b0: bi,
                    b1: bi,
                    repl: Vec::new(),
                });
                r.b1 = bi + 1;
                bi += 1;
            }
            Kind::Ins => {
                let r = open.get_or_insert(Region {
                    b0: bi,
                    b1: bi,
                    repl: Vec::new(),
                });
                r.repl.push(op.b);
            }
        }
    }
    if let Some(r) = open.take() {
        out.push(r);
    }
    out
}

fn put_uvarint(out: &mut Vec<u8>, mut value: u64) {
    loop {
        let mut byte = (value & 0x7f) as u8;
        value >>= 7;
        if value != 0 {
            byte |= 0x80;
        }
        out.push(byte);
        if value == 0 {
            break;
        }
    }
}

/// A minimal binary line-delta: per hunk { base_start, deleted_count,
/// inserted_byte_len, inserted_bytes }. Faithful order-of-magnitude proxy for
/// xdelta/bsdiff on text (dominated by inserted bytes, not file size).
fn encode_patch(side_lines: &[&[u8]], regs: &[Region]) -> Vec<u8> {
    let mut out = Vec::new();
    put_uvarint(&mut out, regs.len() as u64);
    for r in regs {
        put_uvarint(&mut out, r.b0 as u64);
        put_uvarint(&mut out, (r.b1 - r.b0) as u64);
        let bytes: usize = r.repl.iter().map(|&i| side_lines[i].len()).sum();
        put_uvarint(&mut out, bytes as u64);
        for &i in &r.repl {
            out.extend_from_slice(side_lines[i]);
        }
    }
    out
}

/// Measured patch bytes between two byte blobs (0 if non-text / identical).
fn patch_bytes(base: &[u8], new: &[u8]) -> usize {
    if !is_text(base) || !is_text(new) {
        return new.len(); // binary: store whole file, no line delta
    }
    let bl = split_lines(base);
    let nl = split_lines(new);
    let ops = diff(&bl, &nl);
    let regs = regions(&ops);
    encode_patch(&nl, &regs).len()
}

// ----------------------------------------------------------------------------
// Three-way line merge (diff3) + provenance
// ----------------------------------------------------------------------------

#[derive(Debug)]
enum Merge {
    Clean {
        bytes: Vec<u8>,
        prov: Vec<ProvRange>,
    },
    Conflict,
    Ineligible,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct ProvRange {
    start_line: usize, // 1-based
    line_count: usize,
    origin: String,
}

fn push_prov(prov: &mut Vec<ProvRange>, next_line: &mut usize, count: usize, origin: &str) {
    if count == 0 {
        return;
    }
    if let Some(last) = prov.last_mut() {
        if last.origin == origin {
            last.line_count += count;
            *next_line += count;
            return;
        }
    }
    prov.push(ProvRange {
        start_line: *next_line,
        line_count: count,
        origin: origin.to_owned(),
    });
    *next_line += count;
}

/// diff3-style merge. `active_id`/`command_id` label introduced lines.
fn three_way_merge(
    base: &[u8],
    active: &[u8],
    command: &[u8],
    active_id: &str,
    command_id: &str,
) -> Merge {
    if !is_text(base) || !is_text(active) || !is_text(command) {
        return Merge::Ineligible;
    }
    let b = split_lines(base);
    let a = split_lines(active);
    let c = split_lines(command);

    let ra = regions(&diff(&b, &a));
    let rc = regions(&diff(&b, &c));

    let mut out: Vec<&[u8]> = Vec::new();
    let mut prov: Vec<ProvRange> = Vec::new();
    let mut next_line = 1usize;

    let mut bi = 0usize;
    let mut ia = 0usize;
    let mut ic = 0usize;

    loop {
        let na = ra.get(ia).map_or(usize::MAX, |r| r.b0);
        let nc = rc.get(ic).map_or(usize::MAX, |r| r.b0);
        if na == usize::MAX && nc == usize::MAX {
            // tail: emit remaining base unchanged
            let count = b.len() - bi;
            for line in &b[bi..] {
                out.push(line);
            }
            push_prov(&mut prov, &mut next_line, count, "original");
            break;
        }
        let start = na.min(nc);
        if start > bi {
            let count = start - bi;
            for line in &b[bi..start] {
                out.push(line);
            }
            push_prov(&mut prov, &mut next_line, count, "original");
            bi = start;
        }

        // Build the maximal overlapping cluster from both sides starting at `bi`.
        let mut cb1 = bi;
        let mut a_lines: Vec<&[u8]> = Vec::new();
        let mut c_lines: Vec<&[u8]> = Vec::new();
        let mut saw_a = false;
        let mut saw_c = false;
        loop {
            let mut grew = false;
            if let Some(r) = ra.get(ia) {
                if r.b0 <= cb1 {
                    saw_a = true;
                    cb1 = cb1.max(r.b1);
                    for &i in &r.repl {
                        a_lines.push(a[i]);
                    }
                    ia += 1;
                    grew = true;
                }
            }
            if let Some(r) = rc.get(ic) {
                if r.b0 <= cb1 {
                    saw_c = true;
                    cb1 = cb1.max(r.b1);
                    for &i in &r.repl {
                        c_lines.push(c[i]);
                    }
                    ic += 1;
                    grew = true;
                }
            }
            if !grew {
                break;
            }
        }

        match (saw_a, saw_c) {
            (true, false) => {
                let count = a_lines.len();
                out.extend_from_slice(&a_lines);
                push_prov(
                    &mut prov,
                    &mut next_line,
                    count,
                    &format!("workspace_session:{active_id}"),
                );
            }
            (false, true) => {
                let count = c_lines.len();
                out.extend_from_slice(&c_lines);
                push_prov(
                    &mut prov,
                    &mut next_line,
                    count,
                    &format!("workspace_session:{command_id}"),
                );
            }
            (true, true) => {
                if a_lines == c_lines {
                    let count = a_lines.len();
                    out.extend_from_slice(&a_lines);
                    push_prov(&mut prov, &mut next_line, count, "mixed");
                } else {
                    return Merge::Conflict;
                }
            }
            (false, false) => {
                // No real change at this point; advance one base line defensively.
                if bi < b.len() {
                    out.push(b[bi]);
                    push_prov(&mut prov, &mut next_line, 1, "original");
                }
                cb1 = bi + 1;
            }
        }
        bi = cb1.max(bi);
        if bi > b.len() {
            bi = b.len();
        }
    }

    Merge::Clean {
        bytes: join_lines(&out),
        prov,
    }
}

// ----------------------------------------------------------------------------
// Provenance sidecar: hand-serialized JSON (no serde_json dev-dep needed)
// ----------------------------------------------------------------------------

fn sidecar_json(layer_id: &str, path: &str, digest: &str, prov: &[ProvRange]) -> String {
    let mut s = String::new();
    s.push_str("{\"schema_version\":1,\"layer_id\":\"");
    s.push_str(layer_id);
    s.push_str("\",\"path\":\"");
    s.push_str(path);
    s.push_str("\",\"content_digest\":\"");
    s.push_str(digest);
    s.push_str("\",\"ranges\":[");
    for (i, r) in prov.iter().enumerate() {
        if i > 0 {
            s.push(',');
        }
        let _ = write!(
            s,
            "{{\"start_line\":{},\"line_count\":{},\"origin\":\"{}\"}}",
            r.start_line, r.line_count, r.origin
        );
    }
    s.push_str("]}");
    s
}

/// Minimal query: read sidecar file, find the origin covering `line`.
fn query_origin(path: &Path, line: usize) -> Option<String> {
    let data = std::fs::read_to_string(path).ok()?;
    let mut best: Option<String> = None;
    let mut rest = data.as_str();
    while let Some(idx) = rest.find("\"start_line\":") {
        rest = &rest[idx + "\"start_line\":".len()..];
        let start = parse_leading_usize(rest)?;
        let lc_idx = rest.find("\"line_count\":")?;
        let after_lc = &rest[lc_idx + "\"line_count\":".len()..];
        let count = parse_leading_usize(after_lc)?;
        let or_idx = rest.find("\"origin\":\"")?;
        let after_or = &rest[or_idx + "\"origin\":\"".len()..];
        let end = after_or.find('"')?;
        let origin = &after_or[..end];
        if line >= start && line < start + count {
            best = Some(origin.to_owned());
        }
        rest = after_or;
    }
    best
}

fn parse_leading_usize(s: &str) -> Option<usize> {
    let digits: String = s.chars().take_while(char::is_ascii_digit).collect();
    digits.parse().ok()
}

fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(data);
    let out = h.finalize();
    let mut s = String::with_capacity(out.len() * 2);
    for byte in out {
        let _ = write!(s, "{byte:02x}");
    }
    s
}

// ----------------------------------------------------------------------------
// Input generators
// ----------------------------------------------------------------------------

fn gen_text(lines: usize, seed: u64) -> Vec<u8> {
    let mut out = Vec::new();
    let mut x = seed.wrapping_add(1);
    for i in 0..lines {
        x = x
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        let _ = writeln!(
            unsafe_str(&mut out),
            "line {i:06} token={:08x} the quick brown fox jumps over lazy dog",
            (x >> 32) as u32
        );
    }
    out
}

// Tiny helper so we can use write! into a Vec<u8> as utf8.
struct VecWriter<'a>(&'a mut Vec<u8>);
impl std::fmt::Write for VecWriter<'_> {
    fn write_str(&mut self, s: &str) -> std::fmt::Result {
        self.0.extend_from_slice(s.as_bytes());
        Ok(())
    }
}
fn unsafe_str(v: &mut Vec<u8>) -> VecWriter<'_> {
    VecWriter(v)
}

fn replace_line(data: &[u8], line_idx: usize, new_line: &str) -> Vec<u8> {
    let lines = split_lines(data);
    let mut out: Vec<&[u8]> = lines.clone();
    let owned = new_line.as_bytes().to_vec();
    if line_idx < out.len() {
        out[line_idx] = &owned;
    }
    join_lines(&out)
}

// ----------------------------------------------------------------------------
// Result accumulation
// ----------------------------------------------------------------------------

#[derive(Default)]
struct Report {
    lines: Vec<String>,
    json: Vec<String>,
}

impl Report {
    fn say(&mut self, s: String) {
        println!("{s}");
        self.lines.push(s);
    }
    fn jrow(&mut self, obj: String) {
        self.json.push(obj);
    }
}

// ----------------------------------------------------------------------------
// The benchmark
// ----------------------------------------------------------------------------

#[test]
#[ignore = "experiment harness; run explicitly with --ignored --nocapture"]
fn run_occ_merge_benchmarks() {
    let mut r = Report::default();
    r.say("# OCC merge-publish benchmark run".to_owned());

    self_check(&mut r);
    b1_small_tiny_edit(&mut r);
    b2_large_one_line(&mut r);
    b3_large_scattered(&mut r);
    b4_churn(&mut r);
    b5_many_small(&mut r);
    b6_mixed_routes(&mut r);
    b7_overlapping_reject(&mut r);
    b8_nonoverlap_concurrent(&mut r);
    b9_binary_and_friends(&mut r);
    b10_deep_manifest(&mut r);
    b11_cache_and_leases(&mut r);
    b12_provenance_correctness(&mut r);

    let json = format!("{{\"rows\":[{}]}}", r.json.join(","));
    let out = std::env::temp_dir().join("occ_merge_bench_results.json");
    std::fs::write(&out, json).expect("write results json");
    r.say(format!("\nResults JSON: {}", out.display()));
}

fn self_check(r: &mut Report) {
    // Validate the diff engine round-trips on a few shapes before trusting sizes.
    let cases: &[(&str, &str)] = &[
        ("a\nb\nc\n", "a\nB\nc\n"),
        ("a\nb\nc\n", "a\nb\nc\nd\n"),
        ("a\nb\nc\n", "b\nc\n"),
        ("", "x\ny\n"),
        ("x\ny\n", ""),
    ];
    let mut ok = true;
    for (a, b) in cases {
        let al = split_lines(a.as_bytes());
        let bl = split_lines(b.as_bytes());
        let ops = diff(&al, &bl);
        if !diff_roundtrips(&al, &bl, &ops) {
            ok = false;
        }
    }
    r.say(format!(
        "diff self-check round-trips: {}",
        if ok { "PASS" } else { "FAIL" }
    ));
    assert!(ok, "diff engine must round-trip");
}

fn publish_full(stack: &mut LayerStack, path: &str, content: &[u8]) {
    stack
        .publish_layer(&[LayerChange::Write {
            path: lp(path),
            content: content.to_vec(),
        }])
        .expect("publish full layer");
}

fn b1_small_tiny_edit(r: &mut Report) {
    r.say("\n## B1 small text, tiny edit".to_owned());
    let f = Fixture::new("b1");
    let base_bytes = b"alpha\nbeta\ngamma\n";
    let base = f.build_base_with(&[("README.md", base_bytes)]);
    let edited = replace_line(base_bytes, 1, "beta-edited\n");

    let mut pub_us = Vec::new();
    for _ in 0..25 {
        let fi = Fixture::new("b1i");
        let m = fi.build_base_with(&[("README.md", base_bytes)]);
        let mut st = fi.stack();
        let t = Instant::now();
        st.publish_validated_changes(req(
            m,
            vec![LayerChange::Write {
                path: lp("README.md"),
                content: edited.clone(),
            }],
        ))
        .expect("publish");
        pub_us.push(t.elapsed().as_nanos());
    }

    let mut st = f.stack();
    publish_full(&mut st, "README.md", &edited);
    let committed = f.layers_bytes();
    let m = st.read_active_manifest().expect("m");
    let view = MergedView::new(f.root.clone());

    let mut read_us = Vec::new();
    for _ in 0..50 {
        let t = Instant::now();
        let _ = view.read_bytes("README.md", &m).expect("read");
        read_us.push(t.elapsed().as_nanos());
    }
    let proj = f.base.join("proj");
    let mut proj_us = Vec::new();
    for _ in 0..25 {
        let t = Instant::now();
        view.project(&proj, &m).expect("project");
        proj_us.push(t.elapsed().as_nanos());
    }
    let prov = three_way_merge(base_bytes, base_bytes, &edited, "ws-active", "ws-cmd");
    let (sidecar_len, _) = sidecar_for(&prov, "L1", "README.md", &edited);
    let patch = patch_bytes(base_bytes, &edited);

    r.say(format!(
        "committed={committed}B  patch(modeled)={patch}B  sidecar={sidecar_len}B  publish_p50={:.1}us  read_p50={:.1}us  project_p50={:.1}us",
        median_us(pub_us.clone()), median_us(read_us.clone()), median_us(proj_us.clone())
    ));
    r.jrow(format!(
        "{{\"case\":\"B1\",\"committed\":{committed},\"patch_modeled\":{patch},\"sidecar\":{sidecar_len},\"publish_us\":{:.2},\"read_us\":{:.2},\"project_us\":{:.2}}}",
        median_us(pub_us), median_us(read_us), median_us(proj_us)
    ));
    let _ = base;
}

fn sidecar_for(m: &Merge, layer_id: &str, path: &str, content: &[u8]) -> (usize, usize) {
    if let Merge::Clean { prov, .. } = m {
        let digest = format!("sha256:{}", sha256_hex(content));
        let json = sidecar_json(layer_id, path, &digest, prov);
        (json.len(), prov.len())
    } else {
        (0, 0)
    }
}

fn b2_large_one_line(r: &mut Report) {
    r.say("\n## B2 large text, one-line edit".to_owned());
    let f = Fixture::new("b2");
    let base_bytes = gen_text(16_000, 2); // ~1 MiB
    let m = f.build_base_with(&[("big.txt", &base_bytes)]);
    let edited = replace_line(&base_bytes, 8_000, "line 008000 EDITED-ONE-LINE\n");

    let mut st = f.stack();
    let t = Instant::now();
    st.publish_validated_changes(req(
        m,
        vec![LayerChange::Write {
            path: lp("big.txt"),
            content: edited.clone(),
        }],
    ))
    .expect("publish");
    let pub_us = t.elapsed().as_nanos();

    let committed = f.layers_bytes();
    let patch = patch_bytes(&base_bytes, &edited);
    let merge = three_way_merge(&base_bytes, &base_bytes, &edited, "ws-active", "ws-cmd");
    let (sidecar_len, ranges) = sidecar_for(&merge, "L1", "big.txt", &edited);

    r.say(format!(
        "file={}B  committed={committed}B  patch(modeled)={patch}B  ratio_patch/full={:.4}  sidecar={sidecar_len}B ranges={ranges}  publish={:.1}us",
        base_bytes.len(), patch as f64 / committed.max(1) as f64, pub_us as f64 / 1000.0
    ));
    r.jrow(format!(
        "{{\"case\":\"B2\",\"file\":{},\"committed\":{committed},\"patch_modeled\":{patch},\"sidecar\":{sidecar_len},\"publish_us\":{:.2}}}",
        base_bytes.len(), pub_us as f64 / 1000.0
    ));
}

fn b3_large_scattered(r: &mut Report) {
    r.say("\n## B3 large text, scattered non-overlapping edits".to_owned());
    let f = Fixture::new("b3");
    let base_bytes = gen_text(16_000, 3);
    let m = f.build_base_with(&[("big.txt", &base_bytes)]);
    let mut edited = base_bytes.clone();
    for k in 0..40 {
        edited = replace_line(
            &edited,
            200 + k * 300,
            &format!("line scattered edit {k}\n"),
        );
    }
    let mut st = f.stack();
    st.publish_validated_changes(req(
        m,
        vec![LayerChange::Write {
            path: lp("big.txt"),
            content: edited.clone(),
        }],
    ))
    .expect("publish");
    let committed = f.layers_bytes();
    let patch = patch_bytes(&base_bytes, &edited);
    let merge = three_way_merge(&base_bytes, &base_bytes, &edited, "ws-active", "ws-cmd");
    let clean = matches!(merge, Merge::Clean { .. });
    let (sidecar_len, ranges) = sidecar_for(&merge, "L1", "big.txt", &edited);
    r.say(format!(
        "file={}B committed={committed}B patch(modeled)={patch}B sidecar={sidecar_len}B ranges={ranges} merge_clean={clean}",
        base_bytes.len()
    ));
    r.jrow(format!(
        "{{\"case\":\"B3\",\"file\":{},\"committed\":{committed},\"patch_modeled\":{patch},\"sidecar\":{sidecar_len},\"ranges\":{ranges}}}",
        base_bytes.len()
    ));
}

fn b4_churn(r: &mut Report) {
    r.say("\n## B4 repeated edits to same large file across many sessions".to_owned());
    let base_bytes = gen_text(16_000, 4);
    let ks = [1usize, 2, 5, 10, 20, 50];
    for &k in &ks {
        let f = Fixture::new("b4");
        let _m = f.build_base_with(&[("big.txt", &base_bytes)]);
        let mut st = f.stack();
        let mut prev = base_bytes.clone();
        let mut patch_total = 0usize;
        for i in 0..k {
            let next = replace_line(&prev, 5_000 + i, &format!("churn rev {i}\n"));
            patch_total += patch_bytes(&prev, &next);
            publish_full(&mut st, "big.txt", &next);
            prev = next;
        }
        let committed = f.layers_bytes(); // C1/C2/C3 active+cold (no compaction)
        let depth = st.read_active_manifest().expect("m").depth();
        let final_len = prev.len() as u64;
        // C4 modeled: base full + sum patches (cold, caches evicted)
        let c4_cold = base_bytes.len() as u64 + patch_total as u64;
        // C4 active modeled: patches + one materialized cache (~current file)
        let c4_active = patch_total as u64 + final_len;
        // C6 modeled: compaction squashes superseded layers -> one concrete copy
        let c6_cold = final_len;
        r.say(format!(
            "K={k:2}  C1/C3 committed={committed}B depth={depth}  | C4_cold(modeled)={c4_cold}B C4_active(modeled)={c4_active}B  | C6_cold(modeled)={c6_cold}B"
        ));
        r.jrow(format!(
            "{{\"case\":\"B4\",\"k\":{k},\"c1_committed\":{committed},\"depth\":{depth},\"c4_cold\":{c4_cold},\"c4_active\":{c4_active},\"c6_cold\":{c6_cold}}}"
        ));
    }
}

fn b5_many_small(r: &mut Report) {
    r.say("\n## B5 many small files in one publish".to_owned());
    let f = Fixture::new("b5");
    let count = 500usize;
    let mut seed_files: Vec<(String, Vec<u8>)> = Vec::new();
    for i in 0..count {
        seed_files.push((
            format!("src/file_{i:04}.txt"),
            format!("content of file {i}\nsecond line\n").into_bytes(),
        ));
    }
    let seed_refs: Vec<(&str, &[u8])> = seed_files
        .iter()
        .map(|(p, b)| (p.as_str(), b.as_slice()))
        .collect();
    let m = f.build_base_with(&seed_refs);
    let mut changes = Vec::new();
    let mut sidecar_total = 0usize;
    for i in 0..count {
        let path = format!("src/file_{i:04}.txt");
        let new = format!("content of file {i}\nsecond line edited\n");
        changes.push(LayerChange::Write {
            path: lp(&path),
            content: new.clone().into_bytes(),
        });
        let mg = three_way_merge(
            format!("content of file {i}\nsecond line\n").as_bytes(),
            format!("content of file {i}\nsecond line\n").as_bytes(),
            new.as_bytes(),
            "ws-active",
            "ws-cmd",
        );
        sidecar_total += sidecar_for(&mg, "L1", &path, new.as_bytes()).0;
    }
    let mut st = f.stack();
    let t = Instant::now();
    st.publish_validated_changes(req(m, changes))
        .expect("publish many");
    let pub_us = t.elapsed().as_nanos();
    let committed = f.layers_bytes();
    r.say(format!(
        "files={count} committed={committed}B sidecar_total={sidecar_total}B publish={:.1}us",
        pub_us as f64 / 1000.0
    ));
    r.jrow(format!(
        "{{\"case\":\"B5\",\"files\":{count},\"committed\":{committed},\"sidecar_total\":{sidecar_total},\"publish_us\":{:.2}}}",
        pub_us as f64 / 1000.0
    ));
}

fn b6_mixed_routes(r: &mut Report) {
    r.say("\n## B6 mixed source + ignored changes (atomic)".to_owned());
    let f = Fixture::new("b6");
    let m = f.build_base_with(&[(".gitignore", b"*.log\n"), ("README.md", b"hello\n")]);
    let mut st = f.stack();
    let res = st
        .publish_validated_changes(req(
            m,
            vec![
                LayerChange::Write {
                    path: lp("README.md"),
                    content: b"hello world\n".to_vec(),
                },
                LayerChange::Write {
                    path: lp("debug.log"),
                    content: b"noise\n".to_vec(),
                },
            ],
        ))
        .expect("publish mixed");
    r.say(format!(
        "source_count={} ignored_count={} no_op={} -> atomic single layer",
        res.route_summary.source_count, res.route_summary.ignored_count, res.no_op
    ));
    r.jrow(format!(
        "{{\"case\":\"B6\",\"source\":{},\"ignored\":{}}}",
        res.route_summary.source_count, res.route_summary.ignored_count
    ));
}

fn b7_overlapping_reject(r: &mut Report) {
    r.say("\n## B7 overlapping edits must reject".to_owned());
    let base = b"l1\nl2\nl3\nl4\nl5\n";
    let active = replace_line(base, 2, "l3-ACTIVE\n");
    let command = replace_line(base, 2, "l3-COMMAND\n");
    let merge = three_way_merge(base, &active, &command, "ws-active", "ws-cmd");
    let is_conflict = matches!(merge, Merge::Conflict);

    // And the real OCC path rejects too (fingerprint mismatch).
    let f = Fixture::new("b7");
    let m = f.build_base_with(&[("f.txt", base)]);
    let mut st = f.stack();
    publish_full(&mut st, "f.txt", &active); // another session advances active
    let err = st
        .publish_validated_changes(req(
            m,
            vec![LayerChange::Write {
                path: lp("f.txt"),
                content: command.clone(),
            }],
        ))
        .expect_err("must reject");
    let occ_reject = matches!(
        err,
        LayerStackError::PublishRejected(b) if b.reason == PublishRejectReason::SourceConflict
    );
    r.say(format!(
        "merge_conflict={is_conflict}  occ_rejects={occ_reject}"
    ));
    r.jrow(format!(
        "{{\"case\":\"B7\",\"merge_conflict\":{is_conflict},\"occ_rejects\":{occ_reject}}}"
    ));
    assert!(
        is_conflict && occ_reject,
        "B7 must reject overlapping edits"
    );
}

fn b8_nonoverlap_concurrent(r: &mut Report) {
    r.say("\n## B8 non-overlapping concurrent edits auto-merge".to_owned());
    let base = b"top\nl2\nl3\nl4\nbottom\n";
    let active = replace_line(base, 0, "top-changed-by-active\n");
    let command = replace_line(base, 4, "bottom-changed-by-command\n");

    let f = Fixture::new("b8");
    let m = f.build_base_with(&[("f.txt", base)]);
    let mut st = f.stack();
    // Another session advances active with its non-overlapping edit.
    publish_full(&mut st, "f.txt", &active);

    // The live resolver auto-merges this session's disjoint stale write.
    let resolved = st
        .publish_validated_changes(req(
        m.clone(),
        vec![LayerChange::Write {
            path: lp("f.txt"),
            content: command.clone(),
        }],
        ))
        .expect("disjoint stale write must merge");
    let view = MergedView::new(f.root.clone());
    let (resolved_bytes, _) = view
        .read_bytes("f.txt", &resolved.manifest)
        .expect("read resolved merge");
    let resolved_bytes = resolved_bytes.unwrap_or_default();
    let resolved_has_both = String::from_utf8_lossy(&resolved_bytes)
        .contains("top-changed-by-active")
        && String::from_utf8_lossy(&resolved_bytes).contains("bottom-changed-by-command");

    // The independent model must produce the same clean merge.
    let merge = three_way_merge(base, &active, &command, "ws-active", "ws-cmd");
    let model_matches = matches!(
        &merge,
        Merge::Clean { bytes, .. } if bytes == &resolved_bytes
    );
    r.say(format!(
        "resolver_has_both={resolved_has_both}  model_matches={model_matches}"
    ));
    r.jrow(format!(
        "{{\"case\":\"B8\",\"resolver_has_both\":{resolved_has_both},\"model_matches\":{model_matches}}}"
    ));
    assert!(
        resolved_has_both && model_matches,
        "B8: live resolver and model must produce the same disjoint merge"
    );
}

fn b9_binary_and_friends(r: &mut Report) {
    r.say("\n## B9 binary / invalid-UTF-8 / minified / generated".to_owned());
    // binary with NUL
    let bin_base = vec![0u8, 1, 2, 3, 4, 5, 6, 7];
    let bin_new = vec![0u8, 1, 2, 9, 4, 5, 6, 7];
    let m_bin = three_way_merge(&bin_base, &bin_base, &bin_new, "a", "c");
    let bin_ineligible = matches!(m_bin, Merge::Ineligible);

    // invalid utf-8
    let bad = vec![b'a', b'\n', 0xff, 0xfe, b'\n'];
    let bad_text = is_text(&bad);

    // minified: one giant line
    let mut minified = Vec::new();
    for i in 0..50_000 {
        let _ = write!(unsafe_str(&mut minified), "a{i};");
    }
    let mut minified2 = minified.clone();
    minified2.extend_from_slice(b"z9;");
    let min_patch = patch_bytes(&minified, &minified2);
    let min_full = minified2.len();

    r.say(format!(
        "binary_ineligible={bin_ineligible}  invalid_utf8_is_text={bad_text}  minified: full={min_full}B patch(modeled)={min_patch}B (one-line file -> patch ~ full)"
    ));
    r.jrow(format!(
        "{{\"case\":\"B9\",\"binary_ineligible\":{bin_ineligible},\"min_full\":{min_full},\"min_patch\":{min_patch}}}"
    ));
    assert!(
        bin_ineligible && !bad_text,
        "binary/invalid utf8 must be ineligible"
    );
}

fn b10_deep_manifest(r: &mut Report) {
    r.say("\n## B10 deep manifest (many layers)".to_owned());
    let depths = [1usize, 5, 20, 50, 100];
    for &d in &depths {
        let f = Fixture::new("b10");
        let _m = f.build_base_with(&[("root.txt", b"root\n")]);
        let mut st = f.stack();
        // Each publish writes a distinct deep file, growing manifest depth.
        for i in 0..d {
            publish_full(
                &mut st,
                &format!("dir/lvl_{i:04}.txt"),
                format!("content {i}\n").as_bytes(),
            );
        }
        let m = st.read_active_manifest().expect("m");
        let depth = m.depth();
        let view = MergedView::new(f.root.clone());
        // Read the deepest file (lives in the oldest non-base layer).
        let target = "dir/lvl_0000.txt";
        let mut read_us = Vec::new();
        for _ in 0..30 {
            let t = Instant::now();
            let _ = view.read_bytes(target, &m).expect("read deep");
            read_us.push(t.elapsed().as_nanos());
        }
        let proj = f.base.join("proj");
        let mut proj_us = Vec::new();
        for _ in 0..15 {
            let t = Instant::now();
            view.project(&proj, &m).expect("project");
            proj_us.push(t.elapsed().as_nanos());
        }
        let committed = f.layers_bytes();
        r.say(format!(
            "depth={depth:3}  committed={committed}B  read_deep_p50={:.1}us  project_p50={:.1}us",
            median_us(read_us.clone()),
            median_us(proj_us.clone())
        ));
        r.jrow(format!(
            "{{\"case\":\"B10\",\"depth\":{depth},\"committed\":{committed},\"read_us\":{:.2},\"project_us\":{:.2}}}",
            median_us(read_us), median_us(proj_us)
        ));
    }
}

fn b11_cache_and_leases(r: &mut Report) {
    r.say("\n## B11 hot/cold project + active lease retention (cache modeled)".to_owned());
    let f = Fixture::new("b11");
    let base_bytes = gen_text(16_000, 11);
    let _m = f.build_base_with(&[("big.txt", &base_bytes)]);
    let mut st = f.stack();
    let mut prev = base_bytes.clone();
    let mut patch_total = 0usize;
    for i in 0..10 {
        let next = replace_line(&prev, 1000 + i, &format!("rev {i}\n"));
        patch_total += patch_bytes(&prev, &next);
        publish_full(&mut st, "big.txt", &next);
        prev = next;
    }
    let m = st.read_active_manifest().expect("m");
    let view = MergedView::new(f.root.clone());

    // cold-ish: project into a fresh dir each time (forces full copy)
    let mut cold_us = Vec::new();
    for i in 0..10 {
        let proj = f.base.join(format!("cold_{i}"));
        let t = Instant::now();
        view.project(&proj, &m).expect("project cold");
        cold_us.push(t.elapsed().as_nanos());
        let _ = std::fs::remove_dir_all(&proj);
    }
    // hot: reuse same dir (page cache warm)
    let projhot = f.base.join("hot");
    let mut hot_us = Vec::new();
    for _ in 0..10 {
        let t = Instant::now();
        view.project(&projhot, &m).expect("project hot");
        hot_us.push(t.elapsed().as_nanos());
    }
    let materialized = dir_bytes(&projhot);
    let committed = f.layers_bytes();

    // lease retention: hold a snapshot, then release, observe active count
    let lease = st.acquire_snapshot("owner-b11").expect("lease");
    let active_with_lease = st.active_lease_count();
    let released = st.release_lease(&lease.lease_id).expect("release");
    let active_after = st.active_lease_count();

    // C4 modeled: active disk while a lease pins the materialized cache:
    let c4_active = patch_total as u64 + materialized; // patches + 1 cache
    let c4_cold = base_bytes.len() as u64 + patch_total as u64; // caches evicted
    let c6_cold = prev.len() as u64; // compaction -> single concrete copy

    r.say(format!(
        "committed(C1/C3)={committed}B materialized_tree={materialized}B  project_cold_p50={:.1}us project_hot_p50={:.1}us",
        median_us(cold_us.clone()), median_us(hot_us.clone())
    ));
    r.say(format!(
        "C4_active(modeled, lease pins cache)={c4_active}B  C4_cold(modeled, evicted)={c4_cold}B  C6_cold(modeled, compacted)={c6_cold}B"
    ));
    r.say(format!("lease active_count with_lease={active_with_lease} released={released} after={active_after}"));
    r.jrow(format!(
        "{{\"case\":\"B11\",\"committed\":{committed},\"materialized\":{materialized},\"project_cold_us\":{:.2},\"project_hot_us\":{:.2},\"c4_active\":{c4_active},\"c4_cold\":{c4_cold},\"c6_cold\":{c6_cold}}}",
        median_us(cold_us), median_us(hot_us)
    ));
}

fn b12_provenance_correctness(r: &mut Report) {
    r.say("\n## B12 provenance attribution correctness".to_owned());
    // base lines 1..=6; active edits line2; command edits line5; both edit line4 identically.
    let base = b"one\ntwo\nthree\nfour\nfive\nsix\n";
    let active = b"one\ntwo-A\nthree\nFOUR\nfive\nsix\n";
    let command = b"one\ntwo\nthree\nFOUR\nfive\nfive-C\nsix\n";
    let merge = three_way_merge(base, active, command, "ws-active", "ws-cmd");
    let mut checks = Vec::new();
    if let Merge::Clean { bytes, prov } = &merge {
        // Write a real sidecar and query it back from disk.
        let dir = std::env::temp_dir().join(format!("occ-b12-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("mk dir");
        let scar = dir.join("README.md.json");
        let digest = format!("sha256:{}", sha256_hex(bytes));
        std::fs::write(&scar, sidecar_json("L42", "README.md", &digest, prov))
            .expect("write sidecar");

        let text = String::from_utf8_lossy(bytes);
        r.say(format!("merged:\n{}", text.replace('\n', "\\n")));
        for (label, line, expect_contains) in [
            ("line1 original", 1usize, "original"),
            ("line2 active edit", 2, "ws-active"),
            ("line4 identical edit -> mixed", 4, "mixed"),
        ] {
            let got = query_origin(&scar, line).unwrap_or_default();
            let ok = got.contains(expect_contains);
            checks.push(ok);
            r.say(format!("  {label}: origin={got} ok={ok}"));
        }
        // command-only inserted line (five-C) should be attributed to command session
        let cmd_line = text.lines().position(|l| l == "five-C").map(|i| i + 1);
        if let Some(cl) = cmd_line {
            let got = query_origin(&scar, cl).unwrap_or_default();
            let ok = got.contains("ws-cmd");
            checks.push(ok);
            r.say(format!(
                "  inserted 'five-C' (line {cl}): origin={got} ok={ok}"
            ));
        }
        let _ = std::fs::remove_dir_all(&dir);
    } else {
        r.say("MERGE NOT CLEAN (unexpected)".to_owned());
        checks.push(false);
    }
    let all = checks.iter().all(|x| *x);
    r.say(format!("attribution_all_correct={all}"));
    r.jrow(format!("{{\"case\":\"B12\",\"attribution_ok\":{all}}}"));
    assert!(all, "B12 provenance attribution must be correct");
}
