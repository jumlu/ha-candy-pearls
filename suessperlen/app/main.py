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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import AccountConfig, Settings, load_settings
from .ha_client import HAClient
from .harness import handle
from .signal_client import SignalClient

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


def _get_lock(group_id: str) -> asyncio.Lock:
    if group_id not in _group_locks:
        _group_locks[group_id] = asyncio.Lock()
    return _group_locks[group_id]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _settings, _ha, _signal
    _settings = load_settings()
    _setup_logging(_settings.log_level)
    _ha = HAClient(_settings)
    _signal = SignalClient(_settings)
    logger = logging.getLogger(__name__)
    logger.info(
        "Süßperlen Harness started. Model=%s, Accounts=%s",
        _settings.model,
        [a.name for a in _settings.accounts],
    )

    # Soft dependency check: the Supervisor has no add-on-to-add-on
    # dependency mechanism, so we verify the configured signal_api_url is
    # actually reachable and log an actionable hint if not. Non-fatal —
    # signal-cli-rest-api may simply still be starting up.
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

    yield


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
    return {"status": "ok", "signal_reachable": signal_ok}


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
        logger.debug("Unknown group_id %s — ignoring", msg.group_id)
        return JSONResponse(status_code=204, content=None)

    # Serialize per group to prevent race conditions on the balance
    lock = _get_lock(msg.group_id)
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
            # Try to send error notice to the group
            try:
                await _signal.send(
                    account.send_group_id,
                    "Konnte die Nachricht gerade nicht verarbeiten — bitte nochmal versuchen.",
                )
            except Exception:
                pass  # Nothing more we can do

    return {"status": "ok"}
