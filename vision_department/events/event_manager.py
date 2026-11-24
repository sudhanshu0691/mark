"""Deduplicates, stores, and forwards vision events to MARK's host runtime."""
from __future__ import annotations

from collections.abc import Callable

from .event_store import EventStore


class VisionEventManager:
    def __init__(self, store: EventStore, on_event: Callable[[dict], None] | None = None, cooldown: float = 0.0):
        self.store = store
        self.on_event = on_event
        # Kept as a backward-compatible constructor argument; notification
        # cooldown belongs to VisionNotificationAgent, not event persistence.

    def publish(self, event: dict) -> bool:
        event_id = self.store.add(event)
        event["event_id"] = event_id
        if self.on_event:
            self.on_event(dict(event))
        return True
