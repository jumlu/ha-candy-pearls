"""
Thin async wrapper around the signal-cli-rest-api /v2/send endpoint.
"""
import logging

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class SignalClient:
    def __init__(self, settings: Settings) -> None:
        self._api_url = settings.signal_api_url
        self._number = settings.signal_number

    async def send(self, send_group_id: str, text: str) -> None:
        url = f"{self._api_url}/v2/send"
        payload = {
            "message": text,
            "number": self._number,
            "recipients": [send_group_id],
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code not in (200, 201):
                logger.error(
                    "Signal send failed: HTTP %s — %s",
                    resp.status_code,
                    resp.text[:200],
                )
                resp.raise_for_status()
            logger.debug("Signal message sent to %s", send_group_id)
