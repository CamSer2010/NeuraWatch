"""NW-1104: unit tests for InferenceService.process_frame.

Covers bbox normalization (AC: 0-1 against processed frame),
multi-detection handling, and the "errors return []" contract.
Uses mocks so no torch / no real model is needed.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.models.schemas import Detection
from app.services.inference_service import InferenceService


def _loaded_service() -> InferenceService:
    svc = InferenceService(
        weights_path=Path("/tmp/neurawatch-test-not-real.pt"),
        imgsz=640,
        conf_threshold=0.4,
    )
    # Bypass real model load; just need `is_loaded` to be True.
    svc._model = MagicMock()
    return svc


def test_process_frame_normalizes_bbox() -> None:
    svc = _loaded_service()
    frame = np.zeros((100, 200, 3), dtype=np.uint8)  # h=100, w=200

    pixel_det = Detection(
        object_class="person",
        bbox=(50.0, 20.0, 150.0, 80.0),
        confidence=0.9,
        track_id=1,
    )
    with patch.object(svc, "predict", return_value=[pixel_det]):
        wire_dets = svc.process_frame(frame)

    assert len(wire_dets) == 1
    d = wire_dets[0]
    assert d.object_class == "person"
    # x1/w=0.25, y1/h=0.2, x2/w=0.75, y2/h=0.8
    assert d.bbox == (0.25, 0.2, 0.75, 0.8)
    assert d.confidence == 0.9
    assert d.track_id == 1


def test_process_frame_handles_multiple_detections() -> None:
    svc = _loaded_service()
    frame = np.zeros((400, 800, 3), dtype=np.uint8)

    dets = [
        Detection(
            object_class="person",
            bbox=(0.0, 0.0, 400.0, 200.0),
            confidence=0.9,
            track_id=1,
        ),
        Detection(
            object_class="vehicle",
            bbox=(400.0, 200.0, 800.0, 400.0),
            confidence=0.8,
            track_id=2,
        ),
    ]
    with patch.object(svc, "predict", return_value=dets):
        wire = svc.process_frame(frame)

    assert len(wire) == 2
    assert wire[0].bbox == (0.0, 0.0, 0.5, 0.5)
    assert wire[1].bbox == (0.5, 0.5, 1.0, 1.0)


def test_process_frame_preserves_track_id_none() -> None:
    svc = _loaded_service()
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    det = Detection(
        object_class="bicycle",
        bbox=(10.0, 10.0, 50.0, 50.0),
        confidence=0.7,
        track_id=None,
    )
    with patch.object(svc, "predict", return_value=[det]):
        wire = svc.process_frame(frame)
    assert wire[0].track_id is None


def test_process_frame_returns_empty_when_predict_raises() -> None:
    svc = _loaded_service()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    with patch.object(svc, "predict", side_effect=RuntimeError("boom")):
        result = svc.process_frame(frame)
    assert result == []


def test_process_frame_returns_empty_when_model_not_loaded() -> None:
    svc = InferenceService(
        weights_path=Path("/tmp/neurawatch-test-not-real.pt"),
        imgsz=640,
        conf_threshold=0.4,
    )  # _model left as None
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert svc.process_frame(frame) == []


@pytest.mark.parametrize(
    "bad_frame",
    [
        np.zeros((480, 640), dtype=np.uint8),  # 2D
        np.zeros((480, 640, 4), dtype=np.uint8),  # RGBA, 4 channels
    ],
)
def test_process_frame_rejects_bad_frame_shapes(bad_frame: np.ndarray) -> None:
    svc = _loaded_service()
    # predict should NOT be called — process_frame short-circuits on shape.
    with patch.object(svc, "predict", side_effect=AssertionError("unreached")):
        assert svc.process_frame(bad_frame) == []
