"""routers/ebay.py — eBay OAuth connect flow"""
import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from services.auth_service import get_current_user
from services.ebay_service import get_auth_url, exchange_code, encrypt_token
from db.supabase_client import save_ebay_account, get_ebay_accounts
from datetime import datetime, timedelta, timezone

router = APIRouter()

FRONTEND = os.getenv("FRONTEND_URL", "http://localhost:3000")


@router.get("/connect")
async def connect_ebay(sandbox: bool = True, user=Depends(get_current_user)):
    """
    Redirect user to eBay OAuth.
    Use sandbox=true for testing (default), sandbox=false for real listings.
    """
    url = get_auth_url(sandbox=sandbox)
    # Store user_id and sandbox flag in eBay's state param
    url += f"&state={user['id']}|{'1' if sandbox else '0'}"
    return RedirectResponse(url)


@router.get("/callback")
async def ebay_callback(code: str = None, state: str = "", error: str = None):
    """eBay redirects here after user approves. Exchange code for tokens."""
    if error or not code:
        return RedirectResponse(f"{FRONTEND}/dashboard?ebay=error&reason={error or 'no_code'}")

    parts = state.split("|")
    user_id = parts[0] if parts else ""
    sandbox = parts[1] == "1" if len(parts) > 1 else True

    if not user_id:
        return RedirectResponse(f"{FRONTEND}/dashboard?ebay=error&reason=bad_state")

    token_data = await exchange_code(code, sandbox=sandbox)
    if "access_token" not in token_data:
        return RedirectResponse(f"{FRONTEND}/dashboard?ebay=error&reason=token_exchange_failed")

    access_enc  = encrypt_token(token_data["access_token"])
    refresh_enc = encrypt_token(token_data.get("refresh_token", ""))
    expires_in  = token_data.get("expires_in", 7200)
    expires_at  = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    # eBay username comes from the token response
    ebay_username = token_data.get("username") or "eBay account"

    await save_ebay_account(
        user_id=user_id,
        ebay_username=ebay_username,
        access_token_enc=access_enc,
        refresh_token_enc=refresh_enc,
        expires_at=expires_at,
        sandbox=sandbox,
    )

    return RedirectResponse(f"{FRONTEND}/dashboard?ebay=connected")


@router.get("/accounts")
async def list_accounts(user=Depends(get_current_user)):
    accounts = await get_ebay_accounts(user["id"])
    return {"accounts": accounts}


@router.delete("/accounts/{account_id}")
async def disconnect_account(account_id: str, user=Depends(get_current_user)):
    from db.supabase_client import AsyncSessionLocal
    from sqlalchemy import text
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM ebay_accounts WHERE id=:id AND user_id=:uid"),
            {"id": account_id, "uid": user["id"]}
        )
        await db.commit()
    return {"status": "disconnected"}
