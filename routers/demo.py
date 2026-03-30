"""routers/demo.py — Landing page demo, no auth required, rate limited to 3/IP/hour"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from services.groq_service import generate_demo_listing
from db.supabase_client import count_demo_requests_from_ip, log_demo_request

router = APIRouter()

class DemoRequest(BaseModel):
    title: str

@router.post("/generate")
async def demo_generate(req: DemoRequest, request: Request):
    ip = request.client.host

    # Rate limit: 3 per IP per hour
    count = await count_demo_requests_from_ip(ip)
    if count >= 3:
        raise HTTPException(429, "Demo limit reached (3 per hour). Sign up for unlimited access.")

    title = req.title.strip()
    if not title or len(title) < 3:
        raise HTTPException(400, "Please enter a product title")
    if len(title) > 200:
        raise HTTPException(400, "Title too long")

    await log_demo_request(ip)

    try:
        listing = await generate_demo_listing(title)
        return {
            "title":         listing.get("title", ""),
            "description":   listing.get("description", ""),
            "category":      listing.get("category", ""),
            "condition":     listing.get("condition", "Used"),
            "item_specifics": listing.get("item_specifics", {}),
            "price":         listing.get("price", 0),
            "price_low":     listing.get("price_low", 0),
            "price_high":    listing.get("price_high", 0),
        }
    except Exception as e:
        raise HTTPException(500, f"AI generation failed: {str(e)}")
