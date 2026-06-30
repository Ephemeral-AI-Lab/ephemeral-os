//! Publish-time three-way line merge with structural line origin.
//!
//! [`three_way_merge`] reconciles a `base`/`active`/`command` triple on line
//! slices, preserving bytes exactly (CRLF and a missing final newline survive),
//! and returns each final line's *structural* [`Origin`] — the command side this
//! publish introduced, or the active side line it inherited. The merge stores no
//! provenance and takes no owner: the runtime above layerstack maps each origin
//! to an owner string after the layer commits.
//!
//! Non-text (NUL / invalid UTF-8) or oversized inputs are [`MergeOutcome::Ineligible`];
//! overlapping edits to the same base region are [`MergeOutcome::Conflict`].

const MERGE_MAX_BYTES: usize = 8 * 1024 * 1024;

/// Structural origin of one merged line: the command side this publish carries,
/// or the active line (0-based index into the active content) it inherited.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Origin {
    Command,
    Active(usize),
}

/// A run of consecutive final-content lines, `start` 1-based.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LineRange {
    pub start: usize,
    pub len: usize,
}

/// Outcome of a three-way merge over byte content.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MergeOutcome {
    Clean {
        bytes: Vec<u8>,
        origin: Vec<(LineRange, Origin)>,
    },
    Conflict,
    Ineligible,
}

/// Reconcile `base`/`active`/`command` on line granularity.
///
/// Returns [`MergeOutcome::Clean`] with the merged bytes and each final line's
/// structural [`Origin`] when active and command edits are disjoint (or
/// byte-identical), [`MergeOutcome::Conflict`] when they overlap with differing
/// content, and [`MergeOutcome::Ineligible`] for non-text or oversized inputs.
#[must_use]
pub fn three_way_merge(base: &[u8], active: &[u8], command: &[u8]) -> MergeOutcome {
    if !eligible(base) || !eligible(active) || !eligible(command) {
        return MergeOutcome::Ineligible;
    }

    let b = split_lines(base);
    let a = split_lines(active);
    let c = split_lines(command);

    let ra = regions(&diff(&b, &a));
    let rc = regions(&diff(&b, &c));

    let mut out: Vec<&[u8]> = Vec::new();
    let mut builder = OriginBuilder::default();

    let mut bi = 0usize;
    let mut a_cursor = 0usize;
    let mut ia = 0usize;
    let mut ic = 0usize;

    loop {
        let na = ra.get(ia).map_or(usize::MAX, |r| r.b0);
        let nc = rc.get(ic).map_or(usize::MAX, |r| r.b0);
        if na == usize::MAX && nc == usize::MAX {
            let count = b.len() - bi;
            out.extend_from_slice(&b[bi..]);
            builder.push_active(a_cursor, count);
            break;
        }

        let start = na.min(nc);
        if start > bi {
            let count = start - bi;
            out.extend_from_slice(&b[bi..start]);
            builder.push_active(a_cursor, count);
            a_cursor += count;
            bi = start;
        }

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

        let base_len = cb1 - bi;
        match (saw_a, saw_c) {
            (true, false) => {
                let count = a_lines.len();
                out.extend_from_slice(&a_lines);
                builder.push_active(a_cursor, count);
                a_cursor += count;
            }
            (false, true) => {
                out.extend_from_slice(&c_lines);
                builder.push_command(c_lines.len());
                a_cursor += base_len;
            }
            (true, true) => {
                if a_lines == c_lines {
                    let count = a_lines.len();
                    out.extend_from_slice(&a_lines);
                    builder.push_active(a_cursor, count);
                    a_cursor += count;
                } else {
                    return MergeOutcome::Conflict;
                }
            }
            (false, false) => {
                if bi < b.len() {
                    out.push(b[bi]);
                    builder.push_active(a_cursor, 1);
                    a_cursor += 1;
                }
                cb1 = bi + 1;
            }
        }
        bi = cb1.min(b.len());
    }

    MergeOutcome::Clean {
        bytes: join_lines(&out),
        origin: builder.ranges,
    }
}

fn eligible(data: &[u8]) -> bool {
    data.len() <= MERGE_MAX_BYTES && !data.contains(&0) && std::str::from_utf8(data).is_ok()
}

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
    for line in lines {
        out.extend_from_slice(line);
    }
    out
}

#[derive(Default)]
struct OriginBuilder {
    ranges: Vec<(LineRange, Origin)>,
    next_line: usize,
}

impl OriginBuilder {
    fn push_active(&mut self, first_active_idx: usize, count: usize) {
        if count == 0 {
            return;
        }
        let line = self.next_line + 1;
        if let Some((range, Origin::Active(base_idx))) = self.ranges.last_mut() {
            if *base_idx + range.len == first_active_idx {
                range.len += count;
                self.next_line += count;
                return;
            }
        }
        self.ranges.push((
            LineRange {
                start: line,
                len: count,
            },
            Origin::Active(first_active_idx),
        ));
        self.next_line += count;
    }

    fn push_command(&mut self, count: usize) {
        if count == 0 {
            return;
        }
        let line = self.next_line + 1;
        if let Some((range, Origin::Command)) = self.ranges.last_mut() {
            range.len += count;
            self.next_line += count;
            return;
        }
        self.ranges.push((
            LineRange {
                start: line,
                len: count,
            },
            Origin::Command,
        ));
        self.next_line += count;
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Eq,
    Del,
    Ins,
}

#[derive(Clone, Copy)]
struct EditOp {
    kind: Kind,
    b: usize,
}

const MYERS_MAX_D: usize = 200_000;

fn diff<'a>(a: &[&'a [u8]], b: &[&'a [u8]]) -> Vec<EditOp> {
    if a.is_empty() && b.is_empty() {
        return Vec::new();
    }
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
        let mut ops = Vec::with_capacity(a.len() + b.len());
        for _ in 0..a.len() {
            ops.push(EditOp {
                kind: Kind::Del,
                b: 0,
            });
        }
        for j in 0..b.len() {
            ops.push(EditOp {
                kind: Kind::Ins,
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
                b: (y - 1) as usize,
            });
            x -= 1;
            y -= 1;
        }
        if d > 0 {
            if x == prev_x {
                ops.push(EditOp {
                    kind: Kind::Ins,
                    b: (y - 1) as usize,
                });
            } else {
                ops.push(EditOp {
                    kind: Kind::Del,
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

struct Region {
    b0: usize,
    b1: usize,
    repl: Vec<usize>,
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
