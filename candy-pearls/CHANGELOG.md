## 0.1.5 — 2026-06-30

### Added
- `candy-pearls/README.md` and `candy-pearls/DOCS.md` — required by the
  official app repository structure (confirmed against
  `home-assistant/addons-example`); previously only a repo-root README.md
  existed, so the App Store and Documentation tab had no app-level content.
- `accounts` configuration translations now use the documented `fields:`
  sub-structure (per-field name/description for `name`, `recv_group_id`,
  `send_group_id`, `balance_entity`, `daily_refill`, `max_balance`) instead
  of one flat paragraph.

### Verified
- All `translations/*.yaml` `configuration` keys now checked to exactly
  match `config.yaml`'s `schema` keys (automated check, no drift).
- `icon.png` / `logo.png` dimensions checked against the official example
  app (which itself uses non-square, non-128px assets) — current sizes are
  within accepted norms, no change needed.

---

## 0.1.4 — 2026-06-30

### Fixed
- `translations/en.yaml` and `translations/de.yaml`: the `network` section used
  a nested `title`/`description` object per port, but the Supervisor schema
  requires a flat string per port (`8099/tcp: "description"`). This caused
  Supervisor to reject the *entire* translation file silently — so none of the
  configuration option names/descriptions were shown, not just the network
  part. Fixed by flattening the `network` entry.

---

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
