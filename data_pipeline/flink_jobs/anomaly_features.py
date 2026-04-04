"""
Anomaly Feature Extraction (Python-based for development).

In production, this would be an Apache Flink job. For development,
we implement the same logic in Python:

1. Rolling window feature extraction (latency p50/p95/p99)
2. Z-score normalization against 7-day baseline
3. Alert deduplication (30-second tumbling window)
4. Fraud signal enrichment
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

import numpy as np

from shared.schemas import TelemetryEvent
from shared.utils import get_logger

logger = get_logger("anomaly_features")


class RollingWindowAggregator:
    """
    Rolling window aggregator for time-series metrics.
    Maintains a sliding window of events and computes statistics.
    """

    def __init__(self, window_seconds: int = 300, baseline_window_days: int = 7) -> None:
        self.window_seconds = window_seconds
        self.baseline_window_days = baseline_window_days
        self.windows: dict[str, deque] = {}  # service -> deque of (timestamp, metrics)
        self.baselines: dict[str, dict[str, dict[str, float]]] = {}  # service -> metric -> {mean, std}

        # Initialize synthetic baselines
        self._init_synthetic_baselines()

    def _init_synthetic_baselines(self) -> None:
        """Initialize baseline statistics from synthetic 7-day data."""
        services = ["payment-gateway", "fraud-api", "auth-service", "merchant-api"]
        metrics_baselines = {
            "latency_p99_ms": {"mean": 250.0, "std": 45.0},
            "latency_p95_ms": {"mean": 180.0, "std": 30.0},
            "latency_p50_ms": {"mean": 100.0, "std": 20.0},
            "error_rate": {"mean": 0.02, "std": 0.008},
            "cpu_percent": {"mean": 45.0, "std": 12.0},
            "memory_percent": {"mean": 60.0, "std": 8.0},
            "request_rate": {"mean": 5000.0, "std": 800.0},
            "fraud_score_mean": {"mean": 0.038, "std": 0.012},
            "kafka_consumer_lag": {"mean": 500.0, "std": 300.0},
        }
        for service in services:
            self.baselines[service] = metrics_baselines.copy()

    def add_event(self, event: TelemetryEvent) -> dict[str, Any]:
        """
        Add an event to the rolling window and compute features.

        Returns:
            Feature dict with raw metrics, z-scores, and anomaly flags
        """
        service = event.service_name
        now = time.time()

        # Initialize window for service
        if service not in self.windows:
            self.windows[service] = deque()

        # Add event to window
        self.windows[service].append((now, event.payload))

        # Evict old events
        cutoff = now - self.window_seconds
        while self.windows[service] and self.windows[service][0][0] < cutoff:
            self.windows[service].popleft()

        # Compute window statistics
        features = self._compute_window_features(service)
        features["service"] = service
        features["event_id"] = event.event_id
        features["window_size"] = len(self.windows[service])

        return features

    def _compute_window_features(self, service: str) -> dict[str, Any]:
        """Compute aggregate features over the current window."""
        events = [payload for _, payload in self.windows[service]]
        if not events:
            return {}

        features: dict[str, Any] = {}
        baseline = self.baselines.get(service, {})

        metric_keys = [
            "latency_p99_ms", "latency_p95_ms", "latency_p50_ms",
            "error_rate", "cpu_percent", "memory_percent",
            "request_rate", "fraud_score_mean", "kafka_consumer_lag",
        ]

        for key in metric_keys:
            values = [e.get(key) for e in events if e.get(key) is not None]
            if values:
                current_mean = float(np.mean(values))
                features[f"{key}_mean"] = round(current_mean, 4)
                features[f"{key}_max"] = round(float(np.max(values)), 4)
                features[f"{key}_min"] = round(float(np.min(values)), 4)
                features[f"{key}_std"] = round(float(np.std(values)), 4)

                # Z-score against baseline
                if key in baseline:
                    bl = baseline[key]
                    z = (current_mean - bl["mean"]) / bl["std"] if bl["std"] > 0 else 0
                    features[f"{key}_zscore"] = round(z, 3)
                    features[f"{key}_anomalous"] = abs(z) > 2.0

        return features


class AlertDeduplicator:
    """
    Deduplicates alerts within a tumbling window.
    Keeps first occurrence, counts suppressed duplicates.
    """

    def __init__(self, window_seconds: int = 30) -> None:
        self.window_seconds = window_seconds
        self.seen_alerts: dict[str, dict[str, Any]] = {}

    def should_fire(self, alert_key: str, alert_data: dict[str, Any]) -> bool:
        """
        Check if this alert should fire or be suppressed.

        Args:
            alert_key: Deduplication key (e.g., "payment-gateway:latency_spike")
            alert_data: Alert payload

        Returns:
            True if the alert should fire, False if suppressed
        """
        now = time.time()

        if alert_key in self.seen_alerts:
            entry = self.seen_alerts[alert_key]
            if now - entry["first_seen"] < self.window_seconds:
                entry["suppressed_count"] += 1
                return False

        # New alert or window expired
        self.seen_alerts[alert_key] = {
            "first_seen": now,
            "data": alert_data,
            "suppressed_count": 0,
        }
        return True

    def get_suppressed_count(self, alert_key: str) -> int:
        """Get count of suppressed duplicates for an alert."""
        entry = self.seen_alerts.get(alert_key)
        return entry["suppressed_count"] if entry else 0

    def cleanup(self) -> int:
        """Remove expired entries. Returns count of removed entries."""
        now = time.time()
        expired = [
            k for k, v in self.seen_alerts.items()
            if now - v["first_seen"] > self.window_seconds * 2
        ]
        for k in expired:
            del self.seen_alerts[k]
        return len(expired)
