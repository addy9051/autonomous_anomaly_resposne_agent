"""Tests for the UI-driven connector selection/credential store."""

from __future__ import annotations

import pytest

from data_pipeline.connectors.connector_store import ConnectorConfigStore
from shared.config import get_settings


@pytest.fixture
def store(tmp_path):
    cfg = get_settings().integration
    snapshot = {k: getattr(cfg, k) for k in type(cfg).model_fields}
    yield ConnectorConfigStore(tmp_path / "state.json")
    for k, v in snapshot.items():
        setattr(cfg, k, v)


def test_selection_builds_connector_modes(store) -> None:
    store.set_selection(["prometheus", "datadog", "prometheus"])
    assert get_settings().integration.telemetry_connector_modes == "prometheus,datadog"
    with pytest.raises(ValueError):
        store.set_selection(["nope"])


def test_missing_and_one_of_rules(store) -> None:
    store.set_credentials("datadog", {"api_key": "a"})
    assert store.missing("datadog") == ["Application key"]
    cfg = get_settings().integration
    cfg.splunk_token = cfg.splunk_password = ""
    store.set_credentials("splunk", {"host": "h"})
    assert any("Auth token" in g for g in store.missing("splunk"))
    store.set_credentials("splunk", {"token": "t", "port": "8090", "verify_ssl": "false"})
    assert store.missing("splunk") == []
    assert cfg.splunk_port == 8090 and cfg.splunk_verify_ssl is False


def test_view_masks_secrets_and_blank_keeps_existing(store) -> None:
    store.set_credentials("datadog", {"api_key": "secret-1", "app_key": "secret-2"})
    store.set_credentials("datadog", {"api_key": ""})
    assert get_settings().integration.datadog_api_key == "secret-1"
    view = store.view()
    assert "secret-1" not in str(view)
    dd = next(c for c in view["connectors"] if c["id"] == "datadog")
    assert dd["configured"] is True
    assert next(f for f in dd["fields"] if f["key"] == "api_key")["is_set"] is True


def test_persist_and_reload(tmp_path) -> None:
    cfg = get_settings().integration
    before = (cfg.telemetry_connector_modes, cfg.datadog_api_key, cfg.enable_live_ingestion, cfg.ingestion_services)
    try:
        a = ConnectorConfigStore(tmp_path / "s.json")
        a.set_selection(["datadog"])
        a.set_credentials("datadog", {"api_key": "k1", "app_key": "k2"})
        a.set_ingestion(services="checkout, payments", dry_run=True, poll_interval_seconds=10, enabled=True)
        cfg.datadog_api_key = ""
        b = ConnectorConfigStore(tmp_path / "s.json")
        b.load()
        assert cfg.datadog_api_key == "k1"
        assert cfg.ingestion_services == "checkout,payments"
        assert b.activation_problems() == []
    finally:
        (cfg.telemetry_connector_modes, cfg.datadog_api_key, cfg.enable_live_ingestion, cfg.ingestion_services) = before


@pytest.mark.asyncio
async def test_connection_uses_overrides_without_persisting(store) -> None:
    cfg = get_settings().integration
    cfg.datadog_api_key = ""
    cfg.datadog_app_key = ""
    res = await store.test_connection("datadog", {"api_key": "x"})
    assert res["ok"] is False and "Application key" in res["detail"]
    assert cfg.datadog_api_key == ""
