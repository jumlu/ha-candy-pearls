"""
Async client for the Home Assistant REST API.
HA is the authoritative bank — all balance reads and writes go through here.
"""
import logging
from typing import Any

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class HAClient:
    def __init__(self, settings: Settings) -> None:
        self._base = settings.ha_base_url
        self._headers = {
            "Authorization": f"Bearer {settings.ha_token}",
            "Content-Type": "application/json",
        }

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        url = f"{self._base}/api/states/{entity_id}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=self._headers)
            resp.raise_for_status()
            return resp.json()

    async def call_service(self, domain: str, service: str, data: dict[str, Any]) -> None:
        url = f"{self._base}/api/services/{domain}/{service}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, headers=self._headers, json=data)
            resp.raise_for_status()

    # --- balance helpers ---

    async def get_balance(self, entity_id: str) -> float:
        state = await self.get_state(entity_id)
        return float(state["state"])

    async def set_balance(self, entity_id: str, value: float) -> None:
        await self.call_service(
            "input_number",
            "set_value",
            {"entity_id": entity_id, "value": value},
        )

