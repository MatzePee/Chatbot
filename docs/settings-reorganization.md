Settings reorganization — offline candidate

Scope
  Gemeinsam contains installation AI keys, all nine named tasks (including conversation memory), technical
  generation options, budget/cost overview, character management, notification
  transport, update/system controls and the distinct base URLs.
  Fanvue connection credentials belong to AutoChat for the selected character;
  AutoPost reuses only that character's connection. Channel publication tokens
  remain separate from installation notification transport. x_api_tier stays owned.
  Chat vision_model and post openrouter_model_vision are never merged.

Existing active installations: explicit promotion BEFORE new-code activation
  Merely expanding the scope allowlist does not safely preserve old owner rows.
  Parent/operator must stop all app processes, workers and other database writers,
  take a consistent data backup, and run the tool against the explicitly selected
  active generation root. This source-only baseline is NOT a data backup.

  Read-only default (repeat --source for ALL six real owners; use actual paths):
    unshare -n ../clean-release-venv/bin/python -B -m tools.settings_consolidate \
      --active-generation-root /explicit/stopped/active-generation \
      --destination bot.db \
      --source OWNER_A=characters/OWNER_A/bot.db \
      --source OWNER_B=characters/OWNER_B/bot.db

  Resolve every reported conflict using --select FIELD=OWNER_ID. Do not choose
  an arbitrary character. Exact task IDs need no provider validation. Never use
  the supplied nonsensitive production snapshot as permission to access production.

  The tool cannot infer that a legacy global row was deliberately configured
  rather than seeded as an inert default. Only explicit clear markers, earlier
  tool promotion intent markers, or operator --intentional-canonical FIELD
  acknowledgements retain existing canonical values. Use that flag per field
  only after inspecting the stopped installation locally.

  Explicit apply uses the SAME arguments, selected sources and intentional fields:
    ... --apply --acknowledge-stopped-deployment

  Destination writes are one global-only SQLite transaction. Original owner DBs
  are never opened writable; their settings are preserved. Read-only preflight
  and reports contain field names/source IDs, never settings or credentials.
  Apply refuses unknown fields/owners, path traversal, symlinks, inode/hard-link aliases, changed inputs,
  unacknowledged running deployment, and unresolved field conflicts. The stopped
  acknowledgement is an operator assertion, not a service-stop implementation.
  Missing fields are reported and are NOT synthesized from another task/model.
  No account, schema or credential migration is automatically performed.

Legacy/single-character/Sabrina
  Optional existing LAN login behavior remains unchanged. New common AI saves
  require admin when authentication is enabled. Preview mutations remain denied.
  Existing single-character rows stay readable in the original canonical bot.db;
  already-active multi-character installations require the promotion above before
  activation. This work does not prove a live Sabrina GUI update/deployment.

Verification and limitations
  See ../evidence/settings-reorg for strict RED/GREEN, real assembled ASGI tests
  without lifespan, synthetic real-Chromium layouts and saves, and full-suite XML.
  Browser HTTP is fulfilled from synthetic fixtures under unshare -n; no workers,
  providers or live services are started. Screenshots are generated, not visually
  approved by this CLI. Parent must inspect screenshots and executable changes.
  Installation cost aggregation currently covers persisted AutoPost LlmUsage;
  it does not invent historical AutoChat costs that are not recorded there.
  The one configured AutoPost budget threshold still applies per character, not
  as a combined account-wide spending cap. UI and API explicitly state this;
  implementing a full combined AutoChat/AutoPost ledger is outside this change.
  The global AutoChat notification enable switch is in Gemeinsam/Telegram;
  owner notification event/keyword choices remain in AutoChat.
  No deployment, service restart, production verification or publishing occurred.
