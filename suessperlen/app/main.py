"""
FastAPI entry point for the Süßperlen Harness add-on.

Endpoints:
  GET  /health   — liveness probe
  POST /inbound  — receives forwarded Signal messages from HA automation
"""
import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import AccountConfig, Settings, load_settings
from .ha_client import HAClient
from .harness import handle
from .signal_client import SignalClient
from . import refill

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(level_str: str) -> None:
    level = getattr(logging, level_str.upper(), logging.INFO)
    logging.basicConfig(
        stream=sys.stdout,
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# App state (initialised at startup)
# ---------------------------------------------------------------------------

_settings: Settings | None = None
_ha: HAClient | None = None
_signal: SignalClient | None = None
_group_locks: dict[str, asyncio.Lock] = {}
_refill_task: asyncio.Task | None = None


def _get_group_lock(group_id: str) -> asyncio.Lock:
    if group_id not in _group_locks:
        _group_locks[group_id] = asyncio.Lock()
    return _group_locks[group_id]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _settings, _ha, _signal, _refill_task
    _settings = load_settings()
    _setup_logging(_settings.log_level)
    _ha = HAClient(_settings)
    _signal = SignalClient(_settings)
    logger = logging.getLogger(__name__)
    logger.info(
        "Süßperlen Harness started. Model=%s, Timezone=%s, Accounts=%s",
        _settings.model,
        _settings.timezone,
        [a.name for a in _settings.accounts],
    )

    # Soft dependency check: verify signal_api_url is reachable.
    if await _signal.check_reachable():
        logger.info("signal-cli-rest-api reachable at %s", _settings.signal_api_url)
    else:
        logger.warning(
            "signal-cli-rest-api NOT reachable at %s — install/start the "
            "'bbernhard/signal-cli-rest-api' add-on, or update 'signal_api_url' "
            "in this app's configuration if it runs elsewhere. Retrying in background.",
            _settings.signal_api_url,
        )
        asyncio.create_task(_wait_for_signal(logger))

    # Store the task handle so we can cancel it on shutdown and detect failures.
    _refill_task = asyncio.create_task(refill.loop(_settings, _ha), name="refill-loop")

    yield  # app runs here

    # --- Graceful shutdown ---
    if _refill_task and not _refill_task.done():
        _refill_task.cancel()
        try:
            await _refill_task
        except asyncio.CancelledError:
            pass
    logger.info("Süßperlen Harness stopped.")


async def _wait_for_signal(logger: logging.Logger) -> None:
    """Poll until signal-cli-rest-api becomes reachable, then log success."""
    while True:
        await asyncio.sleep(30)
        if _signal and await _signal.check_reachable():
            logger.info("signal-cli-rest-api is now reachable at %s", _settings.signal_api_url)
            return


app = FastAPI(title="Süßperlen Harness", lifespan=lifespan)

# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------

class InboundMessage(BaseModel):
    group_id: str
    sender_uuid: str
    sender_name: str
    text: str
    attachment_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    signal_ok = await _signal.check_reachable() if _signal else False
    refill_alive = _refill_task is not None and not _refill_task.done()
    return {"status": "ok", "signal_reachable": signal_ok, "refill_task_alive": refill_alive}


@app.post("/inbound")
async def inbound(msg: InboundMessage):
    logger = logging.getLogger(__name__)

    if not _settings or not _ha or not _signal:
        raise HTTPException(status_code=503, detail="Not initialised")

    # Map recv_group_id to account
    account: AccountConfig | None = None
    for acc in _settings.accounts:
        if acc.recv_group_id == msg.group_id:
            account = acc
            break

    if account is None:
        logger.debug("Unknown group_id — ignoring")
        return JSONResponse(status_code=204, content=None)

    # Serialise messages per group to prevent overlapping exchanges.
    lock = _get_group_lock(msg.group_id)
    async with lock:
        try:
            await handle(
                account=account,
                sender_uuid=msg.sender_uuid,
                sender_name=msg.sender_name,
                text=msg.text,
                settings=_settings,
                ha=_ha,
                signal=_signal,
                attachment_path=msg.attachment_path,
            )
        except Exception as exc:
            logger.exception("Unhandled error processing message: %s", exc)
            try:
                await _signal.send(
                    account.send_group_id,
                    "Konnte die Nachricht gerade nicht verarbeiten — bitte nochmal versuchen.",
                )
            except Exception:
                pass

    return {"status": "ok"}
