"""
services/auth_service.py
JWT creation, password hashing, token verification.
"""
import os, secrets
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import HTTPException, Header
from typing import Optional
from db.supabase_client import get_user_by_id

SECRET = os.getenv("JWT_SECRET", "changeme")
EXPIRE = int(os.getenv("JWT_EXPIRE_MINUTES", "10080"))  # 7 days default
ALGO   = "HS256"

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_ctx.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)


def create_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=EXPIRE)
    }
    return jwt.encode(payload, SECRET, algorithm=ALGO)


def decode_token(token: str) -> str:
    """Returns user_id or raises HTTPException."""
    try:
        payload = jwt.decode(token, SECRET, algorithms=[ALGO])
        return payload["sub"]
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def generate_verify_token() -> str:
    return secrets.token_urlsafe(32)


async def get_current_user(authorization: Optional[str] = Header(None)):
    """
    FastAPI dependency — extracts user from Authorization: Bearer <token> header.
    Usage:  async def my_route(user=Depends(get_current_user))
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization header")

    token = authorization.split(" ", 1)[1]
    user_id = decode_token(token)
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


async def require_verified(authorization: Optional[str] = Header(None)):
    """Like get_current_user but also requires email to be verified."""
    user = await get_current_user(authorization)
    if not user.get("email_verified"):
        raise HTTPException(status_code=403, detail="Please verify your email first")
    return user
