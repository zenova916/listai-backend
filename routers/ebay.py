"""routers/ebay.py — eBay OAuth connect flow"""
import os
import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from services.auth_service import get_current_user
from services.ebay_service import get_auth_url, exchange_code, encrypt_token
from db.supabase_client import (
    save_ebay_account, get_ebay_accounts, delete_ebay_account,
    PLAN_EBAY_ACCOUNTS
)
from datetime import datetime, timedelta, timezone

router = APIRouter()

FRONTEND = os.getenv("FRONTEND_URL", "http://localhost:3000")


async def fetch_ebay_user_info(access_token: str, sandbox: bool = False):
    """
    Call eBay's identity API to get the real username + email.
    Returns (username, email) — falls back to safe defaults if it fails.
    """
    base = "https://apiz.sandbox.ebay.com" if sandbox else "https://apiz.ebay.com"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{base}/commerce/identity/v1/user/",
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if r.status_code == 200:
            data = r.json()
            username = data.get("username") or "eBay account"
            email = (
                data.get("individualAccount", {}).get("email")
                or data.get("businessAccount", {}).get("email")
                or ""
            )
            return username, email
    except Exception:
        pass
    return "eBay account", ""


@router.get("/connect")
async def connect_ebay(sandbox: bool = False, token: str = None):
    """Redirect user to eBay OAuth."""
    if not token:
        raise HTTPException(401, "Missing token")
    from services.auth_service import decode_token
    user_id = decode_token(token)
    url = get_auth_url(sandbox=sandbox)
    url += f"&state={user_id}|{'1' if sandbox else '0'}"
    return RedirectResponse(url)


@router.get("/callback")
async def ebay_callback(code: str = None, state: str = "", error: str = None):
    """eBay redirects here after user approves. Exchange code for tokens."""
    if error or not code:
        return RedirectResponse(
            f"{FRONTEND}/listing-tool.html?ebay=error&reason={error or 'no_code'}"
        )

    parts = state.split("|")
    user_id = parts[0] if parts else ""
    sandbox = parts[1] == "1" if len(parts) > 1 else True

    if not user_id:
        return RedirectResponse(
            f"{FRONTEND}/listing-tool.html?ebay=error&reason=bad_state"
        )

    # ── Check eBay account limit for user's plan ──────────────
    from db.supabase_client import get_user_by_id
    user = await get_user_by_id(user_id)
    if not user:
        return RedirectResponse(
            f"{FRONTEND}/listing-tool.html?ebay=error&reason=user_not_found"
        )

    plan = user.get("plan", "free")
    max_accounts = PLAN_EBAY_ACCOUNTS.get(plan, 1)
    existing_accounts = await get_ebay_accounts(user_id)

    if len(existing_accounts) >= max_accounts:
        plan_display = plan.capitalize()
        return RedirectResponse(
            f"{FRONTEND}/listing-tool.html?ebay=error"
            f"&reason=account_limit"
            f"&limit={max_accounts}"
            f"&plan={plan_display}"
        )

    # ── Exchange code for tokens ──────────────────────────────
    token_data = await exchange_code(code, sandbox=sandbox)
    if "access_token" not in token_data:
        return RedirectResponse(
            f"{FRONTEND}/listing-tool.html?ebay=error&reason=token_exchange_failed"
        )

    access_enc  = encrypt_token(token_data["access_token"])
    refresh_enc = encrypt_token(token_data.get("refresh_token", ""))
    expires_in  = token_data.get("expires_in", 7200)
    expires_at  = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # Fetch real eBay username + email from eBay Identity API
    ebay_username, ebay_email = await fetch_ebay_user_info(
        token_data["access_token"], sandbox=sandbox
    )

    await save_ebay_account(
        user_id=user_id,
        ebay_username=ebay_username,
        ebay_email=ebay_email,
        access_token_enc=access_enc,
        refresh_token_enc=refresh_enc,
        expires_at=expires_at,
        sandbox=sandbox,
    )

    return RedirectResponse(f"{FRONTEND}/listing-tool.html?ebay=connected")


@router.get("/accounts")
async def list_accounts(user=Depends(get_current_user)):
    accounts = await get_ebay_accounts(user["id"])
    plan = user.get("plan", "free")
    max_accounts = PLAN_EBAY_ACCOUNTS.get(plan, 1)
    return {
        "accounts": accounts,
        "max_accounts": max_accounts,
        "can_add_more": len(accounts) < max_accounts,
    }


@router.delete("/accounts/{account_id}")
async def disconnect_account(account_id: str, user=Depends(get_current_user)):
    deleted = await delete_ebay_account(account_id=account_id, user_id=user["id"])
    if not deleted:
        raise HTTPException(404, "Account not found or not yours")
    return {"status": "disconnected"}
