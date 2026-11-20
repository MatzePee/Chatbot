# SQLite settings layout: explicit OFFLINE COPY conversion

Status: this closes only the default two-WAL-file settings-transaction layout
blocker through an operator-run copy conversion and the normal application
factories. It is NOT production activation or multi-character acceptance.
No production files, services, providers or schedulers were exercised.

## Choice and runtime contract

Keep the existing separate AutoPost database, canonical installation bot.db,
owner AutoChat databases and migration schemas. Convert only NEW copied SQLite
files to DELETE rollback journals. Each gets the versioned
`_settings_atomic_layout(version=1, layout='rollback-full-v1')` marker.
This avoids merging unrelated schemas, rewriting owner IDs, remapping tables or
inventing a replacement for `tools.character_migrate`.

`autoposter.db.make_engine()` is the normal engine factory, used at module startup;
`SessionLocal()` still uses that engine. Both AutoChat connection paths (canonical
and owner files) and this AutoPost factory recognize the copied marker. Marked
connections require an existing durable rollback journal and set connection-local
synchronous=FULL. They NEVER change the marked file's journal mode. An invalid
marker or a marked database subsequently changed to WAL fails connection opening.
Unmarked installations retain their existing WAL defaults. Updating code alone
therefore does not convert a live database or make the unsupported layout safe.
The existing transaction helper and its atomicity guards are unchanged.

The settings session attaches the canonical bot.db and validates FULL/EXTRA plus
rollback journaling on both databases. SQLite's rollback-journal multi-file
commit mechanism, not attached WAL, is the atomicity basis. Keep both databases
and their journals in the converted local filesystem tree on reliable local
storage. FULL relies on the OS/filesystem/device honoring sync; no power-cut,
crash-injection or cross-filesystem recovery certification is claimed.

## Prerequisites — mandatory, not automated by this tool

1. Obtain separate production maintenance/activation authorization. This worker
   did NOT use the parent's service-stop authorization.
2. Stop and drain EVERY application process, AutoChat poller, scheduler/worker,
   updater, external SQLite reader/writer and backup utility using this data.
   Keep them stopped until the snapshot/copy is complete. Do not run the tool
   against an active installation, SMB/NFS tree, another host's mount or a live
   filesystem snapshot with writers. Inputs must be quiescent local regular files.
3. Preserve a complete stopped installation data/configuration backup, all DB,
   -wal/-shm files, media, owner directories, secret/config files and the original
   environment outside the tree. Preserve MP_AUTOPOSTER_ENCRYPTION_KEY and
   MP_AUTOPOSTER_SECRET_KEY exactly, including whichever fallback was used.
   Keys outside source-root must be backed up separately. Never print keys.
4. Resolve actual DB paths from configuration. AutoChat canonical DB is currently
   hardcoded as `<code-root>/data/bot.db`; AutoPost uses its configured database
   URL (normally data/autoposter/autoposter.db). Do not guess from this example.
   Select a source-root containing BOTH stores and any owner DBs/configuration.
   External paths/symlinks must be resolved by a separately reviewed backup plan;
   this tool rejects symlinks instead of following them.
5. Run with Linux /proc visibility over all relevant local processes. The tool
   fails closed if descriptor inspection is inaccessible and rejects any process
   holding a source file open, including readers. This is defense in depth, not
   a service manager, a cross-host lock or a guarantee against an adversarial
   writer starting between checks. Operator-enforced quiescence is essential.

## Conversion command (offline dependency environment)

From the tested source checkout:

    unshare -n /root/autoplan-fix/venv/bin/python -m tools.sqlite_layout \
      --source-root /isolated/stopped-data \
      --destination /isolated/converted-data \
      --autopost-db autoposter/autoposter.db \
      --autochat-db bot.db \
      --sources-stopped

These are placeholders for an ALREADY stopped local backup. Destination must be
new and disjoint from source. It must have enough free disk for the complete
copied tree, WAL replay and rollback journals. No activation command is run.
Exit 0 means the copy was built and its receipt printed; exit 2 means refusal.
There is no automatic retry, in-place conversion or journal-mode env override.

For an existing workspace-migration output (or initialize-empty output), use
relative `autoposter.db` and `bot.db` instead. The original character migration
remains a SEPARATE step, with its existing explicit name/conflict-selection and
key requirements. Do not run character migration a second time on migrated DBs.
Layout conversion never assigns an owner or switches character mode.

## Snapshot, preservation and receipts

The converter makes no SQLite connection to source files, not even mode=ro
(which can change/create SHM) or immutable=1 (which can omit WAL contents).
It hashes all source files, checks open descriptors, copies the COMPLETE tree to
a private staging directory, checks source and copied bytes again, and then opens
ONLY staged DBs. SQLite replays the copied committed WAL frames there, and only
then changes the copied journal mode. This preserves committed WAL-only rows.
Nonempty rollback journals, symlinks, escaping DB paths, unknown existing
outputs, failed integrity/FK checks, unsupported markers and detected concurrent
changes cause refusal and removal of the staging copy. Uncommitted transactions
are not magically committed. Normal stop/rollback semantics apply.

Every .db under source-root is validated and converted (plus explicitly selected
DB paths even if their suffix differs). Do not include unrelated databases in
this data tree. Files that are not DBs, including configuration and key material,
are copied byte-for-byte; encryption is never invoked and ciphertext stays byte-
for-byte identical. No secret rotation, value rewrite, clear resurrection or
unknown-setting removal is performed by layout conversion. Original source file
contents/hashes stay unchanged. Reading may update filesystem access times.

`sqlite-layout-report.json` records input/output relative paths and hashes,
layout/options and `activation_performed=false`, never settings/secret values.
Treat even path/hash reports as private installation metadata. Unchanged repeats
with identical inputs/options/output return the receipt; changed source/output
refuses reuse. Successful application writes deliberately invalidate that reuse.
Keep a separate immutable copy of the original receipt before exercising writes.

If a migration-report.json exists, its original full output hash map must match
before conversion. The copied receipt retains original source fingerprint,
name, owner, technical-source choices, kind and version, and refreshes its full
output hashes (including the new layout receipt). To avoid a circular hash, the
layout receipt validates migration metadata separately and validates the
migration's complete output map. The existing migration's reuse code and hash
exclusions are unchanged; no tables/files are removed from its preservation check.

## Verification and activation boundary

Inspect the receipt and independently verify the target DB journal modes and
that key/configuration files match the stopped backup. The tests under
`tests/autoposter/test_sqlite_layout.py` exercise converted files through the
actual AutoPost factory/SessionLocal and both AutoChat factories, mixed settings
commit, independent-reader visibility, clear rollback and close/reopen. The
six-character smoke creates five empty disabled characters around the migrated
existing owner using the real create-character function; it proves isolated
account/content settings, shared technical values and persisted safe defaults.
It does not test UI/authentication, provider account bindings or scheduling.

The tool does not rewrite absolute paths in config or activate a new data root.
A separately approved offline switch must preserve the expected directory layout
and point AutoPost at the copied DB while AutoChat resolves to the copied
`data/bot.db`; never leave either canonical store pointing at the old tree.
Do not copy a bare .db over a target with stale -wal/-shm/-journal files. Keep
matching code, configuration, keys and ALL stores together as a stopped unit.
Keep existing feature gates/automations disabled; converting a layout is not
permission to enable characters, workers or publication. All other acceptance
gaps in the previous completion reports remain.

## Rollback — no promise of lossless rollback after activation

Before activation or before any target writes: discard/quarantine only the new
copy. The source backup, its original hashes, keys, ciphertext and WAL files
remain unchanged. This operation has no in-place reversal to run.

After target writes: stop/drain all processes again and first take a complete
stopped backup of the NEW tree and current configuration/keys. Reverting to the
original tree discards EVERY write made since the original snapshot, including
settings, messages, posts, token rotations and idempotency markers. It does NOT
undo remote publications or restore a usable old refresh-token chain. Such a
rollback can cause duplicates and stale credentials; reconcile external state
and rotating tokens separately before any worker is allowed to run. Never
restore one database alone, merge old WALs into new DBs, or claim that restoring
old files also rolls back provider state. Any forward-data salvage requires a
separate reviewed migration, not this bounded converter.
