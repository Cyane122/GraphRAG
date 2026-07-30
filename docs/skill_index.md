# Skill Index

Catalog of the agent skills used with this repository: when to use each, what each
must **not** do, and how their triggers are kept from overlapping.

**Location / exposure:** Claude-oriented helper skills remain user-global under
`~/.claude/skills/<name>/SKILL.md`; the `python-dev` plugin is a local
skills-dir plugin at `~/.claude/skills/python-dev/`. Codex skills live under
`~/.codex/skills/<name>/SKILL.md`. Keep one owned copy per tool and use a
documented sync step rather than symlinks when both tools need the same skill.

---

## Python development plugin

`python-dev` is a manual-only plugin. Invoke it explicitly as
`/python-dev plan <task>`, `/python-dev implement <task>`, or
`/python-dev review [paths]`. With no mode argument, it defaults to
`implement`. It must not be auto-triggered.

- `plan` — read-only investigation, scoping, and implementation planning.
- `implement` — the default path: Claude investigates, frames the bounded
  brief, delegates non-trivial implementation to Codex, reviews the diff, and
  confirms validation.
- `review` — review a diff and its validation evidence for correctness,
  completeness, and risk.

The stable Python law lives in `AGENTS.md`. The implementation contract used by
the plugin lives in `~/.claude/skills/python-dev/references/python-contract.md`.
Claude owns investigation, delegation, review, and final reporting; Codex owns
non-trivial Python implementation and validation.

---

## Documentation & portfolio skills

| Skill | Use it for | It must NOT | Trigger boundary |
| --- | --- | --- | --- |
| **changelog-maintainer** | Adding human-readable, meaningful-change entries to `docs/changelog.md` (date-grouped) | restate architecture, edit source, log trivial/formatting-only changes | "record this / update the changelog" — frequent, mechanical, history |
| **architecture-doc-maintainer** | Updating `docs/architecture.md` only when module boundaries / data flow / design change | be touched for trivial changes, duplicate changelog content | "structure/architecture changed" — rare, judgment-heavy |
| **portfolio-retrospective-writer** | Turning repo docs/changelog/history into a Korean Notion portfolio draft | invent metrics, exaggerate, edit source, rewrite architecture docs, make unsupported claims | "write a portfolio/retrospective" — **reads** `docs/changelog.md`, never writes it |

---

## Wiki authoring skill

| Skill | Use it for | It must NOT | Trigger boundary |
| --- | --- | --- | --- |
| **author-wikirag-worlds** | Creating, repairing, or reviewing Wiki V2 world/scenario prompt modules and authoring variants | modify live thread state, expose runtime metadata, or invent missing canon without authority | work under `wiki_v2/worlds/` |

This authoring workflow is intentionally excluded from `docs/changelog.md`; see
`.ai/changelog-policy.md`.

---

## Trigger conflict rules

| Pair | Overlap | Resolution |
| --- | --- | --- |
| changelog vs architecture | both "update docs" | changelog = frequent/mechanical/history; architecture = rare/judgment/design |
| portfolio vs changelog | both narrate work | changelog = repo-side raw record; portfolio = curated external view, reads (never writes) the changelog |

Disambiguating keywords: work under `wiki_v2/worlds/` → author-wikirag-worlds;
"기록/changelog" → changelog-maintainer; "구조/architecture" →
architecture-doc-maintainer. `python-dev` is manual-only, so Python work does
not use keyword auto-triggering.

---

## Deferred (not built)

Nothing is currently deferred. Future skills should still be added only after a
repeated, documented need, and only after a `references`-style checklist in an
existing skill has been considered first.
