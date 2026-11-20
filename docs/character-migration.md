# Character migration — offline COPY candidate, not an activation procedure

**Do not deploy this candidate or enable `MP_CHARACTERS_ENABLED` on production.**
The remaining acceptance blockers are listed in `multi-character-review.md`.
No production migration, service restart, scheduler/provider request or release
publication was performed during implementation.

## Ownership layout implemented

- AutoPost content has mandatory `character_id` fields referencing `character.id`.
  Character-scoped sessions filter reads and bulk writes, validate ORM writes and
  referenced content IDs, and reject raw SQL and unvalidated bulk reassignment.
- AutoChat raw SQL, tokens, conversations, drafts, memory and PPV data use separate
  SQLite files at `DATA_DIR/characters/<canonical-character-uuid>/bot.db`.
  Each file stores its owner in `workspace_owner`. There is no token fallback in
  character mode. This is a storage boundary, not a fully row-migrated AutoChat schema.
- AutoPost media paths use `MEDIA_ROOT/characters/<uuid>/...`. Chat exports use
  `exports/characters/<uuid>/...`. File/thumbnail URLs include `character_id`.
- AutoPost state keys retain the legacy AppSetting API but persist as
  `<uuid>:<key>` for character requests. Global technical-setting separation is
  **not finished**; do not interpret key namespacing as completion of that criterion.

## Preflight (read-only inputs)

First obtain a separately authorized, quiescent local backup of BOTH databases,
media and installation configuration. This implementation used only synthetic
DBs; the SMB snapshot was source-only and is **not** a production database backup.
Resolve the installation's actual paths from its configuration; do not assume
Sally, a particular directory layout or another installation's credentials.

From the isolated source directory, with dependencies installed:

```bash
python -m tools.character_migrate \
  --autopost-db /isolated-input/autoposter.db \
  --autochat-db /isolated-input/bot.db \
  --media-root /isolated-input/media
```

Without `--apply-copy`, the command reports integrity checks, table counts and
source fingerprints. It never starts an application or imports a provider.
Review every reported input and keep the report access-restricted.

## Build a new offline copy

```bash
python -m tools.character_migrate \
  --autopost-db /isolated-input/autoposter.db \
  --autochat-db /isolated-input/bot.db \
  --media-root /isolated-input/media \
  --destination /isolated-output/character-migration \
  --existing-character-name 'Operator-confirmed existing character' \
  --apply-copy
```

The tool requires an explicit nonblank name. The installation-neutral existing
workspace ID is `00000000-0000-0000-0000-000000000001`; it does not name or infer
Sally or copy a Persona into newly created characters.

Outputs are a **new directory**, never an in-place database update:

- `autoposter.db`: copied rows with existing content assigned to the confirmed root;
- `characters/<uuid>/bot.db`: copied AutoChat rows with an owner marker;
- `media/characters/<uuid>/...`: copied originals and existing thumbnails;
- `migration-report.json`: source fingerprints, row counts and output file hashes.

The tool rejects existing unknown destinations, nested source/destination paths,
symlink media and ambiguous existing ownership. Explicit and inline single-column
SHA/Persona-slug uniqueness are converted to per-character uniqueness in the copied
DB, preserving the original table DDL, rows, indexes not being replaced and triggers.
This was exercised on synthetic SQLite fixtures; PostgreSQL is not implemented.
Existing AppSetting rows are preserved and a namespaced copy is added for the
existing root. No posts, drafts, media, cached metrics or prior nightly-gate values
are deliberately deleted or replanned. The copy gains an explicit
`_character_migration` version receipt; character-mode startup checks this receipt,
required model columns and non-null/reachable ownership without create_all or
schema_sync. Unsupported incomplete schemas fail activation rather than repairing
ownership on startup.

Repeating the same migration with the same unchanged inputs/name/destination
returns the existing receipt only if every output hash still matches. Changed
inputs or changed outputs cause a refusal. Configuration files, exports and updater
activation are NOT yet migrated by this tool; this remains a deployment blocker.

For an explicitly new, empty installation only:

```bash
python -m tools.character_migrate --initialize-empty \
  --destination /isolated-output/empty-installation \
  --existing-character-name 'Operator-confirmed name'
```

This creates the schema only in a new output directory, with no tokens, planning
or chat enabled and persisted dry-run/pause defaults. Repeating an unchanged fresh
initialization checks its receipt; it never replaces an existing installation.
Fresh and migrated synthetic installations were exercised through the actual
combined TestClient lifespan with two characters and background effects disabled.
This is not complete productive-worker, GUI updater or multi-character acceptance.

## Optional settings layout copy conversion

After this migration (or fresh initialization), the separate stores still need a
supported atomic-settings layout for the normal connection factories. Use the
explicit local OFFLINE COPY procedure in [sqlite-layout-conversion.md](sqlite-layout-conversion.md)
on the stopped migration output, with relative paths `autoposter.db` and `bot.db`.
It preserves this migration's schema, owner IDs, source metadata and full output
hash-checking semantics. It does not assign owners again or activate character
mode. Never enable this candidate solely because storage conversion passed.

## Rollback and activation boundary

Rollback of this offline operation is simply discarding the new output directory:
all input files remain unchanged. Do not delete the input backup.

**There is deliberately no production activation command here.** An actual
activation must be designed and authorized separately after closing the blockers:
stop and drain all writers, snapshot configuration/keys and both DBs consistently,
verify receipts and account bindings, install matching code and paths atomically,
keep all character automations disabled, verify authenticated read-only scopes,
and only then authorize individual automation/account activation. Never restore
only one of the two databases or restore old tokens over a newer rotating token
chain. Preserve post/send idempotency markers and account-bound credentials.

The GUI updater's multi-character migration/rollback integration, normal lifespan
startup and both scheduler backends have not passed acceptance. Do not use the
review archive as a published update, and do not turn on the feature flag as a
substitute for an activation plan. Parent authorization is required before any
production migration, restart or activation, and is currently blocked by the
remaining acceptance criteria.
