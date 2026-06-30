# Changelog

All notable changes to this project will be documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [0.1.0] — 2026-06-30

Initial release of Candy Pearls as a public, generic Home Assistant add-on.

### Added
- **Core harness** — FastAPI webhook (`POST /inbound`) receives Signal messages
  forwarded by a thin HA automation, runs a Claude tool-use loop, and replies via
  signal-cli-rest-api.
- **HA as the bank** — all balance reads and writes go through HA REST API
  (`input_number` helpers); the AI never computes or stores balances itself.
- **Per-group conversation memory** — SQLite under `/data/memory.db`; only raw
  message text is stored (no stale context snapshots), bounded by `memory_turns`
  and `memory_minutes`.
- **Per-child account config** — `accounts` list in the add-on configuration;
  each entry maps a child's Signal group to their HA balance entity and sets
  `daily_refill` and `max_balance`.
- **Daily refill** — background asyncio task tops up each child's balance once
  per local calendar day, capped at `max_balance`; restart-safe via SQLite
  last-refill-date tracking; no separate HA automation or helper needed.
- **Internationalisation** — `language` option (`en` / `de`); all user-facing
  strings, context block labels, and full system prompts are localised; tool
  definitions stay in English (model-facing API metadata).
- **Configurable pricing** — `sugar_per_pearl` (default `5` g) sets the
  sugar-to-pearl conversion used for unknown products.
- **Confirmation mode** — `require_confirmation` (default `true`); when `false`
  the AI books immediately without a propose → confirm step.
- **Signal dependency check** — startup ping to `signal_api_url/v1/about` with
  background retry and `/health` reporting; actionable log warning if unreachable.
- **Timezone-aware daily refill** — `timezone` option (e.g. `Europe/Berlin`);
  uses `zoneinfo` so families in non-UTC timezones get their refill at local
  midnight.
- **`GET /health`** — reports `signal_reachable` and `refill_task_alive`.
- **`build.yaml`** — supplies the correct HA base-python:3.13 image per arch
  so the Supervisor Docker build works without a hardcoded Dockerfile default.

### Security
- Admin whitelist check uses the HA-verified Signal sender UUID (`ctx.sender_uuid`),
  never a Claude-supplied value — closes a prompt-injection bypass.
- `_book` is restricted to the current group's account; cross-account debit not
  possible.
- `_book` validates product name and pearl count against the parked proposal
  (when `require_confirmation: true`) before debiting.

### Fixed
- Per-entity asyncio lock (`locks.py`) shared by the inbound request path and
  `refill.loop` prevents read-modify-write races on balances.
- SQLite write functions are async and serialised through a module-level
  `asyncio.Lock`.
- Memory is only written after a successful Signal send; a failed delivery no
  longer poisons conversation history.
- `refill.loop` task handle is stored and cancelled on shutdown; an outer
  `except Exception` prevents silent task death from transient errors.
- Context block snapshots (balance, price list) are injected into the current
  Claude call only — not stored in memory, so replayed history never contains
  stale figures.
