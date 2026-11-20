# Technical configuration continuation — not deployment acceptance

The candidate remains incomplete. No service, production source, SMB data or real provider was touched.

## Proven contract

- `app/setting_scope.py` is the explicit shared infrastructure allowlist. The checked-in `settings-scope.json` classifies all 138 AutoChat default fields, 33 AutoPost integration fields and 60 environment declarations. Tests reject unclassified declaration changes and unknown literal AutoChat setting reads/writes. The map also lists the additional `doku_markdown` key and 15 dynamic call sites needing route review. It is not a complete route/form mapping.
- Canonical technical values are in installation `bot.db/settings`. Account/content settings remain owned. Notification Telegram credentials are installation configuration, not publication credentials.
- In character mode the AutoPost control plane reads/writes/clears canonical technical values too. It does not call AutoChat account configuration or return legacy X secrets. Character HTTP middleware and leased worker dispatch refresh synchronous provider configuration before invoking work.
- New OpenRouter, vision, GitHub and notification Telegram secret writes use the versioned `technical-secret-v1` Fernet envelope with the existing AutoPost encryption key. Old plaintext is readable for compatibility. Existing key material/environment must be preserved: this change does not rotate or replace keys. Migration/new writes do not promise forensic erasure of old input backups, WALs or SQLite free pages.
- Copy migration moves AutoChat technical fields into the global technical store, consolidates AutoPost technical fields into the same store, removes technical fields from owner integration JSON, and leaves account/configuration/status rows only under the assigned owner. Original source files stay unchanged. Unknown existing character values are preserved, not silently discarded.
- Conflicting nonempty technical values stop copy migration with a field-only error. Operator selection is explicit through `--technical-sources PATH`, where PATH contains a JSON object mapping field names to `autochat` or `autopost` (no secret values). Selections are validated and included in idempotency receipts. Example: `{"git_branch":"autopost"}`. Explicit AutoChat clear tombstones suppress resurrection of legacy AutoPost values. Apply only to staged copies and only with the original installation encryption key available.
- Common AutoPost adapter HTTP writes and Fanvue S3 PUTs now re-read persisted owner runtime/archival state on a fresh DB connection at the request hook, not from a stale snapshot. Missing, malformed, archived, paused or dry-run state blocks transmission. Exact OAuth token destinations and enumerated Telegram connection-read methods remain available while paused. Telegram local blocking is distinguished from uncertain delivery. A stop cannot recall an already transmitted request and the DB read/HTTP send is not a distributed atomic transaction.

## Bounded transaction contract (not deployment acceptance)

`autoposter.services.appconfig.set_many(session, payload)` and
`clear_secret(session, field)` now enlist canonical technical rows and their
explicit-clear markers in the **same SQLAlchemy connection/transaction** as the
character integration JSON. Neither operation commits. The caller must commit
on success and roll back on failure (the existing `character_session` and
`get_db` dependencies do this). There are no commit callbacks, compensating
writes, or independent technical commits in this path.

Supported storage layouts:

- One file-backed SQLite database containing both AutoPost tables and canonical
  AutoChat `settings`; WAL is supported in this single-file case. Journaling
  must not be OFF/MEMORY and synchronous must not be OFF.
- Two existing file-backed SQLite databases attached to the caller connection;
  both must use DELETE/TRUNCATE/PERSIST rollback journals with synchronous FULL
  or EXTRA on that connection. This uses SQLite's multi-file atomic commit, not
  separate WAL commits.

Unsupported layouts (including the current default **two separate WAL files**,
`:memory:` main, PostgreSQL + AutoChat SQLite, and nested/savepoint settings
writes) raise `SettingsTransactionUnsupported` before this operation mutates
settings or autoflushes pending ORM objects. Settings-save, clear, and Git
settings HTTP routes translate this into 409. Even technical-only writes through
this session API require a supported layout: silently treating them as an
independent transaction would make composition unsafe. Reads still work.

The supported API remains, for example:

```python
async with character_session(supported_engine, owner_id) as session:
    await appconfig.set_many(session, {
        'openrouter_api_key': new_secret,
        'auto_plan_days': 21,
    })
    await appconfig.clear_secret(session, 'github_token')
# One commit on context exit; an exception rolls everything back.
```

The caller sees its pending values through `get_all(session)`/the setter result;
independent readers see only committed values (or SQLite lock/busy behavior).
Pending values are not published by setters to process caches/snapshots.
Character and mixed setters/character clears acquire the SQLite writer lock
before reading integration JSON, and reload stale identity-map objects, so
concurrent disjoint patches do not overwrite each other. Contention may still
raise SQLite busy/timeout; callers must roll back and retry the entire operation,
not just its final write. Blank/masked secrets remain unchanged; explicit clear
and its fallback-suppression marker commit or roll back together. Existing
Fernet envelopes, plaintext-read compatibility, allowlist and owner keys remain.

The transaction patch itself does not relocate databases or change production
journal pragmas. The subsequent bounded layout continuation provides an explicit
OFFLINE COPY converter and marker-aware normal factories; see
[sqlite-layout-conversion.md](sqlite-layout-conversion.md). It retains the
separate-file schema and uses DELETE/FULL only on newly copied files. Unmarked
installations still use WAL and fail closed for mixed settings until an operator
performs the copy conversion and a separately authorized offline switch. Marked
factories validate, not silently change, the journal mode. No production
conversion, deployment or restart was performed.
Standalone AutoChat `set_setting`/`set_technical_settings` remain independent
legacy APIs and must not be composed into an AutoPost atomic operation; use the
session API above instead. Legacy single-character behavior is retained. No
power-cut/OS-crash fault injection, cross-filesystem recovery or PostgreSQL
distributed transaction is claimed.

## Remaining acceptance gaps

The default-installation storage blocker has a tested, explicit offline-copy
resolution through the normal factories (see sqlite-layout-conversion.md).
Unconverted installations remain unsupported, and no live activation or
operational crash-recovery acceptance is claimed. All other gaps below remain.


All dynamic field/form/action classification, environment/default migration interactions, every existing secret round trip and every two-character browser save still require complete acceptance. The legacy settings browser fixture passes but is not a two-character UI proof. Existing control-plane write restrictions need full review. The HTTP request guard does not establish coverage for AutoChat, LLM or other clients that bypass the AutoPost adapter clients.

## Evidence

The bounded transaction continuation is recorded in
`/root/multi-character-activation/TRANSACTION-COMPLETION.json` and
`evidence/transaction/`: real RED/GREEN logs, 54 focused tests passing, and the
final offline full suite with 779 passing and exactly the same six recorded
baseline failures. Tests ran with `unshare -n /root/autoplan-fix/venv/bin/python`.
The receipt includes commands, source hashes, explicit supported/rejected
layouts and remaining limitations. This does not complete the whole criterion.

The earlier configuration continuation evidence follows (historical counts):

`/root/multi-character-activation/evidence/config/` retains RED/GREEN runs 07–24, the initial browser-fixture failure and corrected browser run 25. The complete offline suite is `astra-full.xml`: 755 passed and the same six baseline failure node IDs. Focused suite `astra-focused.xml`: 63 passed. `astra-verification.json` compares the existing baseline XML programmatically and hashes the continuation source files.

The real offline Chromium legacy settings harness verifies 48 AutoPost controls/actions, five synthetic save checks, no writes on load, independent save payloads and cancelled destructive actions, and overflow checks at 320/390/1280 pixels. Fifteen screenshots were captured; no independent visual inspection is claimed. Every request was fulfilled locally. No real provider action was performed.

Old candidate archive/patch/manifests predate this continuation and are stale. No deployable package or completed acceptance criterion is asserted.
