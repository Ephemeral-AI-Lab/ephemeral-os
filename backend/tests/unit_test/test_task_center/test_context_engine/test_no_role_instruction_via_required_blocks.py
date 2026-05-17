"""Schema-boundary guard: no variant injects role_instruction via required_context_blocks.

Under option β the renderer partitions on ``block.kind == 'role_instruction'``;
recipe code is the only place that emits role_instruction blocks (via
``recipes/role_instruction.py``). If a variant tries to inject a
role_instruction block via ``selection.required_context_blocks``, it lands in
``packet.blocks`` AFTER the recipe's own role_instruction and confuses the
partition (two unrelated instructions concatenated into one user msg 2).

This test fails fast if anyone tries.
"""

from __future__ import annotations

from agents import list_definitions


def test_no_variant_emits_role_instruction_via_required_context_blocks():
    for agent_def in list_definitions():
        for variant in agent_def.variants or ():
            for block in variant.required_context_blocks or ():
                assert block.kind != "role_instruction", (
                    f"Agent {agent_def.name!r}, variant when={variant.when!r}, "
                    f"use={variant.use!r} declares a 'role_instruction' block in "
                    "required_context_blocks. This would land AFTER the recipe's "
                    "own role_instruction and concatenate into user msg 2. Move "
                    "the instruction into recipes/role_instruction.py instead."
                )
