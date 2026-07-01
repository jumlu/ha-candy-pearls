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

    async def check_reachable(self) -> bool:
        """Ping signal-cli-rest-api's /v1/about endpoint.

        Used at startup and by /health to verify the configured
        signal_api_url actually points at a running signal-cli-rest-api
        instance — there is no Supervisor-level add-on dependency mechanism
        to enforce this, so we check it ourselves at the network level.
        """
        url = f"{self._api_url}/v1/about"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.warning("signal-cli-rest-api not reachable at %s: %s", self._api_url, exc)
            return False

    async def get_accounts(self) -> list[str]:
        """Return registered Signal account numbers from signal-cli-rest-api."""
        url = f"{self._api_url}/v1/accounts"
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
            if not isinstance(data, list):
                return []
            return [
                item if isinstance(item, str) else item.get("number", str(item))
                for item in data
            ]
        except Exception as exc:
            logger.warning("Could not fetch Signal accounts: %s", exc)
            return []

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
