"""Registry-driven ``<Task Guidance>`` body builder.

A "task guidance" is the per-agent ``<Task Guidance>`` body assembled at launch
time from the rendered context packet and agent definition. The single builder
reads ``packet.blocks`` (kind + metadata) and the agent's name only — it does
not touch stores and takes no kwargs beyond those. Callers import from
``task_center.task_guidance.builders`` directly; the package root re-exports
nothing.
"""
