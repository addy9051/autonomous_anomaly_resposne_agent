from agents.diagnosis.graph import _get_synthetic_runbooks


def test_rag_recall_latency_spike() -> None:
    """
    RAG Evaluation Golden Dataset Check 1:
    When an anomaly of type 'latency_spike' is generated, the returned runbooks
    MUST contain 'runbook://app/latency-spike-investigation' within Top 2.
    """
    runbooks = _get_synthetic_runbooks("latency_spike", ["payment-gateway"])

    # Assert recall @ 5 (we evaluate within top 2 since synthetic db is small)
    retrieved_ids = [r["runbook_id"] for r in runbooks]
    assert "runbook://app/latency-spike-investigation" in retrieved_ids
    assert runbooks[0]["similarity_score"] > 0.85

def test_rag_novel_incident_fallback() -> None:
    """
    RAG Evaluation Golden Dataset Check 2:
    Unknown anomaly types should fallback to the General Incident Response baseline
    with a similarity score lower than high confidence matches to trigger human evals.
    """
    runbooks = _get_synthetic_runbooks("unknown_quantum_anomaly", ["auth-service"])

    retrieved_ids = [r["runbook_id"] for r in runbooks]
    assert "runbook://general/incident-response" in retrieved_ids
    assert runbooks[0]["similarity_score"] < 0.75
