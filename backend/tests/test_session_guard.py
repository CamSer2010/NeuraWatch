"""NW-1103: single-active-session guard state machine.

InferenceService's session guard is application-level state — tests
here don't need a loaded YOLO model.
"""
from __future__ import annotations

from pathlib import Path

from app.services.inference_service import InferenceService


def _service() -> InferenceService:
    return InferenceService(
        weights_path=Path("/tmp/neurawatch-test-not-a-real-path.pt"),
        imgsz=640,
        conf_threshold=0.4,
    )


def test_claim_on_empty_service_succeeds() -> None:
    svc = _service()
    assert svc.active_session is None
    assert svc.claim_session("sess-a") is True
    assert svc.active_session == "sess-a"


def test_claim_is_idempotent_for_same_session() -> None:
    svc = _service()
    svc.claim_session("sess-a")
    assert svc.claim_session("sess-a") is True
    assert svc.active_session == "sess-a"


def test_claim_rejected_for_different_session() -> None:
    svc = _service()
    svc.claim_session("sess-a")
    assert svc.claim_session("sess-b") is False
    assert svc.active_session == "sess-a"  # unchanged


def test_release_with_wrong_session_is_noop() -> None:
    svc = _service()
    svc.claim_session("sess-a")
    svc.release_session("sess-b")
    assert svc.active_session == "sess-a"


def test_release_with_right_session_clears() -> None:
    svc = _service()
    svc.claim_session("sess-a")
    svc.release_session("sess-a")
    assert svc.active_session is None


def test_second_session_can_claim_after_release() -> None:
    svc = _service()
    svc.claim_session("sess-a")
    svc.release_session("sess-a")
    assert svc.claim_session("sess-b") is True
    assert svc.active_session == "sess-b"


def test_reset_tracker_is_safe_on_unloaded_service() -> None:
    # No model loaded; should be a silent no-op rather than an error.
    svc = _service()
    svc.reset_tracker()
