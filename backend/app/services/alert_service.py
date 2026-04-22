"""Zone-boundary transition → event generation (NW-1303 + NW-1304).

One `AlertService` per WebSocket connection. Consumes the per-frame
`in_zone_flags` from `ZoneService.evaluate()` and emits `ZoneEvent`s
only on *sustained* state transitions — a simple N-frame debounce
suppresses the single-frame jitter that happens when a subject's
bottom-center anchor wobbles right at the polygon boundary.

AC:
- NW-1303: enter / exit on transitions; no repeats on steady-state;
  payload = `{track_id, object_class, event_type, timestamp, alert_id}`;
  events ride the WS, no REST polling.
- NW-1304: `DEBOUNCE_FRAMES` env var, default 2. Oscillation ≤1
  frame per side → 0 alerts. Clean ≥N-frame crossing → exactly 1.
  Reset on /session/reset (NW-1405).

State machine (per track, keyed on ByteTrack `track_id`):

  confirmed_in_zone : bool | None   -- committed side; None until first commit
  candidate_side    : bool | None   -- side we're accumulating toward
  streak_count      : int           -- consecutive frames matching candidate_side

  On each frame:
    if first sighting:
      candidate_side = observed, streak_count = 1
    elif observed == confirmed_in_zone:
      streak_count = 0         # we're on the confirmed side again; settled
      candidate_side = None
    elif candidate_side is None or observed != candidate_side:
      candidate_side = observed, streak_count = 1    # new opposite streak
    else:
      streak_count += 1

    if streak_count >= DEBOUNCE_FRAMES:
      -- Candidate has survived long enough; commit.
      if confirmed_in_zone is None or candidate_side != confirmed_in_zone:
        fire event (enter if candidate_side else exit)
      confirmed_in_zone = candidate_side
      candidate_side = None
      streak_count = 0

Design choices:

- **First-sighting also goes through the debounce.** An unknown → X
  transition is a transition like any other; it has to survive the
  N-frame window before firing. At 10 FPS with DEBOUNCE_FRAMES=2
  that's a ~100 ms delay on the "draw zone over standing operator"
  flow — invisible for the demo, but guarantees we don't emit an
  enter for a single-frame ghost detection.

- **Reset-on-zone-change** (WS handler calls `reset_state()` on every
  `zone_update` / `zone_clear`) clears BOTH the confirmed state AND
  the pending streak, so a new polygon starts from a blank slate.
  Does not emit phantom `exit`s for tracks inside the prior zone —
  the operator's redraw declares a new question.

- **`track_id is None` detections are skipped.** ByteTrack returns
  None on the first frame before association. Without an ID we have
  nothing to attach streak state to.

- **`debounce_frames=1` is a pass-through** (no debounce). Used in
  the NW-1303 unit tests that predate this ticket; the WS handler
  reads from `get_settings().debounce_frames` which defaults to 2
  per PROJECT_PLAN decision #9.

- **Track state map is unbounded.** ByteTrack recycles IDs and our
  demo runs minutes at most. If we ever move to a long-lived
  process we'd prune by last-seen frame — flagged for future-us.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from ..models.schemas import EventType, ObjectClass, WireDetection, ZoneEvent

logger = logging.getLogger(__name__)


@dataclass
class _TrackState:
    """Per-track debounce state (see module docstring)."""

    confirmed_in_zone: bool | None = None
    candidate_side: bool | None = None
    streak_count: int = 0


class AlertService:
    """Per-connection zone-event generator with N-frame debounce.

    Holds per-track in-zone state between frames. Not thread-safe on
    its own; the WS handler serialises calls via the event loop.
    """

    def __init__(self, debounce_frames: int = 1) -> None:
        """
        Args:
            debounce_frames: consecutive frames on the new side
                required before a transition fires. 1 = pass-through
                (any single-frame crossing fires). Must be >= 1.

        IMPORTANT: production callers MUST pass `debounce_frames`
        from `get_settings().debounce_frames` (default 2 per
        PROJECT_PLAN decision #9). The constructor default of 1 here
        is a pass-through for the NW-1303 unit tests that predate
        the debounce work — it intentionally diverges from the
        settings default so a call like `AlertService()` in new code
        is obviously under-configured rather than silently wrong.
        See `routes_ws.py detect_ws` for the canonical wiring.
        """
        if debounce_frames < 1:
            raise ValueError(
                f"debounce_frames must be >= 1, got {debounce_frames}"
            )
        self._debounce_frames = debounce_frames
        self._tracks: dict[int, _TrackState] = {}

    @property
    def debounce_frames(self) -> int:
        """Exposed for tests + logging only."""
        return self._debounce_frames

    def reset_state(self) -> None:
        """Forget all known track positions AND pending debounce streaks.

        Called by the WS handler on `zone_update` / `zone_clear` and
        (future) `/session/reset` (NW-1405). After reset, the next
        frame treats every track as a first-sighting — any track
        currently inside the new polygon fires a fresh `enter` once
        the debounce window is satisfied.

        Intentionally does NOT emit `exit` events for tracks that
        were inside the prior zone. The operator's redraw declares a
        new question; prior-zone exits are noise.
        """
        if self._tracks:
            logger.debug(
                "AlertService: reset state (dropped %d tracks)",
                len(self._tracks),
            )
        self._tracks.clear()

    def process_frame(
        self,
        detections: list[WireDetection],
        in_zone_flags: list[bool],
    ) -> list[ZoneEvent]:
        """Return the zone-boundary events triggered by this frame.

        `detections` and `in_zone_flags` must be parallel lists
        (ZoneService.evaluate contract). Detections with
        `track_id is None` are skipped.

        Steady-state membership never produces events; only
        debounced crossings do.
        """
        if len(detections) != len(in_zone_flags):
            raise ValueError(
                "AlertService: detections and in_zone_flags length mismatch "
                f"({len(detections)} vs {len(in_zone_flags)})"
            )

        events: list[ZoneEvent] = []
        now_iso = _now_iso()

        for det, currently_in in zip(detections, in_zone_flags):
            track_id = det.track_id
            if track_id is None:
                continue

            state = self._tracks.get(track_id)
            if state is None:
                state = _TrackState()
                self._tracks[track_id] = state

            self._advance_streak(state, currently_in)

            if state.streak_count < self._debounce_frames:
                continue

            # Streak survived — commit. Fire an event only if this is
            # a genuine transition (first-ever confirm counts IF the
            # committed side is `inside`, so the "operator already in
            # the zone" scenario fires an enter after N frames).
            is_transition = (
                state.confirmed_in_zone is None and state.candidate_side is True
            ) or (
                state.confirmed_in_zone is not None
                and state.candidate_side != state.confirmed_in_zone
            )

            if is_transition:
                event_type: EventType = (
                    "enter" if state.candidate_side else "exit"
                )
                events.append(
                    _make_event(
                        track_id=track_id,
                        object_class=det.object_class,
                        event_type=event_type,
                        timestamp=now_iso,
                    )
                )

            state.confirmed_in_zone = state.candidate_side
            state.candidate_side = None
            state.streak_count = 0

        return events

    def _advance_streak(self, state: _TrackState, observed: bool) -> None:
        """Update the track's candidate / streak for this frame's
        observation. See module docstring state machine."""
        if state.confirmed_in_zone is not None and observed == state.confirmed_in_zone:
            # Back on the committed side — jitter dissolved. Reset
            # any pending opposite streak.
            state.candidate_side = None
            state.streak_count = 0
            return

        if state.candidate_side == observed:
            # Continuing the same candidate streak.
            state.streak_count += 1
        else:
            # Fresh streak — either first sighting or the observation
            # flipped before the previous candidate could commit.
            state.candidate_side = observed
            state.streak_count = 1


def _now_iso() -> str:
    """ISO 8601 UTC string, e.g. '2026-04-22T18:33:07.123456+00:00'."""
    return datetime.now(timezone.utc).isoformat()


def _make_event(
    *,
    track_id: int,
    object_class: ObjectClass,
    event_type: EventType,
    timestamp: str,
) -> ZoneEvent:
    """Build a ZoneEvent with a freshly-minted alert_id.

    Extracted so tests can patch uuid/time generation if we ever
    want deterministic event ids (not needed today).
    """
    return ZoneEvent(
        track_id=track_id,
        object_class=object_class,
        event_type=event_type,
        timestamp=timestamp,
        alert_id=uuid.uuid4().hex,
    )
