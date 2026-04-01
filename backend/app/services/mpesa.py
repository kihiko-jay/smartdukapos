"""
M-PESA Daraja API integration — Lipa Na M-PESA STK Push.

Safaricom sandbox: https://sandbox.safaricom.co.ke
Production:        https://api.safaricom.co.ke

Steps:
  1. POST /oauth/v1/generate  → get access token
  2. POST /mpesa/stkpush/v1/processrequest → trigger STK push on customer phone
  3. Safaricom hits MPESA_CALLBACK_URL with payment result
  4. Our callback handler marks the transaction COMPLETED
"""

import base64
import httpx
from datetime import datetime
from app.core.config import settings


SANDBOX_BASE = "https://sandbox.safaricom.co.ke"
PROD_BASE    = "https://api.safaricom.co.ke"


def _base_url() -> str:
    return SANDBOX_BASE if settings.MPESA_ENV == "sandbox" else PROD_BASE


async def get_access_token() -> str:
    """Fetch a short-lived OAuth2 access token from Safaricom."""
    credentials = base64.b64encode(
        f"{settings.MPESA_CONSUMER_KEY}:{settings.MPESA_CONSUMER_SECRET}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_base_url()}/oauth/v1/generate?grant_type=client_credentials",
            headers={"Authorization": f"Basic {credentials}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]


def _generate_password() -> tuple[str, str]:
    """
    Returns (password, timestamp).
    Password = base64(shortcode + passkey + timestamp)
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    raw = f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}"
    password = base64.b64encode(raw.encode()).decode()
    return password, timestamp


async def stk_push(phone: str, amount: float, txn_number: str) -> dict:
    """
    Trigger an STK Push to the customer's phone.

    Args:
        phone:      Customer phone in format 2547XXXXXXXX
        amount:     Amount in KES (will be rounded to int — M-PESA requires whole shillings)
        txn_number: Our internal transaction reference

    Returns:
        Daraja API response dict with CheckoutRequestID
    """
    # Normalise phone: 07XX → 2547XX
    phone = phone.strip().replace("+", "")
    if phone.startswith("0"):
        phone = "254" + phone[1:]

    token = await get_access_token()
    password, timestamp = _generate_password()

    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerBuyGoodsOnline",
        "Amount": int(round(amount)),
        "PartyA": phone,
        "PartyB": settings.MPESA_SHORTCODE,
        "PhoneNumber": phone,
        "CallBackURL": settings.MPESA_CALLBACK_URL,
        "AccountReference": txn_number,
        "TransactionDesc": f"Payment for {txn_number} - {settings.STORE_NAME}",
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_base_url()}/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()


async def query_stk_status(checkout_request_id: str) -> dict:
    """Poll the status of an STK push (use if callback hasn't arrived)."""
    token = await get_access_token()
    password, timestamp = _generate_password()

    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_request_id,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_base_url()}/mpesa/stkpushquery/v1/query",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
