"""NW-1302 AC: point-in-polygon zone evaluation.

Covers:
- `set_zone` rejects <3 vertices and rejects malformed points
- `set_zone` accepts ≥3 vertices and advances `zone_version`
- `clear_zone` drops the polygon and advances `zone_version`
- `evaluate` uses bottom-center anchor in normalized 0–1 space
- `evaluate` returns all-False when no polygon is installed
- Repeated `evaluate` calls reuse the same Polygon instance (cache)
"""
from __future__ import annotations

import pytest

from app.models.schemas import WireDetection
from app.services.zone_service import ZoneService


def _det(bbox: tuple[float, float, float, float]) -> WireDetection:
    """Minimal WireDetection for evaluation tests."""
    return WireDetection(
        object_class="person",
        bbox=bbox,
        confidence=0.9,
        track_id=1,
    )


# ---- set_zone guards ---------------------------------------------------

def test_rejects_fewer_than_three_points() -> None:
    svc = ZoneService()
    assert svc.set_zone([[0.1, 0.1]], zone_version=1) is False
    assert svc.set_zone([[0.1, 0.1], [0.9, 0.1]], zone_version=1) is False
    # Version must NOT advance on rejection — the service should
    # stay at the last-accepted state so a later valid update still
    # lands on an uncontaminated counter.
    assert svc.zone_version == 0
    assert svc.has_zone is False


def test_rejects_malformed_points() -> None:
    svc = ZoneService()
    bad = [[0.1, 0.1], [0.9, 0.1], "not-a-pair"]  # type: ignore[list-item]
    assert svc.set_zone(bad, zone_version=1) is False
    assert svc.zone_version == 0
    assert svc.has_zone is False


def test_accepts_triangle_and_bumps_version() -> None:
    svc = ZoneService()
    triangle = [[0.2, 0.2], [0.8, 0.2], [0.5, 0.8]]
    assert svc.set_zone(triangle, zone_version=7) is True
    assert svc.zone_version == 7
    assert svc.has_zone is True


# ---- clear_zone --------------------------------------------------------

def test_clear_drops_polygon_and_advances_version() -> None:
    svc = ZoneService()
    svc.set_zone([[0.2, 0.2], [0.8, 0.2], [0.5, 0.8]], zone_version=4)
    assert svc.has_zone is True

    svc.clear_zone(zone_version=5)
    assert svc.has_zone is False
    assert svc.zone_version == 5


def test_clear_on_fresh_service_sets_version() -> None:
    # Even with nothing installed, clear must still echo the new
    # version so the client's monotonic counter stays aligned.
    svc = ZoneService()
    svc.clear_zone(zone_version=3)
    assert svc.has_zone is False
    assert svc.zone_version == 3


# ---- evaluate ----------------------------------------------------------

def test_evaluate_returns_all_false_without_polygon() -> None:
    svc = ZoneService()
    dets = [_det((0.1, 0.1, 0.3, 0.3)), _det((0.5, 0.5, 0.7, 0.7))]
    assert svc.evaluate(dets) == [False, False]


def test_evaluate_uses_bottom_center_anchor() -> None:
    """A square polygon in the bottom-right quadrant.

    A bbox centered top-left with its BOTTOM edge inside the polygon
    should evaluate True. A bbox entirely above the polygon, even if
    its centroid is inside the polygon's x-range, should evaluate
    False — proving we're anchoring on (xmid, y2), not the centroid.
    """
    svc = ZoneService()
    # Polygon covers [0.5, 1.0] x [0.5, 1.0]
    svc.set_zone(
        [[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0]],
        zone_version=1,
    )

    # bbox whose bottom-center (0.75, 0.6) is inside the polygon.
    inside = _det((0.7, 0.3, 0.8, 0.6))
    # bbox whose bottom-center (0.75, 0.4) is ABOVE the polygon even
    # though the centroid (0.75, 0.3) is not inside either — just
    # confirms anchor != centroid would have flipped this result
    # if we'd chosen the centroid.
    above = _det((0.7, 0.2, 0.8, 0.4))

    flags = svc.evaluate([inside, above])
    assert flags == [True, False]


def test_evaluate_handles_empty_detection_list() -> None:
    svc = ZoneService()
    svc.set_zone([[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]], zone_version=1)
    assert svc.evaluate([]) == []


def test_evaluate_with_no_polygon_still_handles_empty() -> None:
    svc = ZoneService()
    assert svc.evaluate([]) == []


# ---- caching -----------------------------------------------------------

def test_polygon_cached_across_evaluations() -> None:
    """AC: 'Polygon object cached per zone_version; not reconstructed
    per frame.' The service should reuse the same Shapely instance
    until set_zone / clear_zone mutates it."""
    svc = ZoneService()
    svc.set_zone([[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]], zone_version=1)
    polygon_first = svc._polygon  # type: ignore[attr-defined]  # test-only access

    for _ in range(5):
        svc.evaluate([_det((0.4, 0.4, 0.6, 0.6))])
        # Identity check — not just equality — to prove we didn't
        # rebuild a structurally-identical polygon on every call.
        assert svc._polygon is polygon_first  # type: ignore[attr-defined]


def test_set_zone_replaces_cached_polygon() -> None:
    svc = ZoneService()
    svc.set_zone([[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]], zone_version=1)
    polygon_v1 = svc._polygon  # type: ignore[attr-defined]
    svc.set_zone([[0.2, 0.2], [0.8, 0.2], [0.5, 0.9]], zone_version=2)
    polygon_v2 = svc._polygon  # type: ignore[attr-defined]

    assert polygon_v1 is not polygon_v2
    assert svc.zone_version == 2


# ---- boundary behavior -------------------------------------------------

@pytest.mark.parametrize(
    "bbox, expected",
    [
        # Bottom-center (0.5, 0.5) is well inside the isoceles
        # triangle (0,0)→(1,0)→(0.5,1).
        ((0.4, 0.0, 0.6, 0.5), True),
        # Outside-left of the left edge: (0.1, 0.5) is in the negative
        # half-plane of the (0,0)→(0.5,1) edge.
        ((0.05, 0.3, 0.15, 0.5), False),
        # Bottom-center on the apex side: (0.5, 1.0) coincides with
        # the vertex — Shapely.contains excludes boundary, so False.
        # (`covers` would include boundary; NW-1303 should stay on
        # `contains` semantics — edge-skim events add noise without
        # demo value.)
        ((0.45, 0.8, 0.55, 1.0), False),
    ],
)
def test_triangle_boundary_cases(
    bbox: tuple[float, float, float, float], expected: bool
) -> None:
    svc = ZoneService()
    svc.set_zone([[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]], zone_version=1)
    assert svc.evaluate([_det(bbox)]) == [expected]
