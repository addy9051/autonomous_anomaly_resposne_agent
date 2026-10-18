"""
Runtime connector selection + credential store.

The SRE picks vendors and enters credentials in the dashboard; this module
validates them, persists them, and applies them onto the live settings object so
adapters (which read settings at construction) and CONNECTOR_MODES pick them up.
Secrets are never returned to the client, only an `is_set` flag.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.config import PROJECT_ROOT, get_settings
from shared.utils import get_logger

logger = get_logger("connector_store")

STATE_PATH = Path(os.environ.get("CONNECTOR_STATE_PATH", PROJECT_ROOT / ".connector_state.json"))


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    attr: str
    kind: str = "text"  # text | password | number | boolean
    secret: bool = False
    required: bool = False
    placeholder: str = ""
    help: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "kind": self.kind,
            "secret": self.secret,
            "required": self.required,
            "placeholder": self.placeholder,
            "help": self.help,
        }


@dataclass(frozen=True)
class ConnectorSpec:
    id: str
    label: str
    description: str
    fields: tuple[FieldSpec, ...]
    # At least one key in each group must be set (e.g. Splunk token OR password).
    required_one_of: tuple[tuple[str, ...], ...] = ()


CATALOG: dict[str, ConnectorSpec] = {
    "prometheus": ConnectorSpec(
        id="prometheus",
        label="Prometheus",
        description="Pull metrics via the HTTP query API (PromQL).",
        fields=(
            FieldSpec(
                "url", "Server URL", "prometheus_url", required=True, placeholder="https://prometheus.example.com"
            ),
            FieldSpec(
                "bearer_token",
                "Bearer token",
                "prometheus_bearer_token",
                "password",
                secret=True,
                help="Optional. Only needed if your Prometheus sits behind an auth proxy.",
            ),
        ),
    ),
    "datadog": ConnectorSpec(
        id="datadog",
        label="Datadog",
        description="Query metrics via the Datadog Metrics API.",
        fields=(
            FieldSpec("api_key", "API key", "datadog_api_key", "password", secret=True, required=True),
            FieldSpec("app_key", "Application key", "datadog_app_key", "password", secret=True, required=True),
            FieldSpec(
                "site", "Site", "datadog_site", placeholder="datadoghq.com", help="e.g. datadoghq.com, datadoghq.eu"
            ),
        ),
    ),
    "splunk": ConnectorSpec(
        id="splunk",
        label="Splunk",
        description="Run SPL searches through the Splunk management API.",
        fields=(
            FieldSpec("host", "Host", "splunk_host", required=True, placeholder="splunk.example.com"),
            FieldSpec("port", "Management port", "splunk_port", "number", placeholder="8089"),
            FieldSpec(
                "token",
                "Auth token",
                "splunk_token",
                "password",
                secret=True,
                help="Provide a token, or a username and password.",
            ),
            FieldSpec("username", "Username", "splunk_username"),
            FieldSpec("password", "Password", "splunk_password", "password", secret=True),
            FieldSpec("verify_ssl", "Verify TLS certificate", "splunk_verify_ssl", "boolean"),
            FieldSpec("ca_bundle", "CA bundle path", "splunk_ca_bundle", help="Optional, for private CAs."),
        ),
        required_one_of=(("token", "password"),),
    ),
}


def _coerce(spec: FieldSpec, value: Any) -> Any:  # noqa: ANN401 - connector form data is untrusted JSON.
    if spec.kind == "number":
        return int(value)
    if spec.kind == "boolean":
        return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
    return str(value).strip()


class ConnectorConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or STATE_PATH
        self.state: dict[str, Any] = {"selected": [], "values": {}, "ingestion": {}}

    # ── persistence ────────────────────────────────────────────
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("connector_state_unreadable", path=str(self.path))
            return
        self.state = {
            "selected": [v for v in data.get("selected", []) if v in CATALOG],
            "values": data.get("values", {}),
            "ingestion": data.get("ingestion", {}),
        }
        self.apply()

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    # ── apply to live settings ─────────────────────────────────
    def apply(self) -> None:
        cfg = get_settings().integration
        for vendor, vals in self.state["values"].items():
            spec = CATALOG.get(vendor)
            if not spec:
                continue
            for f in spec.fields:
                if f.key in vals:
                    setattr(cfg, f.attr, _coerce(f, vals[f.key]))
        cfg.telemetry_connector_modes = ",".join(self.state["selected"])
        ing = self.state["ingestion"]
        if "services" in ing:
            cfg.ingestion_services = ing["services"]
        if "dry_run" in ing:
            cfg.ingestion_dry_run = bool(ing["dry_run"])
        if "poll_interval_seconds" in ing:
            cfg.ingestion_poll_interval_seconds = float(ing["poll_interval_seconds"])
        if "enabled" in ing:
            cfg.enable_live_ingestion = bool(ing["enabled"])

    # ── reads ──────────────────────────────────────────────────
    def _value(self, vendor: str, f: FieldSpec) -> Any:  # noqa: ANN401 - values originate in connector JSON/config.
        stored = self.state["values"].get(vendor, {})
        return stored[f.key] if f.key in stored else getattr(get_settings().integration, f.attr)

    def missing(self, vendor: str, overrides: dict[str, Any] | None = None) -> list[str]:
        spec = CATALOG[vendor]
        vals = {f.key: (overrides or {}).get(f.key) or self._value(vendor, f) for f in spec.fields}
        gaps = [f.label for f in spec.fields if f.required and not vals[f.key]]
        for group in spec.required_one_of:
            if not any(vals.get(k) for k in group):
                gaps.append(" or ".join(next(f.label for f in spec.fields if f.key == k) for k in group))
        return gaps

    def view(self) -> dict[str, Any]:
        connectors = []
        for spec in CATALOG.values():
            fields = []
            for f in spec.fields:
                item = f.public()
                value = self._value(spec.id, f)
                if f.secret:
                    item["is_set"] = bool(value)
                else:
                    item["value"] = value
                fields.append(item)
            gaps = self.missing(spec.id)
            connectors.append(
                {
                    "id": spec.id,
                    "label": spec.label,
                    "description": spec.description,
                    "selected": spec.id in self.state["selected"],
                    "configured": not gaps,
                    "missing": gaps,
                    "fields": fields,
                }
            )
        ing = self.state["ingestion"]
        cfg = get_settings().integration
        return {
            "connectors": connectors,
            "selected": list(self.state["selected"]),
            "connector_modes": ",".join(self.state["selected"]),
            "ingestion": {
                "enabled": bool(ing.get("enabled", cfg.enable_live_ingestion)),
                "services": ing.get("services", cfg.ingestion_services),
                "dry_run": bool(ing.get("dry_run", cfg.ingestion_dry_run)),
                "poll_interval_seconds": float(ing.get("poll_interval_seconds", cfg.ingestion_poll_interval_seconds)),
            },
        }

    # ── writes ─────────────────────────────────────────────────
    def set_selection(self, vendors: list[str]) -> None:
        unknown = [v for v in vendors if v not in CATALOG]
        if unknown:
            raise ValueError(f"Unknown connectors: {', '.join(unknown)}")
        self.state["selected"] = list(dict.fromkeys(vendors))
        self.save()
        self.apply()

    def set_credentials(self, vendor: str, values: dict[str, Any]) -> None:
        spec = CATALOG.get(vendor)
        if spec is None:
            raise ValueError(f"Unknown connector: {vendor}")
        known = {f.key: f for f in spec.fields}
        stored = self.state["values"].setdefault(vendor, {})
        for key, raw in values.items():
            f = known.get(key)
            if f is None:
                raise ValueError(f"Unknown field '{key}' for {vendor}")
            if f.secret and (raw is None or raw == ""):
                continue  # blank secret keeps the existing one
            if raw is None:
                continue
            try:
                stored[key] = _coerce(f, raw)
            except ValueError as exc:
                raise ValueError(f"Invalid value for {f.label}") from exc
        self.save()
        self.apply()

    def set_ingestion(self, *, services: str, dry_run: bool, poll_interval_seconds: float, enabled: bool) -> None:
        if poll_interval_seconds < 1:
            raise ValueError("Poll interval must be at least 1 second")
        self.state["ingestion"] = {
            "services": ",".join(s.strip() for s in services.split(",") if s.strip()),
            "dry_run": dry_run,
            "poll_interval_seconds": poll_interval_seconds,
            "enabled": enabled,
        }
        self.save()
        self.apply()

    def activation_problems(self) -> list[str]:
        problems = []
        if not self.state["selected"]:
            problems.append("Select at least one connector")
        for vendor in self.state["selected"]:
            gaps = self.missing(vendor)
            if gaps:
                problems.append(f"{CATALOG[vendor].label}: missing {', '.join(gaps)}")
        if not self.state["ingestion"].get("services"):
            problems.append("Enter at least one service to watch")
        return problems

    # ── connection test ────────────────────────────────────────
    async def test_connection(self, vendor: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        from data_pipeline.connectors.adapters.factory import get_telemetry_adapter

        spec = CATALOG.get(vendor)
        if spec is None:
            raise ValueError(f"Unknown connector: {vendor}")
        gaps = self.missing(vendor, overrides)
        if gaps:
            return {"ok": False, "detail": f"Missing: {', '.join(gaps)}"}

        cfg = get_settings().integration
        saved = {f.attr: getattr(cfg, f.attr) for f in spec.fields}
        try:
            for f in spec.fields:
                raw = (overrides or {}).get(f.key)
                if raw not in (None, ""):
                    setattr(cfg, f.attr, _coerce(f, raw))
                else:
                    setattr(cfg, f.attr, self._value(vendor, f))
            adapter = get_telemetry_adapter(vendor)  # constructed synchronously, so no awaits while overridden
        except ValueError:
            return {"ok": False, "detail": "Invalid field value"}
        finally:
            for attr, value in saved.items():
                setattr(cfg, attr, value)

        try:
            ok = await asyncio.wait_for(adapter.health_check(), timeout=10)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:300]}
        finally:
            close = getattr(adapter, "close", None)
            if close:
                await close()
        return {"ok": bool(ok), "detail": "Connected" if ok else "Health check failed (check URL and credentials)"}


connector_store = ConnectorConfigStore()
