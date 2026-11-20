# Durable implementation tasks — handoff checkpoint

Status: INCOMPLETE REVIEW CANDIDATE. Production authorization is not granted.
See `multi-character-review.md` for code scope, tests and precise remaining gaps.

## Completed vertical slices
- [x] Fresh source-only SMB snapshot, resource completeness and baseline hashes.
- [x] Inspect AutoPost models, services, API, storage, jobs and UI plus AutoChat raw
  SQLite/token/PPV/memory stores, caches, workers, exports and shared refresh owner.
- [x] Independent Character root with mandatory AutoPost content ownership.
- [x] Explicit scoped-session CRUD, foreign-ID/reference protection and immutable
  session context; reject raw SQL and unvalidated bulk ownership updates.
- [x] Real scoped saved-view API and empty/disabled Character create/list API.
- [x] AutoChat owned SQLite files and no token/environment credential cloning.
- [x] Scoped state/cache primitives; Fanvue cross-owner rejection and two rotating
  account token-chain mocks; cache partition regression tests.
- [x] Media namespace/SHA scope; authenticated file routes and path containment.
- [x] Per-tab URL selector, same-origin fetch/link/form context and local redirects.
- [x] Scoped in-process runs/cancel and owner-carrying thread primitive; fan-list
  refresh and group export integrations; namespaced synchronous exports.
- [x] Explicit SQLite offline-copy migration/preflight, idempotency, receipts,
  source-preservation checks and rollback/authorization documentation.
- [x] Targeted protected midnight/quota tests, full baseline comparison and offline
  two-tab/mobile/desktop Chromium checks; archive/diff/hash-manifest handoff.

## Release-blocking tasks not completed
- [ ] Owner-aware normal startup and complete dispatch in BOTH scheduler backends,
  persisted Celery owner payloads, per-character leases/calendar gates.
- [ ] Multi-character AutoChat poller/watchdog dispatch, memory-backfill and PPV
  import workers/status; all hidden process-state and in-flight coordination.
- [ ] Separate global technical configuration from character configuration; stop
  shared runtime-flag mutation and unsafe account-specific environment fallback.
- [ ] Signed/state-bound OAuth callback ownership; complete provider, publisher,
  watermark, upload and thumbnail ownership/path audits and mock coverage.
- [ ] Exhaustive foreign-ID/permission/error-contract coverage for every AutoChat
  and AutoPost operation; per-character Persona slug uniqueness / DB pair checks.
- [ ] Complete real-renderer media/set/view/channel/calendar/dashboard/analytics/
  settings workflows at mobile/desktop widths, including settings round trips.
- [ ] Production-style copied-data update smoke, multi-character updater packaging,
  PostgreSQL/inline-constraint schema variants and authorized rollback activation.
- [ ] Independent reviewer approval and closure of all security acceptance gaps.
- [ ] Parent's explicit production migration/restart/activation authorization.

No live migration, deployment, service restart, publication or automation action
belongs to this checkpoint. Continue on an isolated tree and repeat acceptance
before proposing a deployable release.
