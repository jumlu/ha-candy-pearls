"""
Daily pearl refill — replaces the old per-child HA automation.

Each account tops up by `daily_refill` pearls once per local calendar day,
capped at `max_balance`. The last-refill date is tracked in our own SQLite
store (memory.py) so a restart never causes a double top-up on the same day.

The loop() coroutine is designed to run forever as an asyncio background task.
It wraps every iteration in a broad except so a transient error (HA unreachable,
misconfigured account) never kills the task — it logs and sleeps, then retries.

Timezone: the config `timezone` field (default "UTC") is used for date() so
families in non-UTC timezones get their refill on the correct local calendar day.
"""
import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Settings
from .ha_client import HAClient
from .locks import get_balance_lock
from . import memory

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 600  # 10 minutes


def _today(tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone %r — falling back to UTC", tz_name)
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date().isoformat()


async def run_once(settings: Settings, ha: HAClient) -> None:
    today = _today(settings.timezone)
    for account in settings.accounts:
        last = memory.get_last_refill_date(account.recv_group_id)
        if last == today:
            continue
        try:
            # Acquire the per-entity balance lock so booking in tools.py
            # cannot interleave with this read-modify-write.
            async with get_balance_lock(account.balance_entity):
                current = await ha.get_balance(account.balance_entity)
                new_balance = min(current + account.daily_refill, account.max_balance)
                if new_balance != current:
                    await ha.set_balance(account.balance_entity, new_balance)
                    logger.info(
                        "Daily refill for %s: %s → %s (cap %s)",
                        account.name, current, new_balance, account.max_balance,
                    )
            await memory.set_last_refill_date(account.recv_group_id, today)
        except asyncio.CancelledError:
            raise  # propagate cancellation — don't swallow it
        except Exception as exc:
            logger.error("Daily refill failed for %s: %s", account.name, exc)


async def loop(settings: Settings, ha: HAClient) -> None:
    """Run refill checks forever, sleeping CHECK_INTERVAL_SECONDS between runs.

    An outer except catches any unexpected exception (bug in run_once, bad
    config, etc.), logs it, sleeps briefly, and continues — the task never
    dies silently from a transient error. CancelledError is re-raised so
    graceful shutdown (task.cancel()) still works.
    """
    while True:
        try:
            await run_once(settings, ha)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Unexpected error in refill loop: %s", exc)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)
