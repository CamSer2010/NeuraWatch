"""Zone-boundary transition → event generation (NW-1303).

One `AlertService` per WebSocket connection. Consumes the per-frame
`in_zone_flags` from `ZoneService.evaluate()` and emits `ZoneEvent`s
only on state transitions, not on steady-state membership.

AC (from JIRA NW-1303):
- enter: `outside → inside` fires exactly one event
- exit: `inside → outside` fires exactly one event
- No repeat alerts while an object stays in the same state
- Event payload: track_id, object_class, event_type, timestamp, alert_id
- Events pushed through the existing WS connection (no REST polling)

Design choices:
- **First-sighting-while-inside fires `enter`.** When a track appears
  for the first time and its anchor is already inside the zone, we
  treat the "unknown → inside" transition as a true enter. Without
  this, someone already standing in a newly-drawn zone would never
  alert until they left and came back — the opposite of what the
  operator wants for a "who is in this zone right now" demo.

- **Zone changes reset per-track state.** The WS handler calls
  `reset_state()` on every `zone_update` / `zone_clear`. This makes
  the next evaluation treat every visible track as a first-sighting,
  so anyone already inside the new zone fires an immediate `enter`.
  Matches spec §Interactions: "Source switching auto-clears the
  polygon and bumps zone_version."

- **`track_id is None` detections are skipped.** ByteTrack returns
  `None` on the very first frame before association. Without an ID
  we have nothing to tie a transition to, so we silently ignore.

- **Debounce is NOT here — NW-1304 wraps this service.** Raw per-
  frame transitions are emitted verbatim; the 2-frame debounce
  layer lives in its own module and can be toggled via env var.

- **Track state map is unbounded.** ByteTrack recycles IDs
  aggressively and our demo runs minutes at most, so the map won't
  grow meaningfully. If we ever bounce up to a long-lived process
  we'd add a periodic prune by last-seen frame — flagged for
  future-us, not this ticket.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from ..models.schemas import EventType, ObjectClass, WireDetection, ZoneEvent

logger = logging.getLogger(__name__)


class AlertService:
    """Per-connection zone-event generator.

    Holds per-track in-zone state between frames. Not thread-safe on
    its own; the WS handler serialises calls via the event loop.
    """

    def __init__(self) -> None:
        self._track_state: dict[int, bool] = {}

    def reset_state(self) -> None:
        """Forget all known track positions.

        Called by the WS handler when the zone changes (update OR
        clear). After reset, the next frame treats every track as a
        first-sighting — any track currently inside the new polygon
        will fire a fresh `enter` event.

        Intentionally does NOT emit `exit` events for tracks that
        were inside the prior zone. The operator just redrew the
        question; prior-zone exits are noise.
        """
        if self._track_state:
            logger.debug(
                "AlertService: reset state (dropped %d tracks)",
                len(self._track_state),
            )
        self._track_state.clear()

    def process_frame(
        self,
        detections: list[WireDetection],
        in_zone_flags: list[bool],
    ) -> list[ZoneEvent]:
        """Return the zone-boundary events triggered by this frame.

        `detections` and `in_zone_flags` must be parallel lists
        (ZoneService.evaluate contract). Any detection with
        `track_id is None` is skipped — we can't track transitions
        without an ID.

        Steady-state membership never produces events; only crossings
        (including the unknown→inside case on first sighting of an
        already-inside track) do.
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

            previously_in = self._track_state.get(track_id)

            if previously_in is None:
                # First sighting of this track. Record its state;
                # fire `enter` only if it starts inside (treated as
                # unknown → inside transition for demo clarity).
                if currently_in:
                    events.append(
                        _make_event(
                            track_id=track_id,
                            object_class=det.object_class,
                            event_type="enter",
                            timestamp=now_iso,
                        )
                    )
            elif currently_in != previously_in:
                event_type: EventType = "enter" if currently_in else "exit"
                events.append(
                    _make_event(
                        track_id=track_id,
                        object_class=det.object_class,
                        event_type=event_type,
                        timestamp=now_iso,
                    )
                )

            self._track_state[track_id] = currently_in

        return events


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
