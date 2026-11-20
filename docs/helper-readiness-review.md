# Skeptical source review — bounded helper-readiness slice

HISTORICAL helper3 review. Environment parity follow-up is documented in
[environment-parity.md](environment-parity.md): helper4 supports the standard
EnvironmentFile path, captures/checks implicit groups and verifies actual launched
argv/executable/cwd plus unchanged config/unit files. R2/R3 blanket-refusal statements
below describe the old slice, not current behavior. R1 external-writer/TOCTOU and
independent parent review remain open. Final exact-tree full evidence is recorded
in ENVIRONMENT-PARITY-COMPLETION.json; do not infer it from this earlier review.

Reviewer is a fresh worker relative to the INHERITED broker/capability slice, but
also the implementer of this readiness change. This is not an independent approval
of the new diff. No nested reviewer or deployment was used.

## Reviewed scope

Read actual functions (not only tests): deploy/fanvue-admin; creatorstudio_update.py
prepare/activate/rollback/snapshot/service_contract/running_environment/
stopped_migrate/migration_request/restart/supervise/main; repair_update.py discovery,
rendered_files and install; app/character_upgrade.py bootstrap/apply_stopped/_save;
app/sqlite_layout.py stopped_authority; tools/sqlite_layout.py _assert_stopped,
_inventory, convert_copy/_publish_copy; tools/character_migrate.py preflight,
_fingerprint and build_workspace_copy copy/publication portions; installed-readiness
trust traversal, API stage/status gate and update-card JavaScript.

Did NOT fully audit every migration SQL transformation, six-owner UI, all workers,
provider code, installer host policy, SELinux/AppArmor, systemd namespace combinations,
or production deployment. No claim that the project is deployable.

## Concrete findings fixed with real RED -> GREEN

F1 — Missing actual installed-version gate (high usability/safety).
  Old HEAD wrapper restart directly called systemctl restart; code publication never
  updated root-owned files. Now app/helper_readiness.py:44-79 reads the actual pinned
  root-owned installed supervisor HELPER_VERSION and wrapper protocol, not deploy/.
  trusted_file:22-41 uses descriptor-relative nofollow traversal and rejects mutable
  ancestors/non-root ownership/type. The read-only installed command executes as the
  caller with -I, sanitized environment and passed FD. API stage checks before writes.
  Evidence: 01/02 RED/GREEN and real non-root old/missing/wrapper-old GUI fixtures.

F2 — Root environment was used instead of systemd service environment (high).
  creatorstudio_update.py:251-300 now validates fixed startup/identity and rejects
  Environment/EnvironmentFiles/etc and manager defaults before stop. Actual original
  service PID environ/PATH is captured before stop with UID/start-time checks.
  stopped_migrate:310-326 rejects absent captured environment and rechecks the unit
  after stop. Child launch:361-364 uses captured env plus FD, not root's os.environ.
  Evidence: 03-06 and 09 RED/GREEN; real non-root PID/env fixture. Explicitly supplied
  environment= in synthetic integration tests remains an internal Python test seam;
  the CLI/HTTP cannot supply it.

F3 — Resolving sources before checking symlinks discarded provenance; AutoChat path
  was guessed (high data-integrity concern).
  app/character_upgrade.py:148-160 now checks lexical-vs-resolved sources and refuses
  custom AutoChat data directories rather than copying the wrong legacy DB.
  Evidence: 07 RED/GREEN for DB/media links and a different AutoChat source.

F4 — Root broker read unbounded, symlink-following service-writable intent (DoS/read
  boundary). creatorstudio_update.py migration_request (following stopped_migrate)
  pins repo/data/request FDs, uses nofollow/nonblock, checks regular service-owned
  non-world/group-writable file and limits reads to 8192 bytes. Both pre/post-stop
  restart checks use it. Root-owned/symlink/oversized request fixtures fail before
  any service/status action. Evidence: 11 RED/GREEN.

F5 — UI allowed staging without helper readiness and hid late refusal details.
  Actual card disables staging for old/missing/unsupported helper, including an
  intervening HTTP409; messages are textContent, not HTML. Evidence: actual offline
  Chromium RED/GREEN (08,10), 320/390/1280 widths. No restart/update network action.

## Inherited controls retained and evaluated

- Broker requires root supervisor and non-root fixed service UID; no repository
  Python executes as root (stopped_migrate:303-364). Root service actions remain
  fixed wrapper actions. No new sudoers/installer capability in HTTP.
- Capability: app/sqlite_layout.py:17-42 checks read-only FD, regular root-owned
  unlinked mode0400 file, live direct root parent, matching child UID, bounded age
  and resolved source containment. tools/sqlite_layout.py:39-69 still checks all
  visible descriptors and refuses inaccessible same-UID descriptors. A root attacker
  can mint authority already; root is explicitly trusted, not defended against.
- Proof scope is root/data; broker excludes prepared releases/backups from privileged
  scan; activation rejects runtime sources there. Outside-data custom sources cannot
  obtain matching scoped authority and fail closed on inaccessible proc entries.
- Copy/layout publication uses private staging, source fingerprints, integrity/FK
  checks, fsync and no-replace layout rename (tools/sqlite_layout.py:126-240).
  Active receipt publication is fsynced and authoritative. Real child exit23 after
  pointer then active write succeeds; later code rollback preserves selected data.
- Installer still requires explicit administrative invocation; its input is reviewed
  root-owned release material. Running it blindly from a mutable app checkout is
  NOT safe and is not an automatic GUI action. Old updater cannot perform this step.

## Open findings / limitations — not approval

R1 — Global /proc scan plus capability is not atomic writer exclusion.
  Broker:327-353 snapshots inode identities and descriptors; later another UID could
  open a source, or a mutable directory/file could be replaced. Child may skip that
  UID's inaccessible descriptors under authority. Inventory hashes catch many changes,
  but not every transient/ABA writer race. No OS-enforced all-writer quiescence exists.
  Parent must review/exclude external writers before activation; adversarial-race
  acceptance and full power-loss matrix remain OPEN.

R2 — Full systemd namespace and supplementary-group parity is not established.
  service_contract rejects enumerated env/root/group overrides, but does not inventory
  every BindPaths/TemporaryFileSystem/ProtectHome/credential/namespace directive.
  Child extra_groups=[] also does not reproduce a service account's implicit extra
  memberships. Real service default PATH/env is captured, but this is NOT universal
  unit-configuration compatibility. Do not approve unreviewed/custom units.

R3 — Standard distributed unit has EnvironmentFile=.env, intentionally rejected.
  Sabrina's standard OLD install is blocked before staging. Even installing helper3
  does not make it supported. A reviewed coordinated env-file solution is still needed;
  never remove it blindly or drop secrets to force readiness green.

R4 — Read-only version/type ownership checks trust installed root-owned code and
  administrator-controlled ancestors, not a cryptographic vendor signature. Changes
  by root between checks are outside the threat model. Real-host sudo authorization,
  actual installed unit, full dependency resolution and publication were not exercised.

R5 — Final late request-reader hardening has targeted GREEN evidence. The completed
  full-pre-final.xml precedes that late source change. No full-suite acceptance for
  the exact final tree is claimed; another standalone offline full run is required.
  The completed full run still had the same four baseline failures; none accepted
  merely because they were pre-existing.
