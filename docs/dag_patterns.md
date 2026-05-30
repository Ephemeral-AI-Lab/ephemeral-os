# DAG Shape Gallery

Common directed-acyclic-graph topologies in ASCII, from trivial to complex
multi-phase. Arrows point in the direction of dependency / data flow.

---

## 1. Linear chain
Sequential steps — the simplest DAG.

```
A ──▶ B ──▶ C ──▶ D
```

## 2. Fan-out (scatter / broadcast)
One node triggers many.

```
         ┌──▶ B
A ───────┼──▶ C
         └──▶ D
```

## 3. Fan-in (gather / join)
Many feed one.

```
A ──┐
B ──┼──▶ D
C ──┘
```

## 4. Diamond (fork–join)
Split work, then reconverge. `E` cannot run until both `B` and `C` finish.

```
      ┌──▶ B ──┐
A ────┤        ├──▶ E
      └──▶ C ──┘
```

## 5. Tree (hierarchical fan-out)
Branching with no reconvergence.

```
A
├──▶ B
│    ├──▶ D
│    └──▶ E
└──▶ C
     ├──▶ F
     └──▶ G
```

## 6. Fully-connected layers
Every node in a layer feeds every node in the next (feed-forward style).

```
 L0          L1          L2

A ──┬──▶ C ──┬──▶ E
    │        │
B ──┴──▶ D ──┴──▶ F
```

## 7. Realistic pipeline
Fan-out into parallel work, fan-in to merge, then a trailing fan-out
(e.g. an ETL / Airflow DAG).

```
                ┌──▶ C ──┐
A ──▶ B ────────┼──▶ D ──┼──▶ F ──▶ G ──┬──▶ H
                └──▶ E ──┘               └──▶ I
```

## 8. Complex multi-phase mesh
Multiple sources, staged merges, parallel models, ensemble, multiple sinks
(e.g. an ML training pipeline).

```
A ──▶ C ──┐
          ├──▶ E ──┬──▶ F ──┐
B ──▶ D ──┘        │        ├──▶ H ──┬──▶ I
                   └──▶ G ──┘        └──▶ J
```

---

## Notes

- The **diamond** (#4) is the canonical pattern that forces synchronization.
- The **mesh** (#8) is what most real-world DAGs degrade into — phases are
  roughly layered, but joins and forks appear at multiple stages.
- ASCII handles **skip / cross-phase edges** poorly — a dependency that jumps
  over a layer (e.g. `E ─────▶ J`, bypassing the models) becomes a long
  horizontal run that tangles with everything in between. These are usually
  drawn as labeled edges rather than literal lines.
