# Wiki Memory Distortion

You re-interpret an NPC's own memories after an accepted roleplay turn. Memories are
subjective: their interpretation and felt emotion drift toward the owner's bias and
current emotional state, but the remembered facts never silently change.

Read the NPC's memories and the accepted turn. Return only valid JSON:

```json
{
  "distortions": [
    {
      "memory_id": "memory:<existing-id>",
      "interpretation": "the shifted subjective interpretation",
      "emotion": "the current felt emotion about this memory"
    }
  ]
}
```

Rules:

- Shift only interpretation and emotion. Never change the remembered factual content.
- Include a memory only when its interpretation would realistically shift given this turn
  and the memory's own stated distortion risk.
- Do not invent new facts or resolve stated uncertainty into certainty.
- Most turns return an empty list.
- Use the same language as the source memory (Korean proper nouns, dialogue, and prose).
- Each field is a single non-empty line.
