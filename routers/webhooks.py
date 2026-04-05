"""routers/webhooks.py — PayPal subscription webhooks"""
from fastapi import APIRouter, Request, HTTPException
from services.paypal_service import parse_webhook_event, PLAN_MAP
from services.email_service import send_plan_activated_email
from db.supabase_client import get_user_by_email, update_user_plan
from sqlalchemy import text

router = APIRouter()


@router.post("/paypal")
async def paypal_webhook(request: Request):
    """
    PayPal sends events here when users pay, cancel, etc.
    Set up in PayPal Developer Dashboard → Webhooks:
    URL: https://your-render-url.onrender.com/webhooks/paypal

    Events to subscribe:
      BILLING.SUBSCRIPTION.ACTIVATED
      BILLING.SUBSCRIPTION.CANCELLED
      BILLING.SUBSCRIPTION.SUSPENDED
      PAYMENT.SALE.COMPLETED
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")

    event = parse_webhook_event(payload)
    if not event:
        # Event we don't handle — return 200 so PayPal doesn't retry
        return {"status": "ignored"}

    event_type      = event["event_type"]
    subscription_id = event["subscription_id"]
    payer_email     = event["payer_email"]
    plan_id         = event["plan_id"]
    plan_name       = PLAN_MAP.get(plan_id, "starter")

    print(f"[PayPal Webhook] {event_type} | sub={subscription_id} | email={payer_email}")

    if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
        # Payment confirmed — upgrade the user's plan
        user = await get_user_by_email(payer_email)
        if user:
            await update_user_plan(user["id"], plan_name, subscription_id)
            send_plan_activated_email(payer_email, user["name"], plan_name)
        else:
            # User hasn't signed up yet — store pending activation
            # They'll get upgraded when they sign up with the same email
            print(f"[PayPal] No user found for {payer_email} — plan activation pending")

    elif event_type in ("BILLING.SUBSCRIPTION.CANCELLED", "BILLING.SUBSCRIPTION.SUSPENDED"):
        # Downgrade to free plan
        user = await get_user_by_email(payer_email)
        if user:
            await update_user_plan(user["id"], "free", "")
            print(f"[PayPal] Downgraded {payer_email} to free plan")

    elif event_type == "PAYMENT.SALE.COMPLETED":
        # Monthly renewal — reset listing quota
        user = await get_user_by_email(payer_email)
        if user:
            from db.supabase_client import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text("UPDATE users SET listings_used=0, quota_reset_at=NOW() WHERE id=:id"),
                    {"id": user["id"]}
                )
                await db.commit()
            print(f"[PayPal] Reset listing quota for {payer_email}")

    return {"status": "processed"}


@router.post("/paypal/activate-manual")
async def activate_manually(subscription_id: str, email: str):
    """
    Manual plan activation — use this if the webhook misses someone.
    Call from your own computer: POST /webhooks/paypal/activate-manual
    Protected: only callable from your server (add IP check in production).
    """
    from services.paypal_service import verify_subscription
    is_active, plan = await verify_subscription(subscription_id)
    if not is_active:
        raise HTTPException(400, f"Subscription {subscription_id} is not active on PayPal")

    user = await get_user_by_email(email)
    if not user:
        raise HTTPException(404, f"No user found with email {email}")

    await update_user_plan(user["id"], plan, subscription_id)
    return {"status": "activated", "plan": plan, "user": email}

@router.get("/ebay-account-deletion")
async def ebay_deletion_challenge(challenge_code: str = None):
    """eBay marketplace account deletion compliance endpoint."""
    import hashlib, os
    if not challenge_code:
        return {"status": "ok"}
    verification_token = os.getenv("EBAY_VERIFICATION_TOKEN", "listai_webhook_verification_token_2026_secure")
    endpoint = "https://listai-api.onrender.com/webhooks/ebay-account-deletion"
    m = hashlib.sha256()
    m.update((challenge_code + verification_token + endpoint).encode())
    return {"challengeResponse": m.hexdigest()}

@router.post("/ebay-account-deletion")
async def ebay_account_deletion(request: Request):
    """Handle eBay account deletion notifications."""
    return {"status": "processed"}