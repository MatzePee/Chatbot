# Environment parity — supported standard activation path

This isolated implementation supersedes the EnvironmentFile blanket refusal in
helper-readiness-review.md F2/R2/R3 and helper-acceptance.md step4. It is NOT a
production deployment, publication, or six-owner GUI/file acceptance.

## Supported path and unavoidable administrator prerequisite

The distributed deploy/fanvue-chatbot.service and deploy/install.sh use a fixed
nonroot User/primary Group, WorkingDirectory=repository, mandatory
EnvironmentFile=repository/.env, and .venv/bin/python -m uvicorn app.main:app.
That standard case is now supported, including Environment= assignments resolved
by systemd before EnvironmentFile. The helper NEVER parses or sources that file.
It captures the actual service PID's original exec environment from /proc.

The ACTUALLY INSTALLED root-owned supervisor AND wrapper must be version/protocol4.
Helpers1/2/3, missing files, writable/incorrect owners, symlinks and mutable trusted
ancestors remain blocked by app/helper_readiness.py. Source version is not evidence
of installed capability. Sabrina cannot upgrade that root helper using the OLD GUI
updater. This one-time administrator step is unavoidable; no new sudo action or
arbitrary-command endpoint has been added:

1. Parent/administrator independently reviews this release and its hashes. Place
   installer and templates in a separate root-owned tree whose ancestors are not
   writable by the service account. Do not blindly sudo from the mutable app tree.
2. From that reviewed release directory, substitute the actual discovered unit:
      sudo bash deploy/install-update-helper.sh --check --service ACTUAL_UNIT
      sudo bash deploy/install-update-helper.sh --service ACTUAL_UNIT
   This backs up helper files and checks permissions; it does not restart the app.
   Never remove EnvironmentFile or rewrite secrets to get a green readiness flag.
3. Install the user-published application release through the normal GitHub GUI.
   Read the actual helper readiness in the character-upgrade card. Only after the
   parent has completed remaining release/data-writer acceptance should an operator
   confirm the character name and invoke the existing fixed restart action.

## Implemented evidence chain

Before status writes/service stop, restart validates the configured start, actual
MainPID argv, resolved executable, actual cwd, all UID/GID slots, and actual implicit
supplementary groups against NSS. It captures /proc environ with duplicate/malformed
entry checks and start-time race checks. PID/unit/environment are reread before stop.

Opaque .env, data/autoposter.env and FragmentPath bytes, inode/owner/mode/timestamps
are fingerprinted. Files must unambiguously predate the process exec tick; otherwise
restart normally under operator control before staging migration. Missing .env for
mandatory EnvironmentFile, symlinks, changed files, drop-ins, pending daemon reload,
manager DefaultEnvironment, PAM, Pass/UnsetEnvironment, custom Group, supplementary
Group directives, chroot/root image, enumerated mount/user namespaces and systemd
credentials safely refuse. This is a limited standard-unit contract, not universal
systemd compatibility. Manager defaults are refused because they can change live
independently of unit-file timestamps. Drop-ins require coordinated review.

A read-only configuration probe executes the FIXED app.character_upgrade --inspect
entrypoint only after dropping to the service UID/GID/groups and using its captured
environment/cwd. Root never imports/executes mutable application Python as root.
The probe uses the actual autoposter.config Settings factory, including pydantic's
environment > data/autoposter.env > default precedence, source containment checks,
and config/key hash comparison in the stopped child before copy/publication.
AutoChat's systemd uvicorn entrypoint does not load .env with python-dotenv.
start.py does load_dotenv(override=False), but is NOT this supported ExecStart.
Python-dotenv interpolation/null semantics must never substitute for systemd syntax.

Actual environment values, including empty strings, are passed unchanged; absent
variables remain absent. No secret/config values or config digests are logged by
the broker. The in-memory attestation is not an HTTP/CLI input. Plain environment=
remains an internal old-test seam only and cannot bypass attestation for a standard
EnvironmentFile service. The stopped child receives captured groups and only the
additional inherited stopped-source descriptor and expected-config digest.

After stop, the helper requires inactive/dead, MainPID=ControlPID=0 and unchanged
unit/configuration before source scanning/child launch. The existing root-only
capability, nonroot copy/conversion, source checks, authoritative active pointer,
post-publication crash handling and no stale-data code rollback remain intact.
Custom AutoChat/export locations and external/reserved/symlink DB/media/data paths
refuse rather than copying guessed stores. Configured local AutoPost sources are
resolved by the real Settings factory, not a filename guess.

## Test boundaries and residual risks

Evidence/environment-parity contains real RED/GREEN XML and the standalone full
run. ENVIRONMENT-PARITY-COMPLETION.json is authoritative for counts/hashes and exact
comparison to helper-readiness/full-pre-final.xml (four named prior failures).
No late executable changes may be represented as covered by an earlier full run.

Real: nonroot processes and /proc, UID/GID/groups, fixed argv/executable/cwd,
read-only config resolution, stopped child migration, SQLite copies, original
file hashes, app startup/health payload and existing rollback/crash tests.
Synthetic transport: systemctl show/stop/start, installed fixture sudo/visudo and
health HTTP transport. A sleeping synthetic uvicorn module supplies the real
pre-stop PID without starting a worker; actual app startup is separately exercised
with TestClient/lifespan after migration. Thus this is not a real systemd manager
integration test and does not independently prove systemd's EnvironmentFile parser.
The service metadata follows the actual bundled unit; captured env is the authority,
not a hand-written approximation of its parser. No network/provider/production/SMB
or real service actions and no package rebuild/publication occurred.

Residual: /proc scans/file fingerprint comparisons do not atomically exclude an
external writer or transient/ABA changes after the last check; parent must review
and quiesce external writers. No full power-loss matrix or arbitrary namespace
compatibility is claimed. Remaining four baseline test failures, independent parent
review, broader six-owner GUI/account-key/file acceptance and actual distribution/
deployment verification remain release gates. GUI-only ACTUAL readiness for an old
installed helper is explicitly false until the administrator prerequisite is met.
