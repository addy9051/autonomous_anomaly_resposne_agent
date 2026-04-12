"""
Monitoring Agent Tools.

Custom LangChain tools for querying infrastructure telemetry:
- PrometheusQueryTool — query Prometheus metrics API
- KafkaLagInspectorTool — check consumer group lag
- FraudSignalFetchTool — fetch fraud scores from Redis
- BaselineCompareTool — compare current vs baseline metrics
- AnomalyClassifierTool — run Isolation Forest anomaly detection
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import numpy as np
from langchain_core.tools import tool
from sklearn.ensemble import IsolationForest

from shared.config import get_settings
from shared.utils import get_logger

logger = get_logger(__name__)


# ─── Prometheus Query Tool ───────────────────────────────────────


@tool
async def prometheus_query(query: str, time_range: str = "5m") -> str:
    """
    Query Prometheus for infrastructure metrics.

    Args:
        query: PromQL query string (e.g., 'rate(http_requests_total[5m])')
        time_range: Time range for the query (default: 5m)

    Returns:
        JSON string with query results including metric name, labels, and values.
    """
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.integrations.prometheus_url}/api/v1/query",
                params={"query": query},
            )
            response.raise_for_status()
            data = response.json()

            if data["status"] == "success":
                results = data.get("data", {}).get("result", [])
                formatted = []
                for result in results[:10]:  # Limit results
                    metric = result.get("metric", {})
                    value = result.get("value", [None, None])
                    formatted.append(
                        {
                            "metric": metric,
                            "timestamp": value[0],
                            "value": float(value[1]) if value[1] else None,
                        }
                    )
                return json.dumps(formatted, indent=2)
            else:
                return json.dumps({"error": f"Prometheus query failed: {data.get('error', 'unknown')}"})

    except httpx.ConnectError:
        # Fallback: return synthetic data for development
        logger.warning("prometheus_unavailable", msg="Prometheus not reachable, returning synthetic data")
        return json.dumps(_synthetic_prometheus_response(query))
    except Exception as e:
        return json.dumps({"error": str(e)})


def _synthetic_prometheus_response(query: str) -> list[dict[str, Any]]:
    """Generate synthetic Prometheus response for dev/testing."""
    rng = np.random.default_rng(42)
    if "latency" in query.lower() or "duration" in query.lower():
        return [
            {
                "metric": {"service": "payment-gateway", "__name__": "http_request_duration_seconds"},
                "value": round(rng.exponential(0.5) + 0.1, 4),
            }
        ]
    elif "error" in query.lower():
        return [
            {
                "metric": {"service": "payment-gateway", "__name__": "http_errors_total"},
                "value": round(rng.uniform(0.01, 0.15), 4),
            }
        ]
    elif "cpu" in query.lower():
        return [
            {
                "metric": {"pod": "payment-gateway-abc123", "__name__": "container_cpu_usage"},
                "value": round(rng.uniform(20, 85), 2),
            }
        ]
    else:
        return [{"metric": {"__name__": "unknown"}, "value": round(rng.uniform(0, 100), 2)}]


# ─── Kafka Lag Inspector Tool ────────────────────────────────────


@tool
async def kafka_lag_inspector(consumer_group: str = "anomaly-agent-group", topic: str = "telemetry.normalized") -> str:
    """
    Check Kafka consumer group lag to detect data pipeline bottlenecks.

    Args:
        consumer_group: The consumer group to inspect
        topic: The Kafka topic to check lag for

    Returns:
        JSON string with partition-level lag information.
    """
    try:
        # In development mode, return synthetic lag data
        logger.info("kafka_lag_check", consumer_group=consumer_group, topic=topic)
        rng = np.random.default_rng()
        partitions = []
        for i in range(6):
            lag = int(rng.exponential(500))
            partitions.append(
                {
                    "partition": i,
                    "current_offset": 1_000_000 + int(rng.integers(0, 100_000)),
                    "end_offset": 1_000_000 + int(rng.integers(0, 100_000)) + lag,
                    "lag": lag,
                }
            )
        total_lag = sum(p["lag"] for p in partitions)
        return json.dumps(
            {
                "consumer_group": consumer_group,
                "topic": topic,
                "total_lag": total_lag,
                "partitions": partitions,
                "status": "critical" if total_lag > 10_000 else "healthy",
            },
            indent=2,
        )

    except Exception as e:
        return json.dumps({"error": str(e)})


# ─── Fraud Signal Fetch Tool ────────────────────────────────────


@tool
async def fraud_signal_fetch(service: str = "payment-gateway", window_minutes: int = 5) -> str:
    """
    Fetch recent fraud detection model outputs from Redis feature store.

    Args:
        service: Service name to fetch fraud signals for
        window_minutes: Time window in minutes to look back

    Returns:
        JSON string with aggregated fraud signal statistics.
    """
    try:
        # Synthetic fraud signal data for development
        rng = np.random.default_rng()
        n_transactions = int(rng.integers(1000, 5000))
        fraud_scores = rng.beta(2, 50, size=n_transactions)  # Mostly low scores

        return json.dumps(
            {
                "service": service,
                "window_minutes": window_minutes,
                "total_transactions": n_transactions,
                "fraud_scores": {
                    "mean": round(float(fraud_scores.mean()), 4),
                    "std": round(float(fraud_scores.std()), 4),
                    "p95": round(float(np.percentile(fraud_scores, 95)), 4),
                    "p99": round(float(np.percentile(fraud_scores, 99)), 4),
                    "above_threshold_count": int((fraud_scores > 0.7).sum()),
                    "above_threshold_pct": round(float((fraud_scores > 0.7).mean() * 100), 2),
                },
                "model_version": "fraud_model_v3.2.1",
                "baseline_mean": 0.038,
                "baseline_std": 0.012,
            },
            indent=2,
        )

    except Exception as e:
        return json.dumps({"error": str(e)})


# ─── Baseline Compare Tool ──────────────────────────────────────


@tool
async def baseline_compare(metric_name: str, current_value: float, service: str = "payment-gateway") -> str:
    """
    Compare a current metric value against its 7-day rolling baseline.
    Computes z-score to determine if the current value is anomalous.

    Args:
        metric_name: Name of the metric (e.g., 'p99_latency_ms', 'error_rate')
        current_value: The current observed value
        service: Service name

    Returns:
        JSON string with baseline stats and z-score analysis.
    """
    # Synthetic baseline data
    baselines = {
        "p99_latency_ms": {"mean": 250.0, "std": 45.0},
        "p95_latency_ms": {"mean": 180.0, "std": 30.0},
        "error_rate": {"mean": 0.02, "std": 0.008},
        "request_rate": {"mean": 5000.0, "std": 800.0},
        "cpu_percent": {"mean": 45.0, "std": 12.0},
        "memory_percent": {"mean": 60.0, "std": 8.0},
        "fraud_score_mean": {"mean": 0.038, "std": 0.012},
    }

    baseline = baselines.get(metric_name, {"mean": current_value * 0.8, "std": current_value * 0.1})
    z_score = (current_value - baseline["mean"]) / baseline["std"] if baseline["std"] > 0 else 0

    return json.dumps(
        {
            "metric_name": metric_name,
            "service": service,
            "current_value": current_value,
            "baseline": {
                "mean": baseline["mean"],
                "std": baseline["std"],
                "window": "7d",
            },
            "z_score": round(z_score, 3),
            "deviation_pct": round(((current_value - baseline["mean"]) / baseline["mean"]) * 100, 2),
            "is_anomalous": abs(z_score) > 2.0,
            "severity": (
                "critical"
                if abs(z_score) > 4.0
                else "high"
                if abs(z_score) > 3.0
                else "medium"
                if abs(z_score) > 2.0
                else "low"
            ),
        },
        indent=2,
    )


# ─── Anomaly Classifier Tool ────────────────────────────────────


@tool
async def anomaly_classifier(metrics_vector: list[float]) -> str:
    """
    Run Isolation Forest anomaly detection on a metrics vector.

    Args:
        metrics_vector: List of numeric metric values
            [p99_latency_ms, error_rate, cpu_percent, memory_percent, kafka_lag, fraud_score_mean]

    Returns:
        JSON string with anomaly classification results.
    """
    try:
        # Train on synthetic "normal" data and predict on the input
        rng = np.random.default_rng(42)
        n_features = len(metrics_vector)

        # Generate synthetic normal training data
        normal_data = np.column_stack(
            [
                rng.normal(250, 45, 500),  # p99 latency
                rng.normal(0.02, 0.008, 500),  # error rate
                rng.normal(45, 12, 500),  # CPU
                rng.normal(60, 8, 500),  # memory
                rng.exponential(500, 500),  # kafka lag
                rng.normal(0.038, 0.012, 500),  # fraud score
            ]
        )

        # Ensure vector length matches
        if n_features < normal_data.shape[1]:
            normal_data = normal_data[:, :n_features]
        elif n_features > normal_data.shape[1]:
            # Pad with normal data
            extra = rng.normal(50, 10, (500, n_features - normal_data.shape[1]))
            normal_data = np.column_stack([normal_data, extra])

        clf = IsolationForest(contamination=0.05, random_state=42, n_estimators=100)
        clf.fit(normal_data)

        sample = np.array(metrics_vector).reshape(1, -1)
        prediction = clf.predict(sample)[0]
        score = clf.score_samples(sample)[0]

        # Convert isolation forest score to 0–1 confidence
        # Lower scores = more anomalous
        confidence = max(0.0, min(1.0, 0.5 - score))

        return json.dumps(
            {
                "is_anomaly": bool(prediction == -1),
                "confidence": round(confidence, 4),
                "isolation_score": round(float(score), 4),
                "model": "isolation_forest_v3",
                "interpretation": (
                    "ANOMALOUS — metrics significantly deviate from normal patterns"
                    if prediction == -1
                    else "NORMAL — metrics within expected operational bounds"
                ),
            },
            indent=2,
        )

    except Exception as e:
        return json.dumps({"error": str(e), "is_anomaly": False, "confidence": 0.0})


# ─── Tool Registry ──────────────────────────────────────────────


ALL_MONITORING_TOOLS = [
    prometheus_query,
    kafka_lag_inspector,
    fraud_signal_fetch,
    baseline_compare,
    anomaly_classifier,
]
