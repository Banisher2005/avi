"""Watchdog supervisor providing bounded execution monitoring, lost callback detection, and state reconciliation."""

import logging
import threading
import time
from typing import Callable

from avi.reliability.models import (
    OperationRecord,
    OperationStatus,
    TimeoutConfig,
)

logger = logging.getLogger("avi.reliability.watchdog")


class WatchdogSupervisor:
    """Watches running operations and workers to enforce hard timeout bounds and state reconciliation."""

    def __init__(
        self,
        timeout_config: TimeoutConfig | None = None,
        check_interval: float | None = None,
    ) -> None:
        self.config = timeout_config or TimeoutConfig()
        if check_interval is not None:
            self.config.watchdog_check_interval = check_interval
        self._active_operations: dict[str, OperationRecord] = {}
        self._active_workers: dict[str, threading.Thread] = {}
        self._cleanup_callbacks: dict[str, list[Callable[[], None]]] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._watchdog_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start background watchdog thread if not already running."""
        with self._lock:
            if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
                self._stop_event.clear()
                self._watchdog_thread = threading.Thread(
                    target=self._watchdog_loop,
                    name="avi_watchdog_supervisor",
                    daemon=True,
                )
                self._watchdog_thread.start()
                logger.debug("WatchdogSupervisor started.")

    def stop(self) -> None:
        """Stop background watchdog thread."""
        with self._lock:
            self._stop_event.set()
            if self._watchdog_thread and self._watchdog_thread.is_alive():
                self._watchdog_thread.join(timeout=1.0)
            self._watchdog_thread = None
            logger.debug("WatchdogSupervisor stopped.")

    def register_operation(
        self,
        record: OperationRecord,
        cleanup_callback: Callable[[], None] | None = None,
    ) -> None:
        """Register an operation to be actively monitored by the watchdog."""
        with self._lock:
            self._active_operations[record.operation_id] = record
            if cleanup_callback:
                self._cleanup_callbacks.setdefault(record.operation_id, []).append(cleanup_callback)
            self.start()

    def register_worker(
        self,
        task_id: str,
        thread: threading.Thread,
        cleanup_callback: Callable[[], None] | None = None,
    ) -> None:
        """Register a background worker thread."""
        with self._lock:
            self._active_workers[task_id] = thread
            if cleanup_callback:
                self._cleanup_callbacks.setdefault(task_id, []).append(cleanup_callback)
            self.start()

    def unregister_operation(self, operation_id: str) -> OperationRecord | None:
        """Unregister a completed operation and release its cleanup handles."""
        with self._lock:
            rec = self._active_operations.pop(operation_id, None)
            callbacks = self._cleanup_callbacks.pop(operation_id, [])
            for cb in callbacks:
                try:
                    cb()
                except Exception as exc:
                    logger.warning("Error running cleanup callback for op %s: %s", operation_id, exc)
            return rec

    def unregister_worker(self, task_id: str) -> None:
        """Unregister a worker thread and execute cleanup."""
        with self._lock:
            self._active_workers.pop(task_id, None)
            callbacks = self._cleanup_callbacks.pop(task_id, [])
            for cb in callbacks:
                try:
                    cb()
                except Exception as exc:
                    logger.warning("Error running worker cleanup for task %s: %s", task_id, exc)

    def reconcile(self, on_timeout: Callable[[OperationRecord], None] | None = None) -> list[str]:
        """Perform on-demand reconciliation with optional timeout callback."""
        reconciled = []
        with self._lock:
            for op_id, op in list(self._active_operations.items()):
                if op.status == OperationStatus.RUNNING and op.is_expired:
                    op.status = OperationStatus.TIMED_OUT
                    reconciled.append(op_id)
                    if on_timeout:
                        try:
                            on_timeout(op)
                        except Exception as exc:
                            logger.warning("Error in on_timeout callback: %s", exc)
            self._check_and_reconcile()
        return reconciled

    def reconcile_now(self) -> list[str]:
        """Perform immediate on-demand reconciliation of active operations and workers."""
        return self._check_and_reconcile()

    def get_active_count(self) -> int:
        """Return number of currently active supervised operations."""
        with self._lock:
            return len(self._active_operations)

    def get_active_operations(self) -> list[OperationRecord]:
        """Return snapshot of active operations."""
        with self._lock:
            return list(self._active_operations.values())

    def _watchdog_loop(self) -> None:
        """Periodic background loop checking timeouts and detecting lost callbacks."""
        interval = max(0.1, self.config.watchdog_check_interval)
        while not self._stop_event.is_set():
            try:
                self._check_and_reconcile()
            except Exception as exc:
                logger.exception("Error in watchdog reconciliation loop: %s", exc)
            self._stop_event.wait(timeout=interval)

    def _check_and_reconcile(self) -> list[str]:
        """Check all active operations against timeouts and verify worker liveness."""
        reconciled_ops: list[str] = []

        with self._lock:
            # 1. Timeout Check for Active Operations
            for op_id, op in list(self._active_operations.items()):
                if op.status == OperationStatus.RUNNING and op.is_expired:
                    logger.warning(
                        "Watchdog detected TIMEOUT on operation '%s' (%s) after %.1fs (timeout: %.1fs)",
                        op.name,
                        op.operation_id,
                        time.time() - op.started_at,
                        op.timeout,
                    )
                    # Trigger cancellation token if present
                    if op.cancellation_token and hasattr(op.cancellation_token, "set"):
                        try:
                            op.cancellation_token.set()
                        except Exception:
                            pass

                    op.mark_timed_out()
                    reconciled_ops.append(op_id)

                    # Run registered cleanups
                    callbacks = self._cleanup_callbacks.pop(op_id, [])
                    for cb in callbacks:
                        try:
                            cb()
                        except Exception as exc:
                            logger.warning("Error during timeout cleanup callback: %s", exc)

            # 2. Worker Liveness & Lost Callback Check
            for task_id, thread in list(self._active_workers.items()):
                if not thread.is_alive():
                    # Thread died
                    logger.info("Worker thread for task %s is dead; reconciling worker registration.", task_id)
                    self._active_workers.pop(task_id, None)
                    callbacks = self._cleanup_callbacks.pop(task_id, [])
                    for cb in callbacks:
                        try:
                            cb()
                        except Exception as exc:
                            logger.warning("Error running cleanup for dead worker %s: %s", task_id, exc)

        return reconciled_ops
