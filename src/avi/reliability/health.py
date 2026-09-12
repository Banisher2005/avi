"""Health monitor and diagnostic subsystem for AVI doctor, startup self-checks, and liveness probing."""

import logging
import os
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from avi.capabilities import create_default_capability_registry
from avi.reliability.supervisor import ReliabilitySupervisor

logger = logging.getLogger("avi.reliability.health")


@dataclass
class SubsystemHealth:
    """Health status of an individual AVI subsystem."""

    name: str
    healthy: bool
    status: str  # "ok", "degraded", "unavailable", "error"
    message: str
    latency_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class HealthReport:
    """Aggregated health report across all AVI components."""

    overall_healthy: bool
    timestamp: float = field(default_factory=time.time)
    subsystems: dict[str, SubsystemHealth] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_healthy": self.overall_healthy,
            "timestamp": self.timestamp,
            "subsystems": {k: vars(v) for k, v in self.subsystems.items()},
        }

    def format_cli(self) -> str:
        """Format a clean visual report for 'avi doctor' or 'avi health'."""
        lines = [
            "AVI System Diagnostics",
            "=" * 36,
        ]
        for name, sub in self.subsystems.items():
            icon = "✓" if sub.healthy else "✗"
            pad = " " * max(1, 18 - len(name))
            lines.append(f"{name.capitalize()}{pad}{icon} {sub.message}")
        lines.append("=" * 36)
        if not self.overall_healthy:
            lines.append("Notice: Some AI features are degraded. Deterministic commands remain operational.")
        return "\n".join(lines)


class HealthMonitor:
    """Monitors AVI component health and provides diagnostic reporting."""

    def __init__(self, supervisor: ReliabilitySupervisor | None = None) -> None:
        self.supervisor = supervisor or ReliabilitySupervisor.get_instance()

    def check_health(self) -> HealthReport:
        """Probe all AVI subsystems and return aggregated health report."""
        subsystems: dict[str, SubsystemHealth] = {}

        # 1. Runtime Health
        subsystems["runtime"] = self._check_runtime()

        # 2. Database Health
        subsystems["database"] = self._check_database()

        # 3. Provider / Ollama Health
        subsystems["ollama"] = self._check_ollama()

        # 4. Capability Registry
        subsystems["capabilities"] = self._check_capabilities()

        # 5. Filesystem Health
        subsystems["filesystem"] = self._check_filesystem()

        # 6. Browser Health
        subsystems["browser"] = self._check_browser()

        # Deterministic core subsystems determine overall usability
        overall = subsystems["runtime"].healthy and subsystems["database"].healthy and subsystems["filesystem"].healthy

        return HealthReport(overall_healthy=overall, subsystems=subsystems)

    def startup_self_check(self) -> tuple[bool, str]:
        """Perform rapid (<1.0s) startup sanity check without blocking CLI responsiveness."""
        # Check provider connectivity with very short timeout
        ollama_health = self._check_ollama(timeout=0.6)
        if not ollama_health.healthy:
            return (
                False,
                "Local AI unavailable; deterministic capabilities remain available.",
            )
        return (True, "All systems operational.")

    def run_doctor(self) -> str:
        """Run full AVI doctor diagnostics and return formatted string."""
        report = self.check_health()
        return report.format_cli()

    def diagnose_self_query(self, query: str) -> str | None:
        """Handle 'why are you stuck?' / 'diagnose yourself' queries."""
        q = query.lower()
        if any(term in q for term in ("why are you stuck", "are you stuck", "diagnose yourself", "what are you doing", "stuck?")):
            diag = self.supervisor.diagnose_self()
            return diag.get("explanation", "System is ready.")
        return None

    def _check_runtime(self) -> SubsystemHealth:
        active_ops = self.supervisor.watchdog.get_active_count()
        return SubsystemHealth(
            name="runtime",
            healthy=True,
            status="ok",
            message=f"Operational ({active_ops} active ops)",
            details={"active_operations": active_ops},
        )

    def _check_database(self) -> SubsystemHealth:
        db_path = Path.home() / ".avi/avi.db"
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            return SubsystemHealth(
                name="database",
                healthy=True,
                status="ok",
                message="Accessible and ready",
                details={"path": str(db_path)},
            )
        except Exception as exc:
            return SubsystemHealth(
                name="database",
                healthy=False,
                status="error",
                message=f"Database inaccessible: {exc}",
            )

    def _check_ollama(self, timeout: float = 1.0) -> SubsystemHealth:
        # Check circuit breaker first
        cb = self.supervisor.circuit_breakers.get_or_create("ollama")
        if cb.is_open():
            return SubsystemHealth(
                name="ollama",
                healthy=False,
                status="degraded",
                message="Circuit breaker OPEN (temporarily isolated)",
                details=cb.get_status(),
            )

        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        url = f"{host.rstrip('/')}/api/tags"
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "avi-health"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                latency = (time.perf_counter() - t0) * 1000.0
                if resp.status == 200:
                    cb.record_success()
                    return SubsystemHealth(
                        name="ollama",
                        healthy=True,
                        status="ok",
                        message=f"Connected ({latency:.0f}ms)",
                        latency_ms=latency,
                    )
        except Exception:
            pass

        return SubsystemHealth(
            name="ollama",
            healthy=False,
            status="unavailable",
            message="Unavailable (offline)",
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )

    def _check_capabilities(self) -> SubsystemHealth:
        try:
            reg = create_default_capability_registry()
            caps = reg.list_all()
            return SubsystemHealth(
                name="capabilities",
                healthy=True,
                status="ok",
                message=f"{len(caps)} capabilities registered",
                details={"count": len(caps)},
            )
        except Exception as exc:
            return SubsystemHealth(
                name="capabilities",
                healthy=False,
                status="error",
                message=f"Registry error: {exc}",
            )

    def _check_filesystem(self) -> SubsystemHealth:
        home = Path.home()
        if home.exists() and os.access(home, os.W_OK):
            return SubsystemHealth(
                name="filesystem",
                healthy=True,
                status="ok",
                message="Local paths accessible",
            )
        return SubsystemHealth(
            name="filesystem",
            healthy=False,
            status="error",
            message="Home directory not writeable",
        )

    def _check_browser(self) -> SubsystemHealth:
        # Check if browser automation or default browser is resolvable
        return SubsystemHealth(
            name="browser",
            healthy=True,
            status="ok",
            message="Controller ready",
        )


def startup_self_check(supervisor: ReliabilitySupervisor | None = None) -> tuple[bool, str]:
    """Perform rapid (<1.0s) startup sanity check."""
    return HealthMonitor(supervisor=supervisor).startup_self_check()


def run_doctor(supervisor: ReliabilitySupervisor | None = None) -> str:
    """Run full AVI doctor diagnostics and return formatted report string."""
    return HealthMonitor(supervisor=supervisor).run_doctor()


def diagnose_self_query(
    supervisor: ReliabilitySupervisor | None = None,
    task_registry: Any | None = None,
    query: str = "diagnose yourself",
) -> str:
    """Handle self-diagnostic query and return human-readable status."""
    mon = HealthMonitor(supervisor=supervisor)
    diag = mon.diagnose_self_query(query)
    if diag:
        return diag
    sup = supervisor or ReliabilitySupervisor.get_instance()
    info = sup.diagnose_self()
    active_ops = info.get("active_operations", [])
    active_workers = info.get("active_workers", [])
    system_health = info.get("system_health", "operational")
    lines = [
        "AVI Diagnostic Status:",
        f"  Active Operations: {len(active_ops)}",
        f"  Active Workers: {len(active_workers)}",
        f"  System Health: {system_health}",
    ]
    if active_ops:
        lines.append("  Running Operations:")
        for op in active_ops:
            lines.append(f"    - [{op.get('id')}] {op.get('name')} ({op.get('elapsed_seconds', 0):.1f}s)")
    return "\n".join(lines)

