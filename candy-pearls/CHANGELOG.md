## 0.1.3 — 2026-06-30

### Added
- `icon.png` and `logo.png` — a candy-pearl cluster icon shown in the HA
  add-on store list and detail page.

---

## 0.1.2 — 2026-06-30

### Added
- UI descriptions and links for every configuration option (translations/en.yaml and translations/de.yaml).
  The HA Configuration tab now shows a description for each field, including links
  to the Anthropic console and instructions for creating HA long-lived tokens.
- Configuration tab language follows the add-on `language` setting (English / German).
- `model` is now a dropdown (Haiku / Sonnet / Opus / Fable) instead of a free-text field.
- `timezone` is now a dropdown of ~65 IANA timezones instead of a free-text field.
- Port description shown in the Network tab.

---

## 0.1.1 — 2026-06-30

### Fixed
- Build now uses `python:3.13-alpine` from Docker Hub. The previous HA base image
  tag (`amd64-base-python:3.12`) does not exist, causing the Docker build to fail
  on installation.
- Removed `bashio` dependency from the startup script — the app reads its
  configuration directly from `/data/options.json`.

---

## 0.1.0 — 2026-06-30

Initial release.

### Added
- Per-child Signal group accounts with configurable `daily_refill` and `max_balance`.
- AI-powered price lookup and booking via Claude tool-use loop.
- Conversation memory per group (SQLite); corrections across turns work correctly.
- Daily pearl refill — runs inside the add-on, no separate HA automation needed.
- Confirmation mode (`require_confirmation`): on by default; disable to book immediately.
- Configurable sugar-to-pearl ratio (`sugar_per_pearl`, default 5 g).
- Language support: English (`en`) and German (`de`).
- Timezone-aware refill scheduling (`timezone` option).
- Signal dependency check at startup with background retry.
- `/health` endpoint reporting signal reachability and refill task status.

### Security
- Admin price changes verified against the HA-verified Signal sender UUID, not a
  Claude-supplied value (closes prompt-injection bypass).
- Booking restricted to the account that owns the current group.
- Balance debit validated against the open proposal before execution.
