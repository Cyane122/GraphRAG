# State Mutation Ownership

This document names the expected writer for each persistent simulation state surface.
The goal is to keep Manager planning, Actor generation, and deferred commit writes from
silently competing for the same fields.

## Commit Boundary

Accepted roleplay turns are the only point where Actor-derived persistent writes should
reach Kuzu. Generation, reroll, edit, and delete paths may stage data in Chainlit session
state, but must not write Actor response side effects directly.

Manager may prepare `manager_effects` during turn setup. Those effects are committed by
`src.ui.deferred_commit.commit_pending()` after the previous response is accepted.

## Writer Matrix

| State surface | Primary writer | Allowed secondary writer | Conflict rule |
| --- | --- | --- | --- |
| `GlobalState` time/weather | Manager core commit via time plan and OOC patch commit | OOC-only parser | OOC time patches suppress Manager time advancement for that turn. |
| Character location | Manager core commit and OOC patch commit | Explicit Actor postprocess only when grounded in accepted text | OOC/user-declared location wins over inferred movement in the same commit. |
| `DynamicState` | Actor postprocess in `src.simulation.state.updater` | Manual OOC state patch | Literal accepted facts only; figurative phrasing must be ignored. |
| `RELATIONSHIP` | Relationship updater in Actor postprocess | Social systems for slow drift | Direct accepted interaction beats background drift; deltas stay conservative. |
| `Event` | Event updater in Actor postprocess | Manager/OOC for explicit system events | Durable narrative changes only; routine low-importance moments should not persist. |
| `Memory` | Memory system after accepted response | Long-running distortion/compression systems | Memory is subjective per character; do not rewrite it into objective fact. |
| `Goal` | Goals system | Actor postprocess only through approved goal hooks | Background goals are best-effort and must not block the turn. |
| `Item` | Item system | Actor postprocess through item hooks | Inventory/location changes require concrete accepted evidence. |
| `Secret` | Secrets system | Actor postprocess through public-hint-safe hooks | `private_summary` must not render directly into Actor prompts. |
| `NeedsState` | Manager auxiliary commit and needs system | OOC time patches through pending effects | OOC elapsed time is authoritative for needs elapsed-minutes calculation. |

## Priority Order

When multiple systems touch the same target in one accepted commit, apply this order:

1. Explicit OOC/user command.
2. Manager core commit effects.
3. Actor response postprocess.
4. Manager auxiliary effects.
5. Long-running best-effort systems.

If a lower-priority system cannot merge without overwriting a higher-priority write, it
should skip the field and log enough context for audit.

## Mutation Log Target

The future mutation log should record one row per field-level write:

| Field | Meaning |
| --- | --- |
| `commit_id` | Pending commit id that authorized the write. |
| `system` | Writer system name, such as `manager_core` or `actor_dynamic_state`. |
| `target` | Node or relationship id being changed. |
| `field` | Property name or logical state key. |
| `before` | Previous value, redacted when needed. |
| `after` | New value, redacted when needed. |
| `evidence` | User input, Actor excerpt hash, or manager effect source. |

This log should be emitted from the deferred commit layer or from helpers called by it,
not during Actor streaming.
