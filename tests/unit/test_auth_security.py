"""
Unit tests for hardened authentication:
- Untrusted identity header protection with gateway secret verification
- Prevention of silent development auth bypasses
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import api


@pytest.mark.asyncio
async def test_dev_auth_fails_closed_when_no_token_configured() -> None:
    """In development, if no API_AUTH_TOKEN is configured, verify_auth must fail closed."""
    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    settings = SimpleNamespace(
        app=SimpleNamespace(
            app_env="development",
            trusted_identity_header="X-Authenticated-User",
            require_gateway_auth=False,
            api_auth_token="",
            api_gateway_secret="",
            gateway_secret_header="X-Gateway-Secret",
            allow_anonymous_dev=False,
        )
    )

    with patch("api.get_settings", return_value=settings), pytest.raises(HTTPException) as exc_info:
        await api.verify_auth(request, api_key=None)

    assert exc_info.value.status_code == 401
    assert "No authentication configured" in exc_info.value.detail


@pytest.mark.asyncio
async def test_dev_auth_allows_anonymous_only_when_explicitly_configured() -> None:
    """When allow_anonymous_dev is explicitly True, unauthenticated access is permitted with warning."""
    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    settings = SimpleNamespace(
        app=SimpleNamespace(
            app_env="development",
            trusted_identity_header="X-Authenticated-User",
            require_gateway_auth=False,
            api_auth_token="",
            api_gateway_secret="",
            gateway_secret_header="X-Gateway-Secret",
            allow_anonymous_dev=True,
        )
    )

    with patch("api.get_settings", return_value=settings):
        identity = await api.verify_auth(request, api_key=None)

    assert identity == "anonymous-dev"


@pytest.mark.asyncio
async def test_dev_auth_spoofed_identity_header_rejected_without_key() -> None:
    """In development, sending X-Authenticated-User without valid API key is rejected."""
    request = Request(
        {
            "type": "http",
            "headers": [(b"x-authenticated-user", b"admin")],
            "method": "POST",
            "path": "/",
        }
    )
    settings = SimpleNamespace(
        app=SimpleNamespace(
            app_env="development",
            trusted_identity_header="X-Authenticated-User",
            require_gateway_auth=False,
            api_auth_token="dev-secret",
            api_gateway_secret="",
            gateway_secret_header="X-Gateway-Secret",
            allow_anonymous_dev=False,
        )
    )

    with patch("api.get_settings", return_value=settings), pytest.raises(HTTPException) as exc_info:
        await api.verify_auth(request, api_key=None)

    assert exc_info.value.status_code == 401
    assert "requires a valid X-API-Key header" in exc_info.value.detail


@pytest.mark.asyncio
async def test_dev_auth_identity_header_accepted_with_valid_key() -> None:
    """In development, a custom identity header is accepted if caller also provides valid API key."""
    request = Request(
        {
            "type": "http",
            "headers": [(b"x-authenticated-user", b"sre-alice")],
            "method": "POST",
            "path": "/",
        }
    )
    settings = SimpleNamespace(
        app=SimpleNamespace(
            app_env="development",
            trusted_identity_header="X-Authenticated-User",
            require_gateway_auth=False,
            api_auth_token="dev-secret",
            api_gateway_secret="",
            gateway_secret_header="X-Gateway-Secret",
            allow_anonymous_dev=False,
        )
    )

    with patch("api.get_settings", return_value=settings):
        identity = await api.verify_auth(request, api_key="dev-secret")

    assert identity == "sre-alice"


@pytest.mark.asyncio
async def test_gateway_secret_verification_succeeds() -> None:
    """When API_GATEWAY_SECRET is configured, matching secret validates identity header."""
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-authenticated-user", b"arn:aws:iam::123456789012:role/sre"),
                (b"x-gateway-secret", b"super-secure-shared-secret"),
            ],
            "method": "POST",
            "path": "/",
        }
    )
    settings = SimpleNamespace(
        app=SimpleNamespace(
            app_env="production",
            trusted_identity_header="X-Authenticated-User",
            require_gateway_auth=True,
            api_auth_token="",
            api_gateway_secret="super-secure-shared-secret",
            gateway_secret_header="X-Gateway-Secret",
            allow_anonymous_dev=False,
        )
    )

    with patch("api.get_settings", return_value=settings):
        identity = await api.verify_auth(request, api_key=None)

    assert identity == "arn:aws:iam::123456789012:role/sre"


@pytest.mark.asyncio
async def test_gateway_secret_verification_fails_on_missing_secret() -> None:
    """When API_GATEWAY_SECRET is configured, missing secret header is rejected."""
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-authenticated-user", b"arn:aws:iam::123456789012:role/sre"),
            ],
            "method": "POST",
            "path": "/",
        }
    )
    settings = SimpleNamespace(
        app=SimpleNamespace(
            app_env="production",
            trusted_identity_header="X-Authenticated-User",
            require_gateway_auth=True,
            api_auth_token="",
            api_gateway_secret="super-secure-shared-secret",
            gateway_secret_header="X-Gateway-Secret",
            allow_anonymous_dev=False,
        )
    )

    with patch("api.get_settings", return_value=settings), pytest.raises(HTTPException) as exc_info:
        await api.verify_auth(request, api_key=None)

    assert exc_info.value.status_code == 401
    assert "Untrusted X-Authenticated-User" in exc_info.value.detail


@pytest.mark.asyncio
async def test_gateway_secret_verification_fails_on_wrong_secret() -> None:
    """When API_GATEWAY_SECRET is configured, wrong secret header is rejected."""
    request = Request(
        {
            "type": "http",
            "headers": [
                (b"x-authenticated-user", b"arn:aws:iam::123456789012:role/sre"),
                (b"x-gateway-secret", b"attacker-guess"),
            ],
            "method": "POST",
            "path": "/",
        }
    )
    settings = SimpleNamespace(
        app=SimpleNamespace(
            app_env="production",
            trusted_identity_header="X-Authenticated-User",
            require_gateway_auth=True,
            api_auth_token="",
            api_gateway_secret="super-secure-shared-secret",
            gateway_secret_header="X-Gateway-Secret",
            allow_anonymous_dev=False,
        )
    )

    with patch("api.get_settings", return_value=settings), pytest.raises(HTTPException) as exc_info:
        await api.verify_auth(request, api_key=None)

    assert exc_info.value.status_code == 401
    assert "Untrusted X-Authenticated-User" in exc_info.value.detail
