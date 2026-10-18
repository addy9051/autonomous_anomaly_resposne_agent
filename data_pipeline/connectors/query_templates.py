"""
Canonical metric -> vendor query templates.

Agents reason over canonical metric names (latency_p99, error_rate, ...). Each vendor
exposes these under different names/query languages, so adapters translate through
these templates. Defaults assume common conventions and are meant to be overridden
per deployment via `TELEMETRY_QUERY_TEMPLATES` (JSON: {"vendor": {"metric": "query"}}).
`{entity}` is replaced with the service name.
"""

from __future__ import annotations

import json

from shared.config import get_settings
from shared.utils import get_logger

logger = get_logger("query_templates")

CANONICAL_METRICS: tuple[str, ...] = ("latency_p99", "error_rate", "transaction_failure_rate")

DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    "prometheus": {
        "latency_p99": (
            "1000*histogram_quantile(0.99, sum by (le) "
            '(rate(http_request_duration_seconds_bucket{service="{entity}"}[5m])))'
        ),
        "error_rate": (
            '100*sum(rate(http_requests_total{service="{entity}",status=~"5.."}[5m]))'
            '/sum(rate(http_requests_total{service="{entity}"}[5m]))'
        ),
    },
    "datadog": {
        "latency_p99": "avg:trace.http.request.duration.by.service.99p{service:{entity}}",
        "error_rate": "avg:trace.http.request.errors{service:{entity}}",
    },
    "splunk": {
        "latency_p99": "search index=* service={entity} | stats perc99(response_time_ms) as latency_p99",
        "error_rate": (
            "search index=* service={entity} | stats count(eval(status>=500)) as errors, count as total "
            "| eval error_rate=round(100*errors/total,2)"
        ),
        "transaction_failure_rate": (
            "search index=* service={entity} | stats count(eval(status>=500)) as errors, count as total "
            "| eval transaction_failure_rate=round(100*errors/total,2)"
        ),
    },
}


def _overrides() -> dict[str, dict[str, str]]:
    raw = get_settings().integration.telemetry_query_templates.strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        logger.warning("invalid_query_templates_json")
        return {}


def resolve_query(vendor: str, metric: str, entity: str) -> str | None:
    """Return the vendor query for a canonical metric, or None if it is not a templated metric."""
    template = _overrides().get(vendor, {}).get(metric) or DEFAULT_TEMPLATES.get(vendor, {}).get(metric)
    if template is None:
        return None
    return template.replace("{entity}", entity)
