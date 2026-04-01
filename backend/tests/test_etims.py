"""
eTIMS service tests — verifies the never-raise contract and response parsing.

These tests mock httpx so no real KRA API calls are made.
The critical invariant: submit_invoice() must NEVER raise, even on network
errors or malformed responses.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
from decimal import Decimal

from app.services.etims import submit_invoice


# ── Shared test fixture ───────────────────────────────────────────────────────

SAMPLE_TXN = {
    "txn_number": "TXN-ETIMS-001",
    "total":       Decimal("116.00"),
    "vat_amount":  Decimal("16.00"),
    "created_at":  datetime(2025, 3, 1, 10, 0, 0),
    "items": [
        {
            "product_name": "Unga Pembe 2kg",
            "qty":          2,
            "unit_price":   Decimal("58.00"),
            "line_total":   Decimal("116.00"),
            "discount":     Decimal("0.00"),
        }
    ],
}


# ── 1. Skip when unconfigured ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_skips_when_unconfigured():
    """
    When ETIMS_PIN is empty, submit_invoice must return etims_synced=False
    immediately without making any HTTP call.
    """
    with patch("app.services.etims.settings") as mock_settings:
        mock_settings.ETIMS_PIN = ""  # not configured

        # httpx.AsyncClient must NOT be called — patch it to fail if invoked
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.side_effect = AssertionError(
                "httpx must not be called when eTIMS is unconfigured"
            )
            result = await submit_invoice(SAMPLE_TXN)

    assert result["etims_synced"] is False
    assert result["etims_invoice_no"] is None
    assert result["etims_qr_code"] is None


# ── 2. Successful sandbox submission ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_submit_success_sandbox():
    """
    A 200 response with resultCd='000' must be parsed into a successful result
    with the invoice number and QR URL from KRA.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "resultCd":  "000",
        "rcptNo":    "KRA-INV-001",
        "qrCodeUrl": "https://qr.kra.go.ke/x",
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__  = AsyncMock(return_value=False)
    mock_client.post       = AsyncMock(return_value=mock_response)

    with patch("app.services.etims.settings") as mock_settings:
        mock_settings.ETIMS_PIN           = "P051234567R"
        mock_settings.ETIMS_BRANCH_ID     = "00"
        mock_settings.ETIMS_DEVICE_SERIAL = "DUKAPOS001"
        mock_settings.ETIMS_URL           = "https://etims-sbx-api.kra.go.ke/etims-api"

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await submit_invoice(SAMPLE_TXN)

    assert result["etims_synced"] is True
    assert result["etims_invoice_no"] == "KRA-INV-001"
    assert result["etims_qr_code"] == "https://qr.kra.go.ke/x"


# ── 3. Application-level rejection does not raise ────────────────────────────

@pytest.mark.asyncio
async def test_submit_failure_does_not_raise():
    """
    KRA returning resultCd != '000' (e.g. invalid PIN) must NOT raise.
    Must return etims_synced=False silently.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "resultCd":  "101",
        "resultMsg": "Invalid PIN",
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__  = AsyncMock(return_value=False)
    mock_client.post       = AsyncMock(return_value=mock_response)

    with patch("app.services.etims.settings") as mock_settings:
        mock_settings.ETIMS_PIN           = "P051234567R"
        mock_settings.ETIMS_BRANCH_ID     = "00"
        mock_settings.ETIMS_DEVICE_SERIAL = "DUKAPOS001"
        mock_settings.ETIMS_URL           = "https://etims-sbx-api.kra.go.ke/etims-api"

        with patch("httpx.AsyncClient", return_value=mock_client):
            # Must not raise
            result = await submit_invoice(SAMPLE_TXN)

    assert result["etims_synced"] is False
    assert result["etims_invoice_no"] is None


# ── 4. Network error does not raise ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_network_error_does_not_raise():
    """
    A network timeout must NOT raise — must return etims_synced=False.
    A failed eTIMS call must never void or block a completed sale.
    """
    import httpx

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__  = AsyncMock(return_value=False)
    mock_client.post       = AsyncMock(side_effect=httpx.ConnectTimeout("timeout"))

    with patch("app.services.etims.settings") as mock_settings:
        mock_settings.ETIMS_PIN           = "P051234567R"
        mock_settings.ETIMS_BRANCH_ID     = "00"
        mock_settings.ETIMS_DEVICE_SERIAL = "DUKAPOS001"
        mock_settings.ETIMS_URL           = "https://etims-api.kra.go.ke/etims-api"

        with patch("httpx.AsyncClient", return_value=mock_client):
            # Must not raise
            result = await submit_invoice(SAMPLE_TXN)

    assert result["etims_synced"] is False
    assert result["etims_invoice_no"] is None
    assert result["etims_qr_code"] is None
