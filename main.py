"""
ListAI — Main FastAPI Application
eBay Listing Automation SaaS
Free stack: Groq AI + Supabase + Render + Resend + PayPal
"""
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()  # Loads .env file when running locally on Windows

from db.supabase_client import create_tables
from routers import auth, demo, ebay, listings, webhooks

app = FastAPI(
    title="ListAI API",
    description="eBay Listing Automation — AI-powered by Groq",
    version="1.0.0",
)

# ── CORS — allow your Vercel frontend ────────────────────────

app = FastAPI()

# Allowed origins
origins = [
    "https://listai-landing.vercel.app",
    "http://localhost:3000",
    "http://localhost:5500",
    "http://127.0.0.1:5500"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────
app.include_router(auth.router,      prefix="/auth",      tags=["Auth"])
app.include_router(demo.router,      prefix="/demo",      tags=["Demo"])
app.include_router(ebay.router,      prefix="/ebay",      tags=["eBay"])
app.include_router(listings.router,  prefix="/listings",  tags=["Listings"])
app.include_router(webhooks.router,  prefix="/webhooks",  tags=["Webhooks"])


@app.on_event("startup")
async def startup():
    """Create DB tables on first run."""
    await create_tables()
    print("✅ ListAI API started")
    print(f"   AI:       Groq ({os.getenv('GROQ_API_KEY', 'NOT SET')[:8]}...)")
    print(f"   Database: Supabase ({os.getenv('SUPABASE_URL', 'NOT SET')})")
    print(f"   PayPal:   {os.getenv('PAYPAL_MODE', 'NOT SET')} mode")


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "ListAI API",
        "ai": "groq/llama-3.3-70b-versatile",
        "database": "supabase",
        "payments": "paypal",
    }
