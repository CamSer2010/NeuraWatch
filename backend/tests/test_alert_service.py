"""NW-1303 + NW-1304: enter/exit events with debounce.

Tests are organized in two blocks:

1. Debounce-off (`debounce_frames=1`) — verifies the base transition
   machine semantics from NW-1303. A `debounce_frames=1` service is
   pass-through: any single-frame crossing fires immediately.

2. Debounce-on (`debounce_frames=2` and a couple of `=3` cases) —
   verifies NW-1304 AC: oscillation is silenced, sustained crossings
   fire exactly once after the streak is satisfied, reset clears
   pending streaks.
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


# =======================================================================
# Block 1 — Debounce OFF (pass-through): NW-1303 transition semantics
# =======================================================================

def test_first_sighting_inside_fires_enter_without_debounce() -> None:
    svc = AlertService(debounce_frames=1)
    events = svc.process_frame([_det(1)], [True])
    assert len(events) == 1
    assert events[0].event_type == "enter"
    assert events[0].track_id == 1


def test_first_sighting_outside_is_silent() -> None:
    svc = AlertService(debounce_frames=1)
    events = svc.process_frame([_det(1)], [False])
    assert events == []


def test_steady_state_inside_no_repeat() -> None:
    svc = AlertService(debounce_frames=1)
    svc.process_frame([_det(1)], [True])  # enter
    events = svc.process_frame([_det(1)], [True])
    assert events == []


def test_steady_state_outside_no_event() -> None:
    svc = AlertService(debounce_frames=1)
    svc.process_frame([_det(1)], [False])  # silent first sighting
    events = svc.process_frame([_det(1)], [False])
    assert events == []


def test_inside_to_outside_fires_exit() -> None:
    svc = AlertService(debounce_frames=1)
    svc.process_frame([_det(1)], [True])
    events = svc.process_frame([_det(1)], [False])
    assert len(events) == 1
    assert events[0].event_type == "exit"


def test_outside_to_inside_fires_enter() -> None:
    svc = AlertService(debounce_frames=1)
    svc.process_frame([_det(1)], [False])
    events = svc.process_frame([_det(1)], [True])
    assert len(events) == 1
    assert events[0].event_type == "enter"


def test_pass_through_oscillation_alternates() -> None:
    """With debounce=1, every per-frame crossing fires. This is the
    'raw transition stream' contract that NW-1304 wraps."""
    svc = AlertService(debounce_frames=1)
    sequence = [True, False, True, False]
    all_events: list = []
    for in_zone in sequence:
        all_events.extend(svc.process_frame([_det(1)], [in_zone]))
    assert [e.event_type for e in all_events] == ["enter", "exit", "enter", "exit"]


# ---- track_id / multi-track / reset (debounce=1) -----------------------

def test_none_track_id_is_skipped() -> None:
    svc = AlertService(debounce_frames=1)
    events = svc.process_frame([_det(None)], [True])
    assert events == []
    events = svc.process_frame([_det(None)], [False])
    assert events == []


def test_independent_tracks_do_not_cross_pollinate() -> None:
    svc = AlertService(debounce_frames=1)
    events = svc.process_frame(
        [_det(1), _det(2)],
        [True, False],
    )
    assert [e.track_id for e in events] == [1]
    assert events[0].event_type == "enter"

    events = svc.process_frame(
        [_det(1), _det(2)],
        [True, True],
    )
    assert [e.track_id for e in events] == [2]
    assert events[0].event_type == "enter"


def test_reset_state_makes_next_frame_first_sighting() -> None:
    svc = AlertService(debounce_frames=1)
    svc.process_frame([_det(1)], [True])  # enter
    svc.process_frame([_det(1)], [True])  # steady
    svc.reset_state()
    events = svc.process_frame([_det(1)], [True])
    assert len(events) == 1
    assert events[0].event_type == "enter"


def test_reset_on_empty_service_is_noop() -> None:
    svc = AlertService(debounce_frames=1)
    svc.reset_state()
    events = svc.process_frame([_det(1)], [False])
    assert events == []


# ---- input validation --------------------------------------------------

def test_mismatched_lengths_raise() -> None:
    svc = AlertService(debounce_frames=1)
    with pytest.raises(ValueError):
        svc.process_frame([_det(1), _det(2)], [True])


def test_empty_inputs_return_empty() -> None:
    svc = AlertService(debounce_frames=1)
    assert svc.process_frame([], []) == []


# ---- event payload shape -----------------------------------------------

def test_event_payload_has_required_fields() -> None:
    svc = AlertService(debounce_frames=1)
    events = svc.process_frame(
        [_det(42, object_class="vehicle")],
        [True],
    )
    assert len(events) == 1
    ev = events[0]
    assert ev.track_id == 42
    assert ev.object_class == "vehicle"
    assert ev.event_type == "enter"
    assert ev.timestamp
    assert len(ev.alert_id) == 32


def test_alert_ids_are_unique_per_event() -> None:
    svc = AlertService(debounce_frames=1)
    e1 = svc.process_frame([_det(1)], [True])
    svc.process_frame([_det(1)], [False])
    e3 = svc.process_frame([_det(1)], [True])
    assert e1[0].alert_id != e3[0].alert_id


# =======================================================================
# Block 2 — Debounce ON: NW-1304 AC
# =======================================================================

def test_constructor_rejects_debounce_below_one() -> None:
    with pytest.raises(ValueError):
        AlertService(debounce_frames=0)
    with pytest.raises(ValueError):
        AlertService(debounce_frames=-1)


def test_default_debounce_is_one() -> None:
    """Pass-through by default — keeps the NW-1303 contract unless
    the WS handler explicitly opts into a larger window."""
    svc = AlertService()
    assert svc.debounce_frames == 1


def test_oscillation_one_frame_per_side_produces_no_events() -> None:
    """NW-1304 AC: 'Object oscillating across boundary ≤1 frame per
    side → 0 alerts.'"""
    svc = AlertService(debounce_frames=2)
    sequence = [True, False, True, False, True, False]
    all_events: list = []
    for in_zone in sequence:
        all_events.extend(svc.process_frame([_det(1)], [in_zone]))
    assert all_events == []


def test_sustained_two_frame_crossing_fires_exactly_one() -> None:
    """NW-1304 AC: 'Clean crossing sustained ≥2 consecutive frames
    on the new side → exactly 1 alert.'

    Sequence: 2× outside (settles confirmed=outside), then 2× inside
    (flips to confirmed=inside and fires enter)."""
    svc = AlertService(debounce_frames=2)
    all_events: list = []
    for in_zone in [False, False, True, True]:
        all_events.extend(svc.process_frame([_det(1)], [in_zone]))
    assert [e.event_type for e in all_events] == ["enter"]


def test_first_sighting_inside_fires_after_debounce_window() -> None:
    """First-sighting transitions also go through the debounce, so
    an inside first-sighting fires only after 2 consecutive inside
    frames — not on frame 1."""
    svc = AlertService(debounce_frames=2)
    f1 = svc.process_frame([_det(1)], [True])
    assert f1 == []
    f2 = svc.process_frame([_det(1)], [True])
    assert [e.event_type for e in f2] == ["enter"]


def test_single_frame_blip_after_committed_does_not_fire() -> None:
    """Common real-world jitter: a track is settled inside, blips
    outside for one frame, settles back. No exit/enter pair should
    fire."""
    svc = AlertService(debounce_frames=2)
    # Settle inside.
    svc.process_frame([_det(1)], [True])
    svc.process_frame([_det(1)], [True])
    # Single outside blip.
    f3 = svc.process_frame([_det(1)], [False])
    # Back inside for two more frames.
    f4 = svc.process_frame([_det(1)], [True])
    f5 = svc.process_frame([_det(1)], [True])
    assert f3 == []
    assert f4 == []
    assert f5 == []


def test_sustained_exit_after_long_inside_fires_once() -> None:
    svc = AlertService(debounce_frames=2)
    # Settle inside (enter fires on frame 2).
    svc.process_frame([_det(1)], [True])
    enter_events = svc.process_frame([_det(1)], [True])
    assert [e.event_type for e in enter_events] == ["enter"]
    # Hold inside a while longer.
    svc.process_frame([_det(1)], [True])
    svc.process_frame([_det(1)], [True])
    # Now exit for 2 frames.
    f1 = svc.process_frame([_det(1)], [False])
    f2 = svc.process_frame([_det(1)], [False])
    assert f1 == []
    assert [e.event_type for e in f2] == ["exit"]


def test_higher_debounce_requires_more_frames() -> None:
    """Parameterized debounce: =3 means 3 consecutive new-side frames
    are needed before committing."""
    svc = AlertService(debounce_frames=3)
    # 2 inside frames: still below the threshold.
    assert svc.process_frame([_det(1)], [True]) == []
    assert svc.process_frame([_det(1)], [True]) == []
    # 3rd inside frame triggers enter.
    events = svc.process_frame([_det(1)], [True])
    assert [e.event_type for e in events] == ["enter"]


def test_reset_clears_pending_streak() -> None:
    """AC: per-track debounce counter reset on /session/reset.

    After 1 inside frame (pending streak of 1), reset wipes it. The
    next single inside frame restarts from zero; a solo inside frame
    post-reset should NOT fire."""
    svc = AlertService(debounce_frames=2)
    svc.process_frame([_det(1)], [True])  # streak=1, not yet confirmed
    svc.reset_state()
    f1 = svc.process_frame([_det(1)], [True])  # restart — streak=1
    f2 = svc.process_frame([_det(1)], [True])  # streak=2 → fire
    assert f1 == []
    assert [e.event_type for e in f2] == ["enter"]


def test_reset_clears_streak_on_committed_side_with_debounce_two() -> None:
    """Belt-and-suspenders: after a track has been COMMITTED inside
    (past the debounce), reset_state drops the committed state too.
    The next frame behaves like a first-sighting; one inside frame
    alone must NOT fire — we still need the N-frame window."""
    svc = AlertService(debounce_frames=2)
    # Commit inside.
    svc.process_frame([_det(1)], [True])
    commit_events = svc.process_frame([_det(1)], [True])
    assert [e.event_type for e in commit_events] == ["enter"]
    svc.reset_state()
    # First post-reset inside frame must NOT fire (streak=1, below 2).
    assert svc.process_frame([_det(1)], [True]) == []
    # Second post-reset inside frame satisfies the debounce — fresh
    # enter fires, proving reset cleared both the confirmed side AND
    # any pending streak math.
    events = svc.process_frame([_det(1)], [True])
    assert [e.event_type for e in events] == ["enter"]


def test_independent_tracks_debounce_independently() -> None:
    """Track 1's streak must not pre-count for track 2."""
    svc = AlertService(debounce_frames=2)
    # Frame 1: both observed inside.
    f1 = svc.process_frame([_det(1), _det(2)], [True, True])
    # Frame 2: track 1 steady inside, track 2 jumps outside.
    f2 = svc.process_frame([_det(1), _det(2)], [True, False])
    # Frame 3: track 1 steady inside, track 2 back inside.
    f3 = svc.process_frame([_det(1), _det(2)], [True, True])
    # Frame 4: both steady inside.
    f4 = svc.process_frame([_det(1), _det(2)], [True, True])
    # Track 1 should fire enter at frame 2 (2 consecutive inside).
    # Track 2's streak was broken at frame 2, restarted at frame 3,
    # and fires enter at frame 4 (2 consecutive inside).
    by_track = {
        (e.track_id, i): e
        for i, frame in enumerate([f1, f2, f3, f4])
        for e in frame
    }
    assert (1, 1) in by_track
    assert by_track[(1, 1)].event_type == "enter"
    assert (2, 3) in by_track
    assert by_track[(2, 3)].event_type == "enter"
    # And nothing else fired.
    assert len(by_track) == 2
