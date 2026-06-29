"""
Daily pearl refill — replaces the old per-child HA automation.

Each account tops up by `daily_refill` pearls once per local calendar day,
capped at `max_balance`. The last-refill date is tracked in our own SQLite
store (memory.py) so a restart never causes a double top-up on the same day —
the same restart-safety the old `input_datetime.perlen_letzter_reset` helper
provided, just self-contained in the add-on instead of needing a per-child
HA helper.

Runs as a background asyncio task: checked once at startup, then polled on
an interval (no need for exact midnight timing — the date-string guard makes
it idempotent within a day).
"""
import asyncio
import logging
from datetime import date

from .config import Settings
from .ha_client import HAClient
from . import memory

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 600  # 10 minutes


async def run_once(settings: Settings, ha: HAClient) -> None:
    today = date.today().isoformat()
    for account in settings.accounts:
        last = memory.get_last_refill_date(account.recv_group_id)
        if last == today:
            continue
        try:
            current = await ha.get_balance(account.balance_entity)
            new_balance = min(current + account.daily_refill, account.max_balance)
            if new_balance != current:
                await ha.set_balance(account.balance_entity, new_balance)
                logger.info(
                    "Daily refill for %s: %s → %s (cap %s)",
                    account.name, current, new_balance, account.max_balance,
                )
            memory.set_last_refill_date(account.recv_group_id, today)
        except Exception as exc:
            logger.error("Daily refill failed for %s: %s", account.name, exc)


async def loop(settings: Settings, ha: HAClient) -> None:
    while True:
        await run_once(settings, ha)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
