"""
Tool definitions (Anthropic schema) and executor for the Candy Pearls harness.

Tools Claude can call:
  - get_balance    read current pearl balance for an account
  - list_prices    return the full price list
  - propose        park a pricing proposal in the session (no booking)
  - book           atomic debit via HA with coverage check
  - set_price      add/update a price (whitelist only)
  - delete_price   remove a price (whitelist only)

Tool definitions are always in English — they are model-facing API metadata,
not end-user text. Claude translates meaning into the configured language via
the system prompt. Each tool returns a plain dict serialised as tool_result content.
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
            "Returns the current pearl balance for an account. "
            "Always call this tool — never compute the balance yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {
                    "type": "string",
                    "description": "Account name as configured in the accounts list",
                }
            },
            "required": ["account"],
        },
    },
    {
        "name": "list_prices",
        "description": "Returns the current price list as an object {product: pearls}.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "propose",
        "description": (
            "Parks a price proposal for a product in the current session. "
            "Presents the proposal to the caregiver before booking. NO actual booking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {
                    "type": "string",
                    "description": "Product name (normalised, lower-case)",
                },
                "pearls": {
                    "type": "integer",
                    "description": "Proposed pearls, min 1, max = account limit (see context block)",
                },
                "source": {
                    "type": "string",
                    "enum": ["price_list", "sugar_calculated", "variant"],
                    "description": "Where the price comes from",
                },
                "sugar_g": {
                    "type": "number",
                    "description": "Estimated sugar content in grams (if calculated)",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "How confident the estimate is",
                },
            },
            "required": ["product", "pearls", "source", "confidence"],
        },
    },
    {
        "name": "book",
        "description": (
            "Debits pearls from the account (atomic: read → check → write). "
            "Only call this after the caregiver has confirmed and an open proposal exists. "
            "If save=true the price is written back to the price list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product": {
                    "type": "string",
                    "description": "Product name (must match the open proposal)",
                },
                "pearls": {
                    "type": "integer",
                    "description": "Pearls to debit (must not exceed the proposed amount)",
                },
                "save": {
                    "type": "boolean",
                    "description": "Save this price to the price list?",
                },
            },
            "required": ["product", "pearls", "save"],
        },
    },
    {
        "name": "set_price",
        "description": (
            "Sets or overwrites a price in the price list. Admins only "
            "(verified against the sender's UUID, not a caller-supplied value)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Product name"},
                "pearls": {"type": "integer", "description": "New price in pearls"},
            },
            "required": ["name", "pearls"],
        },
    },
    {
        "name": "delete_price",
        "description": (
            "Deletes an entry from the price list. Admins only "
            "(verified against the sender's UUID, not a caller-supplied value)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Product name"},
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
                return {"ok": False, "reason": f"Unknown tool: {name}"}
    except Exception as exc:
        logger.exception("Tool %s failed: %s", name, exc)
        return {"ok": False, "reason": f"Internal error: {exc}"}


# ---------------------------------------------------------------------------
# Individual tool implementations
# ---------------------------------------------------------------------------

def _require_own_account(name: str, ctx: ToolContext) -> AccountConfig | None:
    """Return ctx.account if name matches, else None.

    Booking and balance reads are restricted to the account that owns the
    current group — whoever is messaging in one child's group (a parent,
    grandparent, etc.) cannot name another child's account to debit it.
    """
    if name.lower() == ctx.account.name.lower():
        return ctx.account
    return None


async def _get_balance(inp: dict, ctx: ToolContext) -> dict:
    acc = _require_own_account(inp["account"], ctx)
    if acc is None:
        return {"ok": False, "reason": f"Account '{inp['account']}' not found or not accessible"}
    balance = await ctx.ha.get_balance(acc.balance_entity)
    return {"ok": True, "account": acc.name, "balance": balance}


async def _list_prices(ctx: ToolContext) -> dict:
    return {"ok": True, "prices": memory.get_prices()}


async def _propose(inp: dict, ctx: ToolContext) -> dict:
    # A single item shouldn't cost more pearls than the account can ever hold.
    proposal = {
        "product": inp["product"].lower().strip(),
        "pearls": max(1, min(ctx.account.max_balance, int(inp["pearls"]))),
        "source": inp["source"],
        "sugar_g": inp.get("sugar_g"),
        "confidence": inp["confidence"],
    }
    await memory.set_open_proposal(ctx.group_id, proposal)
    return {"ok": True, "proposal": proposal, "status": "pending — waiting for confirmation"}


async def _book(inp: dict, ctx: ToolContext) -> dict:
    pearls = int(inp["pearls"])
    product = inp["product"].lower().strip()

    if pearls <= 0:
        return {"ok": False, "reason": "Pearls must be positive"}

    if ctx.settings.require_confirmation:
        # Validate against the parked proposal so Claude cannot book more than
        # was proposed and confirmed by the caregiver.
        proposal = await memory.get_open_proposal(ctx.group_id)
        if proposal is None:
            return {"ok": False, "reason": "No open proposal — call propose first"}
        if pearls > proposal["pearls"]:
            return {
                "ok": False,
                "reason": f"Booking amount ({pearls}) exceeds the proposal ({proposal['pearls']})",
            }
        if product != proposal["product"]:
            return {
                "ok": False,
                "reason": f"Product '{product}' does not match proposal '{proposal['product']}'",
            }

    acc = ctx.account  # booking is always against the current group's account

    # When confirmation is off the propose step is skipped, so clamp here.
    if not ctx.settings.require_confirmation:
        pearls = min(pearls, acc.max_balance)

    # Atomic read → check → write, protected by per-entity lock so refill
    # cannot interleave between our read and write.
    async with get_balance_lock(acc.balance_entity):
        current = await ctx.ha.get_balance(acc.balance_entity)
        if current < pearls:
            return {
                "ok": False,
                "reason": "insufficient",
                "balance": current,
                "required": pearls,
            }
        new_balance = current - pearls
        await ctx.ha.set_balance(acc.balance_entity, new_balance)

    logger.info("Booked %d pearls for %s (%s → %s)", pearls, acc.name, current, new_balance)

    if inp.get("save"):
        await memory.set_price(product, pearls)
        logger.info("Saved price %s → %d pearls", product, pearls)

    await memory.clear_open_proposal(ctx.group_id)
    return {
        "ok": True,
        "account": acc.name,
        "debited": pearls,
        "new_balance": new_balance,
    }


async def _set_price(inp: dict, ctx: ToolContext) -> dict:
    # Use the HA-verified sender UUID, never a Claude-supplied value.
    if ctx.sender_uuid not in ctx.settings.whitelist_uuids:
        return {"ok": False, "reason": "Not authorised"}
    name = inp["name"].lower().strip()
    pearls = int(inp["pearls"])
    await memory.set_price(name, pearls)
    return {"ok": True, "name": name, "pearls": pearls}


async def _delete_price(inp: dict, ctx: ToolContext) -> dict:
    # Use the HA-verified sender UUID, never a Claude-supplied value.
    if ctx.sender_uuid not in ctx.settings.whitelist_uuids:
        return {"ok": False, "reason": "Not authorised"}
    name = inp["name"].lower().strip()
    deleted = await memory.delete_price(name)
    if not deleted:
        return {"ok": False, "reason": f"'{name}' not in price list"}
    return {"ok": True, "deleted": name}
