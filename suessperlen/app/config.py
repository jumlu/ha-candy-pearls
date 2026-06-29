"""
Loads add-on configuration from /data/options.json (HA runtime) with
ENV-variable fallback for local development.

HA writes options.json from the user's add-on config before starting the
container, so this is always present at runtime.
"""
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_OPTIONS_PATH = "/data/options.json"


@dataclass
class AccountConfig:
    name: str
    recv_group_id: str
    send_group_id: str
    balance_entity: str


@dataclass
class Settings:
    anthropic_api_key: str
    ha_token: str
    ha_base_url: str
    model: str
    max_tokens: int
    memory_turns: int
    memory_minutes: int
    signal_api_url: str
    signal_number: str
    log_level: str
    prices_entity: str
    whitelist_uuids: list[str]
    accounts: list[AccountConfig]


def _load_options() -> dict[str, Any]:
    if os.path.exists(_OPTIONS_PATH):
        with open(_OPTIONS_PATH) as f:
            return json.load(f)
    # Local dev: read from ENV with sane defaults
    logger.warning("No %s found — falling back to environment variables", _OPTIONS_PATH)
    return {
        "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
        "ha_token": os.environ.get("HA_TOKEN", ""),
        "ha_base_url": os.environ.get("HA_BASE_URL", "http://supervisor/core"),
        "model": os.environ.get("MODEL", "claude-haiku-4-5-20251001"),
        "max_tokens": int(os.environ.get("MAX_TOKENS", "1024")),
        "memory_turns": int(os.environ.get("MEMORY_TURNS", "10")),
        "memory_minutes": int(os.environ.get("MEMORY_MINUTES", "15")),
        "signal_api_url": os.environ.get("SIGNAL_API_URL", "http://127.0.0.1:8090"),
        "signal_number": os.environ.get("SIGNAL_NUMBER", ""),
        "log_level": os.environ.get("LOG_LEVEL", "info"),
        "prices_entity": os.environ.get("PRICES_ENTITY", "input_text.perlen_preise"),
        "whitelist_uuids": os.environ.get("WHITELIST_UUIDS", "").split(",") if os.environ.get("WHITELIST_UUIDS") else [],
        "accounts": json.loads(os.environ.get("ACCOUNTS", "[]")),
    }


def load_settings() -> Settings:
    opts = _load_options()
    accounts = [
        AccountConfig(
            name=a["name"],
            recv_group_id=a["recv_group_id"],
            send_group_id=a["send_group_id"],
            balance_entity=a["balance_entity"],
        )
        for a in opts.get("accounts", [])
    ]
    return Settings(
        anthropic_api_key=opts["anthropic_api_key"],
        ha_token=opts["ha_token"],
        ha_base_url=opts["ha_base_url"].rstrip("/"),
        model=opts["model"],
        max_tokens=int(opts["max_tokens"]),
        memory_turns=int(opts["memory_turns"]),
        memory_minutes=int(opts["memory_minutes"]),
        signal_api_url=opts["signal_api_url"].rstrip("/"),
        signal_number=opts["signal_number"],
        log_level=opts["log_level"],
        prices_entity=opts["prices_entity"],
        whitelist_uuids=opts.get("whitelist_uuids", []),
        accounts=accounts,
    )
