# Changelog

All notable changes to this project will be documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The add-on changelog shown in the HA UI lives at `candy-pearls/CHANGELOG.md`.
This file is the repository-level changelog and mirrors it with additional detail.

---

## [0.1.6] — 2026-06-30

### Changed
- Added `homeassistant_api: true` to `config.yaml`. The Supervisor now injects
  a `SUPERVISOR_TOKEN` env var that the app uses as its HA bearer token — no
  user-created Long-Lived Access Token required.
- Removed `ha_token` (password field) and `ha_base_url` from `options`,
  `schema`, and both translation files. The HA base URL is always
  `http://supervisor/core` and requires no user input.
- `config.py`: `load_settings()` now reads `os.environ["SUPERVISOR_TOKEN"]`
  (with `HA_TOKEN` env var fallback for local dev); `ha_base_url` defaults to
  `http://supervisor/core` via `HA_BASE_URL` env var.
- `DOCS.md` and root `README.md` updated: removed the LLAT creation step from
  installation and the token rows from the configuration reference table.

---

## [0.1.5] — 2026-06-30

### Added
- `candy-pearls/README.md` (App Store blurb) and `candy-pearls/DOCS.md`
  (full Documentation tab) — required by the official app repository layout.
  Confirmed against `home-assistant/addons-example`: every app folder needs
  its own `README.md` and `DOCS.md`, distinct from the repo-root README.
- `accounts` translations restructured to use the documented `fields:`
  sub-key (one name/description per nested field) instead of a single
  flattened paragraph, matching the pattern used for nested list/object
  config in the official docs and example repo.

### Audited (no change needed)
- `icon.png` / `logo.png` — official example app ships non-square,
  non-128px assets (238×250 and 725×250), so our 256×256 / 512×512 PNGs
  are within accepted norms.
- `host_network: true` — flagged by the Security docs as a general
  anti-pattern ("Don't run on host network"), but kept intentionally: the
  default `signal_api_url` (`http://127.0.0.1:8090`) targets the
  signal-cli-rest-api app's host-loopback binding, which is only reachable
  with host networking. This tradeoff is already documented in the
  repo-root README ("Why `host_network: true`?"). Disabling it by default
  would break Signal connectivity for most installs; left unchanged pending
  a decision on switching the default to inter-app Docker networking.

---

## [0.1.4] — 2026-06-30

### Fixed
- Translation files (`translations/en.yaml`, `translations/de.yaml`) had a
  `network` section formatted as a nested object (`8099/tcp: {title, description}`)
  instead of the required flat string (`8099/tcp: "description"`). The Supervisor
  rejects the whole translation file when this validation fails, which silently
  discarded all `configuration` descriptions too — confirmed via Supervisor log:
  `Can't read translations from .../translations/en.yaml - expected str for
  dictionary value @ data['network']['8099/tcp']`. Fixed by flattening it.

---

## [0.1.3] — 2026-06-30

### Added
- `candy-pearls/icon.png` (256×256) and `candy-pearls/logo.png` (512×512) —
  a glossy candy-pearl cluster icon/logo, shown in the HA add-on store list
  and on the add-on detail page.

---

## [0.1.2] — 2026-06-30

### Added
- `translations/en.yaml` and `translations/de.yaml` — UI descriptions for every
  configuration option, including direct links to https://console.anthropic.com/
  and step-by-step instructions for HA long-lived token creation and Signal setup.
  The HA Configuration tab now renders in English or German based on the browser
  / HA language setting.
- `model` changed from free-text to a dropdown: `claude-haiku-4-5-20251001`,
  `claude-sonnet-4-6`, `claude-opus-4-8`, `claude-fable-5`.
- `timezone` changed from free-text to a dropdown of ~65 common IANA timezones
  (covers Europe, Americas, Asia, Africa, Oceania).
- Port description added to `ports_description` shown in the Network tab.

---

## [0.1.1] — 2026-06-30

### Fixed
- Dockerfile now uses `python:3.13-alpine` from Docker Hub. The HA Supervisor
  does not pass `BUILD_FROM` as a build arg for local device builds — that
  mechanism only applies to the HA cloud publisher. Using a base image with no
  default caused an empty `FROM` and a failed build; `python:3.13-alpine` is
  always available and needs no HA registry.
- Removed `build.yaml` (Supervisor ignores it for local builds) and `bashio`
  dependency from `run.sh` (app reads `options.json` directly).
- `candy-pearls/CHANGELOG.md` added in the add-on subdirectory so the HA UI
  can display it (root-level `CHANGELOG.md` is not read by the Supervisor).

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
  strings, context block labels, and full system prompts are localised.
- **Configurable pricing** — `sugar_per_pearl` (default `5` g).
- **Confirmation mode** — `require_confirmation` (default `true`); when `false`
  the AI books immediately without a propose → confirm step.
- **Signal dependency check** — startup ping with background retry; `/health`
  reports `signal_reachable` and `refill_task_alive`.
- **Timezone-aware daily refill** — `timezone` option (e.g. `Europe/Berlin`).

### Security
- Admin whitelist check uses the HA-verified Signal sender UUID, never a
  Claude-supplied value — closes a prompt-injection bypass.
- `_book` restricted to the account owning the current group; cross-account
  debit not possible.
- `_book` validates product and pearl count against the parked proposal before
  debiting (when `require_confirmation: true`).

### Fixed
- Per-entity asyncio lock shared by inbound path and `refill.loop` prevents
  balance read-modify-write races.
- SQLite writes serialised through a module-level `asyncio.Lock`.
- Memory only written after a successful Signal send.
- `refill.loop` task handle stored and cancelled on shutdown; outer
  `except Exception` prevents silent task death.
- Context block snapshots not stored in memory — replayed history never
  contains stale balance/price figures.
