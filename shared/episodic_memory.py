"""
Episodic memory: what happened, and how it was handled, at each timestamp.

Backed by Postgres (tables in ``knowledge_base/migrations/003_episodic_memory.sql``). It exists so
a repeated event (same fingerprint within a window) is handled the same way as before instead of
being re-investigated from scratch. If Postgres is unreachable the store degrades to an
in-process backend and says so loudly; memory problems never block incident handling.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, Field

from shared.config import get_settings
from shared.utils import get_logger

if TYPE_CHECKING:
    from shared.schemas import AnomalyEvent

logger = get_logger("episodic_memory")

BRANCH_CRITICAL = "critical_rca"
BRANCH_AUTO_FIX = "auto_fix"
BRANCH_RECOMMEND = "recommend"


class Episode(BaseModel):
    episode_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    fingerprint: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    severity: str
    anomaly_type: str
    services: list[str]
    branch: str
    status: str = "open"  # open | resolved | escalated | failed
    root_cause: str | None = None
    decision: dict[str, Any] = Field(default_factory=dict)
    action: str | None = None
    outcome: str | None = None  # recovered | not_recovered | inconclusive | none
    duplicate_of: str | None = None
    skill: str | None = None
    brief: dict[str, Any] | None = None


def fingerprint_for(anomaly: AnomalyEvent) -> str:
    """Stable identity of an event: same services + anomaly type + severity."""
    services = ",".join(sorted(anomaly.affected_services))
    return f"{services}|{anomaly.anomaly_type.value}|{anomaly.severity.value}"


class EpisodeBackend(Protocol):
    async def insert(self, episode: Episode) -> None: ...
    async def update(self, episode: Episode) -> None: ...
    async def append_event(self, episode_id: str, stage: str, actor: str, detail: dict[str, Any]) -> None: ...
    async def latest_for_fingerprint(self, fingerprint: str, since: datetime) -> Episode | None: ...
    async def get(self, episode_id: str) -> Episode | None: ...
    async def timeline(self, episode_id: str) -> list[dict[str, Any]]: ...


class InMemoryEpisodeBackend:
    def __init__(self) -> None:
        self.episodes: dict[str, Episode] = {}
        self.events: list[dict[str, Any]] = []

    async def insert(self, episode: Episode) -> None:
        self.episodes[episode.episode_id] = episode

    async def update(self, episode: Episode) -> None:
        self.episodes[episode.episode_id] = episode

    async def append_event(self, episode_id: str, stage: str, actor: str, detail: dict[str, Any]) -> None:
        self.events.append(
            {"episode_id": episode_id, "ts": datetime.now(UTC), "stage": stage, "actor": actor, "detail": detail}
        )

    async def latest_for_fingerprint(self, fingerprint: str, since: datetime) -> Episode | None:
        candidates = [e for e in self.episodes.values() if e.fingerprint == fingerprint and e.created_at >= since]
        return max(candidates, key=lambda e: e.created_at, default=None)

    async def get(self, episode_id: str) -> Episode | None:
        return self.episodes.get(episode_id)

    async def timeline(self, episode_id: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["episode_id"] == episode_id]


def _as_json(value: Any) -> Any:  # noqa: ANN401
    return json.loads(value) if isinstance(value, str) else value


class PostgresEpisodeBackend:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def _connect(self):  # noqa: ANN202
        import asyncpg

        return await asyncpg.connect(self.dsn, statement_cache_size=0, timeout=5)

    @staticmethod
    def _row_to_episode(row: Any) -> Episode:  # noqa: ANN401
        return Episode(
            episode_id=row["episode_id"],
            fingerprint=row["fingerprint"],
            created_at=row["created_at"],
            severity=row["severity"],
            anomaly_type=row["anomaly_type"],
            services=list(row["services"]),
            branch=row["branch"],
            status=row["status"],
            root_cause=row["root_cause"],
            decision=_as_json(row["decision"]) or {},
            action=row["action"],
            outcome=row["outcome"],
            duplicate_of=row["duplicate_of"],
            skill=row["skill"],
            brief=_as_json(row["brief"]),
        )

    async def insert(self, episode: Episode) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                """
                INSERT INTO episodes (episode_id, fingerprint, created_at, severity, anomaly_type, services,
                                      branch, status, root_cause, decision, action, outcome, duplicate_of, skill, brief)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$13,$14,$15::jsonb)
                """,
                episode.episode_id,
                episode.fingerprint,
                episode.created_at,
                episode.severity,
                episode.anomaly_type,
                episode.services,
                episode.branch,
                episode.status,
                episode.root_cause,
                json.dumps(episode.decision, default=str),
                episode.action,
                episode.outcome,
                episode.duplicate_of,
                episode.skill,
                json.dumps(episode.brief, default=str) if episode.brief is not None else None,
            )
        finally:
            await conn.close()

    async def update(self, episode: Episode) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                """
                UPDATE episodes SET branch=$9, status=$2, root_cause=$3, decision=$4::jsonb, action=$5, outcome=$6,
                       skill=$7, brief=$8::jsonb, updated_at=NOW()
                WHERE episode_id=$1
                """,
                episode.episode_id,
                episode.status,
                episode.root_cause,
                json.dumps(episode.decision, default=str),
                episode.action,
                episode.outcome,
                episode.skill,
                json.dumps(episode.brief, default=str) if episode.brief is not None else None,
                episode.branch,
            )
        finally:
            await conn.close()

    async def append_event(self, episode_id: str, stage: str, actor: str, detail: dict[str, Any]) -> None:
        conn = await self._connect()
        try:
            await conn.execute(
                "INSERT INTO episode_events (episode_id, stage, actor, detail) VALUES ($1,$2,$3,$4::jsonb)",
                episode_id,
                stage,
                actor,
                json.dumps(detail, default=str),
            )
        finally:
            await conn.close()

    async def latest_for_fingerprint(self, fingerprint: str, since: datetime) -> Episode | None:
        conn = await self._connect()
        try:
            row = await conn.fetchrow(
                """
                SELECT * FROM episodes
                WHERE fingerprint=$1 AND created_at >= $2
                ORDER BY created_at DESC LIMIT 1
                """,
                fingerprint,
                since,
            )
            return self._row_to_episode(row) if row else None
        finally:
            await conn.close()

    async def get(self, episode_id: str) -> Episode | None:
        conn = await self._connect()
        try:
            row = await conn.fetchrow("SELECT * FROM episodes WHERE episode_id=$1", episode_id)
            return self._row_to_episode(row) if row else None
        finally:
            await conn.close()

    async def timeline(self, episode_id: str) -> list[dict[str, Any]]:
        conn = await self._connect()
        try:
            rows = await conn.fetch(
                "SELECT ts, stage, actor, detail FROM episode_events WHERE episode_id=$1 ORDER BY ts", episode_id
            )
            return [
                {
                    "episode_id": episode_id,
                    "ts": r["ts"],
                    "stage": r["stage"],
                    "actor": r["actor"],
                    "detail": _as_json(r["detail"]),
                }
                for r in rows
            ]
        finally:
            await conn.close()


class EpisodicMemory:
    """Facade used by the orchestrator. Postgres first; falls back to memory if it is unreachable."""

    def __init__(self, backend: EpisodeBackend | None = None, window_minutes: int | None = None) -> None:
        settings = get_settings()
        self.window = timedelta(minutes=window_minutes or settings.agent.duplicate_window_minutes)
        self.backend: EpisodeBackend = backend or PostgresEpisodeBackend(settings.data.postgres_dsn)
        self._fallback = InMemoryEpisodeBackend()
        self.degraded = False

    async def _call(self, name: str, *args: Any) -> Any:  # noqa: ANN401
        if not self.degraded:
            try:
                return await getattr(self.backend, name)(*args)
            except Exception as exc:
                self.degraded = True
                logger.error("episodic_memory_degraded", op=name, error=str(exc))
        return await getattr(self._fallback, name)(*args)

    async def find_duplicate(self, anomaly: AnomalyEvent) -> Episode | None:
        since = datetime.now(UTC) - self.window
        return await self._call("latest_for_fingerprint", fingerprint_for(anomaly), since)

    async def open_episode(
        self, anomaly: AnomalyEvent, branch: str, skill: str | None = None, duplicate_of: str | None = None
    ) -> Episode:
        episode = Episode(
            fingerprint=fingerprint_for(anomaly),
            severity=anomaly.severity.value,
            anomaly_type=anomaly.anomaly_type.value,
            services=list(anomaly.affected_services),
            branch=branch,
            skill=skill,
            duplicate_of=duplicate_of,
        )
        await self._call("insert", episode)
        await self.log(episode.episode_id, "intake", "orchestrator", {"duplicate_of": duplicate_of, "branch": branch})
        return episode

    async def log(self, episode_id: str, stage: str, actor: str, detail: dict[str, Any] | None = None) -> None:
        await self._call("append_event", episode_id, stage, actor, detail or {})

    async def close(self, episode: Episode, **fields: Any) -> Episode:  # noqa: ANN401
        for key, value in fields.items():
            setattr(episode, key, value)
        await self._call("update", episode)
        return episode

    async def timeline(self, episode_id: str) -> list[dict[str, Any]]:
        return await self._call("timeline", episode_id)

    async def get(self, episode_id: str) -> Episode | None:
        return await self._call("get", episode_id)
