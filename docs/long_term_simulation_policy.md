# Long-Term Simulation Policy

This document records the TODO 3.3 implementation boundary.

## Memory Types

The first-pass memory taxonomy is intentionally small:

- `episodic`: concrete events and scene continuity.
- `emotional`: remembered affect, insecurity, relief, fear, anger, or shame.
- `relational`: shifts in trust, intimacy, conflict, reconciliation, or attachment.

Later candidates such as sensory, promise-debt, and trauma-scar stay out of the default path until retrieval and rendering prove they improve prompts without adding noise.

## Summary Roles

`summary` remains the compact canonical event/memory label.

`narrative_summary` is for Actor-facing continuity. It can be compressed as memories decay, and renderers prefer it over raw summary when present.

`state_summary` is for fact preservation. It should describe durable state or relationship changes and should not be used as prose flavor.

## Execution Boundaries

Long-term systems must not run simply because a turn happened.

- Memory recall runs only when the context planner includes `memory`.
- SNS/social context runs only when the planner includes `social`.
- Secret hints run only when the planner includes `secrets`, and only `public_hint`/prompt-safe hint data reaches the Actor prompt.
- SSES scheduling runs only when planner context indicates schedule, social, or long-term pressure.
- Branch and rollback remain deferred. The existing deferred commit/reroll pattern is the current safety boundary.

## User Agency

NPC autonomous behavior may add pressure, plans, or interruptions, but it should not hard-commit the PC's reaction. Scheduler output should leave the user room to respond.
