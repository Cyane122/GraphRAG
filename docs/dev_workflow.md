# Development Workflow

The standard agent-assisted development sequence for this repository. It is short
on purpose; the detailed law lives in `AGENTS.md`, and skill procedures live in the
`python-*` skills (see `docs/skill_index.md`).

There is **no lint or build step** and **no pytest**. Validation means
`python -m py_compile <changed files>` and the standalone smoke scripts
(`python tests/smoke_<name>.py`).

---

## The 7-step sequence

1. **Inspect** — read the relevant code, `AGENTS.md`, and `docs/architecture.md`
   before changing anything. Find local conventions in neighboring files.
2. **Plan** — decide the smallest change that satisfies the request. Surface
   assumptions and risky areas first.
3. **Implement** — make the change (see the `python-dev-style` / `python-refactor-safe`
   skills for how).
4. **Test / validate** — run `python -m py_compile` on changed files and any
   relevant `tests/smoke_*.py`. If a change touches runtime behavior, run the
   matching command from `AGENTS.md` (e.g. `python -m src.apps.app`).
5. **Sync Python headers** — **mandatory** after any Python change that affects the
   header (see the rule below). Use the `python-file-header` skill.
6. **Update the changelog** — only if the change is **meaningful** (see definition
   below). History lives in `docs/changelog.md`.
7. **Update architecture docs** — only if the architecture actually changed (module
   boundaries, data flow, design). Architecture lives in `docs/architecture.md`.

---

## When to sync Python headers (step 5)

Re-sync the `# ====` header block of a `.py` file whenever you:

- add or remove a top-level `def` or `class`,
- change a public function/class signature,
- move or rename the file, or
- change the file's responsibility (its one-line role).

You do **not** need to touch the header for body-only edits that don't change any
of the above. Headers use **`src/`-relative paths**. Never put change-history,
TODOs, or FIXMEs in the header.

---

## What counts as a "meaningful" change (step 6)

Update `docs/changelog.md` for:

- a new feature or capability,
- a behavior change,
- a refactor that changes structure or boundaries,
- a documentation restructure.

Do **not** add a changelog entry for:

- formatting-only changes,
- header-only sync,
- trivial local fixes with no externally visible effect

…unless the user explicitly asks. The changelog is **date-grouped** — add entries
under the current date, newest first. It is the source record for portfolio writing
(see the `portfolio-retrospective-writer` skill); Notion drafts are generated from
it, never invented.

---

## Pointers

| Need | Go to |
| --- | --- |
| Working conventions / law | `AGENTS.md` |
| Detailed architecture | `docs/architecture.md` |
| History | `docs/changelog.md` |
| Which skill to use | `docs/skill_index.md` |
| Encoding rules (no PowerShell for Korean) | `AGENTS.md` → Encoding Rules |
