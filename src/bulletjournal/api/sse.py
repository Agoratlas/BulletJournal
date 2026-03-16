from __future__ import annotations

import asyncio
import json

from fastapi import Request
from fastapi.responses import StreamingResponse

from bulletjournal.config import SSE_POLL_INTERVAL_SECONDS


def sse_response(container, project_id: str, request: Request, *, last_event_id: int | None = None) -> StreamingResponse:
    resolved_last_event_id = _resolve_last_event_id(request.headers.get('last-event-id'), last_event_id)

    async def event_stream():
        current_last_event_id = resolved_last_event_id
        retry_ms = int(SSE_POLL_INTERVAL_SECONDS * 1000)
        yield f'retry: {retry_ms}\n\n'
        while True:
            if await request.is_disconnected():
                break
            batch = container.event_service.events_after(current_last_event_id)
            if batch['reset_required']:
                payload = json.dumps(
                    {
                        'reason': 'event_history_truncated',
                        'earliest_available_id': batch['earliest_available_id'],
                    },
                    ensure_ascii=True,
                )
                yield f'event: stream.reset\ndata: {payload}\n\n'
                current_last_event_id = int(batch['earliest_available_id']) - 1
                continue
            events = batch['events']
            emitted = False
            for event in events:
                if event['project_id'] != project_id:
                    continue
                current_last_event_id = int(event['id'])
                payload = json.dumps(event, ensure_ascii=True)
                emitted = True
                yield f'id: {event["id"]}\nevent: {event["event_type"]}\ndata: {payload}\n\n'
            if not emitted:
                yield ': keepalive\n\n'
            await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)

    return StreamingResponse(event_stream(), media_type='text/event-stream')


def _resolve_last_event_id(header_value: str | None, query_value: int | None) -> int:
    candidate = header_value.strip() if header_value else None
    if candidate:
        return int(candidate)
    return 0 if query_value is None else int(query_value)
