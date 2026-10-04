# Skill Index

Catalog of the agent skills used with this repository: when to use each, what each
must **not** do, and how their triggers are kept from overlapping.

**Location / exposure:** Claude-oriented helper skills remain user-global under
`~/.claude/skills/<name>/SKILL.md`; Codex skills live under
`~/.codex/skills/<name>/SKILL.md`. Keep one owned copy per tool and use a
documented sync step rather than symlinks when both tools need the same skill.

Implementation, review, and model routing are not skills: they follow the role
model in `AGENTS.md` and the role files under `.agent/roles/`. Skills must not
start a separate implementation or review chain that bypasses that routing.

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
| **author-wikirag-worlds** | Creating, repairing, or reviewing Wiki V2 world/scenario prompt modules and authoring variants | modify live thread state, expose runtime metadata, or invent missing canon without authority | work under `assets/worlds/wiki/` |

This authoring workflow is intentionally excluded from `docs/changelog.md`; see
`.agent/changelog-policy.md`.

---

## Trigger conflict rules

| Pair | Overlap | Resolution |
| --- | --- | --- |
| changelog vs architecture | both "update docs" | changelog = frequent/mechanical/history; architecture = rare/judgment/design |
| portfolio vs changelog | both narrate work | changelog = repo-side raw record; portfolio = curated external view, reads (never writes) the changelog |

Disambiguating keywords: work under `assets/worlds/wiki/` → author-wikirag-worlds;
"기록/changelog" → changelog-maintainer; "구조/architecture" →
architecture-doc-maintainer.

---

## Deferred (not built)

Nothing is currently deferred. Future skills should still be added only after a
repeated, documented need, and only after a `references`-style checklist in an
existing skill has been considered first.
