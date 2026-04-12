"""
Unit tests for PII masking middleware (shared/pii.py).

Tests cover: credit cards, emails, IPs, SSNs, merchant IDs,
API keys, edge cases, and recursive dict sanitization.
"""

from shared.pii import sanitize_dict, sanitize_for_llm


class TestCreditCardMasking:
    """Card number detection and masking."""

    def test_standard_16_digit_card(self) -> None:
        text = "Card number is 4111111111111111 on file."
        result = sanitize_for_llm(text)
        assert "4111111111111111" not in result
        assert "CARD" in result

    def test_card_with_dashes(self) -> None:
        text = "Payment via 4111-1111-1111-1111"
        result = sanitize_for_llm(text)
        assert "4111-1111-1111-1111" not in result

    def test_card_with_spaces(self) -> None:
        text = "Card: 5500 0000 0000 0004"
        result = sanitize_for_llm(text)
        assert "5500 0000 0000 0004" not in result

    def test_amex_15_digit(self) -> None:
        text = "Amex card 378282246310005"
        result = sanitize_for_llm(text)
        assert "378282246310005" not in result


class TestEmailMasking:
    """Email address detection and masking."""

    def test_standard_email(self) -> None:
        text = "Contact user at john.doe@example.com for details."
        result = sanitize_for_llm(text)
        assert "john.doe@example.com" not in result
        assert "[EMAIL_REDACTED]" in result

    def test_email_with_plus(self) -> None:
        text = "Alerts go to sre+alerts@company.io"
        result = sanitize_for_llm(text)
        assert "sre+alerts@company.io" not in result


class TestIPMasking:
    """IPv4 and IPv6 address detection."""

    def test_ipv4(self) -> None:
        text = "Request from 192.168.1.100 timed out"
        result = sanitize_for_llm(text)
        assert "192.168.1.100" not in result
        assert "[IP_REDACTED]" in result

    def test_ipv4_in_url(self) -> None:
        text = "Connect to http://10.0.0.5:8080/api"
        result = sanitize_for_llm(text)
        assert "10.0.0.5" not in result


class TestSSNMasking:
    """SSN-like pattern detection."""

    def test_ssn_format(self) -> None:
        text = "Employee SSN: 123-45-6789"
        result = sanitize_for_llm(text)
        assert "123-45-6789" not in result
        assert "[SSN_REDACTED]" in result


class TestMerchantIDMasking:
    """Merchant identifier detection."""

    def test_merchant_id_prefix(self) -> None:
        text = "Transaction on MID-ABC123456789"
        result = sanitize_for_llm(text)
        assert "MID-ABC123456789" not in result
        assert "[MERCHANT_ID_REDACTED]" in result

    def test_merch_prefix(self) -> None:
        text = "MERCH_STORE99887766 flagged"
        result = sanitize_for_llm(text)
        assert "MERCH_STORE99887766" not in result


class TestAPIKeyMasking:
    """API key / secret detection."""

    def test_api_key(self) -> None:
        text = "api_key: sk_test_mock_secret_key_12345"
        result = sanitize_for_llm(text)
        assert "sk_test_mock_secret_key_12345" not in result
        assert "[SECRET_REDACTED]" in result


class TestEdgeCases:
    """Edge cases and non-PII preservation."""

    def test_empty_string(self) -> None:
        assert sanitize_for_llm("") == ""

    def test_none_passthrough(self) -> None:
        assert sanitize_for_llm(None) is None  # type: ignore[arg-type]

    def test_no_pii_preserved(self) -> None:
        text = "Latency spike on payment-gateway: p99 = 4500ms"
        assert sanitize_for_llm(text) == text

    def test_normal_numbers_preserved(self) -> None:
        text = "Error rate is 0.025 and request count is 15000"
        result = sanitize_for_llm(text)
        assert "0.025" in result

    def test_multiple_pii_types(self) -> None:
        text = "User john@test.com from 10.0.0.1 used card 4111111111111111 at MERCH_GLOBALSHOP12345"
        result = sanitize_for_llm(text)
        assert "john@test.com" not in result
        assert "10.0.0.1" not in result
        assert "4111111111111111" not in result
        assert "MERCH_GLOBALSHOP12345" not in result


class TestSanitizeDict:
    """Recursive dictionary sanitization."""

    def test_flat_dict(self) -> None:
        data = {"email": "user@test.com", "count": 42}
        result = sanitize_dict(data)
        assert result["email"] == "[EMAIL_REDACTED]"
        assert result["count"] == 42

    def test_nested_dict(self) -> None:
        data = {
            "event": {
                "source_ip": "192.168.1.1",
                "severity": "critical",
            }
        }
        result = sanitize_dict(data)
        assert "192.168.1.1" not in result["event"]["source_ip"]
        assert result["event"]["severity"] == "critical"

    def test_list_values(self) -> None:
        data = {
            "ips": ["10.0.0.1", "10.0.0.2"],
            "tags": ["production", "us-east"],
        }
        result = sanitize_dict(data)
        assert all("[IP_REDACTED]" in ip for ip in result["ips"])
        assert result["tags"] == ["production", "us-east"]
