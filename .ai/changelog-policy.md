# Changelog Policy

`docs/changelog.md` is a concise engineering history, not a content-production
log.

## Include

- engine and runtime behavior,
- persistence and recovery guarantees,
- prompt-compilation infrastructure,
- shared Graph/Wiki state semantics,
- API and UI behavior,
- safety, tests, performance, and developer infrastructure,
- meaningful refactors and documentation-system changes.

## Exclude

Never record Graph or Wiki world- or scenario-related work, including:

- world selection, schemas, state surfaces, or migrations,
- scenarios and opening scenes,
- characters, profiles, relationships as authored canon, locations, and
  organizations,
- lore, prose, dialogue, appearance, background, or other content edits,
- files under `src/assets/worlds/` or `wiki_v2/worlds/`,
- world-authoring tools, validators, or authoring-skill changes.

Do not work around this rule by rewriting a world or scenario change in generic
language. If an entry's subject is a world or scenario concern, omit the entry.

The hook rejects common English and Korean world/scenario terms, paths, and
labels. Reviewers must still enforce the semantic boundary.
