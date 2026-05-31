"""Shared planner submission prompt guidance."""

from __future__ import annotations

PLAN_DAG_GUIDANCE = """\
A plan is a DAG of generator + reducer tasks (edges are `needs`). Generators
do the work; reducers digest their `needs` and gate the result.

Plan shape:
- Root generators may have no `needs`.
- Non-root generator `needs` may reference one or more generator ids.
- Reducer `needs` must reference one or more generator ids.
- No task may need a reducer; reducers are terminal sinks.
- Every generator must be needed by another generator or by a reducer.
- `needs` are direct context inputs, not scheduling shortcuts. A task receives
  only the outcomes of ids listed in its own `needs`; transitive ancestors are
  not included. If `gen_b` needs `gen_a` and `gen_c` needs `gen_b`, then
  `gen_c` receives `gen_b` only. If `gen_c` also needs `gen_a`'s context, set
  `gen_c.needs = ["gen_a", "gen_b"]`.

Patterns:

Overview graph:
   gen_a ----\\
              +--> gen_c ----\\
   gen_b ----/                +--> gen_e ----\\
             \\                /               +--> red_f
              +--> gen_d ----+--------------/
   gen_c -----------------------------------> red_g

1. One full serial lane:
   gen_a -> gen_b -> gen_c -> red_d

2. Multiple serial lanes:
   gen_a -> gen_b -> red_e
   gen_c -> gen_d -> red_f

3. Simple fan-in reducer:
   gen_a ----\\
   gen_b -----+--> red_d
   gen_c ----/

4. Diamond fork-join:
             +--> gen_b ----\\
   gen_a ----+                +--> gen_d ----> red_e
             +--> gen_c ----/

5. Tree:
   gen_a
   +--> gen_b ----\\
   |               +--> red_f
   +--> gen_c ----/
        +--> gen_d ----> red_g
        +--> gen_e ----> red_h

6. Fully-connected layers:
   gen_a ----+--> gen_c ----+--> red_e
             |              |
   gen_b ----+--> gen_d ----+--> red_f

7. Multi-phase mesh:
   gen_a ----> gen_c ----\\
                         +--> gen_e ----\\
   gen_b ----> gen_d ----/                +--> gen_h ----\\
                         +--> gen_f ----/                +--> red_i
   gen_c -----------------------------------------------> red_j
"""

__all__ = ["PLAN_DAG_GUIDANCE"]
