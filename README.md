# Candy Pearls

> AI-powered candy reward system for kids over Signal — the model handles conversation and pricing, Home Assistant stays the bank. Configurable LLM (Claude by default).

Each child has a pearl balance as a proxy currency for sweets. **The child does not message Signal directly** — each child has a dedicated Signal group that their parents, grandparents, or other relatives write in on the child's behalf, reporting what the child wants or already received. The AI figures out the price (or asks), and Home Assistant atomically debits the right child's balance. No pearl ever leaves without HA saying so.

---

## How it works

```
Signal group (per child) → signal-cli-rest-api → signal_websocket → HA sensor
    → HA automation (thin forwarder) → POST /inbound (this add-on)
        → Claude (tool-use loop) → HA REST API (read/write balance & prices)
        → Signal /v2/send (reply to group)
```

Each Signal group belongs to exactly one child, but any relative in that group can message — the AI always knows whose balance to use from the group itself, regardless of who is typing. It tells the two apart in its context: the **account** (which child) vs. the **sender** (which relative is currently writing).

The AI **proposes** a price, waits for a "ja" from whoever is messaging, then **books** via Home Assistant. HA is the only thing that touches the balance — Claude just calls tools.

**Conversation memory** (SQLite under `/data/`) lets the AI understand corrections across turns: "nee, zwei Maoam" after a first proposal works correctly, even if a different relative sent the correction than the one who sent the original message.

---

## Terminology note

Since Home Assistant 2026.2 the UI calls add-ons **"Apps"** (menu: Settings → Apps → App Store). This is purely a rename — the underlying Supervisor API, `config.yaml` schema, and everything else is unchanged. In code and file names you'll still see "add-on" (technically correct). This README uses the new "App" term so you're not confused by the UI.

---

## Prerequisites

- **signal-cli-rest-api** add-on (`bbernhard/signal-cli-rest-api`) — install from the App Store first, see dependency note below
- **signal_websocket** HACS integration — exposes a `sensor.signal_<your_number>` entity for inbound messages
- One `input_number` helper per child (min 0, max = that child's `max_balance`) — create via Settings → Devices & services → Helpers
- One `input_text` helper for the shared price list (default entity: `input_text.perlen_preise`)

### Signal dependency

The HA Supervisor has no mechanism to declare a hard dependency between local add-ons. Instead this add-on checks at the network level:

- The Signal endpoint is fully configurable via **`signal_api_url`** (default `http://127.0.0.1:8090`, pointing at `bbernhard/signal-cli-rest-api` via `host_network: true`). Change it if you run Signal on a different host or port.
- At startup and on every `GET /health` call, the app pings `{signal_api_url}/v1/about`. If unreachable it logs a clear warning and retries every 30 s in the background — the app does **not** crash while Signal is starting up.
- If you see `signal-cli-rest-api NOT reachable` in the logs: install and start `bbernhard/signal-cli-rest-api` first, or correct `signal_api_url`.

---

## Installation

1. **Settings → Apps → App Store** (formerly "Add-ons") → **⋮ → Repositories** → add:
   ```
   https://github.com/jumlu/ha-candy-pearls
   ```
2. Find **"Candy Pearls"** in the App list and install it.
3. Open the app's **Configuration** tab and fill in:
   - `anthropic_api_key` — your Anthropic API key
   - `signal_number` — your sending Signal number (e.g. `+49123456789`)
   - `timezone` — your local timezone (e.g. `Europe/Berlin`) for correct daily refill timing
   - `whitelist_uuids` — UUID(s) allowed to add/change/delete prices (find a sender's UUID in `sensor.signal_<number>` → `attributes.full_envelope.sourceUuid` after they send one message)
   - `accounts` — one entry per child, see **Adding children** below
   - Optionally change `model` (default: `claude-haiku-4-5-20251001`)
4. **Start** the app.
5. Add the HA REST command and automation below.
6. Test: as a parent or other relative, send a candy name in one of the configured Signal groups.

### Adding children

Each child needs their own Signal group, containing whichever relatives (parents, grandparents, etc.) should be able to message on that child's behalf — there's no separate per-relative configuration; anyone in the group can write, and the AI always attributes the booking to the child who owns that group.

All child-specific data — Signal group IDs, names, daily allowance, balance cap — lives **only** in the app's Configuration tab (Supervisor stores it in `/data/options.json` on your HA host). None of it is in this git repo. Add one entry per child:

```yaml
accounts:
  - name: "<child's first name>"
    recv_group_id: "<envelope groupId from sensor.signal_...>"
    send_group_id: "group.<base64 id accepted by /v2/send>"
    balance_entity: "input_number.<your_chosen_id>"
    daily_refill: 3        # pearls added once per day
    max_balance: 5         # balance never exceeds this
```

**Finding `recv_group_id`:** have any relative send a message in the child's Signal group, then read  
`sensor.signal_<number>` → `attributes.full_envelope.dataMessage.groupInfo.groupId`.

**Finding `send_group_id`:** call the signal-cli-rest-api endpoint  
`GET {signal_api_url}/v1/groups/<number>` — it lists all groups with both IDs. The sendable form starts with `group.`.

**Daily refill:** the add-on tops up `daily_refill` pearls once per local calendar day (using `timezone`), capped at `max_balance`. No separate HA automation or helper is needed for this — it is handled internally and is restart-safe.

### Why `host_network: true`?

The app must reach signal-cli-rest-api at `127.0.0.1:8090` (host loopback), which
is only accessible when sharing the host's network namespace. The HA REST API is
accessed via the Supervisor's internal proxy (`http://supervisor/core`), which
doesn't require host networking — but signal-cli-rest-api does.

If you run signal-cli-rest-api on a different machine, set `signal_api_url` to its
address and you can disable `host_network: true`.

---

## HA-side config

### REST command

Add to `configuration.yaml`:

```yaml
rest_command:
  candy_pearls_inbound:
    url: "http://127.0.0.1:8099/inbound"
    method: POST
    content_type: "application/json"
    payload: >-
      {"group_id": {{ group_id | to_json }},
       "sender_uuid": {{ sender_uuid | to_json }},
       "sender_name": {{ sender_name | to_json }},
       "text": {{ text | to_json }}}
```

### Forwarder automation

```yaml
alias: "Candy Pearls: Inbound → Harness"
mode: queued
max: 15
triggers:
  - trigger: state
    entity_id: sensor.signal_<your_number>
conditions:
  - condition: template
    value_template: >
      {{ trigger.to_state.attributes.full_envelope.dataMessage.message | default('') | trim | length > 0 }}
actions:
  - action: rest_command.candy_pearls_inbound
    data:
      group_id: "{{ trigger.to_state.attributes.full_envelope.dataMessage.groupInfo.groupId }}"
      sender_uuid: "{{ trigger.to_state.attributes.full_envelope.sourceUuid | default('') }}"
      sender_name: "{{ trigger.to_state.attributes.full_envelope.sourceName | default('someone') }}"
      text: "{{ trigger.to_state.attributes.full_envelope.dataMessage.message }}"
```

---

## Configuration reference

| Option | Default | Description |
|--------|---------|-------------|
| `anthropic_api_key` | *(required)* | Anthropic API key |
| `model` | `claude-haiku-4-5-20251001` | Claude model — dropdown in the Configuration tab |
| `max_tokens` | `1024` | Max tokens per Claude response |
| `memory_turns` | `10` | Max conversation turns to remember |
| `memory_minutes` | `15` | Max age of turns to include |
| `signal_api_url` | `http://127.0.0.1:8090` | signal-cli-rest-api base URL |
| `signal_number` | *(required)* | Sending Signal number |
| `language` | `en` | Conversation language: `en` (English) or `de` (German) |
| `sugar_per_pearl` | `5` | Grams of sugar that equal one pearl (pricing formula) |
| `require_confirmation` | `true` | If `false` the AI books immediately without a propose/confirm step |
| `timezone` | `UTC` | Local timezone for daily refill (e.g. `Europe/Berlin`) |
| `prices_entity` | `input_text.perlen_preise` | HA entity holding the JSON price list |
| `whitelist_uuids` | `[]` | UUIDs allowed to set/delete prices |
| `accounts` | `[]` | List of children — see above |

Per-account fields inside `accounts`:

| Field | Description |
|-------|-------------|
| `name` | Display name used in AI prompts and logs |
| `recv_group_id` | Envelope `groupId` — identifies inbound messages from this group |
| `send_group_id` | `group.<id>` form used by `/v2/send` for replies |
| `balance_entity` | HA `input_number` entity holding this child's balance |
| `daily_refill` | Pearls added once per local calendar day |
| `max_balance` | Balance cap (also limits single-item price estimates) |

---

## Architecture notes

- **Pricing rule:** 1 pearl = 5 g sugar. Unknown products → `ceil(sugar_g / 5)`, clamped between 1 and the child's `max_balance`.
- **Proposal flow:** AI always calls `propose` first → waits for confirmation → then `book`. Booking without a prior proposal is rejected.
- **Atomic booking:** read balance → check coverage → set balance, protected by a per-entity asyncio lock shared with the daily refill task.
- **Per-group serial queue:** rapid messages from the same group are processed one at a time, preventing overlapping exchanges.
- **Memory window:** last N turns *and* last M minutes — the more restrictive limit applies. Prevents stale context from hours ago appearing in the conversation.
- **Proposal timeout:** open proposals expire after 5 minutes if not confirmed.
- **Daily refill:** background task checks every 10 minutes whether the local calendar date changed; tops up `daily_refill` pearls capped at `max_balance`. Restart-safe — last-refill date is persisted in the add-on's own SQLite store.
- **Account vs. sender:** the AI distinguishes which child's balance it's touching (the group's account) from who is actually typing (the sender's name, passed through from the Signal message). Any relative in a child's group can message; the account is always determined by the group, never by who sent the message.
- **Security:** admin actions (set/delete prices) are authorised against the HA-verified Signal sender UUID, never against a Claude-supplied value.

---

## Privacy

This repository contains **no phone numbers, Signal group IDs, child names, or UUIDs** — `candy-pearls/config.yaml` ships with `accounts: []`, `whitelist_uuids: []`, and an empty `signal_number`. All personal data is entered via the app's Configuration tab and lives only in `/data/options.json` on your HA host — never in git. If you fork this repo, do not hardcode real values into `config.yaml`.

---

## Known TODOs

- **Photo pricing:** `attachment_path` is passed through to the handler but vision is not yet wired up — the Signal envelope structure for image attachments needs to be verified against a real photo first.
- **Proposal timeout** as a config option (currently hardcoded to 5 minutes in `memory.py`).
