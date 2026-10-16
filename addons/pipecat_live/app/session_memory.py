"""Short-lived in-memory conversation context cache."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass
class MemoryEntry:
    messages: list[dict[str, Any]]
    timestamp: float


class SessionMemory:
    """Cache recent LLM context messages per client id."""

    def __init__(self) -> None:
        self._entries: dict[str, MemoryEntry] = {}

    def restore(
        self,
        client_id: str,
        seed_messages: list[dict[str, Any]],
        *,
        enabled: bool,
        reuse_seconds: int,
        max_messages: int,
    ) -> list[dict[str, Any]]:
        """Return seed messages plus recent cached messages when still fresh."""

        seed = [dict(item) for item in seed_messages]
        if not enabled or not client_id or reuse_seconds <= 0:
            return seed

        entry = self._entries.get(client_id)
        if not entry:
            return seed

        age = time.time() - entry.timestamp
        if age > reuse_seconds:
            self._entries.pop(client_id, None)
            logger.debug("Session memory expired for {} after {:.1f}s", client_id, age)
            return seed

        reusable = [
            dict(item)
            for item in entry.messages
            if item.get("role") not in {"system", "developer"}
        ]
        if max_messages > 0:
            reusable = reusable[-max_messages:]
        logger.info("Restored {} conversation memory messages for {}", len(reusable), client_id)
        return seed + reusable

    def cache(self, client_id: str, context: Any, *, enabled: bool, max_messages: int) -> None:
        """Store recent context messages for a future reconnect."""

        if not enabled or not client_id or context is None:
            return

        getter = getattr(context, "get_messages", None)
        if not callable(getter):
            return

        try:
            messages = getter() or []
        except Exception as err:
            logger.debug("Could not read session memory context for {}: {}", client_id, err)
            return

        serializable = [dict(item) for item in messages if isinstance(item, dict)]
        if max_messages > 0:
            seed = [item for item in serializable if item.get("role") in {"system", "developer"}]
            body = [item for item in serializable if item.get("role") not in {"system", "developer"}]
            serializable = seed[-2:] + body[-max_messages:]
        if not serializable:
            return

        self._entries[client_id] = MemoryEntry(messages=serializable, timestamp=time.time())
        logger.info("Cached {} conversation memory messages for {}", len(serializable), client_id)

    def clear(self, client_id: str | None = None) -> None:
        """Clear one client or the whole cache."""

        if client_id:
            self._entries.pop(client_id, None)
        else:
            self._entries.clear()


SESSION_MEMORY = SessionMemory()
