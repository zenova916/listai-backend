"""
db/supabase_client.py
All database operations via Supabase (PostgreSQL).
Tables are created automatically on first run.
"""
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy import (
    Column, String, Integer, Float, Boolean,
    DateTime, Text, ForeignKey
)
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import func, text
import uuid

DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    poolclass=NullPool,
    connect_args={
        "ssl": "require",
        "statement_cache_size": 0,
        "server_settings": {"application_name": "listai"},
    },
)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ── TABLES ────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    id                 = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name               = Column(String, nullable=False)
    email              = Column(String, nullable=False, unique=True)
    password_hash      = Column(String, nullable=False)
    email_verified     = Column(Boolean, default=False)
    verify_token       = Column(String)
    plan               = Column(String, default="free")   # free / starter / pro / agency
    listings_used      = Column(Integer, default=0)
    listings_quota     = Column(Integer, default=5)       # free=5, starter=50, pro=500, agency=999999
    quota_reset_at     = Column(DateTime(timezone=True), server_default=func.now())  # tracks monthly reset
    paypal_sub_id      = Column(String)
    created_at         = Column(DateTime(timezone=True), server_default=func.now())


class EbayAccount(Base):
    __tablename__ = "ebay_accounts"
    id                 = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id            = Column(String, ForeignKey("users.id"), nullable=False)
    ebay_username      = Column(String)
    ebay_email         = Column(String)
    access_token       = Column(Text)   # encrypted
    refresh_token      = Column(Text)   # encrypted
    token_expires_at   = Column(DateTime(timezone=True))
    sandbox            = Column(Boolean, default=True)
    created_at         = Column(DateTime(timezone=True), server_default=func.now())


class Listing(Base):
    __tablename__ = "listings"
    id                 = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id            = Column(String, ForeignKey("users.id"), nullable=False)
    ebay_account_id    = Column(String, ForeignKey("ebay_accounts.id"))
    input_type         = Column(String)   # title / csv / image
    input_raw          = Column(Text)

    # AI generated
    ai_title           = Column(String)
    ai_description     = Column(Text)
    ai_category        = Column(String)
    ai_category_id     = Column(String)
    ai_condition       = Column(String)
    ai_price           = Column(Float)
    ai_specifics       = Column(Text)   # JSON string

    # Final (user-edited before publish)
    final_title        = Column(String)
    final_description  = Column(Text)
    final_price        = Column(Float)
    final_condition    = Column(String)
    final_category_id  = Column(String)
    final_specifics    = Column(Text)   # JSON string

    # eBay result
    status             = Column(String, default="draft")  # draft/published/active/sold/ended/failed
    ebay_item_id       = Column(String)
    ebay_url           = Column(String)
    error_msg          = Column(Text)

    created_at         = Column(DateTime(timezone=True), server_default=func.now())
    published_at       = Column(DateTime(timezone=True))


class DemoLog(Base):
    __tablename__ = "demo_logs"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    ip_address = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ── SETUP ─────────────────────────────────────────────────────

async def create_tables():
    """Run once on startup to create all tables if they don't exist."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Add quota_reset_at column if it doesn't exist yet (safe migration)
        await conn.execute(text("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS quota_reset_at TIMESTAMPTZ DEFAULT NOW()
        """))


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ── PLAN CONFIG ───────────────────────────────────────────────

# Listings quota per plan (agency = effectively unlimited)
PLAN_QUOTAS = {
    "free":    5,
    "starter": 50,
    "pro":     500,
    "agency":  999999,
}

# Max eBay accounts per plan
PLAN_EBAY_ACCOUNTS = {
    "free":    1,
    "starter": 1,
    "pro":     3,
    "agency":  10,
}

# Which plans can use CSV bulk upload
PLAN_CSV_ALLOWED = {"starter", "pro", "agency"}

# Which plans can use image upload
PLAN_IMAGE_ALLOWED = {"pro", "agency"}


# ── MONTHLY QUOTA RESET ───────────────────────────────────────

async def reset_quota_if_needed(user: dict) -> dict:
    """
    Check if the user's quota needs a monthly reset.
    Resets listings_used to 0 if it's been more than 30 days since last reset.
    Returns the updated user dict.
    """
    from datetime import datetime, timezone, timedelta

    reset_at = user.get("quota_reset_at")
    if reset_at is None:
        # Column didn't exist before, set it now
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("UPDATE users SET quota_reset_at = NOW() WHERE id = :id"),
                {"id": user["id"]}
            )
            await db.commit()
        return user

    # Make reset_at timezone-aware if it isn't
    if hasattr(reset_at, 'tzinfo') and reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    days_since_reset = (now - reset_at).days

    if days_since_reset >= 30:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("""
                    UPDATE users
                    SET listings_used = 0, quota_reset_at = NOW()
                    WHERE id = :id
                """),
                {"id": user["id"]}
            )
            await db.commit()
        # Return updated user
        user = dict(user)
        user["listings_used"] = 0
        print(f"[QUOTA] Reset monthly quota for user {user['id']}")

    return user


# ── QUERY HELPERS ─────────────────────────────────────────────

async def get_user_by_email(email: str):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("SELECT * FROM users WHERE email = :email LIMIT 1"),
            {"email": email}
        )
        row = result.mappings().first()
        return dict(row) if row else None


async def get_user_by_id(user_id: str):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("SELECT * FROM users WHERE id = :id LIMIT 1"),
            {"id": user_id}
        )
        row = result.mappings().first()
        return dict(row) if row else None


async def create_user(name: str, email: str, password_hash: str, verify_token: str):
    uid = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                INSERT INTO users (id, name, email, password_hash, verify_token)
                VALUES (:id, :name, :email, :pw, :token)
            """),
            {"id": uid, "name": name, "email": email, "pw": password_hash, "token": verify_token}
        )
        await db.commit()
    return uid


async def verify_user_email(token: str) -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("SELECT id FROM users WHERE verify_token = :token LIMIT 1"),
            {"token": token}
        )
        row = result.mappings().first()
        if not row:
            return False
        await db.execute(
            text("UPDATE users SET email_verified=true, verify_token=null WHERE id=:id"),
            {"id": row["id"]}
        )
        await db.commit()
        return True


async def update_user_plan(user_id: str, plan: str, paypal_sub_id: str):
    # FIXED: agency was missing, now all 4 plans covered
    quota = PLAN_QUOTAS.get(plan, 5)
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                UPDATE users
                SET plan=:plan, listings_quota=:quota, paypal_sub_id=:sub_id,
                    listings_used=0, quota_reset_at=NOW()
                WHERE id=:id
            """),
            {"plan": plan, "quota": quota, "sub_id": paypal_sub_id, "id": user_id}
        )
        await db.commit()


async def save_listing(listing: dict):
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                INSERT INTO listings (
                    id, user_id, ebay_account_id, input_type, input_raw,
                    ai_title, ai_description, ai_category, ai_category_id,
                    ai_condition, ai_price, ai_specifics,
                    final_title, final_description, final_price,
                    final_condition, final_category_id, final_specifics, status
                ) VALUES (
                    :id, :user_id, :ebay_account_id, :input_type, :input_raw,
                    :ai_title, :ai_description, :ai_category, :ai_category_id,
                    :ai_condition, :ai_price, :ai_specifics,
                    :final_title, :final_description, :final_price,
                    :final_condition, :final_category_id, :final_specifics, 'draft'
                )
            """),
            listing
        )
        await db.commit()


async def get_user_listings(user_id: str, status: str = None):
    async with AsyncSessionLocal() as db:
        if status:
            result = await db.execute(
                text("SELECT * FROM listings WHERE user_id=:uid AND status=:status ORDER BY created_at DESC"),
                {"uid": user_id, "status": status}
            )
        else:
            result = await db.execute(
                text("SELECT * FROM listings WHERE user_id=:uid ORDER BY created_at DESC"),
                {"uid": user_id}
            )
        return [dict(r) for r in result.mappings().all()]


async def update_listing(listing_id: str, user_id: str, fields: dict):
    set_clause = ", ".join([f"{k}=:{k}" for k in fields])
    fields["id"] = listing_id
    fields["user_id"] = user_id
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(f"UPDATE listings SET {set_clause} WHERE id=:id AND user_id=:user_id"),
            fields
        )
        await db.commit()


async def mark_listing_published(listing_id: str, ebay_item_id: str, ebay_url: str):
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                UPDATE listings
                SET status='active', ebay_item_id=:item_id, ebay_url=:url,
                    published_at=NOW()
                WHERE id=:id
            """),
            {"item_id": ebay_item_id, "url": ebay_url, "id": listing_id}
        )
        await db.commit()


async def mark_listing_failed(listing_id: str, error: str):
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE listings SET status='failed', error_msg=:err WHERE id=:id"),
            {"err": error, "id": listing_id}
        )
        await db.commit()


async def get_ebay_accounts(user_id: str):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("SELECT * FROM ebay_accounts WHERE user_id=:uid"),
            {"uid": user_id}
        )
        return [dict(r) for r in result.mappings().all()]


async def save_ebay_account(user_id: str, ebay_username: str, ebay_email: str,
                             access_token_enc: str, refresh_token_enc: str,
                             expires_at, sandbox: bool):
    aid = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("""
                INSERT INTO ebay_accounts
                    (id, user_id, ebay_username, ebay_email, access_token, refresh_token, token_expires_at, sandbox)
                VALUES (:id, :uid, :username, :email, :at, :rt, :exp, :sandbox)
            """),
            {"id": aid, "uid": user_id, "username": ebay_username, "email": ebay_email,
             "at": access_token_enc, "rt": refresh_token_enc,
             "exp": expires_at, "sandbox": sandbox}
        )
        await db.commit()
    return aid


async def delete_ebay_account(account_id: str, user_id: str) -> bool:
    """Delete an eBay account. Returns True if a row was deleted."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("DELETE FROM ebay_accounts WHERE id=:id AND user_id=:uid RETURNING id"),
            {"id": account_id, "uid": user_id}
        )
        await db.commit()
        return result.rowcount > 0


async def count_demo_requests_from_ip(ip: str) -> int:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("""
                SELECT COUNT(*) as cnt FROM demo_logs
                WHERE ip_address=:ip
                AND created_at > NOW() - INTERVAL '1 hour'
            """),
            {"ip": ip}
        )
        row = result.mappings().first()
        return row["cnt"] if row else 0


async def log_demo_request(ip: str):
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO demo_logs (ip_address) VALUES (:ip)"),
            {"ip": ip}
        )
        await db.commit()
