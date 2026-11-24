# core/events/__init__.py
# Event bus for MARK modular architecture

from __future__ import annotations

from typing import Any, Dict, List, Optional, Callable, TypeVar, Generic
from enum import Enum
import asyncio
import threading
import json
from pathlib import Path

T = TypeVar("T")

class EventType(Enum):
    """Types of events in the MARK system."""
    USER_COMMAND = "user_command"
    INTENT_CLASSIFIED = "intent_classified"
    COMPLEXITY_ANALYZED = "complexity_analyzed"
    MODEL_ROUTED = "model_routed"
    AGENT_DELEGATED = "agent_delegated"
    AGENT_RESULT = "agent_result"
    TOOL_EXECUTED = "tool_executed"
    TOOL_RESULT = "tool_result"
    MEMORY_STORED = "memory_stored"
    MEMORY_RETRIEVED = "memory_retrieved"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    PLUGIN_LOADED = "plugin_loaded"
    PLUGIN_UNLOADED = "plugin_unloaded"
    PLUGIN_ENABLED = "plugin_enabled"
    PLUGIN_DISABLED = "plugin_disabled"
    CONNECTION_ESTABLISHED = "connection_established"
    CONNECTION_LOST = "connection_lost"
    SESSION_STARTED = "session_started"
    SESSION_RESUMED = "session_resumed"
    SESSION_ENDED = "session_ended"
    STATE_CHANGED = "state_changed"
    ERROR_OCCURRED = "error_occurred"
    WARNING_OCCURRED = "warning_occurred"
    PERFORMANCE_METRIC = "performance_metric"
    STARTUP = "startup"
    SHUTDOWN = "shutdown"

class Event(Generic[T]):
    """Event data class."""
    
    def __init__(
        self,
        event_type: EventType,
        source: str,
        data: T | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self.event_type = event_type
        self.source = source
        self.data = data or {}
        self.correlation_id = correlation_id or ""
        self.timestamp = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else __import__("time").time()
    
    def to_dict(self) -> dict:
        return {
            "event_type": self.event_type.value,
            "source": self.source,
            "data": self.data,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        return cls(
            event_type=EventType(data["event_type"]),
            source=data.get("source", ""),
            data=data.get("data", {}),
            correlation_id=data.get("correlation_id", ""),
        )

class EventBus(Generic[T]):
    """Thread-safe event bus for MARK system communication."""
    
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: Dict[EventType, List[Callable[[Event[T]], None]]] = {}
        self._history: List[Event[T]] = []
        self._max_history = 1000
    
    def subscribe(self, event_type: EventType, handler: Callable[[Event[T]], None]) -> None:
        """Subscribe to an event type."""
        with self._lock:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            self._handlers[event_type].append(handler)
    
    def unsubscribe(self, event_type: EventType, handler: Callable[[Event[T]], None]) -> None:
        """Unsubscribe from an event type."""
        with self._lock:
            if event_type in self._handlers and handler in self._handlers[event_type]:
                self._handlers[event_type].remove(handler)
    
    def publish(self, event: Event[T]) -> None:
        """Publish an event to all subscribers."""
        with self._lock:
            handlers = self._handlers.get(event.event_type, []).copy()
        
        # Store in history
        with self._lock:
            if len(self._history) >= self._max_history:
                self._history = self._history[-self._max_history + 1:]
            self._history.append(event)
        
        # Dispatch to handlers (outside lock to avoid deadlocks)
        for handler in handlers:
            try:
                if asyncio.get_event_loop().is_running():
                    asyncio.create_task(handler(event))
                else:
                    handler(event)
            except Exception as e:
                print(f"[events] Handler error: {type(e).__name__}: {e}")

    def publish_sync(self, event: Event[T]) -> None:
        """Synchronously publish an event to all subscribers."""
        with self._lock:
            handlers = self._handlers.get(event.event_type, []).copy()
        
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                print(f"[events] Handler error: {type(e).__name__}: {e}")

    def history(self, event_type: EventType | None = None) -> List[Event[T]]:
        """Get event history, optionally filtered by type."""
        with self._lock:
            if event_type is None:
                return list(self._history)
            return [e for e in self._history if e.event_type == event_type]

    def clear_history(self) -> None:
        """Clear event history."""
        with self._lock:
            self._history.clear()

# Global event bus instance
_EVENT_BUS: Optional[EventBus] = None

def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    global _EVENT_BUS
    if _EVENT_BUS is None:
        _EVENT_BUS = EventBus()
    return _EVENT_BUS

def set_event_bus(bus: EventBus) -> None:
    """Set the global event bus instance."""
    global _EVENT_BUS
    _EVENT_BUS = bus

__all__ = [
    "EventType",
    "Event",
    "EventBus",
    "get_event_bus",
    "set_event_bus",
]