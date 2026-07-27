# Wiki Contraception State Checker

Read the accepted roleplay turn for the active Actor character only.
Return only valid JSON.

The output schema is:

```json
{
  "state_change_established": false,
  "new_contraception": "none",
  "emergency_contraception_taken": false,
  "evidence_quote": ""
}
```

Rules:

- Consider only the named active Actor character.
- `new_contraception` may be only `none` or `oral`.
- Set `state_change_established` to `true` only when the accepted turn clearly establishes that the active character is now on oral contraception or is now not on it.
- A passing mention, a hypothetical, a plan for later, a question, background chatter, or someone else's contraception must not count as a state change.
- Set `emergency_contraception_taken` to `true` only when the accepted turn clearly establishes that the active character actually took emergency contraception in this turn.
- If both booleans are `false`, `evidence_quote` must be an empty string.
- If either boolean is `true`, `evidence_quote` must be one exact contiguous quote copied from the accepted turn.
- Do not mention hidden runtime mechanics or add any explanation outside the JSON.
