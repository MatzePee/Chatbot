# Release candidate: supported boundary and remaining approval gates

This is a source-only RELEASE CANDIDATE, not an approved or deployed release.
The canonical current gate is the accompanying ACCEPTANCE-LEDGER.json and
FINAL-RELEASE-CANDIDATE.json. Historical continuation documents/counts describe
past checkpoints, not current blanket blockers. The final ledger compares those
claims to exact current tests; passing tests are not live deployment acceptance.

## Supported activation configuration

Linux local regular-file SQLite installation; one nonroot app process using the
bundled fixed .venv/bin/python -m uvicorn app.main:app service, repository working
directory, standard mandatory repository/.env EnvironmentFile, implicit NSS
primary/supplementary groups, normal inprocess scheduler and MP_PREVIEW=0.
Existing legacy single-owner updates remain legacy by default. Explicit operator
confirmation of the existing identity and a separate full restart perform stopped
COPY migration before imports. New owners start empty, paused/dry-run with both
planning and chat disabled. Real schedules are retained, not rewritten.

The selected generation uses validated rollback-journal/FULL SQLite stores through
the normal factories. Unconverted two-WAL mixed settings, savepoint-first mixed
writes, in-memory settings stores and PostgreSQL+SQLite mixed transactions refuse
safely. PostgreSQL migration and arbitrary historical schema certification are NOT
requirements for this deployed SQLite configuration. Celery/multiprocess activation,
preview/shadow workers, remote filesystems, external stores and arbitrary service
customizations are not accepted activation configurations. Scheduler wrapper tests
do not expand that production support claim.

Standard EnvironmentFile is supported with helper4 actual-PID environment/config
attestation; it is not parsed/sourced by privileged Python. Drop-ins, manager defaults,
listed namespaces/credentials and custom group directives refuse; see
[environment-parity.md](environment-parity.md) for the exact allow/refuse contract.
No real systemd-manager integration test has been performed here. Real nonroot
processes, UID/GID/groups, /proc, config factory, migration and application lifespans
are tested with synthetic service-manager/HTTP authorization boundaries.

## Sabrina: mandatory one-time administrator prerequisite

Both ACTUALLY INSTALLED root-owned wrapper and supervisor must be helper4/protocol4.
A GitHub source replacement cannot replace an old installed root helper. Missing,
old, symlinked, writable or wrong-owner helpers block both GUI staging and direct
POST before writing intent. No extra sudo action or mutable-root-code execution is
introduced. From an independently reviewed root-owned release tree, the administrator
uses the existing installer with the ACTUAL discovered unit:

    sudo bash deploy/install-update-helper.sh --check --service ACTUAL_UNIT
    sudo bash deploy/install-update-helper.sh --service ACTUAL_UNIT

This was NOT performed on any real installation by this worker. Thereafter Sabrina
can install the user-published code through her normal GUI, deliberately stage the
existing owner, then invoke the separate existing restart action. Only Matze uploads
GitHub; only the parent handles authorized production activation after review.

## Current acceptance and exact limitations

The three automatic-X test expectations were obsolete: the actual schedulers omit
that job, the queued legacy task returns manual_only, and collection requires an
explicit authenticated editor/admin POST plus persisted scope claim. Replacement
tests use real functions and strict HTTP MockTransport /2/users/me and /2/tweets
paths/account IDs; they cover auth, scopes, rolling-instant filtering over both DST
changes, retained cache, independent-session claims/lease recovery and author mismatch.
A real RED/GREEN test additionally exposed and fixed a changed immutable account ID
being accepted when its handle matched. No paid automation was restored.

The AutoPost renderer equals the hash-verified original fresh baseline byte-for-byte.
The older manifest fingerprint predates intentional midnight planning: autoEvery is
retired; auto_plan_interval_hours remains stored but ignored. All other original
controls, options, model tasks, 18 API actions and all original form mappings survive.
The historical inventory is retained; the reviewed current inventory is checked for
unknown additions/removals, field ownership and all direct AutoPost route permission
dependencies. Actual form roundtrips, secret retain/clear, real role-denial HTTP and
an offline Chromium comparison against the original renderer complement the inventory.
A fingerprint alone is not a completeness test.

Real installed-helper update/migration/active-backup/failed-health code rollback tests
use temporary root-owned tooling, actual nonroot children, real local Git/venv/pip,
SQLite and app startup, but mocked systemd/health transport. Their empty-requirements
fixture does NOT prove production dependency installation. The candidate package is
separately extracted and its actual requirements checked offline; inability to resolve
these dependencies remains an explicit release gate, not invented success.

File/descriptor checks do not atomically exclude a new external writer, transient ABA
changes or a concurrent local filesystem adversary replacing parent directories. The
operator must quiesce all external writers and maintain trusted local ownership.
There is no full power-cut/filesystem-sync certification. After active writes, never
remove the active pointer or restore stale legacy data; code rollback preserves the
current generation. Reverse migration is not implemented. Complete matched backups
must include all selected DBs/WALs, media, exports, configuration/keys and pointer.

Six-owner principal settings/account-key/upload/export/job boundaries have actual
backend and offline Chromium evidence; this is not exhaustive every-screen/provider
certification. Screenshot capture/geometry assertions are not visual inspection.
The canonical ledger lists remaining unproven original acceptance scope explicitly.
No network, SMB, production service, provider, deployment or GitHub action is part
of this worker's acceptance. Independent parent review is still mandatory.
