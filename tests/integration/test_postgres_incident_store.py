"""PostgresIncidentStore against a real Postgres (skipped when none is reachable)."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from agents.action.policy import compute_proposal_hash
from shared.config import get_settings
from shared.incident_store import PostgresIncidentStore
from shared.schemas import ActionResult, ActionTier, IncidentRecord, IncidentStatus

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def store() -> Iterator[PostgresIncidentStore]:
    try:
        pg = PostgresIncidentStore(get_settings().data.postgres_dsn)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"postgres unavailable: {exc}")
    yield pg
    conn = pg._connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM incident_records WHERE incident_id LIKE 'pg-%'")
            cur.execute("DELETE FROM incident_audit_trail WHERE incident_id LIKE 'pg-%'")
    finally:
        conn.close()


def _pending(incident_id: str) -> tuple[IncidentRecord, str]:
    params = {"deployment": "checkout", "namespace": "payments", "replicas": 3}
    proposal_hash = compute_proposal_hash("scale_replicas", params)
    record = IncidentRecord(
        incident_id=incident_id,
        status=IncidentStatus.AWAITING_APPROVAL,
        action_results=[
            ActionResult(
                incident_id=incident_id,
                action_taken="scale_replicas",
                tier=ActionTier.TIER_1_AUTO,
                execution_status="pending_approval",
                output={"recommended_params": params},
                proposal_hash=proposal_hash,
            )
        ],
    )
    return record, proposal_hash


def test_roundtrip_and_active_listing(store: PostgresIncidentStore) -> None:
    record, _ = _pending(f"pg-{uuid.uuid4().hex[:8]}")
    store.save(record)
    loaded = store.get(record.incident_id)
    assert loaded is not None and loaded.status == IncidentStatus.AWAITING_APPROVAL
    assert record.incident_id in {i.incident_id for i in store.list_active()}
    store.record_audit(record.incident_id, "test", "saved", {"ok": True})


def test_approval_is_single_use(store: PostgresIncidentStore) -> None:
    record, proposal_hash = _pending(f"pg-{uuid.uuid4().hex[:8]}")
    store.save(record)
    first = store.claim_pending_approval(record.incident_id, proposal_hash, approved=True, actor="sre")
    replay = store.claim_pending_approval(record.incident_id, proposal_hash, approved=True, actor="sre")
    assert first is not None and first.status == IncidentStatus.EXECUTING
    assert replay is None
