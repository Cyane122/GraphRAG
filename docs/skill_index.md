# Skill Index

Catalog of the agent skills used with this repository: when to use each, what each
must **not** do, and how their triggers are kept from overlapping.

**Location / exposure:** Claude-oriented skills live user-global under
`~/.claude/skills/<name>/SKILL.md`; Codex skills live under
`~/.codex/skills/<name>/SKILL.md`. Keep one owned copy per tool and use a documented
sync step rather than symlinks when both tools need the same skill.

---

## Core skills (always available)

| Skill | Use it for | It must NOT | Trigger boundary |
| --- | --- | --- | --- |
| **python-file-header** | Creating or re-syncing the `# ====` header block of a `.py` file | refactor, rename, change behavior, move files, touch unrelated docs | "update/sync the header" only — **not** general "edit this Python file" |
| **python-dev-style** | New Python code or local edits where **style or placement** is the question (typing, docstrings, naming, package structure, split/merge judgment) | broad refactors, style-only rewrites of working code, header-only sync as the whole task, portfolio/architecture docs | writing/placing new code — **not** behavior-preserving restructuring |
| **python-refactor-safe** | Behavior-preserving refactoring: small scoped diffs, dedup, restructure without changing public behavior | add features, change public APIs silently, massive rewrites, behavior changes without explicit ask | explicit refactor/cleanup/dedup/"keep behavior" — **not** new features |

### Sequential chains (not conflicts)

- `python-dev-style` → finish with `python-file-header`
- `python-refactor-safe` → finish with `python-file-header`

Header sync is always the **last step**, never the task itself.

---

## Documentation & portfolio skills

| Skill | Use it for | It must NOT | Trigger boundary |
| --- | --- | --- | --- |
| **changelog-maintainer** | Adding human-readable, meaningful-change entries to `docs/changelog.md` (date-grouped) | restate architecture, edit source, log trivial/formatting-only changes | "record this / update the changelog" — frequent, mechanical, history |
| **architecture-doc-maintainer** | Updating `docs/architecture.md` only when module boundaries / data flow / design change | be touched for trivial changes, duplicate changelog content | "structure/architecture changed" — rare, judgment-heavy |
| **portfolio-retrospective-writer** | Turning repo docs/changelog/history into a Korean Notion portfolio draft | invent metrics, exaggerate, edit source, rewrite architecture docs, make unsupported claims | "write a portfolio/retrospective" — **reads** `docs/changelog.md`, never writes it |

---

## Test skill

| Skill | Use it for | It must NOT | Trigger boundary |
| --- | --- | --- | --- |
| **python-test-review** | Dedicated test-hardening passes: add/improve smoke checks, regression risk, edge cases, verify modified behavior with minimal source change | rewrite features, do broad refactors | "improve/add tests" — first-write tests for new code still belong to `python-dev-style` |

---

## Wiki authoring skill

| Skill | Use it for | It must NOT | Trigger boundary |
| --- | --- | --- | --- |
| **author-wikirag-worlds** | Creating, expanding, repairing or reviewing English-authored, independently assembled Wiki V2 world/scenario prompt modules, character variants, locations, organizations and work-specific prose | modify live thread state or pending commits, invent or defer major missing canon, leave Korean explanatory prose outside allowed literals/examples, let one body reference another prompt/scenario, expose Actor/Updater/runtime mechanics, duplicate PromptBuilder rules | requests involving `wiki_v2/worlds`, Wiki형 월드/시나리오 작성, or GraphRAG-to-WikiRAG content promotion |

---

## Trigger conflict rules

| Pair | Overlap | Resolution |
| --- | --- | --- |
| file-header vs dev-style | both edit `.py` on changes | dev-style wins the task; file-header runs only as the final sync step |
| file-header vs refactor-safe | refactor changes defs → header needs update | refactor-safe wins; file-header runs sequentially at the end |
| dev-style vs refactor-safe | "improve code" is ambiguous | new code/new behavior → dev-style; behavior-preserving restructure → refactor-safe |
| changelog vs architecture | both "update docs" | changelog = frequent/mechanical/history; architecture = rare/judgment/design |
| portfolio vs changelog | both narrate work | changelog = repo-side raw record; portfolio = curated external view, reads (never writes) the changelog |
| test-review vs dev-style/refactor-safe | both involve tests | dev-style owns first-write tests; refactor-safe owns regression-safety notes; test-review is for explicit test-improvement passes |

Disambiguating keywords: "Wiki형 월드/시나리오" → author-wikirag-worlds; "추가/구현/만들어" (add/build) → dev-style; "동작 그대로/중복 제거/정리/리팩토링" (keep behavior/dedup/clean up/refactor) → refactor-safe; "헤더/header" → file-header; "기록/changelog" → changelog-maintainer; "구조/architecture" → architecture-doc-maintainer.

---

## Deferred (not built)

Nothing is currently deferred. Future skills should still be added only after a
repeated, documented need, and only after a `references`-style checklist in an
existing skill has been considered first.
