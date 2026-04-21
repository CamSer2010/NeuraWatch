"""NW-1103: unit tests for _parse_results against mocked Ultralytics Results.

Covers track_id extraction (NW-1103 addition), class filtering (NW-1102),
and empty-input handling. Uses a minimal tensor stub so the tests don't
need torch or a real model.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from app.services.inference_service import _parse_results


class _FakeTensor:
    """Mimics the .cpu().numpy() contract of a torch Tensor."""

    def __init__(self, arr: np.ndarray) -> None:
        self._arr = arr

    def cpu(self) -> "_FakeTensor":
        return self

    def numpy(self) -> np.ndarray:
        return self._arr


class _FakeBoxes:
    def __init__(
        self,
        cls: list[int],
        conf: list[float],
        xyxy: list[list[float]],
        ids: list[int] | None,
    ) -> None:
        self.cls = _FakeTensor(np.asarray(cls))
        self.conf = _FakeTensor(np.asarray(conf, dtype=float))
        self.xyxy = _FakeTensor(np.asarray(xyxy, dtype=float))
        self.id = _FakeTensor(np.asarray(ids)) if ids is not None else None

    def __len__(self) -> int:
        return len(self.cls._arr)


def _result(boxes: _FakeBoxes | None) -> SimpleNamespace:
    return SimpleNamespace(boxes=boxes)


def test_parse_empty_inputs() -> None:
    assert _parse_results(None) == []
    assert _parse_results([]) == []
    assert _parse_results([_result(None)]) == []
    empty_boxes = _FakeBoxes(cls=[], conf=[], xyxy=[], ids=[])
    assert _parse_results([_result(empty_boxes)]) == []


def test_parse_with_track_ids() -> None:
    boxes = _FakeBoxes(
        cls=[0, 2],  # person, car -> vehicle
        conf=[0.9, 0.7],
        xyxy=[[10, 20, 100, 200], [150, 50, 300, 250]],
        ids=[1, 2],
    )
    dets = _parse_results([_result(boxes)])

    assert len(dets) == 2
    assert dets[0].object_class == "person"
    assert dets[0].track_id == 1
    assert dets[0].bbox == (10.0, 20.0, 100.0, 200.0)
    assert dets[0].confidence == 0.9
    assert dets[1].object_class == "vehicle"
    assert dets[1].track_id == 2


def test_parse_without_track_ids() -> None:
    # First frame before ByteTrack assigns IDs -> boxes.id is None.
    boxes = _FakeBoxes(
        cls=[0],
        conf=[0.85],
        xyxy=[[5, 10, 50, 100]],
        ids=None,
    )
    dets = _parse_results([_result(boxes)])

    assert len(dets) == 1
    assert dets[0].object_class == "person"
    assert dets[0].track_id is None


def test_parse_filters_unmapped_classes() -> None:
    # COCO 11 (stop sign) is not in _CLASS_MAP; should be dropped even
    # though it sneaks through with a track ID.
    boxes = _FakeBoxes(
        cls=[0, 11, 2],  # person, stop sign, car
        conf=[0.9, 0.8, 0.7],
        xyxy=[
            [10, 20, 100, 200],
            [200, 200, 250, 250],
            [300, 300, 400, 400],
        ],
        ids=[1, 2, 3],
    )
    dets = _parse_results([_result(boxes)])

    assert len(dets) == 2
    assert dets[0].object_class == "person"
    assert dets[0].track_id == 1
    assert dets[1].object_class == "vehicle"
    assert dets[1].track_id == 3  # stop sign (id=2) was dropped
