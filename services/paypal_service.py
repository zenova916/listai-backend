"""
services/paypal_service.py
PayPal subscription verification and webhook handling.
Uses PayPal REST API v1 — works with your existing PayPal account.
"""
import os, httpx, base64

PAYPAL_MODE          = os.getenv("PAYPAL_MODE", "sandbox")
PAYPAL_CLIENT_ID     = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")

BASE_URL = (
    "https://api.sandbox.paypal.com" if PAYPAL_MODE == "sandbox"
    else "https://api.paypal.com"
)

PLAN_MAP = {
    os.getenv("PAYPAL_PLAN_STARTER", ""): "starter",
    os.getenv("PAYPAL_PLAN_PRO", ""):     "pro",
    os.getenv("PAYPAL_PLAN_AGENCY", ""):  "agency",
}


async def get_paypal_token() -> str:
    """Get a PayPal OAuth access token."""
    credentials = base64.b64encode(
        f"{PAYPAL_CLIENT_ID}:{PAYPAL_CLIENT_SECRET}".encode()
    ).decode()
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{BASE_URL}/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=10,
        )
    return r.json().get("access_token", "")


async def get_subscription_details(subscription_id: str) -> dict:
    """Fetch a subscription from PayPal API to verify it's active."""
    token = await get_paypal_token()
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{BASE_URL}/v1/billing/subscriptions/{subscription_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
    return r.json()


async def verify_subscription(subscription_id: str) -> tuple[bool, str]:
    """
    Returns (is_active, plan_name).
    Call this when user submits their PayPal subscription ID.
    """
    details = await get_subscription_details(subscription_id)
    status  = details.get("status", "")
    plan_id = details.get("plan_id", "")
    plan    = PLAN_MAP.get(plan_id, "unknown")
    return status == "ACTIVE", plan


def parse_webhook_event(payload: dict) -> dict | None:
    """
    Parse a PayPal webhook event and return what we need to act on.
    Returns dict with: event_type, subscription_id, payer_email, plan_id
    """
    event_type = payload.get("event_type", "")
    resource   = payload.get("resource", {})

    if event_type in (
        "BILLING.SUBSCRIPTION.ACTIVATED",
        "BILLING.SUBSCRIPTION.CANCELLED",
        "BILLING.SUBSCRIPTION.SUSPENDED",
        "PAYMENT.SALE.COMPLETED",
    ):
        return {
            "event_type":      event_type,
            "subscription_id": resource.get("id") or resource.get("billing_agreement_id", ""),
            "plan_id":         resource.get("plan_id", ""),
            "payer_email":     resource.get("subscriber", {}).get("email_address", ""),
            "status":          resource.get("status", ""),
        }
    return None
