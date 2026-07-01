# Candy Pearls

## How it works

```
Signal group (per child) → signal-cli-rest-api → signal_websocket → HA sensor
    → HA automation (thin forwarder) → POST /inbound (this app)
        → Claude (tool-use loop) → HA REST API (read/write balance & prices)
        → Signal /v2/send (reply to group)
```

Each Signal group belongs to exactly one child, but any relative in that group
can message — Claude always knows whose balance to use from the group itself,
regardless of who is typing. It tells the two apart in its context: the
**account** (which child) vs. the **sender** (which relative is currently
writing).

Claude **proposes** a price, waits for confirmation from whoever is
messaging, then **books** via Home Assistant. HA is the only thing that
touches the balance — Claude just calls tools.

**Conversation memory** (SQLite under `/data/`) lets Claude understand
corrections across turns — a correction works correctly even if a different
relative sent it than the one who sent the original message.

---

## Prerequisites

- **signal-cli-rest-api** app (`bbernhard/signal-cli-rest-api`) — install
  from the App Store first, see the dependency note below
- **signal_websocket** HACS integration — exposes a `sensor.signal_<your_number>`
  entity for inbound messages
- One `input_number` helper per child (min 0, max = that child's `max_balance`)
  — create via Settings → Devices & services → Helpers

### Signal dependency

The HA Supervisor has no mechanism to declare a hard dependency between local
apps. Instead this app checks at the network level:

- The Signal endpoint is fully configurable via **`signal_api_url`** (default
  `http://127.0.0.1:8090`, reachable via `host_network: true`). Change it if
  you run Signal on a different host or port.
- At startup and on every `GET /health` call, the app pings
  `{signal_api_url}/v1/about`. If unreachable it logs a clear warning and
  retries every 30s in the background — the app does **not** crash while
  Signal is starting up.
- If you see `signal-cli-rest-api NOT reachable` in the logs: install and
  start `bbernhard/signal-cli-rest-api` first, or correct `signal_api_url`.

---

## Installation

1. Open the **Configuration** tab and fill in:
   - `anthropic_api_key` — your Anthropic API key
   - `signal_number` — your sending Signal number (e.g. `+49123456789`)
   - `timezone` — your local timezone for correct daily refill timing
   - `whitelist_uuids` — UUID(s) allowed to add/change/delete prices (easiest
     way: open the **Admin** page in the HA sidebar — every sender's UUID
     appears there after their first message)
   - `accounts` — one entry per child, see **Adding children** below
   - Optionally change `model` (default: `claude-haiku-4-5-20251001`)
2. **Start** the app.
3. Add the HA REST command and automation below.
4. Test: as a parent or other relative, send a candy name in one of the
   configured Signal groups.

### Adding children

Each child needs their own Signal group, containing whichever relatives
(parents, grandparents, etc.) should be able to message on that child's
behalf — there's no separate per-relative configuration; anyone in the group
can write, and Claude always attributes the booking to the child who owns
that group.

All child-specific data — Signal group IDs, names, daily allowance, balance
cap — lives **only** in this app's Configuration tab (Supervisor stores it in
`/data/options.json` on your HA host). Add one entry per child:

```yaml
accounts:
  - name: "<child's first name>"
    recv_group_id: "<envelope groupId from sensor.signal_...>"
    send_group_id: "group.<base64 id accepted by /v2/send>"
    balance_entity: "input_number.<your_chosen_id>"
    daily_refill: 3        # pearls added once per day
    max_balance: 5         # balance never exceeds this
```

**Finding `recv_group_id`:** have any relative send a message in the child's
Signal group, then read
`sensor.signal_<number>` → `attributes.full_envelope.dataMessage.groupInfo.groupId`.

**Finding `send_group_id`:** call the signal-cli-rest-api endpoint
`GET {signal_api_url}/v1/groups/<number>` — it lists all groups with both
IDs. The sendable form starts with `group.`.

**Daily refill:** the app tops up `daily_refill` pearls once per local
calendar day (using `timezone`), capped at `max_balance`. No separate HA
automation or helper is needed for this — it is handled internally and is
restart-safe.

---

## Price management

Prices are stored in the app's SQLite database (`/data/memory.db`) — no HA
helper required. The price list is shared across all children/groups.

**Viewing prices:** any group member can send `/list` to get the current
price list formatted as a Signal message.

**Adding/changing a price:** send a natural-language message to any group,
e.g. *"Set gummy bears to 2 pearls"*. Claude will call the `set_price` tool
if the sender is on the admin whitelist.

**Removing a price:** *"Delete gummy bears from the price list"*. Claude calls
`delete_price` — again requires admin UUID.

**Auto-saving new prices:** when booking an item that was not in the price
list, Claude can ask *"Should I save this price?"*. If the caregiver agrees,
the price is written to the database.

---

## HA-side configuration

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
| `model` | `claude-haiku-4-5-20251001` | Claude model used for conversation |
| `max_tokens` | `1024` | Max tokens per Claude response |
| `memory_turns` | `10` | Max conversation turns to remember |
| `memory_minutes` | `15` | Max age of turns to include |
| `signal_api_url` | `http://127.0.0.1:8090` | signal-cli-rest-api base URL |
| `signal_number` | *(required)* | Sending Signal number |
| `language` | `en` | Conversation language: `en` (English) or `de` (German) |
| `sugar_per_pearl` | `5` | Grams of sugar that equal one pearl (pricing formula) |
| `require_confirmation` | `true` | If `false` Claude books immediately without a propose/confirm step |
| `timezone` | `UTC` | Local timezone for daily refill |
| `whitelist_uuids` | `[]` | UUIDs allowed to set/delete prices |
| `accounts` | `[]` | List of children — see **Adding children** above |

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

- **Pricing rule:** 1 pearl = `sugar_per_pearl` g sugar. Unknown products →
  `ceil(sugar_g / sugar_per_pearl)`, clamped between 1 and the child's
  `max_balance`.
- **Proposal flow:** when `require_confirmation` is on, Claude always calls
  `propose` first → waits for confirmation → then `book`. Booking without a
  prior proposal is rejected.
- **Atomic booking:** read balance → check coverage → set balance, protected
  by a per-entity asyncio lock shared with the daily refill task.
- **Per-group serial queue:** rapid messages from the same group are
  processed one at a time, preventing overlapping exchanges.
- **Memory window:** last N turns *and* last M minutes — the more
  restrictive limit applies. Prevents stale context from hours ago appearing
  in the conversation.
- **Proposal timeout:** open proposals expire after 5 minutes if not
  confirmed.
- **Daily refill:** background task checks every 10 minutes whether the
  local calendar date changed; tops up `daily_refill` pearls capped at
  `max_balance`. Restart-safe — last-refill date is persisted in this app's
  own SQLite store.
- **Account vs. sender:** Claude distinguishes which child's balance it's
  touching (the group's account) from who is actually typing (the sender's
  name, passed through from the Signal message).
- **Security:** admin actions (set/delete prices) are authorised against the
  HA-verified Signal sender UUID, never against a Claude-supplied value.

---

## Privacy

This repository contains **no phone numbers, Signal group IDs, child names,
or UUIDs** — `config.yaml` ships with `accounts: []`, `whitelist_uuids: []`,
and an empty `signal_number`. All personal data is entered via this app's
Configuration tab and lives only in `/data/options.json` on your HA host —
never in git.
