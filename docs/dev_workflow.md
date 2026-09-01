# Development Workflow

The standard agent-assisted development sequence for this repository. It is short
on purpose; the detailed law lives in `AGENTS.md`, and orchestrated Python
implementation runs through the manual `/python-dev` plugin (see
`docs/skill_index.md`).

There is **no lint or build step** and **no pytest**. Validation means
`python -m py_compile <changed files>` and the standalone smoke scripts
(`python tests/smoke_<name>.py`).

---

## Windows launcher scripts

The root launcher scripts first run `cd /d "%~dp0"`, then use
`.venv\Scripts\python.exe` when available or fall back to `python -m`.

- `launch.bat` starts the app (`src.apps.app --open-browser`, port 8000), Graph
  Viewer (`src.apps.graph_viewer`, port 8001), and World Editor
  (`src.apps.world_editor`, port 8765).
- `ppt_viewer.bat` starts Graph Viewer only.
- `world_editor.bat` starts World Editor only.

`launch.bat` labels the Graph Viewer command window "PPT Viewer". This is a
known naming mismatch; the service is Graph Viewer and the label remains
unchanged for now.

---

## The 7-step sequence

1. **Inspect** — read the relevant code, `AGENTS.md`, and `docs/architecture.md`
   before changing anything. Find local conventions in neighboring files.
2. **Plan** — decide the smallest change that satisfies the request. Surface
   assumptions and risky areas first.
3. **Implement** — make the change. Delegate non-trivial implementation to
   Codex, normally via `/python-dev implement`.
4. **Test / validate** — run `python -m py_compile` on changed files and any
   relevant `tests/smoke_*.py`. If a change touches runtime behavior, run the
   matching command from `AGENTS.md` (e.g. `python -m src.apps.app`).
5. **Sync Python headers** — **mandatory** after any Python change that affects the
   header (see the rule below).
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

Never add Graph or Wiki world- or scenario-related history. This includes world
selection, schemas, state surfaces, migrations, named worlds, scenarios,
characters as authored canon, lore, prose, opening scenes, authoring tools/skills,
and paths under `src/assets/worlds/` or `wiki_v2/worlds/`. If a change is
substantively about a world or scenario, omit it rather than generalizing the
wording. See `.ai/changelog-policy.md`.

Do **not** add a changelog entry for:

- formatting-only changes,
- header-only sync,
- trivial local fixes with no externally visible effect

…unless the user explicitly asks. The changelog is **date-grouped** — add entries
under the current date, following the file's chronological order. It is the source record for portfolio writing
(see the `portfolio-retrospective-writer` skill); Notion drafts are generated from
it, never invented.

---

## Pointers

| Need | Go to |
| --- | --- |
| Working conventions / law | `AGENTS.md` |
| Detailed architecture | `docs/architecture.md` |
| History | `docs/changelog.md` |
| Changelog inclusion policy | `.ai/changelog-policy.md` |
| Which skill or plugin to use | `docs/skill_index.md` |
| Encoding rules (no PowerShell for Korean) | `AGENTS.md` → Encoding Rules |
