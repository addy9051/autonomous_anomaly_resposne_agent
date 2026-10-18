"""
Deploy-event source (GitHub Deployments API).

Recent deployments are the highest-signal "what changed?" evidence for an
incident. Read-only. Raises TelemetrySourceError on failure; returns an empty
list only when the source is not configured or no deployment falls in the window.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from data_pipeline.connectors.adapters.base import TelemetrySourceError
from shared.config import get_settings
from shared.egress import guarded_async_client
from shared.schemas import (
    NormalizedTelemetryRecord,
    Severity,
    TelemetryConnectorSource,
    TelemetrySignalType,
)
from shared.utils import get_logger

logger = get_logger("deploy_events")

GITHUB_API = "https://api.github.com"


def is_configured() -> bool:
    return bool(get_settings().integrations.github_deploy_repo)


async def fetch_recent_deployments(
    window_seconds: int = 6 * 3600,
    client: httpx.AsyncClient | None = None,
) -> list[NormalizedTelemetryRecord]:
    """Return deployments created within the window, newest last."""
    cfg = get_settings().integrations
    if not cfg.github_deploy_repo:
        return []

    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if cfg.github_token:
        headers["Authorization"] = f"Bearer {cfg.github_token}"

    since = datetime.now(UTC) - timedelta(seconds=window_seconds)
    url = f"{GITHUB_API}/repos/{cfg.github_deploy_repo}/deployments"
    params = {"environment": cfg.github_deploy_environment, "per_page": 30}

    owns_client = client is None
    http = client or guarded_async_client(timeout=10.0)
    try:
        resp = await http.get(url, headers=headers, params=params)
        if resp.status_code != 200:
            raise TelemetrySourceError(f"GitHub deployments returned HTTP {resp.status_code}")
        items = resp.json()
    except httpx.HTTPError as exc:
        raise TelemetrySourceError(f"GitHub deployments request failed: {exc}") from exc
    except ValueError as exc:
        raise TelemetrySourceError(f"GitHub deployments response could not be parsed: {exc}") from exc
    finally:
        if owns_client:
            await http.aclose()

    records: list[NormalizedTelemetryRecord] = []
    try:
        for d in items:
            created = datetime.fromisoformat(d["created_at"].replace("Z", "+00:00"))
            if created < since:
                continue
            records.append(
                NormalizedTelemetryRecord(
                    timestamp=created,
                    entity=cfg.github_deploy_repo,
                    environment=d.get("environment", cfg.github_deploy_environment),
                    connector_source=TelemetryConnectorSource.GITHUB,
                    signal_type=TelemetrySignalType.DEPLOY_EVENT,
                    metric_name="deployment",
                    value=1.0,
                    unit="event",
                    severity=Severity.LOW,
                    tags={
                        "sha": d.get("sha", ""),
                        "ref": d.get("ref", ""),
                        "creator": (d.get("creator") or {}).get("login", ""),
                        "description": d.get("description") or "",
                    },
                    raw_query=url,
                    source_ref=d.get("url"),
                )
            )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise TelemetrySourceError(f"GitHub deployments payload malformed: {exc}") from exc

    records.sort(key=lambda r: r.timestamp)
    return records
