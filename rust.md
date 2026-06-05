# Rust Implementation And Proposal Standard

Rust design in this repo should use OOP ideas through ownership,
encapsulation, composition, and trait-based behavior. Do not translate
class-inheritance designs directly into Rust. Start with the smallest concrete
model that expresses the domain, then add polymorphism only where the boundary
actually varies.

## Proposal Requirements

Every Rust implementation proposal that introduces an API, provider, backend,
plugin, workflow hook, or sandbox operation must state:

- The owning crate and module, using the `agent-core/` or `sandbox/` ownership
  map before falling back to legacy Python.
- The public interface surface: concrete structs, enums, DTOs, traits, error
  types, and re-exports.
- The dispatch choice: concrete type, enum, generic or `impl Trait`, or
  `dyn Trait`, with the reason for that choice.
- The test seam: concrete fixture, fake, mock trait, integration test, or golden
  DTO/protocol coverage.
- The compatibility contract: wire shape, persisted state, serde defaults,
  feature flags, or versioning impact when applicable.

## Polymorphism Choice

| Situation | Prefer | Reason |
| --- | --- | --- |
| One implementation or no real substitution | Concrete type | Smallest API and easiest lifecycle |
| Closed set of variants known in this crate | `enum` | Exhaustive matching without indirection |
| Type known at compile time | Generics or `impl Trait` | Static dispatch and zero runtime cost |
| Runtime-selected provider/plugin/backend | `dyn Trait` behind `Box` or `Arc` | Open set and heterogeneous values |
| Public trait callers should use but not implement | Sealed trait | Preserves future API evolution |
| Shared resource used across async tasks | `Arc<dyn Trait + Send + Sync>` when dynamic dispatch is needed | Clear ownership and thread-safety contract |

Prefer static dispatch for hot paths and simple generic algorithms. Prefer
dynamic dispatch only when runtime selection, plugin registration, test doubles,
or cross-crate extensibility is the load-bearing requirement. If a trait is
used as `dyn Trait`, keep it object-safe: no generic methods, no `Self` return
types, and no required `Self: Sized` bound on object methods.

## API And Interface Rules

- Use traits as behavior contracts at real boundaries, not as Java-style
  interfaces for every struct.
- Keep traits small and semantic. A trait should name one capability that the
  caller needs, not mirror every method on a concrete type.
- Keep bounds where they are needed. Put bounds on methods when only one method
  needs them; use `where` clauses when a signature has multiple constraints.
- Prefer associated types when the trait owns a family of related output,
  handle, or error types.
- Use newtypes and typed IDs for domain values instead of `String`, `Uuid`, or
  integer aliases flowing through public APIs.
- Use explicit enums for state transitions and closed protocol choices instead
  of bool flag bags or stringly mode values.
- Return concrete types from constructors unless hiding the concrete type is
  part of the API contract; use `impl Trait` for opaque static returns and
  `Box<dyn Trait>` only for runtime-selected returns.
- Keep `lib.rs`, `main.rs`, and `mod.rs` as thin routing surfaces. Re-export
  public APIs intentionally with `pub use`; keep implementation modules private
  or `pub(crate)`.
- Document public invariants for traits, DTOs, and protocol types. If external
  implementations would lock the crate into a compatibility promise, seal the
  trait or keep it crate-private.
- Use typed errors for library/domain contracts and `anyhow` only near binary,
  orchestration, or test edges where concrete error matching is not useful.

## Anti-Patterns To Reject

- Deep trait hierarchies that exist only to look object-oriented.
- `IThing`, `AbstractThing`, or `BaseThing` naming imported from class-based
  languages.
- Generic parameters that appear only once or do not encode a real type
  relationship.
- `Box<dyn Trait>` for a single known implementation.
- Public traits created only to make unit tests easier when a crate-local fake,
  private trait, or integration test would preserve the API.
- Broad `Send + Sync + 'static` bounds copied everywhere instead of placed at
  the async or thread-spawn boundary that requires them.
- Public stringly JSON maps where a typed DTO, enum, or newtype can express the
  contract.

## Verification

For Rust-owned changes, run checks from the owning workspace:

- `cd agent-core && cargo check -p <crate> --all-targets`
- `cd sandbox && cargo check -p <crate> --all-targets`
- Add targeted `cargo test -p <crate> <test>` or crate-level tests when behavior,
  protocol shape, or public API contracts change.
- Use `cargo clippy -p <crate> --all-targets -- -D warnings` when changing
  shared abstractions, trait surfaces, async lifecycles, or public DTOs.

Broaden to workspace-level checks only when the change crosses crate or
workspace dependency edges.
