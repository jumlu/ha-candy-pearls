"""
Tool definitions (Anthropic schema) and executor for the Candy Pearls harness.

Tools Claude can call:
  - get_balance       read current pearl balance for an account
  - list_prices       return the full price list
  - propose           park a pricing proposal in the session (no booking)
  - book              atomic debit via HA with coverage check
  - set_price         add/update a price (whitelist only)
  - delete_price      remove a price (whitelist only)

Each tool returns a plain dict that is JSON-serialised as tool_result content.
"""
import logging
from dataclasses import dataclass
from typing import Any

from .config import AccountConfig, Settings
from .ha_client import HAClient
from .locks import get_balance_lock
from . import memory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Anthropic tool schema
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_balance",
        "description": (
            "Gibt den aktuellen Perlenkontostand für ein Konto zurück. "
            "Immer dieses Tool aufrufen – nie selbst rechnen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {
                    "type": "string",
                    "description": "Name des Kontos, wie in accounts konfiguriert",
                }
            },
            "required": ["account"],
        },
    },
    {
        "name": "list_prices",
        "description": "Gibt die aktuelle Preisliste als Objekt {Produkt: Perlen} zurück.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "propose",
        "description": (
            "Speichert einen Preisvorschlag für ein Produkt in der aktuellen Sitzung. "
            "Schlägt dem Kind vor, bevor gebucht wird. KEINE Buchung."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "produkt": {"type": "string", "description": "Produktname (normalisiert, kleingeschrieben)"},
                "perlen": {
                    "type": "integer",
                    "description": "Vorgeschlagene Perlen, min 1, max = Kontolimit (siehe Kontext-Block)",
                },
                "quelle": {
                    "type": "string",
                    "enum": ["preisliste", "zucker_berechnet", "variante"],
                    "description": "Woher der Preis stammt",
                },
                "zucker_g": {
                    "type": "number",
                    "description": "Geschätzter Zuckergehalt in Gramm (falls berechnet)",
                },
                "konfidenz": {
                    "type": "string",
                    "enum": ["hoch", "mittel", "niedrig"],
                    "description": "Wie sicher ist die Schätzung",
                },
            },
            "required": ["produkt", "perlen", "quelle", "konfidenz"],
        },
    },
    {
        "name": "book",
        "description": (
            "Bucht Perlen vom Konto ab (atomar: lesen → prüfen → setzen). "
            "Nur aufrufen, nachdem das Kind zugestimmt hat und ein offener Vorschlag vorliegt. "
            "Bei speichern=true wird der Preis in die Preisliste übernommen."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "produkt": {"type": "string", "description": "Produktname (muss dem Vorschlag entsprechen)"},
                "perlen": {"type": "integer", "description": "Abzubuchende Perlen (darf Vorschlag nicht überschreiten)"},
                "speichern": {
                    "type": "boolean",
                    "description": "Preis in Preisliste speichern?",
                },
            },
            "required": ["produkt", "perlen", "speichern"],
        },
    },
    {
        "name": "set_price",
        "description": "Setzt oder überschreibt einen Preis in der Preisliste. Nur für Admins (wird anhand des verifizierten Absenders geprüft).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Produktname"},
                "perlen": {"type": "integer", "description": "Neuer Preis in Perlen"},
            },
            "required": ["name", "perlen"],
        },
    },
    {
        "name": "delete_price",
        "description": "Löscht einen Eintrag aus der Preisliste. Nur für Admins (wird anhand des verifizierten Absenders geprüft).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Produktname"},
            },
            "required": ["name"],
        },
    },
]


# ---------------------------------------------------------------------------
# Context passed to each tool execution
# ---------------------------------------------------------------------------

@dataclass
class ToolContext:
    account: AccountConfig
    sender_uuid: str
    group_id: str
    settings: Settings
    ha: HAClient


# ---------------------------------------------------------------------------
# Tool executor
# ---------------------------------------------------------------------------

async def run_tool(name: str, tool_input: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    try:
        match name:
            case "get_balance":
                return await _get_balance(tool_input, ctx)
            case "list_prices":
                return await _list_prices(ctx)
            case "propose":
                return await _propose(tool_input, ctx)
            case "book":
                return await _book(tool_input, ctx)
            case "set_price":
                return await _set_price(tool_input, ctx)
            case "delete_price":
                return await _delete_price(tool_input, ctx)
            case _:
                logger.warning("Claude called unknown tool: %r", name)
                return {"ok": False, "reason": f"Unbekanntes Tool: {name}"}
    except Exception as exc:
        logger.exception("Tool %s failed: %s", name, exc)
        return {"ok": False, "reason": f"Interner Fehler: {exc}"}


# ---------------------------------------------------------------------------
# Individual tool implementations
# ---------------------------------------------------------------------------

def _require_own_account(name: str, ctx: ToolContext) -> AccountConfig | None:
    """Return ctx.account if name matches, else None.

    Booking and balance reads are restricted to the account that owns the
    current group — a child cannot name another child's account to debit it.
    """
    if name.lower() == ctx.account.name.lower():
        return ctx.account
    return None


async def _get_balance(inp: dict, ctx: ToolContext) -> dict:
    acc = _require_own_account(inp["account"], ctx)
    if acc is None:
        return {"ok": False, "reason": f"Konto '{inp['account']}' nicht gefunden oder nicht zugänglich"}
    balance = await ctx.ha.get_balance(acc.balance_entity)
    return {"ok": True, "account": acc.name, "balance": balance}


async def _list_prices(ctx: ToolContext) -> dict:
    prices = await ctx.ha.get_prices(ctx.settings.prices_entity)
    return {"ok": True, "prices": prices}


async def _propose(inp: dict, ctx: ToolContext) -> dict:
    # A single item shouldn't cost more pearls than the account can ever hold.
    proposal = {
        "produkt": inp["produkt"].lower().strip(),
        "perlen": max(1, min(ctx.account.max_balance, int(inp["perlen"]))),
        "quelle": inp["quelle"],
        "zucker_g": inp.get("zucker_g"),
        "konfidenz": inp["konfidenz"],
    }
    await memory.set_open_proposal(ctx.group_id, proposal)
    return {"ok": True, "proposal": proposal, "status": "vorgemerkt – warte auf Bestätigung"}


async def _book(inp: dict, ctx: ToolContext) -> dict:
    # Validate against the parked proposal — book can only be called after propose.
    proposal = await memory.get_open_proposal(ctx.group_id)
    if proposal is None:
        return {"ok": False, "reason": "Kein offener Vorschlag — bitte erst propose aufrufen"}

    perlen = int(inp["perlen"])
    produkt = inp["produkt"].lower().strip()

    if perlen > proposal["perlen"]:
        return {
            "ok": False,
            "reason": f"Buchungsmenge ({perlen}) überschreitet den Vorschlag ({proposal['perlen']})",
        }
    if produkt != proposal["produkt"]:
        return {
            "ok": False,
            "reason": f"Produkt '{produkt}' stimmt nicht mit Vorschlag '{proposal['produkt']}' überein",
        }
    if perlen <= 0:
        return {"ok": False, "reason": "Perlen muss positiv sein"}

    acc = ctx.account  # booking is always against the current group's account

    # Atomic read → check → write, protected by per-entity lock so refill
    # cannot interleave between our read and write.
    async with get_balance_lock(acc.balance_entity):
        current = await ctx.ha.get_balance(acc.balance_entity)
        if current < perlen:
            return {
                "ok": False,
                "reason": "insufficient",
                "balance": current,
                "required": perlen,
            }
        new_balance = current - perlen
        await ctx.ha.set_balance(acc.balance_entity, new_balance)

    logger.info("Booked %d pearls for %s (%s → %s)", perlen, acc.name, current, new_balance)

    # Optionally persist derived price (own RMW, acceptable race risk for price saves)
    if inp.get("speichern"):
        prices = await ctx.ha.get_prices(ctx.settings.prices_entity)
        prices[produkt] = perlen
        await ctx.ha.set_prices(ctx.settings.prices_entity, prices)
        logger.info("Saved price %s → %d pearls", produkt, perlen)

    await memory.clear_open_proposal(ctx.group_id)
    return {
        "ok": True,
        "account": acc.name,
        "debited": perlen,
        "new_balance": new_balance,
    }


async def _set_price(inp: dict, ctx: ToolContext) -> dict:
    # Use the HA-verified sender UUID, never a Claude-supplied value.
    if ctx.sender_uuid not in ctx.settings.whitelist_uuids:
        return {"ok": False, "reason": "Nicht berechtigt"}
    name = inp["name"].lower().strip()
    perlen = int(inp["perlen"])
    prices = await ctx.ha.get_prices(ctx.settings.prices_entity)
    prices[name] = perlen
    await ctx.ha.set_prices(ctx.settings.prices_entity, prices)
    return {"ok": True, "name": name, "perlen": perlen}


async def _delete_price(inp: dict, ctx: ToolContext) -> dict:
    # Use the HA-verified sender UUID, never a Claude-supplied value.
    if ctx.sender_uuid not in ctx.settings.whitelist_uuids:
        return {"ok": False, "reason": "Nicht berechtigt"}
    name = inp["name"].lower().strip()
    prices = await ctx.ha.get_prices(ctx.settings.prices_entity)
    if name not in prices:
        return {"ok": False, "reason": f"'{name}' nicht in Preisliste"}
    del prices[name]
    await ctx.ha.set_prices(ctx.settings.prices_entity, prices)
    return {"ok": True, "deleted": name}
