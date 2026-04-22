"""NW-1303 AC: enter/exit events on zone boundary transitions.

Covers:
- First sighting inside zone → single `enter`
- First sighting outside zone → no event
- Steady-state inside → no repeat events
- Steady-state outside → no events
- inside → outside → `exit`
- outside → inside → `enter`
- Oscillation (every frame) → alternating events (debounce is NW-1304)
- `track_id is None` → silently skipped
- Independent tracks don't cross-pollinate
- `reset_state()` makes the next frame re-treat tracks as first-sightings
- Mismatched list lengths raise ValueError
- Event payload has track_id / object_class / event_type / timestamp / alert_id
"""
from __future__ import annotations

import pytest

from app.models.schemas import WireDetection
from app.services.alert_service import AlertService


def _det(
    track_id: int | None,
    object_class: str = "person",
    bbox: tuple[float, float, float, float] = (0.4, 0.4, 0.5, 0.5),
) -> WireDetection:
    return WireDetection(
        object_class=object_class,  # type: ignore[arg-type]
        bbox=bbox,
        confidence=0.9,
        track_id=track_id,
    )


# ---- single-track transitions ------------------------------------------

def test_first_sighting_inside_fires_enter() -> None:
    svc = AlertService()
    events = svc.process_frame([_det(1)], [True])
    assert len(events) == 1
    assert events[0].event_type == "enter"
    assert events[0].track_id == 1


def test_first_sighting_outside_is_silent() -> None:
    svc = AlertService()
    events = svc.process_frame([_det(1)], [False])
    assert events == []


def test_steady_state_inside_no_repeat() -> None:
    svc = AlertService()
    svc.process_frame([_det(1)], [True])  # enter
    events = svc.process_frame([_det(1)], [True])
    assert events == []


def test_steady_state_outside_no_event() -> None:
    svc = AlertService()
    svc.process_frame([_det(1)], [False])  # silent first sighting
    events = svc.process_frame([_det(1)], [False])
    assert events == []


def test_inside_to_outside_fires_exit() -> None:
    svc = AlertService()
    svc.process_frame([_det(1)], [True])
    events = svc.process_frame([_det(1)], [False])
    assert len(events) == 1
    assert events[0].event_type == "exit"


def test_outside_to_inside_fires_enter() -> None:
    svc = AlertService()
    svc.process_frame([_det(1)], [False])
    events = svc.process_frame([_det(1)], [True])
    assert len(events) == 1
    assert events[0].event_type == "enter"


def test_oscillation_alternates_events() -> None:
    """Raw per-frame transitions are emitted verbatim here. The
    2-frame debounce that suppresses edge-jitter is NW-1304's
    responsibility; this service must surface every crossing."""
    svc = AlertService()
    sequence = [True, False, True, False]
    all_events = []
    for in_zone in sequence:
        all_events.extend(svc.process_frame([_det(1)], [in_zone]))
    event_types = [e.event_type for e in all_events]
    assert event_types == ["enter", "exit", "enter", "exit"]


# ---- track_id semantics ------------------------------------------------

def test_none_track_id_is_skipped() -> None:
    svc = AlertService()
    events = svc.process_frame([_det(None)], [True])
    assert events == []
    # And even if another frame arrives with an ID, the None track
    # never seeded state so there's no phantom history to clean up.
    events = svc.process_frame([_det(None)], [False])
    assert events == []


def test_independent_tracks_do_not_cross_pollinate() -> None:
    svc = AlertService()
    # Track 1 enters, track 2 outside on frame 1.
    events = svc.process_frame(
        [_det(1), _det(2)],
        [True, False],
    )
    assert [e.track_id for e in events] == [1]
    assert events[0].event_type == "enter"

    # Frame 2: track 1 steady inside, track 2 enters.
    events = svc.process_frame(
        [_det(1), _det(2)],
        [True, True],
    )
    assert [e.track_id for e in events] == [2]
    assert events[0].event_type == "enter"


# ---- reset_state -------------------------------------------------------

def test_reset_state_makes_next_frame_first_sighting() -> None:
    svc = AlertService()
    svc.process_frame([_det(1)], [True])  # enter
    svc.process_frame([_det(1)], [True])  # steady, no event
    svc.reset_state()
    events = svc.process_frame([_det(1)], [True])
    # Post-reset: a currently-inside track fires enter again because
    # we've forgotten its prior state. This is the "new zone drawn
    # over people already standing inside" flow.
    assert len(events) == 1
    assert events[0].event_type == "enter"


def test_reset_on_empty_service_is_noop() -> None:
    svc = AlertService()
    svc.reset_state()
    events = svc.process_frame([_det(1)], [False])
    assert events == []


# ---- input validation --------------------------------------------------

def test_mismatched_lengths_raise() -> None:
    svc = AlertService()
    with pytest.raises(ValueError):
        svc.process_frame([_det(1), _det(2)], [True])


def test_empty_inputs_return_empty() -> None:
    svc = AlertService()
    assert svc.process_frame([], []) == []


# ---- event payload shape -----------------------------------------------

def test_event_payload_has_required_fields() -> None:
    svc = AlertService()
    events = svc.process_frame(
        [_det(42, object_class="vehicle")],
        [True],
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.track_id == 42
    assert ev.object_class == "vehicle"
    assert ev.event_type == "enter"
    assert ev.timestamp  # non-empty ISO string
    assert len(ev.alert_id) == 32  # uuid4 hex


def test_alert_ids_are_unique_per_event() -> None:
    svc = AlertService()
    e1 = svc.process_frame([_det(1)], [True])
    svc.process_frame([_det(1)], [False])
    e3 = svc.process_frame([_det(1)], [True])
    assert e1[0].alert_id != e3[0].alert_id
