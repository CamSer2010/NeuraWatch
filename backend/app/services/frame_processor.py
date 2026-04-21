"""Backpressure-aware frame processor.

Wraps `InferenceService` with a dedicated worker thread and a size-1
queue so the WS handler (NW-1203) can submit frames without blocking
its own receive loop. Latest-wins dropping keeps the pipeline from
building a backlog under load: if a newer frame arrives while an
older one is still queued (not yet picked up by the worker), the
older submission's future is cancelled.

Per ratified decision #6: "Monotonic `seq` per frame; FE in-flight
boolean, 2s watchdog; server drops stale frames silently." This
module is the server-side half of that contract.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import threading

import numpy as np

from ..models.schemas import ProcessedFrame
from .inference_service import InferenceService

logger = logging.getLogger(__name__)

_QUEUE_POLL_SEC = 0.1
_SHUTDOWN_JOIN_SEC = 2.0


class FrameProcessor:
    """Serializes frame inference on a dedicated worker thread.

    Lifecycle:
      start() — launches the worker thread. Must be called from an
                async context (uses the running event loop to resolve
                submission futures).
      submit(frame, seq) — coroutine. Returns a `ProcessedFrame` with
                the caller's `seq` echoed and the detection list, or
                raises `asyncio.CancelledError` if the submission was
                superseded before the worker picked it up.
      stop()  — signals the worker to exit and drains pending futures.

    Errors raised by `InferenceService.process_frame` are caught and
    logged; the caller's future resolves with an empty detection list
    so one bad frame cannot crash the WS handler loop.
    """

    def __init__(self, inference_service: InferenceService) -> None:
        self._service = inference_service
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._queue: queue.Queue[
            tuple[np.ndarray, int, asyncio.Future[ProcessedFrame]]
        ] = queue.Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._submit_lock: asyncio.Lock | None = None
        self._dropped_count: int = 0

    # -------- lifecycle ---------------------------------------------------

    def start(self) -> None:
        """Capture the running loop and spawn the worker thread."""
        if self._thread is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._submit_lock = asyncio.Lock()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="InferenceWorker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the worker to exit and cancel any pending submissions."""
        self._stop_event.set()
        # First pass: cancel anything still sitting in the queue.
        self._drain_pending()
        if self._thread is not None:
            self._thread.join(timeout=_SHUTDOWN_JOIN_SEC)
            self._thread = None

    # -------- public API --------------------------------------------------

    @property
    def dropped_count(self) -> int:
        """How many submissions were displaced by a newer one. Diagnostic."""
        return self._dropped_count

    async def submit(
        self, frame: np.ndarray, seq: int
    ) -> ProcessedFrame:
        """Hand a frame to the worker; return the processed result.

        Raises `asyncio.CancelledError` if a newer submit arrives
        before this frame is picked up by the worker (latest-wins).
        Frames the worker has already popped are not cancelled.
        """
        if self._loop is None or self._submit_lock is None:
            raise RuntimeError("FrameProcessor.start() must be called first")

        future: asyncio.Future[ProcessedFrame] = self._loop.create_future()

        # Lock drain+put so two coroutines from the same session
        # (reset+retry, watchdog) can't race. The await is outside.
        async with self._submit_lock:
            # Latest-wins: clear any older queued submission by cancelling
            # its future. The worker may have already popped the slot —
            # that's fine, we'd just find an empty queue here.
            self._drain_pending()
            # Copy the frame so the caller can reuse / release their
            # buffer immediately after submit returns. ~900KB @ 640x480x3;
            # the copy is ~100µs on an M-series CPU — noise vs inference.
            buffered = np.ascontiguousarray(frame).copy()
            try:
                self._queue.put_nowait((buffered, seq, future))
            except queue.Full:
                # Unreachable under the lock + drain discipline above,
                # but defensive: log and resolve with an empty result.
                logger.warning(
                    "FrameProcessor queue full despite drain (seq=%d); dropping",
                    seq,
                )
                future.set_result(ProcessedFrame(seq=seq, detections=[]))

        return await future

    # -------- internals ---------------------------------------------------

    def _drain_pending(self) -> None:
        """Pop any queued submissions and cancel their futures."""
        while not self._queue.empty():
            try:
                _, _, stale_future = self._queue.get_nowait()
            except queue.Empty:
                return
            self._dropped_count += 1
            if not stale_future.done() and self._loop is not None:
                self._schedule_callback(stale_future.cancel)

    def _schedule_callback(self, callback, *args) -> None:
        """call_soon_threadsafe guarded against a closed event loop."""
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(callback, *args)
        except RuntimeError:
            # Loop already closed during shutdown; nothing to do.
            pass

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                frame, seq, future = self._queue.get(timeout=_QUEUE_POLL_SEC)
            except queue.Empty:
                continue

            # Stop signal after we popped but before we processed — cancel
            # rather than burn an inference cycle on a frame nobody will
            # receive.
            if self._stop_event.is_set():
                if not future.done():
                    self._schedule_callback(future.cancel)
                continue

            if future.cancelled():
                continue

            try:
                detections = self._service.process_frame(frame)
            except Exception:
                logger.exception(
                    "FrameProcessor worker failed on seq=%d", seq
                )
                detections = []

            if future.done():
                continue

            result = ProcessedFrame(seq=seq, detections=detections)
            self._schedule_callback(_set_if_pending, future, result)


def _set_if_pending(
    future: asyncio.Future[ProcessedFrame],
    result: ProcessedFrame,
) -> None:
    """Set a result only if the future is still pending.

    Callbacks scheduled via call_soon_threadsafe race with local
    cancellation; this guard keeps `InvalidStateError` out of the logs.
    """
    if not future.done():
        future.set_result(result)
