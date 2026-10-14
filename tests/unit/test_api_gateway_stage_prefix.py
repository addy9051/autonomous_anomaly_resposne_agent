from api import _strip_api_gateway_stage_prefix


def test_strip_api_gateway_stage_prefix_from_proxy_path() -> None:
    assert _strip_api_gateway_stage_prefix("/prod/api/v1/incidents", "/prod") == "/api/v1/incidents"


def test_strip_exact_api_gateway_stage_prefix_to_root() -> None:
    assert _strip_api_gateway_stage_prefix("/prod", "/prod") == "/"


def test_stage_prefix_does_not_match_a_similar_path_segment() -> None:
    assert _strip_api_gateway_stage_prefix("/production/health", "/prod") == "/production/health"


def test_empty_stage_prefix_preserves_path() -> None:
    assert _strip_api_gateway_stage_prefix("/health", "") == "/health"
