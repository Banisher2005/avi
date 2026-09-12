"""Centralized reliability and supervision layer providing no-hang guarantees, bounded timeouts, and failure containment."""

import logging
import threading
import time
from typing import Any, Callable

from avi.reliability.circuit_breaker import CircuitBreaker, CircuitBreakerRegistry
from avi.reliability.models import (
    FailureCategory,
    OperationRecord,
    OperationStatus,
    OperationType,
    SupervisedResult,
    TimeoutConfig,
)
from avi.reliability.watchdog import WatchdogSupervisor

logger = logging.getLogger("avi.reliability.supervisor")


class ReliabilitySupervisor:
    """Central supervisor coordinating timeouts, circuit breakers, retries, and failure containment."""

    _instance = None
    _lock = threading.RLock()

    def __init__(
        self,
        timeout_config: TimeoutConfig | None = None,
        timeouts: TimeoutConfig | None = None,
        circuit_breakers: CircuitBreakerRegistry | None = None,
        watchdog: WatchdogSupervisor | None = None,
    ) -> None:
        self.config = timeouts or timeout_config or TimeoutConfig()
        self.timeouts = self.config
        self.circuit_breakers = circuit_breakers or CircuitBreakerRegistry()
        self.watchdog = watchdog or WatchdogSupervisor(timeout_config=self.config)
        self.watchdog.start()

    @classmethod
    def get_instance(cls) -> "ReliabilitySupervisor":
        """Singleton accessor for centralized supervisor."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = ReliabilitySupervisor()
            return cls._instance

    def register_operation(
        self,
        op_type: OperationType = OperationType.TOOL_CALL,
        name: str = "",
        task_id: str = "",
        timeout: float | None = None,
        cancellation_token: Any | None = None,
        cleanup_callback: Callable[[], None] | None = None,
    ) -> OperationRecord:
        """Register an operation record under watchdog supervision."""
        actual_timeout = (
            timeout
            if timeout is not None
            else self.config.get_timeout_for_operation(op_type, name)
        )
        record = OperationRecord(
            task_id=task_id,
            name=name,
            operation_type=op_type,
            timeout=actual_timeout,
            cancellation_token=cancellation_token,
            status=OperationStatus.PENDING,
        )
        self.watchdog.register_operation(record, cleanup_callback=cleanup_callback)
        return record

    def start_operation(self, operation_id: str) -> None:
        """Mark operation as running."""
        with self.watchdog._lock:
            op = self.watchdog._active_operations.get(operation_id)
            if op:
                op.status = OperationStatus.RUNNING
                op.started_at = time.time()

    def complete_operation(self, operation_id: str, result: Any = None, user_message: str = "") -> None:
        """Mark operation completed and unregister."""
        with self.watchdog._lock:
            op = self.watchdog._active_operations.get(operation_id)
            if op:
                op.mark_completed(result, user_message=user_message)
        self.watchdog.unregister_operation(operation_id)

    def fail_operation(
        self,
        operation_id: str,
        error: str,
        category: FailureCategory = FailureCategory.TOOL_EXECUTION_FAILED,
        user_message: str = "",
    ) -> None:
        """Mark operation failed and unregister."""
        with self.watchdog._lock:
            op = self.watchdog._active_operations.get(operation_id)
            if op:
                op.mark_failed(error, category=category, user_message=user_message)
        self.watchdog.unregister_operation(operation_id)

    def cancel_operation(self, operation_id: str, reason: str = "") -> None:
        """Mark operation cancelled and unregister."""
        with self.watchdog._lock:
            op = self.watchdog._active_operations.get(operation_id)
            if op:
                op.mark_cancelled(reason)
        self.watchdog.unregister_operation(operation_id)

    def register_worker(
        self,
        task_id: str,
        thread: threading.Thread,
        cleanup_callback: Callable[[], None] | None = None,
        name: str = "",
    ) -> None:
        """Register a worker thread under supervision."""
        self.watchdog.register_worker(task_id, thread, cleanup_callback=cleanup_callback)

    def unregister_worker(self, task_id: str) -> None:
        """Unregister finished worker thread."""
        self.watchdog.unregister_worker(task_id)

    def execute_tool(
        self,
        capability_name: str,
        func: Callable[..., Any],
        args: dict[str, Any] | None = None,
        timeout: float | None = None,
        task_id: str = "",
        cancellation_token: Any | None = None,
        max_retries: int = 1,
        cleanup_callback: Callable[[], None] | None = None,
    ) -> SupervisedResult:
        """Execute a tool or capability call with strict supervision, timeouts, and failure containment."""
        actual_args = args or {}
        actual_timeout = (
            timeout
            if timeout is not None
            else self.config.get_timeout_for_operation(OperationType.TOOL_CALL, capability_name)
        )

        record = OperationRecord(
            task_id=task_id,
            name=capability_name,
            operation_type=OperationType.TOOL_CALL,
            timeout=actual_timeout,
            cancellation_token=cancellation_token,
            max_retries=max_retries,
            status=OperationStatus.RUNNING,
        )
        self.watchdog.register_operation(record, cleanup_callback=cleanup_callback)

        attempt = 0
        last_error = ""

        try:
            while attempt <= max_retries:
                attempt += 1
                record.retry_count = attempt - 1

                # Check cancellation
                if cancellation_token and hasattr(cancellation_token, "is_set") and cancellation_token.is_set():
                    record.mark_cancelled("Tool execution cancelled by user.")
                    return SupervisedResult(
                        success=False,
                        status=OperationStatus.CANCELLED,
                        error=record.error,
                        failure_category=FailureCategory.USER_CANCELLED,
                        user_message="Action was cancelled by user.",
                        duration_ms=record.duration_ms,
                        operation_record=record,
                    )

                # Execute with bounded thread join to prevent indefinite hangs
                result_container: dict[str, Any] = {"result": None, "error": None, "completed": False}

                def _target() -> None:
                    try:
                        result_container["result"] = func(**actual_args)
                        result_container["completed"] = True
                    except Exception as exc:
                        result_container["error"] = exc
                        result_container["completed"] = True

                t = threading.Thread(target=_target, name=f"tool_{capability_name}", daemon=True)
                t.start()
                t.join(timeout=actual_timeout)

                if not result_container["completed"]:
                    # Timed out
                    record.mark_timed_out()
                    user_msg = record.user_message or f"Action '{capability_name}' timed out after {actual_timeout:.1f}s."
                    logger.warning("Tool %s timed out after %.1fs", capability_name, actual_timeout)
                    return SupervisedResult(
                        success=False,
                        status=OperationStatus.TIMED_OUT,
                        error=record.error,
                        failure_category=FailureCategory.TOOL_TIMEOUT,
                        user_message=user_msg,
                        duration_ms=record.duration_ms,
                        operation_record=record,
                    )

                exc = result_container["error"]
                if exc is None:
                    # Success
                    res = result_container["result"]
                    record.mark_completed(res)
                    return SupervisedResult(
                        success=True,
                        status=OperationStatus.COMPLETED,
                        data=res,
                        user_message=getattr(res, "message", "") or getattr(res, "summary", "") or "Operation completed successfully.",
                        duration_ms=record.duration_ms,
                        operation_record=record,
                    )

                # Handle failure
                last_error = str(exc)
                logger.warning("Tool %s attempt %d failed: %s", capability_name, attempt, last_error)

                # Check retryability (transient network or temporary I/O errors only)
                is_retryable = any(
                    err_term in last_error.lower()
                    for err_term in ("connection reset", "broken pipe", "temporary failure", "resource temporarily unavailable")
                )

                if is_retryable and attempt <= max_retries:
                    time.sleep(0.2 * (2 ** (attempt - 1)))  # Exponential backoff
                    continue

                break

            # All retries exhausted or non-retryable failure
            cat = self._classify_tool_error(last_error)
            user_msg = self._generate_user_error_message(capability_name, last_error, cat)
            record.mark_failed(last_error, category=cat, user_message=user_msg)
            return SupervisedResult(
                success=False,
                status=OperationStatus.FAILED,
                error=last_error,
                failure_category=cat,
                user_message=user_msg,
                duration_ms=record.duration_ms,
                operation_record=record,
            )

        finally:
            self.watchdog.unregister_operation(record.operation_id)

    def execute_provider(
        self,
        provider_name: str,
        func: Callable[..., Any],
        prompt: str = "",
        timeout: float | None = None,
        task_id: str = "",
        cancellation_token: Any | None = None,
        cleanup_callback: Callable[[], None] | None = None,
    ) -> SupervisedResult:
        """Execute a provider generation call with circuit breaking, timeout enforcement, and isolation."""
        cb: CircuitBreaker = self.circuit_breakers.get_or_create(
            name=provider_name,
            failure_threshold=3,
            recovery_cooldown=30.0,
        )

        # 1. Circuit Breaker Check: Fast-fail if provider is known to be failing
        if not cb.allow_request():
            logger.info("Circuit breaker for provider '%s' is OPEN. Bypassing request.", provider_name)
            record = OperationRecord(
                task_id=task_id,
                name=f"provider.{provider_name}",
                operation_type=OperationType.PROVIDER_CALL,
                status=OperationStatus.FAILED,
                error=f"Circuit breaker for provider '{provider_name}' is OPEN.",
                failure_category=FailureCategory.PROVIDER_UNAVAILABLE,
                user_message="Local AI unavailable; deterministic capabilities remain available.",
            )
            return SupervisedResult(
                success=False,
                status=OperationStatus.FAILED,
                error=record.error,
                failure_category=FailureCategory.PROVIDER_UNAVAILABLE,
                user_message=record.user_message,
                operation_record=record,
            )

        actual_timeout = timeout if timeout is not None else self.config.provider_total_generation
        record = OperationRecord(
            task_id=task_id,
            name=f"provider.{provider_name}",
            operation_type=OperationType.PROVIDER_CALL,
            timeout=actual_timeout,
            cancellation_token=cancellation_token,
            status=OperationStatus.RUNNING,
        )
        self.watchdog.register_operation(record, cleanup_callback=cleanup_callback)

        try:
            # Check early cancellation
            if cancellation_token and hasattr(cancellation_token, "is_set") and cancellation_token.is_set():
                record.mark_cancelled("AI generation cancelled by user.")
                return SupervisedResult(
                    success=False,
                    status=OperationStatus.CANCELLED,
                    error=record.error,
                    failure_category=FailureCategory.USER_CANCELLED,
                    user_message="AI request was cancelled.",
                    operation_record=record,
                )

            result_container: dict[str, Any] = {"result": None, "error": None, "completed": False}

            def _target() -> None:
                try:
                    result_container["result"] = func()
                    result_container["completed"] = True
                except Exception as exc:
                    result_container["error"] = exc
                    result_container["completed"] = True

            t = threading.Thread(target=_target, name=f"provider_{provider_name}", daemon=True)
            t.start()
            t.join(timeout=actual_timeout)

            if not result_container["completed"]:
                # Timed out
                cb.record_failure(f"Timed out after {actual_timeout:.1f}s")
                record.mark_timed_out()
                return SupervisedResult(
                    success=False,
                    status=OperationStatus.TIMED_OUT,
                    error=record.error,
                    failure_category=FailureCategory.PROVIDER_TIMEOUT,
                    user_message="The local AI model timed out. Your other AVI commands are still available.",
                    duration_ms=record.duration_ms,
                    operation_record=record,
                )

            exc = result_container["error"]
            if exc is None:
                # Success
                cb.record_success()
                res = result_container["result"]
                record.mark_completed(res)
                return SupervisedResult(
                    success=True,
                    status=OperationStatus.COMPLETED,
                    data=res,
                    duration_ms=record.duration_ms,
                    operation_record=record,
                )

            # Failure
            err_msg = str(exc)
            cb.record_failure(err_msg)
            cat = FailureCategory.PROVIDER_UNAVAILABLE if any(
                term in err_msg.lower()
                for term in ("connection refused", "not running", "failed to connect", "service unavailable")
            ) else FailureCategory.PROVIDER_TIMEOUT if "timeout" in err_msg.lower() else FailureCategory.TOOL_EXECUTION_FAILED

            user_msg = (
                "Local AI service unavailable. Your deterministic commands (calculator, RAM, files) are still available."
                if cat == FailureCategory.PROVIDER_UNAVAILABLE
                else f"AI request failed: {err_msg}"
            )
            record.mark_failed(err_msg, category=cat, user_message=user_msg)
            return SupervisedResult(
                success=False,
                status=OperationStatus.FAILED,
                error=err_msg,
                failure_category=cat,
                user_message=user_msg,
                duration_ms=record.duration_ms,
                operation_record=record,
            )

        finally:
            self.watchdog.unregister_operation(record.operation_id)

    def execute_worker(
        self,
        task_id: str,
        target: Callable[..., Any] | None = None,
        worker_func: Callable[..., Any] | None = None,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        timeout: float | None = None,
        on_complete: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        cleanup_callback: Callable[[], None] | None = None,
    ) -> threading.Thread:
        """Spawn a supervised background worker with guaranteed exception capture and state reconciliation."""
        actual_target = target or worker_func
        if actual_target is None:
            raise ValueError("execute_worker requires target or worker_func")
        actual_kwargs = kwargs or {}
        actual_timeout = timeout if timeout is not None else self.config.task_total

        worker_op = OperationRecord(
            task_id=task_id,
            name=f"worker.{task_id}",
            operation_type=OperationType.BACKGROUND_WORKER,
            timeout=actual_timeout,
            status=OperationStatus.RUNNING,
        )
        self.watchdog.register_operation(worker_op)

        def _worker_wrapper() -> None:
            try:
                result = actual_target(*args, **actual_kwargs)
                worker_op.mark_completed(result)
                if on_complete:
                    try:
                        on_complete(result)
                    except Exception as exc:
                        logger.warning("Error in on_complete callback for task %s: %s", task_id, exc)
            except Exception as exc:
                logger.exception("Supervised worker for task %s failed with exception: %s", task_id, exc)
                worker_op.mark_failed(str(exc), category=FailureCategory.TOOL_EXECUTION_FAILED)
                if on_error:
                    try:
                        on_error(exc)
                    except Exception as cb_exc:
                        logger.warning("Error in on_error callback for task %s: %s", task_id, cb_exc)
            finally:
                self.watchdog.unregister_operation(worker_op.operation_id)
                self.watchdog.unregister_worker(task_id)

        t = threading.Thread(target=_worker_wrapper, name=f"worker_{task_id}", daemon=True)
        self.watchdog.register_worker(task_id, t, cleanup_callback=cleanup_callback)
        t.start()
        return t

    def execute_with_retry(
        self,
        func: Callable[[], Any],
        op_type: OperationType = OperationType.TOOL_CALL,
        name: str = "",
        task_id: str = "",
        max_retries: int = 3,
        initial_backoff: float = 0.1,
        backoff_factor: float = 2.0,
        retry_on: tuple[type[Exception], ...] | None = None,
    ) -> SupervisedResult:
        """Execute callable with bounded retries and exponential backoff."""
        exceptions_to_retry = retry_on or (Exception,)
        delay = initial_backoff
        last_exc: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                res = func()
                return SupervisedResult(
                    success=True,
                    status=OperationStatus.COMPLETED,
                    value=res,
                )
            except exceptions_to_retry as exc:
                last_exc = exc
                logger.warning(
                    "Operation '%s' failed attempt %d/%d: %s",
                    name or "unnamed",
                    attempt,
                    max_retries,
                    exc,
                )
                if attempt < max_retries:
                    time.sleep(delay)
                    delay *= backoff_factor

        category = FailureCategory.TOOL_EXECUTION_FAILED
        return SupervisedResult(
            success=False,
            status=OperationStatus.FAILED,
            error=str(last_exc) if last_exc else "Failed after retries",
            failure_category=category,
        )

    def diagnose_self(self, task_id: str = "") -> dict[str, Any]:
        """Produce a truthful, self-diagnostic snapshot for 'why are you stuck?' / 'diagnose yourself' queries."""
        active_ops = self.watchdog.get_active_operations()
        active_cbs = self.circuit_breakers.get_all_statuses()
        active_ops_dicts = [op.to_dict() for op in active_ops]
        active_workers = list(getattr(self.watchdog, "_active_workers", {}).keys())

        target_op = None
        if task_id:
            for op in active_ops:
                if op.task_id == task_id:
                    target_op = op
                    break
        elif active_ops:
            target_op = active_ops[0]

        now = time.time()
        if target_op:
            elapsed = now - target_op.started_at
            explanation = (
                f"Currently running operation '{target_op.name}' for {elapsed:.1f}s "
                f"(allocated timeout: {target_op.timeout:.1f}s). "
            )
            if elapsed > target_op.timeout:
                explanation += "The operation has exceeded its timeout threshold and is being reconciled. You can cancel it."
            else:
                explanation += "Execution is progressing. You can cancel it at any time."

            return {
                "active": True,
                "system_health": "busy",
                "operation": target_op.to_dict(),
                "active_operations": active_ops_dicts,
                "active_workers": active_workers,
                "elapsed_seconds": round(elapsed, 1),
                "explanation": explanation,
                "circuit_breakers": active_cbs,
            }

        return {
            "active": False,
            "system_health": "operational",
            "active_operations": active_ops_dicts,
            "active_workers": active_workers,
            "explanation": "No operations are currently stalled or executing in the background. System is ready.",
            "circuit_breakers": active_cbs,
        }

    def _classify_tool_error(self, error: str) -> FailureCategory:
        """Categorize tool failure from exception message."""
        e_lower = error.lower()
        if "timeout" in e_lower or "timed out" in e_lower:
            return FailureCategory.TOOL_TIMEOUT
        if "unknown" in e_lower or "not found" in e_lower or "unsupported" in e_lower:
            return FailureCategory.UNKNOWN_CAPABILITY
        if "missing" in e_lower or "invalid argument" in e_lower or "type" in e_lower:
            return FailureCategory.TOOL_INVALID_ARGUMENT
        if "cancelled" in e_lower:
            return FailureCategory.USER_CANCELLED
        return FailureCategory.TOOL_EXECUTION_FAILED

    def _generate_user_error_message(
        self,
        capability_name: str,
        error: str,
        category: FailureCategory,
    ) -> str:
        """Produce concise, actionable user-facing messages."""
        if category == FailureCategory.TOOL_TIMEOUT:
            if "search" in capability_name.lower():
                return "✗ File search took too long."
            if "browser" in capability_name.lower():
                return "✗ Browser navigation timed out."
            return f"✗ Action '{capability_name}' took too long and was stopped."

        if category == FailureCategory.UNKNOWN_CAPABILITY:
            return f"Unsupported capability: '{capability_name}'."

        if category == FailureCategory.TOOL_INVALID_ARGUMENT:
            return f"Invalid parameters for '{capability_name}': {error}"

        if "does not exist" in error:
            return f"Cannot perform '{capability_name}': target path does not exist."

        return f"Action '{capability_name}' failed: {error}"
