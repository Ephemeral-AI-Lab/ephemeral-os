# Phase 3T Mixed Non-Plugin CP-4/AV-4 Report

- run_id: `local-12cb8bd20f51`
- gate_pass: `True`
- cp4_gate: `True`
- av4_gate: `True`
- audit_events: `2422`
- audit_drop_free: `True`

## Operation Cells

- read_heavy c=1: 2/2 ok, p95=1.7102090059779584 ms, conflicts=0
- read_heavy c=3: 6/6 ok, p95=6.922791013494134 ms, conflicts=0
- read_heavy c=5: 10/10 ok, p95=9.634457994252443 ms, conflicts=0
- read_heavy c=10: 20/20 ok, p95=8.77229101024568 ms, conflicts=0
- write_heavy c=1: 2/2 ok, p95=6.100874976255 ms, conflicts=0
- write_heavy c=3: 6/6 ok, p95=11.763916991185397 ms, conflicts=0
- write_heavy c=5: 10/10 ok, p95=48.24350000126287 ms, conflicts=0
- write_heavy c=10: 20/20 ok, p95=24.326292041223496 ms, conflicts=0
- edit_heavy c=1: 2/2 ok, p95=6.276166008319706 ms, conflicts=0
- edit_heavy c=3: 6/6 ok, p95=11.011332971975207 ms, conflicts=2
- edit_heavy c=5: 10/10 ok, p95=7.978416979312897 ms, conflicts=6
- edit_heavy c=10: 20/20 ok, p95=28.196291998028755 ms, conflicts=10
- conflict_heavy c=1: 2/2 ok, p95=1.4144169981591403 ms, conflicts=1
- conflict_heavy c=3: 6/6 ok, p95=4.241166985593736 ms, conflicts=6
- conflict_heavy c=5: 10/10 ok, p95=3.211166011169553 ms, conflicts=10
- conflict_heavy c=10: 20/20 ok, p95=12.315541971474886 ms, conflicts=20
- exec_tty_false c=1: 2/2 ok, p95=51.5092090354301 ms, conflicts=0
- exec_tty_false c=3: 6/6 ok, p95=53.52979199960828 ms, conflicts=0
- exec_tty_false c=5: 10/10 ok, p95=90.50995897268876 ms, conflicts=0
- exec_tty_false c=10: 20/20 ok, p95=168.51616697385907 ms, conflicts=0
- exec_tty_true c=1: 2/2 ok, p95=50.684207992162555 ms, conflicts=0
- exec_tty_true c=3: 6/6 ok, p95=63.60320799285546 ms, conflicts=0
- exec_tty_true c=5: 10/10 ok, p95=106.68741702102125 ms, conflicts=0
- exec_tty_true c=10: 20/20 ok, p95=194.23404097324237 ms, conflicts=0
- glob c=1: 2/2 ok, p95=18.029041995760053 ms, conflicts=0
- glob c=3: 6/6 ok, p95=22.793958021793514 ms, conflicts=0
- glob c=5: 10/10 ok, p95=36.223167029675096 ms, conflicts=0
- glob c=10: 20/20 ok, p95=55.0513329799287 ms, conflicts=0
- grep c=1: 2/2 ok, p95=31.31475002737716 ms, conflicts=0
- grep c=3: 6/6 ok, p95=36.283207999076694 ms, conflicts=0
- grep c=5: 10/10 ok, p95=65.63487503444776 ms, conflicts=0
- grep c=10: 20/20 ok, p95=98.73333299765363 ms, conflicts=0
- pty_input c=1: 2/2 ok, p95=164.1539999982342 ms, conflicts=0
- pty_input c=3: 6/6 ok, p95=164.8637080215849 ms, conflicts=0
- pty_input c=5: 10/10 ok, p95=263.65566602908075 ms, conflicts=0
- pty_input c=10: 20/20 ok, p95=434.03691600542516 ms, conflicts=0
- pty_long_session c=1: 2/2 ok, p95=114.07304200110957 ms, conflicts=0
- pty_long_session c=3: 6/6 ok, p95=114.60774997249246 ms, conflicts=0
- pty_long_session c=5: 10/10 ok, p95=168.1384580442682 ms, conflicts=0
- pty_long_session c=10: 20/20 ok, p95=333.00450001843274 ms, conflicts=0
- mixed_shared c=1: 2/2 ok, p95=3.6392079782672226 ms, conflicts=0
- mixed_shared c=3: 6/6 ok, p95=54.827500018291175 ms, conflicts=1
- mixed_shared c=5: 10/10 ok, p95=47.7972500375472 ms, conflicts=2
- mixed_shared c=10: 20/20 ok, p95=102.40191698540002 ms, conflicts=3

## Audit Event Types

{
  "background_tool.cancelled": 38,
  "background_tool.completed": 43,
  "background_tool.input": 38,
  "background_tool.progress": 8,
  "background_tool.started": 76,
  "layer_stack.lease_released": 208,
  "layer_stack.maintenance": 2,
  "occ.conflict": 61,
  "occ.publish": 228,
  "overlay_workspace.cleanup": 208,
  "tool_call.completed": 504,
  "tool_call.finished": 504,
  "tool_call.started": 504
}
