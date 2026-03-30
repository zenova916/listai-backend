"""routers/auth.py — Signup, login, email verify"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, EmailStr
from db.supabase_client import (
    get_user_by_email, create_user, verify_user_email, get_user_by_id
)
from services.auth_service import (
    hash_password, verify_password, create_token, generate_verify_token
)
from services.email_service import send_verification_email, send_welcome_email

router = APIRouter()


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    token: str
    user_id: str
    email: str
    name: str
    plan: str
    listings_used: int
    listings_quota: int


@router.post("/register", response_model=AuthResponse)
async def register(req: RegisterRequest, bg: BackgroundTasks):
    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    existing = await get_user_by_email(str(req.email))
    if existing:
        raise HTTPException(400, "An account with this email already exists")

    pw_hash      = hash_password(req.password)
    verify_token = generate_verify_token()
    user_id      = await create_user(req.name, str(req.email), pw_hash, verify_token)

    # Send verification email in background (non-blocking)
    bg.add_task(send_verification_email, str(req.email), req.name, verify_token)

    token = create_token(user_id)
    return AuthResponse(
        token=token, user_id=user_id, email=str(req.email),
        name=req.name, plan="free", listings_used=0, listings_quota=5
    )


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest):
    user = await get_user_by_email(str(req.email))
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")

    token = create_token(user["id"])
    return AuthResponse(
        token=token,
        user_id=user["id"],
        email=user["email"],
        name=user["name"],
        plan=user["plan"],
        listings_used=user["listings_used"],
        listings_quota=user["listings_quota"],
    )


@router.get("/verify-email")
async def verify_email(token: str, bg: BackgroundTasks):
    ok = await verify_user_email(token)
    if not ok:
        raise HTTPException(400, "Invalid or expired verification link")
    # Send welcome email
    # (we don't have email here easily — bg task fires separately)
    return {"status": "verified", "message": "Email verified. You can now log in."}


@router.get("/me")
async def me(authorization: str = None):
    """Return current user info from token."""
    from services.auth_service import get_current_user
    from fastapi import Header
    # Used by frontend to refresh user state
    if not authorization:
        raise HTTPException(401, "No token")
    from services.auth_service import decode_token
    user_id = decode_token(authorization.replace("Bearer ", ""))
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return {
        "user_id": user["id"], "email": user["email"],
        "name": user["name"], "plan": user["plan"],
        "listings_used": user["listings_used"],
        "listings_quota": user["listings_quota"],
        "email_verified": user["email_verified"],
    }
