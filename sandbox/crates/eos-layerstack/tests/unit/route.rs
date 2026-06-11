use crate::model::LayerChange;

use crate::test_fixture::{lp, Fixture, TestResult};
use crate::LayerStack;

use super::{route_for_path, Route};

fn is_ignored(fixture: &Fixture, path: &str) -> TestResult<bool> {
    Ok(route_of(fixture, path)? == Route::Direct)
}

fn route_of(fixture: &Fixture, path: &str) -> TestResult<Route> {
    let stack = LayerStack::open(fixture.root.clone())?;
    Ok(route_for_path(&stack, &lp(path)?)?)
}

#[test]
fn root_gitignore_routes_target_as_direct() -> TestResult {
    let fixture = Fixture::new_with_gitignore("gitignore_direct", "target/\n*.pyc\n")?;

    assert!(is_ignored(&fixture, "target/out.txt")?);
    assert!(is_ignored(&fixture, "pkg/cache.pyc")?);
    assert!(!is_ignored(&fixture, "src/main.rs")?);
    Ok(())
}

#[test]
fn routes_tracked_ignored_and_git_paths_distinctly() -> TestResult {
    let fixture = Fixture::new_with_gitignore("route_kinds", "target/\n*.pyc\n")?;

    assert_eq!(route_of(&fixture, "src/main.rs")?, Route::Gated);
    assert_eq!(route_of(&fixture, "target/out.txt")?, Route::Direct);
    assert_eq!(route_of(&fixture, "pkg/cache.pyc")?, Route::Direct);
    assert_eq!(route_of(&fixture, ".git/config")?, Route::Drop);
    Ok(())
}

// N2 (HIGH): a no-slash dir-only pattern is anchored at *any* depth, so a
// file under `frontend/node_modules/` routes DIRECT — the most common
// misroute the old root-anchored prefix check produced.
#[test]
fn dir_only_pattern_matches_at_any_depth() -> TestResult {
    let fixture = Fixture::new_with_gitignore("n2_dir_only", "node_modules/\n")?;
    assert!(is_ignored(&fixture, "frontend/node_modules/index.js")?);
    assert!(is_ignored(&fixture, "node_modules/index.js")?);
    assert!(!is_ignored(&fixture, "frontend/src/index.js")?);
    Ok(())
}

// N3 (HIGH, data-loss): `*` must not cross `/`. `logs/*.log` does NOT match
// `logs/sub/x.log`, so it routes GATED (base-hash validated) — not
// DIRECT-then-silently-clobber as the old `wildcard_match` allowed.
#[test]
fn star_does_not_cross_slash() -> TestResult {
    let fixture = Fixture::new_with_gitignore("n3_star_slash", "logs/*.log\n")?;
    assert!(is_ignored(&fixture, "logs/app.log")?);
    assert!(!is_ignored(&fixture, "logs/sub/x.log")?);
    Ok(())
}

// Nested `.gitignore` is scoped to its own subtree.
#[test]
fn nested_gitignore_is_scoped_to_its_subtree() -> TestResult {
    let fixture = Fixture::new_with_gitignores("nested", &[("frontend", "dist/\n")])?;
    assert!(is_ignored(&fixture, "frontend/dist/bundle.js")?);
    assert!(!is_ignored(&fixture, "dist/bundle.js")?);
    Ok(())
}

// `**` matches across path segments.
#[test]
fn double_star_matches_across_segments() -> TestResult {
    let fixture = Fixture::new_with_gitignore("double_star", "**/build/\n")?;
    assert!(is_ignored(&fixture, "a/b/build/out.o")?);
    assert!(is_ignored(&fixture, "build/out.o")?);
    assert!(!is_ignored(&fixture, "a/b/builder.rs")?);
    Ok(())
}

// `!` re-includes within a non-sealed directory.
#[test]
fn bang_re_includes_in_unsealed_dir() -> TestResult {
    let fixture = Fixture::new_with_gitignore("bang", "*.log\n!keep.log\n")?;
    assert!(is_ignored(&fixture, "other.log")?);
    assert!(!is_ignored(&fixture, "keep.log")?);
    Ok(())
}

// Directory seal: an excluded ancestor dir seals its subtree — a deeper `!`
// cannot rescue contents under it (Git semantics).
#[test]
fn excluded_dir_seals_against_deeper_reinclude() -> TestResult {
    let fixture =
        Fixture::new_with_gitignores("seal", &[("", "build/\n"), ("build", "!keep.txt\n")])?;
    assert!(is_ignored(&fixture, "build/keep.txt")?);
    Ok(())
}

// Composite ruleset: the N2/N3/nested/seal behaviors above hold together on
// one fixture, including the `.git` drop.
#[test]
fn composite_ruleset_routes_each_path_as_expected() -> TestResult {
    let fixture = Fixture::new_with_gitignores(
        "composite_routes",
        &[
            ("", "node_modules/\nlogs/*.log\nbuild/\n"),
            ("build", "!keep.txt\n"),
        ],
    )?;
    for (path, expected) in [
        ("frontend/node_modules/index.js", Route::Direct), // N2 dir-only any depth
        ("logs/sub/x.log", Route::Gated),                  // N3 star not crossing /
        ("logs/app.log", Route::Direct),
        ("build/keep.txt", Route::Direct), // seal beats deeper !
        ("src/main.rs", Route::Gated),
        (".git/config", Route::Drop),
    ] {
        assert_eq!(route_of(&fixture, path)?, expected, "route for {path}");
    }
    Ok(())
}

// Overlay/layerstack composition: a `.gitignore` published into an *upper*
// layer (the base layer carries none) is resolved through the active merged
// manifest — the same newest-layer-wins, whiteout-aware view the overlay
// mount projects. Proves the oracle reads `.gitignore` via `read_bytes`/
// `MergedView` across layers, not just from a single seeded layer.
#[test]
fn gitignore_resolves_through_published_upper_layer() -> TestResult {
    let fixture = Fixture::new("cross_layer")?;
    LayerStack::open(fixture.root.clone())?.publish_layer(&[
        LayerChange::Write {
            path: lp(".gitignore")?,
            content: b"node_modules/\n".to_vec(),
        },
        LayerChange::Write {
            path: lp("frontend/.gitignore")?,
            content: b"dist/\n".to_vec(),
        },
    ])?;
    // Root rule from the upper layer, matched at depth via the seal.
    assert!(is_ignored(&fixture, "frontend/node_modules/index.js")?);
    // Nested rule, also published into the upper layer.
    assert!(is_ignored(&fixture, "frontend/dist/bundle.js")?);
    assert!(!is_ignored(&fixture, "src/main.rs")?);
    Ok(())
}

// Regression (double-strip on prefix replay, data-loss-class): a per-level
// matcher for dir `D` must not strip `D` from a path whose next component
// repeats `D`'s name. The caller already makes the path relative to `D`, so
// the matcher must be rooted at `.` — `GitignoreBuilder::new(D)` would strip
// `D` a SECOND time (raw byte prefix), turning `a/x` into `x` and matching an
// anchored `/x`. Ground truth below is `git check-ignore --no-index`.
#[test]
fn nested_anchored_pattern_not_double_stripped_on_prefix_replay() -> TestResult {
    let fixture = Fixture::new_with_gitignores(
        "prefix_replay",
        &[("a", "/x\n/b\n"), ("build", "/build/x\n")],
    )?;
    // `/x` anchored at `a/` matches `a/x` (DIRECT) but NOT `a/a/x` — routing
    // the tracked `a/a/x` DIRECT would bypass the gate and silently clobber.
    assert!(is_ignored(&fixture, "a/x")?);
    assert!(!is_ignored(&fixture, "a/a/x")?);
    // Seal variant: `/b` seals `a/b`'s subtree, but `a/a/b` is not the
    // anchored `a/b`, so its whole subtree must stay GATED.
    assert!(is_ignored(&fixture, "a/b/file.txt")?);
    assert!(!is_ignored(&fixture, "a/a/b/file.txt")?);
    // Opposite (false-GATED) direction: `/build/x` anchored at `build/` DOES
    // match `build/build/x`; the old double-strip dropped it to `x` and missed.
    assert!(is_ignored(&fixture, "build/build/x")?);
    assert!(!is_ignored(&fixture, "build/x")?);
    Ok(())
}
