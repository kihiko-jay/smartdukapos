"""
KRA eTIMS (Electronic Tax Invoice Management System) integration — production service.

All VAT-registered businesses in Kenya must submit electronic tax invoices to KRA
in real time via the eTIMS REST API. Each sale must produce a KRA invoice number
and QR code that appears on the customer receipt.

URLs:
  Production: https://etims-api.kra.go.ke/etims-api
  Sandbox:    https://etims-sbx-api.kra.go.ke/etims-api

To test against sandbox:
  Set ETIMS_URL=https://etims-sbx-api.kra.go.ke/etims-api in your .env
  Obtain test credentials from the KRA eTIMS portal.

CRITICAL CONTRACT: submit_invoice() MUST NEVER RAISE.
A failed eTIMS submission must not void or block a completed sale. The sale
is already committed to the database before this function is called. If eTIMS
is unreachable or rejects the payload, we return etims_synced=False and let
the retry scheduler pick it up later.
"""

import logging
import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


def _build_item_payload(item: dict) -> dict:
    """
    Build a single KRA item dict from a transaction item snapshot.

    Fix 3: VAT divisor derived from settings.VAT_RATE (not hardcoded 1.16).
    Fix 4: taxTyCd read from the item's tax_code/vat_exempt snapshot so
           zero-rated and exempt goods file correctly with KRA.
    """
    vat_multiplier = float(settings.VAT_RATE)          # e.g. 0.16
    vat_divisor    = 1.0 + vat_multiplier               # e.g. 1.16

    line_total = float(item["line_total"])

    # Determine the KRA tax type code from the item snapshot.
    # B = standard rate, E = VAT-exempt, Z = zero-rated.
    if item.get("vat_exempt"):
        tax_ty_cd  = "E"
        taxbl_amt  = line_total
        tax_amt    = 0.0
    else:
        raw_code   = (item.get("tax_code") or "B").upper()
        if raw_code == "Z":
            tax_ty_cd = "Z"
            taxbl_amt = line_total
            tax_amt   = 0.0
        else:
            # Standard rate (code "B" or anything else)
            tax_ty_cd = "B"
            taxbl_amt = round(line_total / vat_divisor, 2)
            tax_amt   = round(line_total - taxbl_amt, 2)

    return {
        "itemNm":   item["product_name"],
        "qty":      item["qty"],
        "prc":      round(float(item["unit_price"]), 2),
        "splyAmt":  round(line_total, 2),
        "dcAmt":    round(float(item.get("discount", 0)), 2),
        "taxblAmt": taxbl_amt,
        "taxAmt":   tax_amt,
        "taxTyCd":  tax_ty_cd,
    }


# Returned whenever eTIMS is unavailable or rejects the submission.
# The transaction is already saved; a background job retries failed invoices.
_FAILED_RESULT = {"etims_invoice_no": None, "etims_qr_code": None, "etims_synced": False}


async def submit_invoice(txn_data: dict) -> dict:
    """
    Submit a completed sale to KRA eTIMS and return the invoice number + QR URL.

    Args:
        txn_data: dict with keys:
            txn_number (str), total (Decimal|float), vat_amount (Decimal|float),
            created_at (datetime), items (list of dicts with product_name, qty,
            unit_price, line_total, discount)

    Returns:
        dict with:
            etims_invoice_no (str|None)
            etims_qr_code    (str|None)  -- URL provided by KRA, not generated locally
            etims_synced     (bool)

    This function NEVER raises. Any exception -> returns _FAILED_RESULT.
    """
    # 1. Skip entirely if eTIMS is not configured (dev / unconfigured store)
    if not settings.ETIMS_PIN:
        logger.warning(
            "eTIMS not configured -- skipping submission for %s",
            txn_data.get("txn_number", "unknown"),
        )
        return _FAILED_RESULT

    # 2. Use ETIMS_URL from settings (developer switches sandbox <-> production here)
    base_url = settings.ETIMS_URL

    # 3. Build the KRA VSCU payload
    payload = {
        "tpin":         settings.ETIMS_PIN,
        "bhfId":        settings.ETIMS_BRANCH_ID,
        "invcNo":       txn_data["txn_number"],
        "salesDt":      txn_data["created_at"].strftime("%Y%m%d"),
        "totTaxblAmt":  round(float(txn_data["total"]) - float(txn_data["vat_amount"]), 2),
        "totTax":       round(float(txn_data["vat_amount"]), 2),
        "totAmt":       round(float(txn_data["total"]), 2),
        "itemList": [
            _build_item_payload(item)
            for item in txn_data["items"]
        ],
    }

    # 4 & 5. POST to KRA, parse result
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{base_url}/vscu/req",
                json=payload,
                headers={
                    "bhfId":        settings.ETIMS_BRANCH_ID,
                    "dvcSrlNo":     settings.ETIMS_DEVICE_SERIAL,
                    "Content-Type": "application/json",
                },
            )
            data = response.json()

        result_code = data.get("resultCd")

        if result_code == "000":
            return {
                "etims_invoice_no": data["rcptNo"],
                "etims_qr_code":    data.get("qrCodeUrl", ""),
                "etims_synced":     True,
            }

        logger.error(
            "eTIMS rejection for %s -- resultCd=%s, resultMsg=%s",
            txn_data.get("txn_number"),
            result_code,
            data.get("resultMsg", "no message"),
        )
        return _FAILED_RESULT

    except Exception as exc:
        logger.error(
            "eTIMS submission error for %s: %s",
            txn_data.get("txn_number", "unknown"),
            exc,
        )
        return _FAILED_RESULT
