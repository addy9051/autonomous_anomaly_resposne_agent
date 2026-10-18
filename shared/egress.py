"""
Application-level egress guard (defence in depth).

The real boundary is the VPC (default-deny egress, VPC endpoints, security groups).
This module makes the application fail closed as well: every outbound HTTP call goes
through ``guarded_async_client`` / ``check_url`` and is refused unless the target is
a private address, an explicitly allowed CIDR, or an explicitly allowed host.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

from shared.config import get_settings

PRIVATE_DEFAULT_CIDRS = (
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "::1/128",
    "fc00::/7",
)


class EgressViolation(PermissionError):
    """Raised when an outbound call targets a destination outside the allowlist."""


def _split(value: str) -> list[str]:
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _networks(app: Any = None) -> list[ipaddress._BaseNetwork]:  # noqa: ANN401
    settings = app or get_settings().app
    cidrs = list(PRIVATE_DEFAULT_CIDRS) + [c for c in settings.egress_allowed_cidrs.split(",") if c.strip()]
    return [ipaddress.ip_network(c.strip(), strict=False) for c in cidrs]


def _host_allowed(host: str, app: Any = None) -> bool:  # noqa: ANN401
    for pattern in _split((app or get_settings().app).egress_allowed_hosts):
        if pattern == host or (pattern.startswith("*.") and host.endswith(pattern[1:])):
            return True
    return False


def _ip_allowed(ip: ipaddress._BaseAddress, app: Any = None) -> bool:  # noqa: ANN401
    return any(ip in net for net in _networks(app) if net.version == ip.version)


def check_host(host: str, app: Any = None) -> None:  # noqa: ANN401
    """Raise EgressViolation unless ``host`` is a permitted destination.

    ``app`` lets config code pass its own settings while ``get_settings()`` is still being built.
    """
    if not (app or get_settings().app).egress_enforce:
        return
    host = (host or "").strip().lower().strip("[]")
    if not host:
        raise EgressViolation("empty egress host")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if _ip_allowed(ip, app) or _host_allowed(host, app):
            return
        raise EgressViolation(f"egress to {host} is not allowed")
    if _host_allowed(host, app):
        return
    try:
        resolved = {ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)}
    except (OSError, ValueError) as exc:
        raise EgressViolation(f"cannot resolve {host}; failing closed") from exc
    if resolved and all(_ip_allowed(addr, app) for addr in resolved):
        return
    raise EgressViolation(f"egress to {host} is not allowed")


def check_url(url: str, app: Any = None) -> str:  # noqa: ANN401
    """Validate a URL against the egress policy and return it unchanged."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise EgressViolation(f"unsupported scheme for egress: {parsed.scheme!r}")
    check_host(parsed.hostname or "", app)
    return url


async def _request_hook(request: httpx.Request) -> None:
    check_host(request.url.host)


def guarded_async_client(**kwargs: Any) -> httpx.AsyncClient:  # noqa: ANN401
    """httpx.AsyncClient that refuses requests to non-allowlisted destinations."""
    hooks = kwargs.pop("event_hooks", {}) or {}
    hooks.setdefault("request", []).insert(0, _request_hook)
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)


def egress_self_test() -> dict[str, bool]:
    """Prove at startup that a public destination is refused (when enforcement is on)."""
    if not get_settings().app.egress_enforce:
        return {"enforced": False, "public_blocked": False}
    try:
        check_host("93.184.216.34")  # public IP literal; no DNS needed
    except EgressViolation:
        return {"enforced": True, "public_blocked": True}
    return {"enforced": True, "public_blocked": False}
