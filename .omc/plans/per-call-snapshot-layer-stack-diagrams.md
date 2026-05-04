# Per-Call Snapshot Layer Stack Demonstration Diagrams

These diagrams are a draft companion to
`per-call-snapshot-layer-stack.md`. They keep the plan's contract intact:
commands read from a frozen manifest, captured upperdir writes are evaluated by
the changeset/OCC path, accepted writes append a fresh layer, and squash rewrites
only old immutable suffixes.

## 1. Stacked Overlay Concept vs Traditional COW

```mermaid
flowchart LR
  subgraph T["Traditional live-root copy-on-write"]
    T0["live_root mutable workspace"]
    T1["command lowerdir points at live_root"]
    T2["per-call upperdir captures writes"]
    T3["commit writes accepted bytes back into live_root"]
    TC["concurrent commit mutates live_root during command"]

    T0 --> T1 --> T2 --> T3 --> T0
    TC -. "read drift risk" .-> T1
  end

  subgraph A["Append-only layer stack"]
    A0["L0 baseline immutable"]
    A1["L1 accepted diff immutable"]
    AK["LK accepted diff immutable"]
    AS["call snapshot: LK:L1:L0"]
    AU["per-call upperdir captures writes"]
    AO["changeset + OCC verdict"]
    AN["L(K+1) fresh immutable layer"]
    AM["new manifest: L(K+1):LK:...:L0"]

    A0 --> A1 --> AK --> AS --> AU --> AO --> AN --> AM
    AM -. "future calls only" .-> AS
  end
```

The key difference is where accepted writes land. Traditional COW merges back
into the mutable live root. The append-only design never mutates existing layers;
it publishes a new layer that only future snapshots can see.

## 2. Lowerdir + Upperdir Command Lifecycle

```mermaid
sequenceDiagram
  participant Shell as Shell request
  participant LM as LayerManager
  participant Overlay as Overlay mount namespace
  participant Upper as tmpfs upperdir
  participant OCC as Changeset/OCC gate
  participant Manifest as Active manifest

  Shell->>LM: snapshot()
  LM-->>Shell: frozen manifest K = [LK..L0]
  Shell->>LM: acquire lease for K
  Shell->>Overlay: mount lowerdir=LK:...:L0, upperdir=tmpfs
  Shell->>Overlay: run command against merged view
  Overlay->>Upper: record writes, deletes, whiteouts
  Shell->>Upper: capture diff.ndjson
  Shell->>OCC: evaluate diff against base K and current manifest
  alt at least one accepted write
    OCC->>Manifest: write L(K+1).staging
    OCC->>Manifest: rename to L(K+1), CAS-publish manifest
    Manifest-->>Shell: newer overlay layer exists
  else read_only, no changes, or all rejected
    OCC-->>Shell: no new layer is published
  end
  Shell->>LM: release lease for K
```

`lowerdir` is frozen because the kernel pins the lowerdir list at mount time.
`upperdir` is only the command's private mutation buffer. A newer overlay layer
is created only after accepted changes are materialized into a fresh layer and
the manifest swap succeeds.

## 3. Conditions to Create a New Overlay Layer

```mermaid
flowchart TD
  Start["request finishes"] --> Mode{"mode"}
  Mode -->|read_only| Drop["discard upperdir; release lease; no layer"]
  Mode -->|gated / strict_stale / exclusive| Changes{"captured changes?"}

  Changes -->|none| NoLayer["no layer"]
  Changes -->|some| OverlayOK{"overlay capture valid?"}
  OverlayOK -->|no| Reject["reject request changes; no layer"]
  OverlayOK -->|yes| Stale{"strict_stale cutoff exceeded?"}
  Stale -->|yes| Reject
  Stale -->|no, or not strict_stale| Verdict{"any accepted change?"}

  Verdict -->|none accepted| NoLayer
  Verdict -->|one or more accepted| Coalesce{"coalescing active?"}
  Coalesce -->|yes| Stage["merge accepted bytes into pending staging layer"]
  Stage --> Flush{"flush timer or threshold fires?"}
  Flush -->|yes| Publish["rename staging layer and CAS-publish manifest"]
  Coalesce -->|no| Fresh["create fresh L(N).staging, rename, CAS-publish"]

  Publish --> NewLayer["new immutable layer visible to future snapshots"]
  Fresh --> NewLayer
```

`exclusive` changes the concurrency rule, not the layer rule: it blocks
concurrent commits while the request runs, then still publishes a layer only if
there are accepted changes.

## 4. Overlay Squash Algorithm

```mermaid
flowchart TD
  Tick["squash worker tick"] --> Depth{"manifest depth >= SQUASH_TRIGGER?"}
  Depth -->|no| Sleep["do nothing"]
  Depth -->|yes| Plan["read frozen manifest M"]
  Plan --> Split["keep newest SQUASH_TARGET - 1 layers"]
  Split --> Suffix["select older suffix to squash"]
  Suffix --> Build["build unpublished B(N).staging"]
  Build --> Apply["apply suffix oldest to newest with overlay semantics"]
  Apply --> Ready["rename B(N).staging to B(N)"]
  Ready --> Reload["reload current manifest C"]
  Reload --> Match{"C still ends with same suffix?"}
  Match -->|no| Discard["discard B(N), retry later"]
  Match -->|yes| Swap["CAS-publish kept prefix + B(N)"]
  Swap --> Retire["mark old suffix retired"]
  Retire --> GC{"retired layer refcount == 0?"}
  GC -->|no| Wait["keep layer for leased requests"]
  GC -->|yes| Delete["delete retired layer"]
```

Example shape:

```mermaid
flowchart LR
  Before["before: L099 L098 ... L061 | L060 ... L000"]
  Keep["keep newest 39 live deltas"]
  Squash["squash older suffix into B100"]
  After["after: L099 L098 ... L061 B100"]

  Before --> Keep --> Squash --> After
```

## 5. Long Polling Requests, Leases, and Squash

```mermaid
sequenceDiagram
  participant Req as Long polling request
  participant LM as LayerManager
  participant Active as Active manifest
  participant Squash as Squash worker
  participant GC as GC

  Req->>LM: snapshot M = [LK..L0]
  Req->>LM: acquire lease for every layer in M
  LM-->>Req: mounted view stays frozen at M
  Active->>Active: newer commits append L(K+1)..L(N)
  Note over Squash: selects suffix by position only;<br/>lease state is not consulted
  Squash->>Active: build checkpoint for old suffix
  Squash->>Active: publish active manifest with B checkpoint
  Squash->>GC: retire replaced old layers
  Note over Req: request still reads from its leased lowerdir list M
  GC-->>GC: skip retired layers pinned by request lease

  alt lease budget exceeded
    LM->>Req: terminate request, discard upperdir
    Req->>LM: release lease
  else request completes normally
    Req->>LM: release lease
  end

  GC->>GC: delete retired layers once unpinned
```

Squash is allowed to publish a newer checkpoint while a long request is still
running. The safety rule is that active-manifest replacement and physical
deletion are separate: replacement can happen immediately, deletion waits until
all leases on the old layers are released.
