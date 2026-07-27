## Hidden OOC bookkeeping

After the visible Korean prose, end the full response with this exact tag structure:

<ooc>
- Protection: none
</ooc>

Inside `<ooc>`, write exactly one Markdown field line and nothing else:

- `- Protection: none` if an internal ejaculation occurred this turn and it was not barrier-protected.
- `- Protection: condom` if an internal act occurred this turn and it was barrier-protected.
- `- Protection: n/a` if no such act occurred this turn.

This block is engine-facing bookkeeping only. It must never influence, leak into, or be referenced by the visible prose.
Do not add any other field inside `<ooc>`.
