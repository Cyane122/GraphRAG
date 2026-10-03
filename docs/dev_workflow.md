# Development Workflow

Practical run and validation notes for this repository. Routing, delegation,
review, and completion rules live in `AGENTS.md`; the changelog inclusion policy
lives in `.agent/changelog-policy.md`.

There is **no lint or build step** and **no pytest**. Validation means
`python -m py_compile <changed files>` and the standalone smoke scripts
(`python tests/smoke_<name>.py`), run with `.venv\Scripts\python.exe`.

---

## Windows launcher scripts

The root launcher scripts first run `cd /d "%~dp0"`, then use
`.venv\Scripts\python.exe` when available or fall back to `python -m`.

- `launch.bat` starts only the JSON/NDJSON API (`src.apps.app`, port 8000).
- Open the active `graphrag-chat-site/` client separately. The API's root URL
  returns 404; its interactive API documentation is at `/api/docs`.
- `--open-browser` is removed, and the `ppt_viewer.bat` and `world_editor.bat`
  launchers have been deleted.

---

## Change checklist

1. Validate: `py_compile` on changed files and the relevant `tests/smoke_*.py`.
   For runtime behavior, run `python -m src.apps.app` and exercise the change.
2. Sync the `# ====` header of any `.py` file whose top-level `def`/`class`
   set, public signatures, path, or one-line responsibility changed. Body-only
   edits do not need a header change.
3. Add a `docs/changelog.md` entry only for a meaningful change: a new feature,
   a behavior change, a structural refactor, or a documentation restructure.
   Skip formatting-only, header-only, and trivial local fixes. The changelog is
   date-grouped and is the source record for portfolio writing.
4. Update `docs/architecture.md` and `docs/architecture/` only when module
   boundaries, data flow, or design actually changed.
5. Update `docs/architecture/TODO.md` when a Wiki parity item changes status.
6. Run `graphify update .` after code changes.

## Wiki authoring validator after asset relocation

The installed `author-wikirag-worlds` validator still joins `--repo` with
`wiki_v2/worlds/`. Use its existing option with the asset root, and retain the
repository root on `PYTHONPATH` for engine imports:

```powershell
$env:PYTHONPATH = 'F:\python\NLP\GraphRAG'
.venv\Scripts\python.exe C:\Users\bling\.codex\skills\author-wikirag-worlds\scripts\validate_wiki_world.py --repo F:\python\NLP\GraphRAG\assets --world-id babe_university
```

The controller's independent reviewer observed this invocation pass from a
foreign cwd. Passing the original repository root still targets the old vault
location. No global skill files or runtime world content are changed by this
documentation workaround.
