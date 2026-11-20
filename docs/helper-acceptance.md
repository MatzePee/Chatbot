# Installed-helper readiness — isolated implementation, NOT release approval

UPDATE: The historical helper3 EnvironmentFile refusal and step4 below are
superseded by docs/environment-parity.md. Standard EnvironmentFile is supported by
helper4/protocol4 with live-process/config attestation. The administrator prerequisite
remains mandatory for OLD installed helpers; no GUI-only bootstrap is claimed.

No production/SMB/provider/GitHub operations or real service control were performed.
Only Matze publishes; the parent retains deployment. This is not a deployable release.

## Sabrina's existing GitHub GUI updater: precise boundary

The OLD installed root-owned wrapper can schedule the existing fixed updater and
restart/reboot. Its updater prepares/checks out application code and a separate
virtualenv as the service account. It does NOT replace /usr/local/bin/fanvue-admin,
/usr/local/libexec/mp-creatorstudio-update or sudoers. The old restart only invokes
systemctl restart, not the new stopped-source broker. Publishing new source is NOT
privileged helper installation. No safe GUI-only privileged bootstrap exists in that
old capability set. No arbitrary-command endpoint or automatic sudoers installation
from the writable app tree has been added.

Sabrina is NOT GUI-only activation ready on such installations. A code-only update
remains legacy-by-default; character migration is a separate explicit confirmed stage.
The normal character-upgrade status exposes helper.ready, installed_version,
required_version and a controlled actionable message. POST rechecks and returns 409
BEFORE writing migration intent if the installed helper is missing/old/untrusted or
its service contract refuses. The actual update card disables staging and shows the
admin prerequisite; an intervening POST refusal is also displayed without a restart.
Cancel remains available; active-pointer selection and source-only code updates are
not reversed or silently re-migrated.

## One-time administrator prerequisite (not executed here)

1. Obtain an independently reviewed release; inspect and place its installer/templates
   in an administrator-controlled, root-owned tree, not a service-user-writable app
   checkout. Verify the release hashes through the parent. Do NOT blindly sudo a
   script downloaded into the app's mutable tree.
2. From that reviewed root-owned release directory, using the ACTUAL discovered unit:
   `sudo bash deploy/install-update-helper.sh --check --service ACTUAL_UNIT`
   `sudo bash deploy/install-update-helper.sh --service ACTUAL_UNIT`
   The existing installer discovers user/repository/port and performs backup and
   permission checks. It does not restart the running app. No new sudo actions.
3. Helper protocol/version must BOTH be 3. Version is read from the ACTUALLY INSTALLED
   root-owned supervisor, not from deploy/ in the newly downloaded application.
   Missing, stale, symlinked, wrong-owner, writable files or mutable ancestors block.
   The pinned installed supervisor's read-only readiness command runs unprivileged
   using /usr/bin/python3 -I and a passed file descriptor, never via sudo.
4. Have the administrator review service configuration. This bounded implementation
   deliberately REFUSES all nonempty Environment, EnvironmentFiles, PassEnvironment,
   UnsetEnvironment, PAMName, RootDirectory/RootImage, ExecSearchPath,
   SupplementaryGroups and manager DefaultEnvironment; DynamicUser and custom Group
   also refuse. The standard deploy/fanvue-chatbot.service HAS EnvironmentFile=.env
   and is therefore explicitly BLOCKED, even after helper installation. Do NOT just
   remove it: secrets/configuration must first be preserved by a separately reviewed
   coordinated configuration/migration plan, or an audited environment loader must
   be implemented. This slice does not provide that plan or claim standard-unit readiness.
5. Only supported fixed `.venv/bin/python -m uvicorn app.main:app` starts are accepted;
   --env-file/--app-dir/--factory overrides refuse. The service must be running and
   its /proc environment readable to attest PATH/env. Failure to inspect is blocked.
   Additional service namespace/resource customizations have NOT been exhaustively
   accepted: see helper-readiness-review.md before ANY production activation.

## Activation environment and data behavior

For the accepted conservative unit subset, the root restart broker captures the real
service PID's original /proc environment/PATH BEFORE stopping; it does not inherit
the root/systemd-run environment for copying. It checks service properties before
status writes/stop and checks them again after stop. The migration child has the
fixed non-root service identity, fixed interpreter/cwd and captured environment plus
only the inherited stopped-source authority FD. A direct stopped migration without
captured environment refuses rather than guessing. The Python environment= argument
is an internal synthetic-test injection seam, NOT an HTTP/CLI capability.

AutoPost source DB/media are selected by the actual application configuration under
that environment, not guessed filenames. Custom AutoChat data locations refuse
instead of copying root/data/bot.db while the app uses another store. Source symlinks
and noncanonical paths are rejected before copying; reserved releases/backups remain
unsupported. Root-owned broker checks stopped state and visible open FDs; child
conversion still checks visible/same-UID writers and preserves copies and secrets.
These scans are NOT an atomic guarantee against newly launched external writers.

The active receipt remains authoritative after a child crashes after publication.
A synthetic actual exit(23) after the pointer, subsequent active write and later
code-update rollback are exercised. Automatic rollback changes code/environment,
NEVER restores stale data or removes the active pointer. No reverse migration after
active writes. Full power-loss/startup failure matrix remains incomplete.

## Evidence and remaining acceptance

See evidence/helper-readiness/test-comparison.json, targeted.xml, full-initial.xml,
full-final.xml, RED/GREEN XML/logs, browser logs and the completion JSON for actual
counts/hashes. Service manager, visudo/sudo authorization and HTTP transport are
mocked boundaries; real UIDs, installed root-owned temporary files, /proc, SQLite,
local Git, venv/pip and app lifespans run offline under `unshare -n`.

The initial standalone full run completed with 857 passed / 4 failed. Exact comparison
confirms both root-dependent repair fixtures resolved, without weakening root guards;
no blanket acceptance of the old six failures. The four remaining failures and all
broader six-owner UI/account-key/file-isolation acceptance gates are still blockers.
The final machine-readable comparison is authoritative for subsequent test counts.

Independent-in-context source review of inherited broker/capability code is recorded
with line references in helper-readiness-review.md. No nested reviewer was spawned;
parent independent review and real deployment/distribution acceptance remain required.
