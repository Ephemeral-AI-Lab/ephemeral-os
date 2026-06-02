# Phase 3T Mixed Non-Plugin CP-4/AV-4 Report

- run_id: `local-037cdd9a4a6f`
- gate_pass: `True`
- cp4_gate: `True`
- av4_gate: `True`
- audit_events: `11724`
- audit_drop_free: `True`

## Operation Cells

- read_heavy c=1: 3/3 ok, p95=1.6353329992853105 ms, conflicts=0
- read_heavy c=3: 9/9 ok, p95=8.255833003204316 ms, conflicts=0
- read_heavy c=5: 15/15 ok, p95=4.427333013154566 ms, conflicts=0
- read_heavy c=10: 30/30 ok, p95=13.374999980442226 ms, conflicts=0
- write_heavy c=1: 3/3 ok, p95=6.94591598585248 ms, conflicts=0
- write_heavy c=3: 9/9 ok, p95=9.792624972760677 ms, conflicts=0
- write_heavy c=5: 15/15 ok, p95=13.782583002466708 ms, conflicts=0
- write_heavy c=10: 30/30 ok, p95=27.64708298491314 ms, conflicts=0
- edit_heavy c=1: 3/3 ok, p95=7.744374976027757 ms, conflicts=0
- edit_heavy c=3: 9/9 ok, p95=5.998624954372644 ms, conflicts=3
- edit_heavy c=5: 15/15 ok, p95=11.348708998411894 ms, conflicts=9
- edit_heavy c=10: 30/30 ok, p95=17.348250024951994 ms, conflicts=15
- conflict_heavy c=1: 3/3 ok, p95=2.2273330250754952 ms, conflicts=2
- conflict_heavy c=3: 9/9 ok, p95=2.7163749909959733 ms, conflicts=9
- conflict_heavy c=5: 15/15 ok, p95=5.089582991786301 ms, conflicts=15
- conflict_heavy c=10: 30/30 ok, p95=7.678834022954106 ms, conflicts=30
- exec_tty_false c=1: 3/3 ok, p95=59.04274998465553 ms, conflicts=0
- exec_tty_false c=3: 9/9 ok, p95=58.55212494498119 ms, conflicts=0
- exec_tty_false c=5: 15/15 ok, p95=103.72154100332409 ms, conflicts=0
- exec_tty_false c=10: 30/30 ok, p95=200.2222909941338 ms, conflicts=0
- exec_tty_true c=1: 3/3 ok, p95=54.42637501982972 ms, conflicts=0
- exec_tty_true c=3: 9/9 ok, p95=60.887042025569826 ms, conflicts=0
- exec_tty_true c=5: 15/15 ok, p95=121.06374994618818 ms, conflicts=0
- exec_tty_true c=10: 30/30 ok, p95=190.61308295931667 ms, conflicts=0
- glob c=1: 3/3 ok, p95=18.120332970283926 ms, conflicts=0
- glob c=3: 9/9 ok, p95=26.012042013462633 ms, conflicts=0
- glob c=5: 15/15 ok, p95=42.73520899005234 ms, conflicts=0
- glob c=10: 30/30 ok, p95=68.52654198883101 ms, conflicts=0
- grep c=1: 3/3 ok, p95=36.97108302731067 ms, conflicts=0
- grep c=3: 9/9 ok, p95=43.710250000003725 ms, conflicts=0
- grep c=5: 15/15 ok, p95=92.72733400575817 ms, conflicts=0
- grep c=10: 30/30 ok, p95=146.86012495076284 ms, conflicts=0
- pty_input c=1: 3/3 ok, p95=215.15570901101455 ms, conflicts=0
- pty_input c=3: 9/9 ok, p95=243.68408299051225 ms, conflicts=0
- pty_input c=5: 15/15 ok, p95=358.4288749843836 ms, conflicts=0
- pty_input c=10: 30/30 ok, p95=520.2624999801628 ms, conflicts=0
- pty_long_session c=1: 3/3 ok, p95=112.371583993081 ms, conflicts=0
- pty_long_session c=3: 9/9 ok, p95=118.20741702103987 ms, conflicts=0
- pty_long_session c=5: 15/15 ok, p95=172.03125002561137 ms, conflicts=0
- pty_long_session c=10: 30/30 ok, p95=293.8540419563651 ms, conflicts=0
- mixed_shared c=1: 3/3 ok, p95=3.1614580075256526 ms, conflicts=1
- mixed_shared c=3: 9/9 ok, p95=56.38658400857821 ms, conflicts=1
- mixed_shared c=5: 15/15 ok, p95=66.24399998690933 ms, conflicts=2
- mixed_shared c=10: 30/30 ok, p95=87.6289579900913 ms, conflicts=4

## Audit Event Types

{
  "background_tool.cancelled": 57,
  "background_tool.completed": 64,
  "background_tool.input": 57,
  "background_tool.progress": 2036,
  "background_tool.started": 114,
  "layer_stack.lease_released": 313,
  "layer_stack.maintenance": 2,
  "occ.conflict": 91,
  "occ.publish": 340,
  "overlay_workspace.cleanup": 313,
  "tool_call.completed": 2779,
  "tool_call.finished": 2779,
  "tool_call.started": 2779
}
