"""
Core AI harness: loads conversation history, runs the Claude tool-use loop,
sends the reply via Signal, and persists the exchange to memory.
"""
import json
import logging
from typing import Any

import anthropic

from .config import AccountConfig, Settings
from .ha_client import HAClient
from .signal_client import SignalClient
from . import i18n, memory
from .tools import TOOLS, ToolContext, run_tool

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6


def _build_context_block(
    account: AccountConfig,
    sender_name: str,
    is_admin: bool,
    language: str,
    balance: float | None = None,
    prices: dict | None = None,
) -> str:
    """Build the per-turn context block injected into Claude's user message.

    The block is NOT stored in memory — only the raw message text is, so
    replayed history never contains stale balance/price snapshots.
    """
    L = lambda key: i18n.t(key, language)
    admin_tag = L("label_admin") if is_admin else ""
    header = (
        f"{L('context_header')}\n"
        f"{L('label_child')}: {account.name} | {L('label_sender')}: {sender_name}{admin_tag}\n"
    )
    if balance is None or prices is None:
        return header + L("context_unavailable") + "\n"
    return (
        header
        + f"{L('label_balance')}: {balance:.0f} {L('unit_pearls')}\n"
        + f"{L('label_max_balance')}: {account.max_balance} {L('unit_pearls')}\n"
        + f"{L('label_prices')}: {json.dumps(prices, ensure_ascii=False)}\n"
    )


async def handle(
    account: AccountConfig,
    sender_uuid: str,
    sender_name: str,
    text: str,
    settings: Settings,
    ha: HAClient,
    signal: SignalClient,
    attachment_path: str | None = None,
) -> None:
    """Process one inbound message: run Claude tool-use loop, send reply."""

    group_id = account.recv_group_id
    is_admin = sender_uuid in settings.whitelist_uuids
    lang = settings.language

    # --- Load conversation history ---
    # History stores only raw message text (no context snapshots), so replayed
    # turns never show stale balance/price figures to Claude.
    hist = memory.history(group_id, settings.memory_turns, settings.memory_minutes)

    # --- Build context block for the current turn only ---
    # This snapshot is injected into the live Claude call but NOT stored in memory.
    try:
        balance = await ha.get_balance(account.balance_entity)
        prices = await ha.get_prices(settings.prices_entity)
        context_block = _build_context_block(account, sender_name, is_admin, lang, balance, prices)
    except Exception as exc:
        logger.warning("Could not fetch live context from HA: %s", exc)
        context_block = _build_context_block(account, sender_name, is_admin, lang)

    # raw_user_text is what gets stored in memory (no snapshot).
    # full_user_content is what Claude sees this turn (snapshot prepended).
    raw_user_text = f"{sender_name}: {text}"
    if attachment_path:
        # TODO: load image bytes and add as image content block once envelope verified
        raw_user_text += "\n" + i18n.t("attachment_placeholder", lang).format(path=attachment_path)
    full_user_content = f"{context_block}\n{raw_user_text}"

    messages: list[dict[str, Any]] = list(hist)
    messages.append({"role": "user", "content": full_user_content})

    # --- Claude tool-use loop ---
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    tool_ctx = ToolContext(
        account=account,
        sender_uuid=sender_uuid,
        group_id=group_id,
        settings=settings,
        ha=ha,
    )

    reply_text: str | None = None

    for round_num in range(MAX_TOOL_ROUNDS):
        logger.debug("Claude call round %d, messages=%d", round_num + 1, len(messages))
        response = await client.messages.create(
            model=settings.model,
            max_tokens=settings.max_tokens,
            system=i18n.system_prompt(lang, settings.sugar_per_pearl, settings.require_confirmation),
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            for block in response.content:
                if block.type == "text":
                    reply_text = block.text.strip()
                    break
            if reply_text is None:
                reply_text = i18n.t("no_response", lang)
            break

        # --- Execute all tool calls in this response ---
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            logger.info("Tool call: %s(%s)", block.name, json.dumps(block.input, ensure_ascii=False))
            result = await run_tool(block.name, block.input, tool_ctx)
            logger.debug("Tool result: %s", result)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    else:
        logger.warning("Reached max tool rounds (%d) without final text", MAX_TOOL_ROUNDS)
        reply_text = i18n.t("max_rounds_fallback", lang)

    # --- Send reply via Signal ---
    # Only persist the exchange to memory when the send succeeded, so a failed
    # delivery never poisons the conversation history with an unseen reply.
    try:
        await signal.send(account.send_group_id, reply_text)
    except Exception as exc:
        logger.error("Failed to send Signal reply: %s", exc)
        return  # do not store — recipient never saw this exchange

    # --- Persist this exchange to memory (raw text only, no context snapshot) ---
    await memory.append(group_id, "user", raw_user_text)
    await memory.append(group_id, "assistant", reply_text)
    logger.info("Exchange stored for group %s", group_id)
