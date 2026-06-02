# Phase 3T Mixed Non-Plugin CP-4/AV-4 Report

- run_id: `local-952ace06c045`
- gate_pass: `True`
- cp4_gate: `True`
- av4_gate: `True`
- audit_events: `12087`
- audit_drop_free: `True`

## Operation Cells

- read_heavy c=1: 3/3 ok, p95=1.9699169788509607 ms, conflicts=0
- read_heavy c=3: 9/9 ok, p95=5.322291981428862 ms, conflicts=0
- read_heavy c=5: 15/15 ok, p95=5.740624968893826 ms, conflicts=0
- read_heavy c=10: 30/30 ok, p95=14.862124982755631 ms, conflicts=0
- write_heavy c=1: 3/3 ok, p95=6.624292000196874 ms, conflicts=0
- write_heavy c=3: 9/9 ok, p95=9.141875023487955 ms, conflicts=0
- write_heavy c=5: 15/15 ok, p95=13.074708986096084 ms, conflicts=0
- write_heavy c=10: 30/30 ok, p95=25.974666990805417 ms, conflicts=0
- edit_heavy c=1: 3/3 ok, p95=7.575375027954578 ms, conflicts=0
- edit_heavy c=3: 9/9 ok, p95=5.648125021252781 ms, conflicts=3
- edit_heavy c=5: 15/15 ok, p95=11.02974999230355 ms, conflicts=9
- edit_heavy c=10: 30/30 ok, p95=17.96466700034216 ms, conflicts=15
- conflict_heavy c=1: 3/3 ok, p95=2.0847080159001052 ms, conflicts=2
- conflict_heavy c=3: 9/9 ok, p95=2.8723340365104377 ms, conflicts=9
- conflict_heavy c=5: 15/15 ok, p95=7.045125006698072 ms, conflicts=15
- conflict_heavy c=10: 30/30 ok, p95=7.415250001940876 ms, conflicts=30
- exec_tty_false c=1: 3/3 ok, p95=57.02316598035395 ms, conflicts=0
- exec_tty_false c=3: 9/9 ok, p95=60.7777499826625 ms, conflicts=0
- exec_tty_false c=5: 15/15 ok, p95=111.86012497637421 ms, conflicts=0
- exec_tty_false c=10: 30/30 ok, p95=191.95804197806865 ms, conflicts=0
- exec_tty_true c=1: 3/3 ok, p95=56.53108301339671 ms, conflicts=0
- exec_tty_true c=3: 9/9 ok, p95=78.89950001845136 ms, conflicts=0
- exec_tty_true c=5: 15/15 ok, p95=133.4940829547122 ms, conflicts=0
- exec_tty_true c=10: 30/30 ok, p95=200.73554199188948 ms, conflicts=0
- glob c=1: 3/3 ok, p95=18.40595802059397 ms, conflicts=0
- glob c=3: 9/9 ok, p95=25.615999998990446 ms, conflicts=0
- glob c=5: 15/15 ok, p95=47.31566703412682 ms, conflicts=0
- glob c=10: 30/30 ok, p95=69.4505000137724 ms, conflicts=0
- grep c=1: 3/3 ok, p95=35.52662499714643 ms, conflicts=0
- grep c=3: 9/9 ok, p95=50.167083041742444 ms, conflicts=0
- grep c=5: 15/15 ok, p95=78.90666701132432 ms, conflicts=0
- grep c=10: 30/30 ok, p95=147.63337495969608 ms, conflicts=0
- pty_input c=1: 3/3 ok, p95=216.6048749932088 ms, conflicts=0
- pty_input c=3: 9/9 ok, p95=253.79045895533636 ms, conflicts=0
- pty_input c=5: 15/15 ok, p95=379.0218750364147 ms, conflicts=0
- pty_input c=10: 30/30 ok, p95=566.0831250133924 ms, conflicts=0
- pty_long_session c=1: 3/3 ok, p95=111.37562501244247 ms, conflicts=0
- pty_long_session c=3: 9/9 ok, p95=111.56012496212497 ms, conflicts=0
- pty_long_session c=5: 15/15 ok, p95=173.97166701266542 ms, conflicts=0
- pty_long_session c=10: 30/30 ok, p95=317.98083399189636 ms, conflicts=0
- mixed_shared c=1: 3/3 ok, p95=3.9175000274553895 ms, conflicts=1
- mixed_shared c=3: 9/9 ok, p95=69.71920805517584 ms, conflicts=1
- mixed_shared c=5: 15/15 ok, p95=75.7280410034582 ms, conflicts=2
- mixed_shared c=10: 30/30 ok, p95=109.0005420264788 ms, conflicts=4

## Audit Event Types

{
  "background_tool.cancelled": 57,
  "background_tool.completed": 64,
  "background_tool.input": 57,
  "background_tool.progress": 2078,
  "background_tool.started": 114,
  "layer_stack.lease_acquired": 192,
  "layer_stack.lease_released": 313,
  "layer_stack.maintenance": 2,
  "layer_stack.squash_completed": 3,
  "occ.conflict": 91,
  "occ.publish": 340,
  "overlay_workspace.cleanup": 313,
  "tool_call.completed": 2821,
  "tool_call.finished": 2821,
  "tool_call.started": 2821
}
