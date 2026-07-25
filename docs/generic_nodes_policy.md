# Generic Node Policy

This document records the TODO 3.2 review for optional generic nodes. The default schema should stay small: a node belongs in `src/assets/worlds/base.py` only when it improves most worlds, can be retrieved with narrow conditions, and has a `prompt_hint`-first renderer.

## Default Schema

No TODO 3.2 candidate is promoted to the default schema yet.

The default generic layer remains:

- `Location`: active place context.
- `Rule`: active scene, place, or character rule.
- `SpeechProfile`: speaker and audience-specific voice hints.
- `RelationshipProfile`: pair-specific relationship behavior hints.

These are enough to move location, rules, speech, and relationship guidance out of fixed prompts without making every world carry unused tables.

## Candidate Decisions

| Candidate | Decision | Reason |
| --- | --- | --- |
| `Association` | World extension only | Useful for schools, factions, noble houses, companies, and politics-heavy worlds, but not required for intimate or small-cast worlds. |
| `SceneTemplate` | Do not add now | Current genre prompts, few-shot examples, `Rule`, and `StaticEvent` already cover most scene-shape needs. Add only if a world needs reusable runtime scene beats. |
| `KnowledgeScope` | Do not add now | This overlaps with `Secret` and risks leaking private knowledge into Actor prompts. Add only with a reveal-safe renderer. |
| `SecretReveal` | Do not add now | Existing `Secret` already has `public_hint`, `current_reveal_level`, and reveal update logic. Extend `Secret` first before adding a separate node. |
| `ReputationProfile` | Do not add now | Reputation currently propagates through gossip `Memory` and SNS/social context. A profile node is premature until multiple worlds need stable public-image records. |
| `Routine` / `ScheduleBlock` | World extension only | SSES already has a world-specific schedule generator using `GlobalState`. Generalizing schedules should wait until another world needs the same behavior. |

## Extension Rules

A world-specific optional node may be added when all of these are true:

- It has `id`, `name`, `summary`, `prompt_hint`, `prompt_priority`, and `tags`.
- It has a retrieval condition narrower than “all active records”.
- Its renderer emits `prompt_hint` or `summary`, never raw private data.
- Missing rows are skipped silently.
- It does not duplicate an existing system such as `Secret`, `Memory`, `StaticEvent`, `Goal`, or SNS context.

## Retrieval And Renderer Sketches

`Association`

- Retrieval: only associations connected to the present character, active location, or mentioned organization.
- Renderer: `[Associations] - name: prompt_hint`.

`SceneTemplate`

- Retrieval: match `scene_type`, location tag, and optional world tag.
- Renderer: `[Scene Template]` with one concise structure hint, not full few-shot text.

`KnowledgeScope`

- Retrieval: only for the current POV character and only records marked public or actor-safe.
- Renderer: `[Known / Unknown]` using explicit `prompt_hint`; never include hidden facts.

`SecretReveal`

- Retrieval: through existing `Secret` rows by reveal level.
- Renderer: reuse `[Subtext]`; private summaries stay out of prompt.

`ReputationProfile`

- Retrieval: only when context planner asks for `social` or user mentions rumors/reputation.
- Renderer: public-facing reputation versus private reality as separate hints.

`Routine` / `ScheduleBlock`

- Retrieval: current in-game time window plus current or next location.
- Renderer: schedule pressure or likely movement, not a hard command unless the world system commits it.
