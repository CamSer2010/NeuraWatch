"""Zone-based point-in-polygon evaluation (NW-1302).

One `ZoneService` instance per active WebSocket connection. Holds the
polygon the current client last committed (via `zone_update`) and
evaluates each inbound frame's detections against it.

Key design points (per ratified plan + JIRA AC):

- **Shapely** for the geometry. `shapely.prepare()` accelerates
  point-in-polygon by building an STR-tree on the polygon's edges;
  rebuilding the prepared tree per frame would dwarf the per-point
  cost so the prepared polygon is cached until `zone_version` changes.
- **Normalized 0–1 coordinate space** (ratified decision #5). FE sends
  polygon vertices in the same frame of reference as `WireDetection.bbox`,
  so no rescaling happens here.
- **Bottom-center anchor** for each detection: `((x1+x2)/2, y2)`. An
  object is "in-zone" when its feet are inside the polygon, which
  matches human intuition for e.g. restricted-area monitoring far
  better than the bbox centroid.
- **Polygons with <3 points are rejected** by `set_zone`. The client
  already gates Close Zone on ≥3 via `PolygonToolbar`, but the service
  re-checks so a malformed upstream message can't crash inference.
- **`zone_version`** is the monotonic counter assigned by the client.
  It's echoed back on every `detection_result` so the client can tell
  which polygon produced a given event (important once NW-1303's
  alerts start flowing and the user changes the zone mid-stream).
"""
from __future__ import annotations

import logging

from shapely import prepare
from shapely.geometry import Point, Polygon

from ..models.schemas import WireDetection

logger = logging.getLogger(__name__)

MIN_POLYGON_VERTICES = 3


class ZoneService:
    """Per-connection zone state.

    Not thread-safe on its own — the WS handler is single-tasked per
    connection (coroutines await serially), so no lock is required.
    """

    def __init__(self) -> None:
        self._polygon: Polygon | None = None
        self._zone_version: int = 0

    @property
    def zone_version(self) -> int:
        """Last-accepted version from the wire. Echoed on every
        `detection_result`. Starts at 0 (no polygon ever set)."""
        return self._zone_version

    @property
    def has_zone(self) -> bool:
        """True iff a polygon is currently active for evaluation."""
        return self._polygon is not None

    def set_zone(self, points: list[list[float]], zone_version: int) -> bool:
        """Install a new polygon. Returns True iff accepted.

        Rejects payloads with <3 vertices — those are either malformed
        or in-flight draft messages that the UI should have gated away.
        The version counter is NOT advanced on a rejection so a later
        valid update can still overwrite whatever was there before.

        The prepared geometry cache lives on `self._polygon` for the
        lifetime of this version. A subsequent set_zone with the same
        version would still rebuild — that's intentional, because the
        vertex list could have changed with the version unchanged (a
        bug upstream), and rebuilding is cheap relative to the per-
        frame evaluation savings. In practice versions always bump.
        """
        if len(points) < MIN_POLYGON_VERTICES:
            logger.warning(
                "ZoneService: rejecting polygon with %d vertices (need >= %d)",
                len(points),
                MIN_POLYGON_VERTICES,
            )
            return False

        # Shapely accepts a list of (x, y) tuples. FE sends list[list[float]]
        # but the inner tuples are effectively pairs; coerce defensively.
        try:
            ring = [(float(p[0]), float(p[1])) for p in points]
        except (TypeError, IndexError, ValueError) as exc:
            logger.warning("ZoneService: malformed polygon points: %s", exc)
            return False

        # Defensive: log if the client sent a version that went
        # backward. Don't reject — a reconnecting client that reset
        # its counter is still the authority on "what polygon is
        # active right now" — but the log lets us catch it if it
        # ever correlates with an alert-system bug.
        if zone_version <= self._zone_version and self._zone_version != 0:
            logger.warning(
                "ZoneService: non-monotonic zone_version (%d -> %d)",
                self._zone_version,
                zone_version,
            )

        polygon = Polygon(ring)
        # `prepare()` attaches an STR-tree on the polygon's edges so
        # subsequent contains() calls amortize the per-frame cost.
        # The call mutates `polygon` in place; no separate cache var.
        prepare(polygon)

        self._polygon = polygon
        self._zone_version = zone_version
        logger.info(
            "ZoneService: installed polygon v=%d with %d vertices",
            zone_version,
            len(ring),
        )
        return True

    def clear_zone(self, zone_version: int) -> None:
        """Drop the polygon but advance the version counter.

        The client bumps its local `zoneVersion` on every Clear, so
        the server must echo the new value going forward. Otherwise
        the client's version-mismatch gate (NW-1204) would see the
        server lagging by one version forever after a clear.
        """
        self._polygon = None
        self._zone_version = zone_version
        logger.info("ZoneService: cleared polygon, version now %d", zone_version)

    def evaluate(self, detections: list[WireDetection]) -> list[bool]:
        """Return a parallel list of in-zone flags, one per detection.

        When no polygon is installed every entry is False. The anchor
        is the bottom-center of each bbox, in the same normalized
        coordinate space as the polygon.

        Called every frame by the WS handler. The prepared polygon's
        STR-tree makes the typical-case contains() fast; pathological
        self-intersecting polygons fall back to O(V) per detection.
        """
        if self._polygon is None:
            return [False] * len(detections)

        flags: list[bool] = []
        for det in detections:
            x1, _y1, x2, y2 = det.bbox
            # Bottom-center anchor: midpoint of the bottom edge. For a
            # person bbox this is roughly where their feet land in the
            # frame, which is the intuitive "where is this object".
            anchor = Point((x1 + x2) / 2.0, y2)
            flags.append(self._polygon.contains(anchor))
        return flags
