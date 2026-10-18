"""
Durable Incident Store.

Blueprint Section 4 & 7: "Preserve an incident thread containing the evidence
gathered, hypotheses considered, queries performed, action proposals, approvals,
execution results, and verification outcome."
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.schemas import IncidentRecord, IncidentStatus
from shared.utils import get_logger

logger = get_logger("incident_store")

ACTIVE_STATUSES = {
    IncidentStatus.DETECTED.value,
    IncidentStatus.INVESTIGATING.value,
    IncidentStatus.DIAGNOSING.value,
    IncidentStatus.RECOMMENDATION_READY.value,
    IncidentStatus.AWAITING_APPROVAL.value,
    IncidentStatus.ACTION_PENDING.value,
    IncidentStatus.EXECUTING.value,
    IncidentStatus.ACTION_EXECUTING.value,
    IncidentStatus.VERIFYING.value,
    IncidentStatus.ESCALATED.value,
}


def _apply_approval(
    incident: IncidentRecord, proposal_hash: str, *, approved: bool, actor: str, reason: str | None
) -> bool:
    """Mutate the incident for a single-use approval decision; False if it is not claimable."""
    if incident.status not in {IncidentStatus.AWAITING_APPROVAL, IncidentStatus.ACTION_PENDING}:
        return False

    pending = [a for a in incident.action_results if a.execution_status == "pending_approval"]
    if len(pending) != 1 or pending[0].proposal_hash != proposal_hash:
        return False

    action = pending[0]
    action.human_approved = approved
    action.approver = actor
    if approved:
        incident.status = IncidentStatus.EXECUTING
    else:
        action.execution_status = "rejected"
        incident.status = IncidentStatus.ESCALATED
        incident.human_overrode = True
        incident.human_feedback = f"Action rejected by {actor}. Reason: {reason or 'None provided'}"
    incident.updated_at = datetime.now(timezone.utc)
    return True


class IncidentStore:
    """
    SQLite-backed durable storage for incident records and audit trail.
    Ensures state transitions and approval checkpoints survive restarts.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            data_dir = Path(__file__).parent.parent / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = str(data_dir / "incidents.db")
        else:
            self.db_path = str(db_path)

        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_trail (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def save(self, incident: IncidentRecord) -> None:
        """Upsert an incident record."""
        now_iso = datetime.now(timezone.utc).isoformat()
        incident.updated_at = datetime.now(timezone.utc)
        data_json = incident.model_dump_json()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO incidents (incident_id, status, data, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    status=excluded.status,
                    data=excluded.data,
                    updated_at=excluded.updated_at
                """,
                (
                    incident.incident_id,
                    incident.status.value,
                    data_json,
                    incident.created_at.isoformat(),
                    now_iso,
                ),
            )
            conn.commit()

    def get(self, incident_id: str) -> IncidentRecord | None:
        """Fetch an incident by ID."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT data FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if row:
                return IncidentRecord.model_validate_json(row["data"])
        return None

    def claim_pending_approval(
        self,
        incident_id: str,
        proposal_hash: str,
        *,
        approved: bool,
        actor: str,
        reason: str | None = None,
    ) -> IncidentRecord | None:
        """Atomically consume one pending approval so concurrent retries cannot replay it."""
        with self._get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT data FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if row is None:
                return None

            incident = IncidentRecord.model_validate_json(row["data"])
            if not _apply_approval(incident, proposal_hash, approved=approved, actor=actor, reason=reason):
                return None

            now = incident.updated_at
            conn.execute(
                "UPDATE incidents SET status = ?, data = ?, updated_at = ? WHERE incident_id = ?",
                (incident.status.value, incident.model_dump_json(), now.isoformat(), incident_id),
            )
            conn.commit()
            return incident

    def list_active(self) -> list[IncidentRecord]:
        """Fetch all currently active / in-flight incidents."""
        with self._get_connection() as conn:
            rows = conn.execute("SELECT data, status FROM incidents ORDER BY created_at DESC").fetchall()
            active = []
            for r in rows:
                if r["status"] in ACTIVE_STATUSES:
                    try:
                        active.append(IncidentRecord.model_validate_json(r["data"]))
                    except Exception as exc:
                        logger.warning("corrupted_incident_record", error=str(exc))
            return active

    def list_resolved(self, limit: int = 50, status: str | None = None) -> list[IncidentRecord]:
        """Fetch resolved / terminal incidents."""
        with self._get_connection() as conn:
            if status:
                query = "SELECT data FROM incidents WHERE status = ? ORDER BY updated_at DESC LIMIT ?"
                rows = conn.execute(query, (status, limit)).fetchall()
            else:
                placeholders = ",".join(["?"] * len(ACTIVE_STATUSES))
                query = f"SELECT data FROM incidents WHERE status NOT IN ({placeholders}) ORDER BY updated_at DESC LIMIT ?"
                rows = conn.execute(query, (*list(ACTIVE_STATUSES), limit)).fetchall()

            resolved = []
            for r in rows:
                try:
                    resolved.append(IncidentRecord.model_validate_json(r["data"]))
                except Exception as exc:
                    logger.warning("corrupted_incident_record", error=str(exc))
            return resolved

    def list_recovered(self, limit: int = 1000) -> list[IncidentRecord]:
        """Fetch incidents that were verified as recovered, newest first."""
        statuses = (IncidentStatus.VERIFIED_RECOVERED.value, IncidentStatus.RESOLVED.value)
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT data FROM incidents WHERE status IN (?, ?) ORDER BY updated_at DESC LIMIT ?",
                (*statuses, limit),
            ).fetchall()
        recovered = []
        for r in rows:
            try:
                recovered.append(IncidentRecord.model_validate_json(r["data"]))
            except Exception as exc:
                logger.warning("corrupted_incident_record", error=str(exc))
        return recovered

    def record_audit(self, incident_id: str, actor: str, action: str, details: dict[str, Any]) -> None:
        """Append an audit log entry for compliance and review."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO audit_trail (incident_id, timestamp, actor, action, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (incident_id, now_iso, actor, action, json.dumps(details)),
            )
            conn.commit()


_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS incident_records (
    incident_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incident_records_status ON incident_records (status);
CREATE INDEX IF NOT EXISTS idx_incident_records_updated ON incident_records (updated_at DESC);
CREATE TABLE IF NOT EXISTS incident_audit_trail (
    id BIGSERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    details JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incident_audit_incident ON incident_audit_trail (incident_id, timestamp);
"""


class PostgresIncidentStore:
    """
    Postgres-backed incident store with the same interface as `IncidentStore`.

    State lives in the database, so several API tasks can share it. Approvals are claimed with a row
    lock (SELECT ... FOR UPDATE), which keeps them single-use across processes.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._init_db()

    def _connect(self) -> Any:  # noqa: ANN401
        import psycopg2

        return psycopg2.connect(self.dsn, connect_timeout=5)

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(_PG_SCHEMA)
        finally:
            conn.close()

    def save(self, incident: IncidentRecord) -> None:
        incident.updated_at = datetime.now(timezone.utc)
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO incident_records (incident_id, status, data, created_at, updated_at)
                    VALUES (%s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (incident_id) DO UPDATE SET
                        status = EXCLUDED.status, data = EXCLUDED.data, updated_at = EXCLUDED.updated_at
                    """,
                    (
                        incident.incident_id,
                        incident.status.value,
                        incident.model_dump_json(),
                        incident.created_at,
                        incident.updated_at,
                    ),
                )
        finally:
            conn.close()

    def get(self, incident_id: str) -> IncidentRecord | None:
        rows = self._fetch("SELECT data FROM incident_records WHERE incident_id = %s", (incident_id,))
        return IncidentRecord.model_validate(rows[0][0]) if rows else None

    def claim_pending_approval(
        self,
        incident_id: str,
        proposal_hash: str,
        *,
        approved: bool,
        actor: str,
        reason: str | None = None,
    ) -> IncidentRecord | None:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("SELECT data FROM incident_records WHERE incident_id = %s FOR UPDATE", (incident_id,))
                row = cur.fetchone()
                if row is None:
                    return None
                incident = IncidentRecord.model_validate(row[0])
                if not _apply_approval(incident, proposal_hash, approved=approved, actor=actor, reason=reason):
                    return None
                cur.execute(
                    "UPDATE incident_records SET status = %s, data = %s::jsonb, updated_at = %s WHERE incident_id = %s",
                    (incident.status.value, incident.model_dump_json(), incident.updated_at, incident_id),
                )
                return incident
        finally:
            conn.close()

    def list_active(self) -> list[IncidentRecord]:
        rows = self._fetch(
            "SELECT data FROM incident_records WHERE status = ANY(%s) ORDER BY created_at DESC",
            (sorted(ACTIVE_STATUSES),),
        )
        return self._parse(rows)

    def list_resolved(self, limit: int = 50, status: str | None = None) -> list[IncidentRecord]:
        if status:
            rows = self._fetch(
                "SELECT data FROM incident_records WHERE status = %s ORDER BY updated_at DESC LIMIT %s",
                (status, limit),
            )
        else:
            rows = self._fetch(
                "SELECT data FROM incident_records WHERE NOT (status = ANY(%s)) ORDER BY updated_at DESC LIMIT %s",
                (sorted(ACTIVE_STATUSES), limit),
            )
        return self._parse(rows)

    def list_recovered(self, limit: int = 1000) -> list[IncidentRecord]:
        statuses = [IncidentStatus.VERIFIED_RECOVERED.value, IncidentStatus.RESOLVED.value]
        rows = self._fetch(
            "SELECT data FROM incident_records WHERE status = ANY(%s) ORDER BY updated_at DESC LIMIT %s",
            (statuses, limit),
        )
        return self._parse(rows)

    def record_audit(self, incident_id: str, actor: str, action: str, details: dict[str, Any]) -> None:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO incident_audit_trail (incident_id, timestamp, actor, action, details)
                    VALUES (%s, %s, %s, %s, %s::jsonb)
                    """,
                    (incident_id, datetime.now(timezone.utc), actor, action, json.dumps(details, default=str)),
                )
        finally:
            conn.close()

    def _fetch(self, sql: str, params: tuple[Any, ...]) -> list[Any]:
        conn = self._connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall()
        finally:
            conn.close()

    @staticmethod
    def _parse(rows: list[Any]) -> list[IncidentRecord]:
        records = []
        for (data,) in rows:
            try:
                records.append(IncidentRecord.model_validate(data))
            except Exception as exc:  # noqa: BLE001
                logger.warning("corrupted_incident_record", error=str(exc))
        return records


def get_incident_store() -> IncidentStore | PostgresIncidentStore:
    """
    Postgres when INCIDENT_STORE_BACKEND=postgres (the default), SQLite for `sqlite`.

    A reachable database is required for the default: falling back to a local file would silently
    split incident state across tasks, which is the problem the Postgres store exists to remove.
    """
    import os

    from shared.config import get_settings

    if os.environ.get("INCIDENT_STORE_BACKEND", "postgres").lower() == "sqlite":
        return IncidentStore()
    return PostgresIncidentStore(get_settings().data.postgres_dsn)
