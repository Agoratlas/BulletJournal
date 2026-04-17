from __future__ import annotations

import threading
from collections import deque
from typing import Any

from bulletjournal.config import SSE_EVENT_RETENTION
from bulletjournal.utils import utc_now_iso


class EventService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: deque[dict[str, Any]] = deque(maxlen=SSE_EVENT_RETENTION)
        self._counter = 0

    def publish(
        self, event_type: str, *, project_id: str, graph_version: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            self._counter += 1
            event = {
                'id': self._counter,
                'event_type': event_type,
                'project_id': project_id,
                'graph_version': graph_version,
                'timestamp': utc_now_iso(),
                'payload': payload,
            }
            self._events.append(event)
        return event

    def events_after(self, last_event_id: int) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)
        if not events:
            return {
                'events': [],
                'reset_required': False,
                'earliest_available_id': 0,
            }
        earliest_available_id = int(events[0]['id'])
        reset_required = last_event_id > 0 and last_event_id < earliest_available_id - 1
        filtered = events if reset_required else [event for event in events if int(event['id']) > last_event_id]
        return {
            'events': filtered,
            'reset_required': reset_required,
            'earliest_available_id': earliest_available_id,
        }
