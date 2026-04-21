"""NW-1104: unit tests for FrameProcessor.

Covers:
  - start/stop lifecycle
  - submit returns the worker's result
  - queued submission is cancelled when a newer one arrives
  - submit raises if start() hasn't been called

Uses a mock service whose process_frame can be made artificially slow
so the latest-wins behavior is deterministic.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.services.frame_processor import FrameProcessor
from app.services.inference_service import InferenceService


def _mock_service(delay_sec: float = 0.0) -> MagicMock:
    svc = MagicMock(spec=InferenceService)

    def _process_frame(_frame: np.ndarray) -> list:
        if delay_sec:
            time.sleep(delay_sec)
        return []

    svc.process_frame.side_effect = _process_frame
    return svc


def test_submit_without_start_raises() -> None:
    async def _run() -> None:
        proc = FrameProcessor(_mock_service())
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        with pytest.raises(RuntimeError):
            await proc.submit(frame, seq=1)

    asyncio.run(_run())


def test_submit_returns_processed_frame_with_echoed_seq() -> None:
    async def _run() -> None:
        proc = FrameProcessor(_mock_service())
        proc.start()
        try:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            result = await proc.submit(frame, seq=42)
            assert result.seq == 42
            assert result.detections == []
        finally:
            proc.stop()

    asyncio.run(_run())


def test_queued_submission_cancelled_by_newer_one() -> None:
    """frame1 is processed (slow). frame2 enters the queue. frame3
    arrives and cancels frame2's future before the worker picks it up.
    Expected: frame1 and frame3 complete; frame2 raises CancelledError.
    dropped_count should reflect the displacement.
    """

    async def _run() -> None:
        proc = FrameProcessor(_mock_service(delay_sec=0.3))
        proc.start()
        try:
            frame = np.zeros((480, 640, 3), dtype=np.uint8)

            task1 = asyncio.create_task(proc.submit(frame, seq=1))
            # Let the worker pop frame1 and start processing it
            await asyncio.sleep(0.05)

            task2 = asyncio.create_task(proc.submit(frame, seq=2))
            # Let task2 queue up
            await asyncio.sleep(0.02)

            task3 = asyncio.create_task(proc.submit(frame, seq=3))

            # task1 (in-flight) completes normally
            r1 = await task1
            assert r1.seq == 1 and r1.detections == []

            # task2 (queued, then displaced) was cancelled
            with pytest.raises(asyncio.CancelledError):
                await task2

            # task3 (the newest) completes
            r3 = await task3
            assert r3.seq == 3 and r3.detections == []

            # Diagnostic counter picked up the drop
            assert proc.dropped_count >= 1
        finally:
            proc.stop()

    asyncio.run(_run())


def test_submit_copies_frame_so_caller_can_mutate() -> None:
    """If the caller mutates their frame reference after submit, the
    worker must still see the original bytes.
    """

    async def _run() -> None:
        svc = MagicMock(spec=InferenceService)
        observed = {}

        def _capture(frame: np.ndarray) -> list:
            observed["mean"] = float(frame.mean())
            return []

        svc.process_frame.side_effect = _capture

        proc = FrameProcessor(svc)
        proc.start()
        try:
            frame = np.full((480, 640, 3), 100, dtype=np.uint8)
            task = asyncio.create_task(proc.submit(frame, seq=1))
            # Mutate the caller's frame immediately after submit returns
            await asyncio.sleep(0)
            frame.fill(0)
            await task
            # Worker must have seen the original 100, not the zeroed version
            assert observed["mean"] == 100.0
        finally:
            proc.stop()

    asyncio.run(_run())


def test_stop_is_idempotent_and_joins() -> None:
    async def _run() -> None:
        proc = FrameProcessor(_mock_service())
        proc.start()
        proc.stop()
        proc.stop()  # second call must not raise

    asyncio.run(_run())
