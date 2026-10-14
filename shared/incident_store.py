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
            if incident.status not in {IncidentStatus.AWAITING_APPROVAL, IncidentStatus.ACTION_PENDING}:
                return None

            pending = [
                action
                for action in incident.action_results
                if action.execution_status == "pending_approval"
            ]
            if len(pending) != 1 or pending[0].proposal_hash != proposal_hash:
                return None

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

            now = datetime.now(timezone.utc)
            incident.updated_at = now
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
