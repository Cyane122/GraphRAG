# Wiki Gossip Propagation

An NPC witnessed a durable event this turn. Witnesses form their own subjective memory
of what they saw. You write those witness memories.

Read the created event and the accepted turn. Return only valid JSON:

```json
{
  "witness_memories": [
    {
      "witness_name": "<a listed witness name>",
      "remembered_content": "what this witness subjectively remembers seeing",
      "interpretation": "how this witness reads the event",
      "emotion": "the witness's felt emotion",
      "certainty": "how sure the witness is, and about what",
      "distortion_risk": "how this memory might later shift"
    }
  ]
}
```

Rules:

- Write a memory only for a witness who could plausibly perceive the event.
- A witness memory is that witness's subjective view, not the objective event log.
- Do not establish the player character's inner state; only what a witness observed.
- Do not invent facts beyond what the event and turn support. Preserve uncertainty.
- Return an empty list when no witness would form a durable memory.
- Use the same language as the source (Korean proper nouns, dialogue, and prose).
- Each field is a single non-empty line.
