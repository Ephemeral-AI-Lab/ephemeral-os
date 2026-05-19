"""TaskCenter context-engine internals. Use ``task_center`` externally.

Recipes build :class:`ContextPacket` instances from store data; the
:class:`XmlPromptRenderer` then wraps each block's body verbatim in its XML
tag — no markdown headings, no whitespace normalization, no silent escaping.

A block whose text contains any structural tag-closer the renderer would emit
(for example ``</goal>``, ``</attempt>``) is rejected at render time with a
remediation hint; rewrite the offending body or use a different
:class:`ContextBlockKind`. Recipes that hand-assemble nested XML inside a
single block (the ``attempts`` recipe) opt out via
``metadata['pre_rendered_xml']='true'`` and own the sanitization of the
user-supplied fragments they embed.
"""
