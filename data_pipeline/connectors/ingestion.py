"""
Live multi-vendor telemetry ingestion.

One polling task per configured vendor feeds a single bounded queue. A deterministic
rolling-baseline detector (merged across vendors by service/metric) decides which
signals are suspicious; only those are escalated to the LLM-based monitoring pipeline.
A vendor outage backs off independently and never stops the other vendors.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from data_pipeline.connectors.adapters.base import TelemetrySourceError
from data_pipeline.connectors.query_templates import CANONICAL_METRICS
from shared.utils import get_logger

if TYPE_CHECKING:
    from datetime import datetime

    from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter
    from orchestrator import AgentOrchestrator
    from shared.schemas import NormalizedTelemetryRecord

logger = get_logger("ingestion")

MAX_BACKOFF_SECONDS = 300.0
HISTORY_LEN = 120


@dataclass
class VendorStatus:
    enabled: bool = True
    last_success_at: float | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    records_ingested: int = 0


@dataclass
class _SeriesState:
    values: deque[float] = field(default_factory=lambda: deque(maxlen=HISTORY_LEN))
    last_ts: datetime | None = None


class StreamDetector:
    """Rolling z-score detector keyed by (service, metric), shared by all vendors."""

    def __init__(self, z_threshold: float, min_samples: int, corroboration_window: float = 120.0) -> None:
        self.z_threshold = z_threshold
        self.min_samples = min_samples
        self.corroboration_window = corroboration_window
        self._series: dict[tuple[str, str, str], _SeriesState] = {}
        self._flags: dict[tuple[str, str], dict[str, float]] = {}

    def observe(self, rec: NormalizedTelemetryRecord) -> dict[str, Any] | None:
        """Return suspicion details if this record is anomalous versus its own history."""
        key = (rec.connector_source.value, rec.entity, rec.metric_name)
        state = self._series.setdefault(key, _SeriesState())
        if state.last_ts is not None and rec.timestamp <= state.last_ts:
            return None
        state.last_ts = rec.timestamp

        suspicious = None
        if len(state.values) >= self.min_samples:
            mean = sum(state.values) / len(state.values)
            var = sum((v - mean) ** 2 for v in state.values) / len(state.values)
            std = max(math.sqrt(var), 0.05 * abs(mean), 1e-6)
            z = (rec.value - mean) / std
            if z >= self.z_threshold:
                suspicious = {"z_score": round(z, 2), "baseline_mean": round(mean, 4)}
        state.values.append(rec.value)
        if suspicious is None:
            return None

        now = time.monotonic()
        flags = self._flags.setdefault((rec.entity, rec.metric_name), {})
        flags[rec.connector_source.value] = now
        recent = sorted(s for s, t in flags.items() if now - t <= self.corroboration_window)
        suspicious["flagged_by"] = recent
        return suspicious


class TelemetryIngestionService:
    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        adapters: dict[str, BaseTelemetryAdapter],
        services: list[str],
        poll_interval_seconds: float = 15.0,
        z_threshold: float = 3.0,
        min_samples: int = 8,
        dry_run: bool = True,
        cooldown_seconds: float = 300.0,
        max_concurrent_escalations: int = 2,
        metrics: tuple[str, ...] = CANONICAL_METRICS,
    ) -> None:
        self.orchestrator = orchestrator
        self.adapters = adapters
        self.services = services
        self.poll_interval = poll_interval_seconds
        self.dry_run = dry_run
        self.cooldown = cooldown_seconds
        self.metrics = list(metrics)
        self.detector = StreamDetector(z_threshold, min_samples)
        self.queue: asyncio.Queue[NormalizedTelemetryRecord] = asyncio.Queue(maxsize=5000)
        self.status: dict[str, VendorStatus] = {name: VendorStatus() for name in adapters}
        self._last_escalation: dict[tuple[str, str], float] = {}
        self._sem = asyncio.Semaphore(max_concurrent_escalations)
        self._tasks: list[asyncio.Task[Any]] = []
        self.escalations = 0

    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._poll_vendor(n, a)) for n, a in self.adapters.items()]
        self._tasks.append(asyncio.create_task(self._consume()))
        logger.info("ingestion_started", vendors=sorted(self.adapters), services=self.services)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for adapter in self.adapters.values():
            closer = getattr(adapter, "close", None)
            if closer is not None:
                await closer()

    def snapshot(self) -> dict[str, Any]:
        return {
            "services": self.services,
            "queue_depth": self.queue.qsize(),
            "escalations": self.escalations,
            "vendors": {
                name: {
                    "last_success_age_seconds": (
                        round(time.monotonic() - st.last_success_at, 1) if st.last_success_at else None
                    ),
                    "last_error": st.last_error,
                    "consecutive_failures": st.consecutive_failures,
                    "records_ingested": st.records_ingested,
                }
                for name, st in self.status.items()
            },
        }

    async def poll_once(self, name: str, adapter: BaseTelemetryAdapter) -> int:
        """Poll one vendor for every watched service; raises TelemetrySourceError only if all fail."""
        st = self.status[name]
        window = max(300, int(self.poll_interval * 4))
        ingested = 0
        errors: list[str] = []
        for service in self.services:
            try:
                records = await adapter.fetch_signals(service, self.metrics, window)
            except TelemetrySourceError as exc:
                errors.append(str(exc))
                continue
            for rec in sorted(records, key=lambda r: r.timestamp):
                try:
                    self.queue.put_nowait(rec)
                    ingested += 1
                except asyncio.QueueFull:
                    logger.warning("ingestion_queue_full", vendor=name)
        if errors and len(errors) == len(self.services):
            raise TelemetrySourceError(errors[0])
        st.last_success_at = time.monotonic()
        st.last_error = errors[0] if errors else None
        st.consecutive_failures = 0
        st.records_ingested += ingested
        return ingested

    async def _poll_vendor(self, name: str, adapter: BaseTelemetryAdapter) -> None:
        st = self.status[name]
        while True:
            delay = self.poll_interval
            try:
                await self.poll_once(name, adapter)
            except asyncio.CancelledError:
                raise
            except TelemetrySourceError as exc:
                st.consecutive_failures += 1
                st.last_error = str(exc)
                delay = min(self.poll_interval * 2**st.consecutive_failures, MAX_BACKOFF_SECONDS)
                logger.warning("ingestion_vendor_failed", vendor=name, error=str(exc), retry_in=delay)
            except Exception as exc:
                st.consecutive_failures += 1
                st.last_error = f"unexpected: {exc}"
                delay = min(self.poll_interval * 2**st.consecutive_failures, MAX_BACKOFF_SECONDS)
                logger.exception("ingestion_vendor_error", vendor=name)
            await asyncio.sleep(delay)

    async def _consume(self) -> None:
        while True:
            rec = await self.queue.get()
            try:
                self.handle_record(rec)
            except Exception:
                logger.exception("ingestion_record_error")

    def handle_record(self, rec: NormalizedTelemetryRecord) -> bool:
        """Buffer for the UI, run deterministic detection, and escalate if suspicious."""
        suspicious = self.detector.observe(rec)
        if rec.is_simulated:
            return False
        self.orchestrator.telemetry_history.append(rec)
        if suspicious is None:
            return False

        key = (rec.entity, rec.metric_name)
        now = time.monotonic()
        if now - self._last_escalation.get(key, -math.inf) < self.cooldown:
            return False
        self._last_escalation[key] = now
        rec.tags.update(
            {
                "ingestion_z_score": suspicious["z_score"],
                "ingestion_flagged_by": suspicious["flagged_by"],
                "ingestion_baseline_mean": suspicious["baseline_mean"],
            }
        )
        rec.baseline_mean = suspicious["baseline_mean"]
        rec.deviation_sigma = suspicious["z_score"]
        self.escalations += 1
        asyncio.create_task(self._escalate(rec))
        return True

    async def _escalate(self, rec: NormalizedTelemetryRecord) -> None:
        async with self._sem:
            try:
                await self.orchestrator.process_event(rec, dry_run=self.dry_run)
            except Exception:
                logger.exception("ingestion_escalation_failed", entity=rec.entity, metric=rec.metric_name)
