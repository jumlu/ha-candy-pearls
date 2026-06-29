# Süßperlen Harness

> AI-powered candy reward system for kids over Signal — the model handles conversation and pricing, Home Assistant stays the bank. Configurable LLM (Claude by default).

Kids earn and spend "pearls" (Perlen) as a proxy currency for sweets. They message a Signal group, the AI figures out the price (or asks), and Home Assistant atomically debits the balance. No pearl ever leaves without HA saying so.

---

## How it works

```
Signal group → signal-cli-rest-api → signal_websocket → HA sensor
    → HA automation (thin forwarder) → POST /inbound (this add-on)
        → Claude (tool-use loop) → HA REST API (read/write balance & prices)
        → Signal /v2/send (reply to group)
```

The AI **proposes** a price, waits for a "ja" from the kid, then **books** via Home Assistant. HA is the only thing that touches the balance — Claude just calls tools.

**Conversation memory** (SQLite under `/data/`) lets the AI understand corrections across turns: "nee, zwei Maoam" after a first proposal works correctly.

---

## Terminology note

Since Home Assistant 2026.2 the UI calls add-ons **"Apps"** (menu: Settings → Apps → App Store). This is purely a rename — the underlying Supervisor API, `config.yaml` schema, and everything else is unchanged. In code and file names you'll still see "add-on" (technically correct). This README uses the new "App" term so you're not confused by the UI.

---

## Prerequisites (already in place)

- **signal-cli-rest-api** add-on (`bbernhard/signal-cli-rest-api`) — **hard dependency**, see note below
- **signal_websocket** HACS integration → exposes a `sensor.signal_<your number>` entity
- One `input_number` helper per child (0 up to that child's `max_balance`) — see "Adding children" below
- `input_text.perlen_preise` (JSON string, shared price list across all children)

**Deactivate / delete the old large Gemini automation** — this add-on replaces it.
**Also delete any standalone "daily top-up" automation** — the add-on now handles
the daily refill itself, per child, driven by the `daily_refill` / `max_balance`
config fields (see below). No separate `input_datetime` reset-guard helper is
needed any more; restart-safety is tracked internally.

### Signal dependency

The HA Supervisor has no mechanism for one local add-on to declare a hard
dependency on another and trigger its auto-install — `config.yaml` simply
doesn't support that. Instead, this add-on handles it at the network level:

- The Signal endpoint is fully configurable via the **`signal_api_url`** option
  (default `http://127.0.0.1:8090`, i.e. `bbernhard/signal-cli-rest-api` on the
  same host via `host_network: true`). Point it elsewhere if you run Signal on
  a different host/port.
- At startup, and on every `GET /health` call, the app pings
  `{signal_api_url}/v1/about`. If unreachable, it logs a clear warning naming
  the missing add-on and keeps retrying every 30s in the background — it does
  **not** crash, since Signal may simply still be starting up.
- If you see `signal-cli-rest-api NOT reachable` in the add-on log: install
  and start `bbernhard/signal-cli-rest-api` first, or fix `signal_api_url`.

---

## Installation

1. **Settings → Apps → App Store** (formerly "Add-ons") → **⋮ → Repositories** → add:
   ```
   https://github.com/jumlu/ha-candy-pearls
   ```
2. Find **"Süßperlen Harness"** in the App list and install it.
3. Open the app's **Configuration** tab and fill in:
   - `anthropic_api_key` — your Anthropic API key
   - `ha_token` — a Long-Lived Access Token (HA Profile → Security → Long-lived access tokens)
   - `signal_number` — your sending Signal number
   - `whitelist_uuids` — UUID(s) allowed to set/delete prices (find a sender's UUID in
     `sensor.signal_<number>` → `attributes.full_envelope.sourceUuid` after they send one message)
   - `accounts` — one entry per child, see **Adding children** below
   - Optionally change `model` (default: `claude-haiku-4-5-20251001`)
4. **Start** the app.
5. Add the HA automation and REST command below.
6. Test: write "ein maoam" in a configured child's Signal group.

### Adding children

All child-specific data — Signal group IDs, names, daily allowance, balance
cap — lives **only** in the app's Configuration tab (Supervisor stores it in
`/data/options.json` on your HA host). None of it is in this git repo; the
shipped `config.yaml` defaults to an empty `accounts: []` for exactly that
reason. Add one entry per child:

```yaml
accounts:
  - name: "<child's first name>"                          # display name, used in prompts/logs
    recv_group_id: "<envelope groupId from sensor.signal_...>"
    send_group_id: "group.<base64 id accepted by /v2/send>"
    balance_entity: "input_number.<your_chosen_id>"        # create this helper first (Settings → Devices & services → Helpers)
    daily_refill: 3                                        # pearls added once per day
    max_balance: 5                                          # balance never exceeds this
```

To find `recv_group_id`: send any message in the child's Signal group and read
`sensor.signal_<number>` → `attributes.full_envelope.dataMessage.groupInfo.groupId`.
To find the matching `send_group_id` (used for `/v2/send`), check the
signal-cli-rest-api `/v1/receive/<number>` or `/v1/groups/<number>` response —
it's the same group, formatted as `group.<id>`.

When you create the `input_number` helper, set its slider `min: 0` and
`max:` at least `max_balance` — the add-on enforces the actual cap in code, the
helper's own max is just for a sane UI display.

### Why `host_network: true`?

The add-on needs to reach two services on the host:
- `127.0.0.1:8090` — the signal-cli-rest-api add-on (host loopback)
- `http://supervisor/core` — the HA Supervisor proxy (already reachable from add-on network, but host_network keeps things simple)

Without host network the Signal URL would need to be changed to the host's LAN IP.

---

## HA-side config (add to configuration.yaml / automations)

### REST command

Add to `configuration.yaml`:

```yaml
rest_command:
  suessperlen_inbound:
    url: "http://127.0.0.1:8099/inbound"
    method: POST
    content_type: "application/json"
    payload: >-
      {"group_id": {{ group_id | to_json }},
       "sender_uuid": {{ sender_uuid | to_json }},
       "sender_name": {{ sender_name | to_json }},
       "text": {{ text | to_json }}}
```

### Thin forwarder automation

```yaml
alias: "Süßperlen: Inbound → Harness"
mode: queued
max: 15
triggers:
  - trigger: state
    entity_id: sensor.signal_<your_number>   # the signal_websocket sensor for your sending number
conditions:
  - condition: template
    value_template: >
      {{ trigger.to_state.attributes.full_envelope.dataMessage.message | default('') | trim | length > 0 }}
actions:
  - action: rest_command.suessperlen_inbound
    data:
      group_id: "{{ trigger.to_state.attributes.full_envelope.dataMessage.groupInfo.groupId }}"
      sender_uuid: "{{ trigger.to_state.attributes.full_envelope.sourceUuid | default('') }}"
      sender_name: "{{ trigger.to_state.attributes.full_envelope.sourceName | default('jemand') }}"
      text: "{{ trigger.to_state.attributes.full_envelope.dataMessage.message }}"
```

The existing `rest_command.signal_perlen_send` / notify setup is no longer needed (the add-on sends replies directly via `/v2/send`), but can stay as a manual fallback.

---

## Add-on configuration reference

| Option | Default | Description |
|--------|---------|-------------|
| `anthropic_api_key` | *(required)* | Anthropic API key |
| `ha_token` | *(required)* | HA Long-Lived Access Token |
| `ha_base_url` | `http://supervisor/core` | HA REST base URL |
| `model` | `claude-haiku-4-5-20251001` | Any Anthropic model string |
| `max_tokens` | `1024` | Max tokens per Claude response |
| `memory_turns` | `10` | Max conversation turns to remember |
| `memory_minutes` | `15` | Max age of turns to include |
| `signal_api_url` | `http://127.0.0.1:8090` | signal-cli-rest-api base URL |
| `signal_number` | *(empty — required)* | Sending Signal number |
| `prices_entity` | `input_text.perlen_preise` | HA entity holding JSON price list |
| `whitelist_uuids` | `[]` | UUIDs allowed to set/delete prices |
| `accounts` | `[]` | List of children — see **Adding children** above |

Per-account fields inside `accounts`:

| Field | Description |
|-------|-------------|
| `name` | Display name used in prompts/logs |
| `recv_group_id` | Envelope `groupId` — identifies inbound messages from this child's group |
| `send_group_id` | `group.<id>` form used by `/v2/send` for replies |
| `balance_entity` | HA `input_number` entity holding this child's balance |
| `daily_refill` | Pearls added once per local day |
| `max_balance` | Balance never exceeds this (also caps single-item price estimates) |

**Adding another child:** just add an entry to the `accounts` list in the app
config — no code changes needed. All personal data (group IDs, names) stays
in your local HA config, never in this repo.

---

## Architecture notes

- **Pricing rule:** 1 pearl = 5 g sugar. Unknown products → `ceil(sugar_g / 5)`, clamped between 1 and that child's `max_balance`.
- **Proposal flow:** AI always proposes first (`propose` tool) → waits for confirmation → then `book`.
- **Atomic booking:** read balance → check coverage → set balance. If not covered, returns `insufficient` — no partial debit.
- **Per-group serial lock:** rapid messages from the same group are queued, preventing race conditions on the balance.
- **Memory window:** last N turns *and* last M minutes — whichever is more restrictive. Prevents stale context from hours ago leaking in.
- **Open proposal timeout:** proposals expire after 5 minutes if not confirmed (TODO: make configurable).
- **Daily refill:** a background task (`app/refill.py`) checks every 10 minutes whether each child's local calendar day has changed; if so it tops up `daily_refill` pearls, capped at `max_balance`, and records the date in the add-on's own SQLite store. Restart-safe by design — no separate HA helper needed.

---

## Privacy / what's in this repo vs. your HA instance

This repository contains **no phone numbers, Signal group IDs, or child
names** — `suessperlen/config.yaml` ships with `accounts: []`,
`whitelist_uuids: []`, and an empty `signal_number`. All of that is entered
once through the app's Configuration tab after install, and lives only in
`/data/options.json` on your Home Assistant host (not in git, not on GitHub).
If you fork this repo, double-check before pushing that you never hardcode
real values back into `config.yaml` — they belong in the Supervisor config
only.

---

## Known TODOs

- **Photo pricing:** `attachment_path` is forwarded to the handler but Vision is not yet wired up. The Signal envelope structure for image attachments needs to be verified against a real photo first.
- **Proposal timeout** as a config option (currently hardcoded to 5 minutes in `memory.py`).
