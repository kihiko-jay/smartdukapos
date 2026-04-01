"""
Tests for subscription M-PESA callback HMAC-SHA256 signature verification.

Covers three required behaviours:
  (a) A request carrying a valid HMAC-SHA256 signature is accepted (HTTP 200).
  (b) A request carrying an invalid signature is rejected with HTTP 400.
  (c) When MPESA_WEBHOOK_SECRET is not set the endpoint accepts all requests
      (backward compatibility — IP allowlisting in nginx is the outer guard).
"""

import hashlib
import hmac
import json

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

CALLBACK_URL = "/api/v1/subscription/mpesa-callback"

# A minimal, structurally valid Safaricom STK callback body that the endpoint
# can parse without hitting a KeyError (ResultCode != 0 so no DB writes occur).
_BODY = {
    "Body": {
        "stkCallback": {
            "MerchantRequestID": "test-merchant-1",
            "CheckoutRequestID": "ws_CO_test_001",
            "ResultCode": 1,
            "ResultDesc": "Request cancelled by user",
        }
    }
}


def _make_body_bytes() -> bytes:
    return json.dumps(_BODY).encode()


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSubscriptionCallbackSignatureVerification:

    def test_valid_signature_is_accepted(self, client, monkeypatch):
        """
        (a) A request with a correct HMAC-SHA256 signature must be accepted.
        The endpoint returns HTTP 200 with {"ResultCode": 0, ...}.
        """
        secret = "test-webhook-secret-abc123"
        monkeypatch.setenv("MPESA_WEBHOOK_SECRET", secret)

        body = _make_body_bytes()
        sig  = _sign(body, secret)

        response = client.post(
            CALLBACK_URL,
            content=body,
            headers={
                "Content-Type":    "application/json",
                "X-Mpesa-Signature": sig,
            },
        )

        assert response.status_code == 200, (
            f"Expected 200 for valid signature, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data.get("ResultCode") == 0

    def test_invalid_signature_is_rejected_with_400(self, client, monkeypatch):
        """
        (b) A request carrying an incorrect signature must be rejected with
        HTTP 400.  The wrong signature should never activate a subscription.
        """
        secret = "test-webhook-secret-abc123"
        monkeypatch.setenv("MPESA_WEBHOOK_SECRET", secret)

        body         = _make_body_bytes()
        wrong_sig    = "deadbeef" * 8   # wrong length and value

        response = client.post(
            CALLBACK_URL,
            content=body,
            headers={
                "Content-Type":     "application/json",
                "X-Mpesa-Signature": wrong_sig,
            },
        )

        assert response.status_code == 400, (
            f"Expected 400 for invalid signature, got {response.status_code}: {response.text}"
        )

    def test_missing_signature_is_rejected_with_400_when_secret_set(self, client, monkeypatch):
        """
        When MPESA_WEBHOOK_SECRET is configured, a request with no
        X-Mpesa-Signature header must also be rejected with HTTP 400.
        """
        monkeypatch.setenv("MPESA_WEBHOOK_SECRET", "some-configured-secret")

        body = _make_body_bytes()

        response = client.post(
            CALLBACK_URL,
            content=body,
            headers={"Content-Type": "application/json"},
            # intentionally no X-Mpesa-Signature
        )

        assert response.status_code == 400, (
            f"Expected 400 when secret set but header absent, "
            f"got {response.status_code}: {response.text}"
        )

    def test_no_secret_configured_accepts_request(self, client, monkeypatch):
        """
        (c) Backward-compatibility requirement: if MPESA_WEBHOOK_SECRET is not
        set, all callbacks are accepted regardless of the signature header.
        In production this path relies on nginx IP allowlisting.
        """
        monkeypatch.delenv("MPESA_WEBHOOK_SECRET", raising=False)

        body = _make_body_bytes()

        # No signature header at all — should still succeed
        response = client.post(
            CALLBACK_URL,
            content=body,
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200, (
            f"Expected 200 when no secret configured, "
            f"got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data.get("ResultCode") == 0

    def test_no_secret_configured_accepts_request_with_any_signature(self, client, monkeypatch):
        """
        (c continued) Even a garbage signature header should not cause a
        rejection when MPESA_WEBHOOK_SECRET is not set.
        """
        monkeypatch.delenv("MPESA_WEBHOOK_SECRET", raising=False)

        body = _make_body_bytes()

        response = client.post(
            CALLBACK_URL,
            content=body,
            headers={
                "Content-Type":     "application/json",
                "X-Mpesa-Signature": "totally-wrong-signature",
            },
        )

        assert response.status_code == 200, (
            f"Expected 200 regardless of signature when no secret configured, "
            f"got {response.status_code}: {response.text}"
        )
