"""
Shared asyncio locks for cross-module coordination.

balance_locks: keyed by balance_entity (e.g. "input_number.perlen_henry").
Both the inbound request path (_book in tools.py) and the background refill
loop (refill.py) acquire the relevant lock before their read-modify-write on
a child's balance, preventing interleaved concurrent writes.
"""
import asyncio

_balance_locks: dict[str, asyncio.Lock] = {}


def get_balance_lock(balance_entity: str) -> asyncio.Lock:
    if balance_entity not in _balance_locks:
        _balance_locks[balance_entity] = asyncio.Lock()
    return _balance_locks[balance_entity]
