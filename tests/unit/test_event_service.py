from bulletjournal.services.event_service import EventService


def test_event_service_keeps_recent_events_and_requests_reset_for_old_cursor() -> None:
    service = EventService()

    for index in range(1005):
        service.publish(
            'test.event',
            project_id='demo',
            graph_version=1,
            payload={'index': index},
        )

    batch = service.events_after(1)

    assert batch['reset_required'] is True
    assert batch['earliest_available_id'] == 6
    assert len(batch['events']) == 1000


def test_event_service_filters_incremental_events_without_reset() -> None:
    service = EventService()
    service.publish('event.one', project_id='demo', graph_version=1, payload={})
    service.publish('event.two', project_id='demo', graph_version=1, payload={})
    service.publish('event.three', project_id='demo', graph_version=1, payload={})

    batch = service.events_after(2)

    assert batch['reset_required'] is False
    assert [event['event_type'] for event in batch['events']] == ['event.three']
