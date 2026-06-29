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
from . import memory
from .tools import TOOLS, ToolContext, run_tool

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6

# ---------------------------------------------------------------------------
# System prompt  (edit here — no code changes needed)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Du bist der Süßperlen-Assistent für ein Kinder-Belohnungssystem.
Jede Perle entspricht 5 g Zucker. Du hilfst dabei, Süßigkeiten-Käufe fair abzubuchen.

**Deine Rolle:**
- Preis-Bewerter: Du schätzt, wie viele Perlen ein Produkt kostet.
- Buchhalter-Assistent: Du buchst Perlen ab, nachdem das Kind zugestimmt hat.
- Du bist freundlich, kindgerecht und antwortest knapp auf Deutsch.

**Preisfindung:**
1. Schau zuerst in der Preisliste nach (Tool: list_prices). Tippfehler und Varianten matchen.
2. Falls nicht gefunden: schätze den Zuckergehalt in Gramm → Perlen = ceil(Zucker_g / 5), min 1, max 5.
3. Schlage immer erst vor und warte auf „ja" oder „nein" des Kindes, BEVOR du buchst.

**Buchungsregeln:**
- NIEMALS ungefragt buchen — immer erst Vorschlag, dann auf Bestätigung warten.
- Nach „ja" (oder klarer Bestätigung): Tool book aufrufen.
- KontostÃ¤nde NIE selbst ausrechnen — immer get_balance aufrufen.
- Wenn nicht genug Perlen: freundlich erklären und nicht buchen.

**Korrekturen im Gespräch:**
- Nutze den Gesprächsverlauf. Wenn das Kind sagt „nee, zwei Maoam", korrigiere deinen Vorschlag.

**Sicherheit:**
- Preise setzen/löschen geht nur über Tools (set_price / delete_price).
- Diese Tools prüfen selbst, ob der Absender berechtigt ist.

**Foto-Erkennung:**
- TODO: Bildanhänge sind vorbereitet (attachment_path), aber die genaue Envelope-Struktur
  eines Signal-Bildanhangs ist noch nicht verifiziert. Vision-Call daher noch nicht aktiv.

**Antwort-Format:** Kurz, direkt, auf Deutsch. Emoji sparsam und kindgerecht.
"""

# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------


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

    # --- Load conversation history ---
    hist = memory.history(group_id, settings.memory_turns, settings.memory_minutes)

    # --- Build context block for the current turn ---
    # We load live values here so Claude always has a fresh snapshot in its context,
    # even though Tools remain the authoritative source for any write operation.
    try:
        balance = await ha.get_balance(account.balance_entity)
        prices = await ha.get_prices(settings.prices_entity)
        context_block = (
            f"[Kontext]\n"
            f"Konto: {account.name} | Absender: {sender_name}"
            f"{' (Admin)' if is_admin else ''}\n"
            f"Aktueller Kontostand: {balance:.0f} Perlen\n"
            f"Preisliste: {json.dumps(prices, ensure_ascii=False)}\n"
        )
    except Exception as exc:
        logger.warning("Could not fetch live context from HA: %s", exc)
        context_block = (
            f"[Kontext]\nKonto: {account.name} | Absender: {sender_name}"
            f"{' (Admin)' if is_admin else ''}\n"
            f"(Kontostand und Preisliste konnten nicht geladen werden.)\n"
        )

    # --- Build user message ---
    # TODO: when attachment_path is set and Signal envelope structure for images
    #       is verified, add a vision content block here.
    user_content = f"{context_block}\n{sender_name}: {text}"
    if attachment_path:
        # TODO: load image bytes and add as image content block once envelope verified
        user_content += f"\n[Anhang: {attachment_path} – Bildverarbeitung noch nicht aktiv]"

    messages: list[dict[str, Any]] = list(hist)
    messages.append({"role": "user", "content": user_content})

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
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            # Extract final text
            for block in response.content:
                if block.type == "text":
                    reply_text = block.text.strip()
                    break
            if reply_text is None:
                reply_text = "(Keine Antwort)"
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

        # Append assistant response + tool results to message list
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    else:
        logger.warning("Reached max tool rounds (%d) without final text", MAX_TOOL_ROUNDS)
        reply_text = "Ich bin gerade etwas durcheinander — bitte nochmal versuchen."

    # --- Send reply via Signal ---
    try:
        await signal.send(account.send_group_id, reply_text)
    except Exception as exc:
        logger.error("Failed to send Signal reply: %s", exc)
        # Best-effort fallback — can't send error message if Signal itself is broken

    # --- Persist this exchange to memory ---
    memory.append(group_id, "user", user_content)
    memory.append(group_id, "assistant", reply_text)
    logger.info("Exchange stored for group %s", group_id)
