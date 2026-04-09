"""routers/listings.py — Generate, edit, publish, and track listings"""
import json, uuid, csv, io
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from services.auth_service import get_current_user
from services.groq_service import generate_listing_from_title, generate_listing_from_csv_row
from services.ebay_service import publish_to_ebay, get_item_status
from db.supabase_client import (
    save_listing, get_user_listings, update_listing,
    mark_listing_published, mark_listing_failed,
    get_ebay_accounts, reset_quota_if_needed,
    PLAN_CSV_ALLOWED, PLAN_IMAGE_ALLOWED
)
from db.supabase_client import get_db

router = APIRouter()


class TitleRequest(BaseModel):
    title: str
    condition: Optional[str] = "Used"
    ebay_account_id: Optional[str] = None


class EditRequest(BaseModel):
    final_title: Optional[str] = None
    final_description: Optional[str] = None
    final_price: Optional[float] = None
    final_condition: Optional[str] = None
    final_category_id: Optional[str] = None
    final_specifics: Optional[dict] = None


class PublishRequest(BaseModel):
    listing_id: str
    ebay_account_id: str


# ── Check + consume quota ─────────────────────────────────────

async def check_quota(user: dict, count: int = 1):
    """Check quota AFTER monthly reset has been applied."""
    quota = user.get("listings_quota") or 5
    used  = user.get("listings_used") or 0
    if quota == 999999:
        return  # agency — unlimited
    remaining = quota - used
    if remaining < count:
        raise HTTPException(
            403,
            f"Listing quota reached ({used}/{quota} used on {user.get('plan','free')} plan). "
            "Upgrade your plan to get more listings."
        )


async def consume_quota(user_id: str, count: int = 1):
    from db.supabase_client import AsyncSessionLocal
    from sqlalchemy import text
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE users SET listings_used = listings_used + :n WHERE id = :id"),
            {"n": count, "id": user_id}
        )
        await db.commit()


# ── Routes ─────────────────────────────────────────────────────

@router.post("/generate/title")
async def generate_from_title(req: TitleRequest, user=Depends(get_current_user)):
    # 1. Reset monthly quota if 30 days have passed
    user = await reset_quota_if_needed(user)

    # 2. Check quota
    await check_quota(user, 1)

    try:
        ai = await generate_listing_from_title(req.title, req.condition or "Used")
    except Exception as e:
        raise HTTPException(500, f"AI generation failed: {e}")

    lid = str(uuid.uuid4())
    specifics_json = json.dumps(ai.get("item_specifics", {}))

    listing = {
        "id":               lid,
        "user_id":          user["id"],
        "ebay_account_id":  req.ebay_account_id,
        "input_type":       "title",
        "input_raw":        req.title,
        "ai_title":         ai.get("title", ""),
        "ai_description":   ai.get("description", ""),
        "ai_category":      ai.get("category", ""),
        "ai_category_id":   ai.get("category_id", ""),
        "ai_condition":     ai.get("condition", "Used"),
        "ai_price":         ai.get("price", 0),
        "ai_specifics":     specifics_json,
        "final_title":      ai.get("title", ""),
        "final_description":ai.get("description", ""),
        "final_price":      ai.get("price", 0),
        "final_condition":  ai.get("condition", "Used"),
        "final_category_id":ai.get("category_id", ""),
        "final_specifics":  specifics_json,
    }
    await save_listing(listing)
    await consume_quota(user["id"], 1)

    return {**listing, "ai_specifics": ai.get("item_specifics", {}),
            "final_specifics": ai.get("item_specifics", {}),
            "price_low": ai.get("price_low"), "price_high": ai.get("price_high")}


@router.post("/generate/csv")
async def generate_from_csv(
    file: UploadFile = File(...),
    ebay_account_id: str = "",
    bg: BackgroundTasks = None,
    user=Depends(get_current_user),
):
    # Plan check — CSV only for Starter, Pro, Agency
    plan = user.get("plan", "free")
    if plan not in PLAN_CSV_ALLOWED:
        raise HTTPException(
            403,
            "CSV bulk upload requires Starter plan or higher. "
            "Upgrade your plan to use this feature."
        )

    contents = await file.read()
    try:
        reader = csv.DictReader(io.StringIO(contents.decode("utf-8-sig")))
        rows = [row for row in reader]
    except Exception:
        raise HTTPException(400, "Could not parse CSV. Make sure it's a valid UTF-8 CSV file.")

    if not rows:
        raise HTTPException(400, "CSV is empty")
    if len(rows) > 100:
        raise HTTPException(400, "Max 100 rows per upload")

    # Reset monthly quota then check
    user = await reset_quota_if_needed(user)
    await check_quota(user, len(rows))

    bg.add_task(_process_csv_rows, rows, user["id"], ebay_account_id)

    return {
        "status": "processing",
        "total_rows": len(rows),
        "message": f"Generating {len(rows)} listings. Check /listings?status=draft in 30 seconds.",
    }


async def _process_csv_rows(rows: list[dict], user_id: str, ebay_account_id: str):
    for row in rows:
        try:
            ai = await generate_listing_from_csv_row(row)
            specifics_json = json.dumps(ai.get("item_specifics", {}))
            listing = {
                "id":               str(uuid.uuid4()),
                "user_id":          user_id,
                "ebay_account_id":  ebay_account_id or None,
                "input_type":       "csv",
                "input_raw":        json.dumps(row),
                "ai_title":         ai.get("title", ""),
                "ai_description":   ai.get("description", ""),
                "ai_category":      ai.get("category", ""),
                "ai_category_id":   ai.get("category_id", ""),
                "ai_condition":     ai.get("condition", "Used"),
                "ai_price":         ai.get("price", 0),
                "ai_specifics":     specifics_json,
                "final_title":      ai.get("title", ""),
                "final_description":ai.get("description", ""),
                "final_price":      ai.get("price", 0),
                "final_condition":  ai.get("condition", "Used"),
                "final_category_id":ai.get("category_id", ""),
                "final_specifics":  specifics_json,
            }
            await save_listing(listing)
            await consume_quota(user_id, 1)
        except Exception as e:
            print(f"[CSV] Row failed: {e}")


@router.get("/")
async def get_listings(
    status: Optional[str] = None,
    user=Depends(get_current_user),
):
    listings = await get_user_listings(user["id"], status)
    return {"listings": listings, "total": len(listings)}


@router.patch("/{listing_id}")
async def edit_listing(listing_id: str, req: EditRequest, user=Depends(get_current_user)):
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "final_specifics" in fields:
        fields["final_specifics"] = json.dumps(fields["final_specifics"])
    if not fields:
        raise HTTPException(400, "No fields to update")
    await update_listing(listing_id, user["id"], fields)
    return {"status": "updated", "listing_id": listing_id}


@router.post("/publish")
async def publish(req: PublishRequest, user=Depends(get_current_user)):
    # Get eBay account
    accounts = await get_ebay_accounts(user["id"])
    account = next((a for a in accounts if a["id"] == req.ebay_account_id), None)
    if not account:
        raise HTTPException(404, "eBay account not found")

    # Get listing
    listings = await get_user_listings(user["id"])
    listing = next((l for l in listings if l["id"] == req.listing_id), None)
    if not listing:
        raise HTTPException(404, "Listing not found")

    try:
        result = await publish_to_ebay(
            listing,
            access_token_enc=account["access_token"],
            sandbox=account["sandbox"],
        )
        await mark_listing_published(listing["id"], result["item_id"], result["url"])
        return {
            "status": "published",
            "ebay_item_id": result["item_id"],
            "ebay_url": result["url"],
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        await mark_listing_failed(listing["id"], str(e))
        raise HTTPException(500, f"Publish failed: {e}")


class RegenFieldRequest(BaseModel):
    listing_id: Optional[str] = None
    field: str  # title / description / price / category / specifics
    product_title: str
    condition: Optional[str] = "Used"
    category: Optional[str] = ""


@router.post("/regen-field")
async def regen_field(req: RegenFieldRequest, user=Depends(get_current_user)):
    """Regenerate a single field of a listing using Groq."""
    from services.groq_service import generate_listing_from_title
    try:
        ai = await generate_listing_from_title(req.product_title, req.condition or "Used")
    except Exception as e:
        raise HTTPException(500, f"AI regen failed: {e}")

    field_map = {
        "title":       ai.get("title", ""),
        "description": ai.get("description", ""),
        "price":       str(ai.get("price", 0)),
        "category":    ai.get("category", ""),
        "specifics":   json.dumps(ai.get("item_specifics", {})),
    }
    value = field_map.get(req.field, "")
    return {"field": req.field, "value": value}


async def refresh_status(listing_id: str, user=Depends(get_current_user)):
    listings = await get_user_listings(user["id"])
    listing  = next((l for l in listings if l["id"] == listing_id), None)
    if not listing or not listing.get("ebay_item_id"):
        raise HTTPException(404, "Listing not found or not yet published")

    accounts = await get_ebay_accounts(user["id"])
    account  = next((a for a in accounts if a["id"] == listing.get("ebay_account_id")), None)
    if not account:
        raise HTTPException(404, "eBay account not found")

    ebay_status = await get_item_status(
        listing["ebay_item_id"],
        account["access_token"],
        sandbox=account["sandbox"],
    )
    status_map = {"Active": "active", "Completed": "sold", "Ended": "ended"}
    our_status = status_map.get(ebay_status, "active")
    await update_listing(listing_id, user["id"], {"status": our_status})
    return {"listing_id": listing_id, "ebay_status": ebay_status, "status": our_status}
