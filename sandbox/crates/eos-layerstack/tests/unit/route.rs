use crate::model::LayerChange;

use crate::commit::prepare::RouteProvider;
use crate::test_fixture::{lp, Fixture, TestResult};
use crate::LayerStack;

use super::{route_metrics, StackRouteProvider};

fn route_provider(fixture: &Fixture) -> StackRouteProvider {
    StackRouteProvider {
        root: fixture.root.clone(),
    }
}

#[test]
fn root_gitignore_routes_target_as_direct() -> TestResult {
    let fixture = Fixture::new_with_gitignore("gitignore_direct", "target/\n*.pyc\n")?;
    let provider = route_provider(&fixture);

    assert!(provider.is_ignored(&lp("target/out.txt")?)?);
    assert!(provider.is_ignored(&lp("pkg/cache.pyc")?)?);
    assert!(!provider.is_ignored(&lp("src/main.rs")?)?);
    Ok(())
}

#[test]
fn route_metrics_count_gated_and_direct_paths() -> TestResult {
    let fixture = Fixture::new_with_gitignore("route_metrics", "target/\n*.pyc\n")?;
    let metrics = route_metrics(
        &fixture.root,
        &[
            LayerChange::Write {
                path: lp("src/main.rs")?,
                content: b"tracked".to_vec(),
            },
            LayerChange::Write {
                path: lp("target/out.txt")?,
                content: b"direct".to_vec(),
            },
            LayerChange::Write {
                path: lp("pkg/cache.pyc")?,
                content: b"direct".to_vec(),
            },
            LayerChange::Write {
                path: lp(".git/config")?,
                content: b"drop".to_vec(),
            },
        ],
    )?;

    assert_eq!(metrics.gated_path_count, 1);
    assert_eq!(metrics.direct_path_count, 2);
    Ok(())
}

// N2 (HIGH): a no-slash dir-only pattern is anchored at *any* depth, so a
// file under `frontend/node_modules/` routes DIRECT — the most common
// misroute the old root-anchored prefix check produced.
#[test]
fn dir_only_pattern_matches_at_any_depth() -> TestResult {
    let fixture = Fixture::new_with_gitignore("n2_dir_only", "node_modules/\n")?;
    let provider = route_provider(&fixture);
    assert!(provider.is_ignored(&lp("frontend/node_modules/index.js")?)?);
    assert!(provider.is_ignored(&lp("node_modules/index.js")?)?);
    assert!(!provider.is_ignored(&lp("frontend/src/index.js")?)?);
    Ok(())
}

// N3 (HIGH, data-loss): `*` must not cross `/`. `logs/*.log` does NOT match
// `logs/sub/x.log`, so it routes GATED (base-hash validated) — not
// DIRECT-then-silently-clobber as the old `wildcard_match` allowed.
#[test]
fn star_does_not_cross_slash() -> TestResult {
    let fixture = Fixture::new_with_gitignore("n3_star_slash", "logs/*.log\n")?;
    let provider = route_provider(&fixture);
    assert!(provider.is_ignored(&lp("logs/app.log")?)?);
    assert!(!provider.is_ignored(&lp("logs/sub/x.log")?)?);
    Ok(())
}

// Nested `.gitignore` is scoped to its own subtree.
#[test]
fn nested_gitignore_is_scoped_to_its_subtree() -> TestResult {
    let fixture = Fixture::new_with_gitignores("nested", &[("frontend", "dist/\n")])?;
    let provider = route_provider(&fixture);
    assert!(provider.is_ignored(&lp("frontend/dist/bundle.js")?)?);
    assert!(!provider.is_ignored(&lp("dist/bundle.js")?)?);
    Ok(())
}

// `**` matches across path segments.
#[test]
fn double_star_matches_across_segments() -> TestResult {
    let fixture = Fixture::new_with_gitignore("double_star", "**/build/\n")?;
    let provider = route_provider(&fixture);
    assert!(provider.is_ignored(&lp("a/b/build/out.o")?)?);
    assert!(provider.is_ignored(&lp("build/out.o")?)?);
    assert!(!provider.is_ignored(&lp("a/b/builder.rs")?)?);
    Ok(())
}

// `!` re-includes within a non-sealed directory.
#[test]
fn bang_re_includes_in_unsealed_dir() -> TestResult {
    let fixture = Fixture::new_with_gitignore("bang", "*.log\n!keep.log\n")?;
    let provider = route_provider(&fixture);
    assert!(provider.is_ignored(&lp("other.log")?)?);
    assert!(!provider.is_ignored(&lp("keep.log")?)?);
    Ok(())
}

// Directory seal: an excluded ancestor dir seals its subtree — a deeper `!`
// cannot rescue contents under it (Git semantics).
#[test]
fn excluded_dir_seals_against_deeper_reinclude() -> TestResult {
    let fixture =
        Fixture::new_with_gitignores("seal", &[("", "build/\n"), ("build", "!keep.txt\n")])?;
    let provider = route_provider(&fixture);
    assert!(provider.is_ignored(&lp("build/keep.txt")?)?);
    Ok(())
}

// Telemetry shares the one routine, so counts equal the route decision for
// the same inputs (including the N2/N3/nested/seal cases above).
#[test]
fn route_metrics_match_route_decision() -> TestResult {
    let fixture = Fixture::new_with_gitignores(
        "metrics_parity",
        &[
            ("", "node_modules/\nlogs/*.log\nbuild/\n"),
            ("build", "!keep.txt\n"),
        ],
    )?;
    let provider = route_provider(&fixture);
    let paths = [
        "frontend/node_modules/index.js", // DIRECT (N2 dir-only any depth)
        "logs/sub/x.log",                 // GATED  (N3 star not crossing /)
        "logs/app.log",                   // DIRECT
        "build/keep.txt",                 // DIRECT (seal beats deeper !)
        "src/main.rs",                    // GATED
        ".git/config",                    // skipped by metrics
    ];
    let mut expected_direct = 0;
    let mut expected_gated = 0;
    for path in paths {
        if path == ".git/config" {
            continue;
        }
        if provider.is_ignored(&lp(path)?)? {
            expected_direct += 1;
        } else {
            expected_gated += 1;
        }
    }
    let changes: Vec<LayerChange> = paths
        .iter()
        .map(|path| {
            Ok(LayerChange::Write {
                path: lp(path)?,
                content: b"x".to_vec(),
            })
        })
        .collect::<TestResult<_>>()?;
    let metrics = route_metrics(&fixture.root, &changes)?;
    assert_eq!(metrics.direct_path_count, expected_direct);
    assert_eq!(metrics.gated_path_count, expected_gated);
    assert_eq!(expected_direct, 3);
    assert_eq!(expected_gated, 2);
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
    let provider = route_provider(&fixture);
    // Root rule from the upper layer, matched at depth via the seal.
    assert!(provider.is_ignored(&lp("frontend/node_modules/index.js")?)?);
    // Nested rule, also published into the upper layer.
    assert!(provider.is_ignored(&lp("frontend/dist/bundle.js")?)?);
    assert!(!provider.is_ignored(&lp("src/main.rs")?)?);
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
    let provider = route_provider(&fixture);
    // `/x` anchored at `a/` matches `a/x` (DIRECT) but NOT `a/a/x` — routing
    // the tracked `a/a/x` DIRECT would bypass the gate and silently clobber.
    assert!(provider.is_ignored(&lp("a/x")?)?);
    assert!(!provider.is_ignored(&lp("a/a/x")?)?);
    // Seal variant: `/b` seals `a/b`'s subtree, but `a/a/b` is not the
    // anchored `a/b`, so its whole subtree must stay GATED.
    assert!(provider.is_ignored(&lp("a/b/file.txt")?)?);
    assert!(!provider.is_ignored(&lp("a/a/b/file.txt")?)?);
    // Opposite (false-GATED) direction: `/build/x` anchored at `build/` DOES
    // match `build/build/x`; the old double-strip dropped it to `x` and missed.
    assert!(provider.is_ignored(&lp("build/build/x")?)?);
    assert!(!provider.is_ignored(&lp("build/x")?)?);
    Ok(())
}
