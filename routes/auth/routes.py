from enum import Enum
from typing import Optional
import re

from fastapi import APIRouter, Depends, HTTPException, status, Form, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from applications.user.models import User, UserRole, UserStatus

from app.token import (
    get_current_user,
    create_access_token,
    create_refresh_token,
    blocklist_refresh_token,
    set_auth_cookies,
    REFRESH_TOKEN_EXPIRE_DAYS,
    REFRESH_SECRET_KEY,
    ALGORITHM,
)
from app.utils.otp_manager import generate_otp, verify_otp, verify_session_key
from app.config import settings

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


class UserSignupRole(str, Enum):
    MEMBRE  = "membre"



async def detect_input_type(value: str) -> str:
    value = value.strip()
    if re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', value):
        return "email"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid email address",
    )


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _build_token_data(user: User) -> dict:
    return {
        "sub":          str(user.id),
        "email":        user.email or "",
        "role":         user.role,
        "is_active":    user.is_active,
        "is_superuser": user.is_superuser,
    }


def _check_user_status(user: User) -> None:
    """
    Enforce §4.2 — pending and suspended users must be rejected with a clear
    message and must never receive tokens.
    """
    if user.status == UserStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending. Please wait for payment validation.",
        )
    if user.status == UserStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been suspended. Please contact support.",
        )


class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN (OAuth2 / Swagger)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/login_auth2", response_model=TokenResponse)
async def login_auth2(form_data: OAuth2PasswordRequestForm = Depends()):
    email = _normalize_email(form_data.username)
    await detect_input_type(email)

    user = await User.get_or_none(email=email)
    if not user or not user.verify_password(form_data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    _check_user_status(user)

    token_data = _build_token_data(user)
    return {
        "access_token":  create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type":    "bearer",
    }


# ─────────────────────────────────────────────────────────────────────────────
# LOGIN WITH OTP SUPPORT
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(
    email:     str           = Form(...),
    password:  str           = Form(...),
    otp_value: Optional[str] = Form(None),
):
    email = _normalize_email(email)
    await detect_input_type(email)

    user = await User.get_or_none(email=email)
    if not user or not user.verify_password(password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    _check_user_status(user)

    # OTP gate: if 2FA is enabled on this account, require OTP before issuing tokens
    if getattr(user, "is_active_2fa", False):
        normalized_otp = otp_value.strip() if otp_value else None
        if not normalized_otp:
            otp = await generate_otp(email, "login")
            return {
                "status":  "otp_required",
                "message": (
                    f"OTP sent to {email}"
                    + (f" (DEBUG MODE: OTP is {otp})" if settings.DEBUG else "")
                ),
                "purpose": "login",
            }
        await verify_otp(email, normalized_otp, "login")

    token_data = _build_token_data(user)
    return {
        "access_token":  create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type":    "bearer",
        "role":          user.role,
    }


# ─────────────────────────────────────────────────────────────────────────────
# REFRESH TOKEN  (§4.2 — POST /auth/refresh)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token_endpoint(
    request:       Request,
    response:      Response,
    refresh_token: Optional[str] = Form(None),
):
    """
    Issues a new access + refresh token pair from a valid, non-blocklisted
    refresh token.  The old refresh token is blocklisted immediately (rotation).
    Accepts the token either via the form body or the HTTP-only cookie.
    """
    from jose import jwt, JWTError, ExpiredSignatureError
    from app.token import _normalize_token, is_refresh_token_blocked

    raw = refresh_token or request.cookies.get("refresh_token")
    raw = _normalize_token(raw)

    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token required.",
        )

    try:
        payload = jwt.decode(raw, REFRESH_SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token expired. Please log in again.",
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token.",
        )

    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type.",
        )

    jti = payload.get("jti")
    if jti and await is_refresh_token_blocked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked. Please log in again.",
        )

    from tortoise.exceptions import DoesNotExist
    try:
        user = await User.get(id=payload.get("sub"))
    except DoesNotExist:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    _check_user_status(user)

    # Blocklist the old refresh token (rotation)
    if jti:
        ttl = int(REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)
        await blocklist_refresh_token(jti, ttl)

    token_data        = _build_token_data(user)
    new_access_token  = create_access_token(token_data)
    new_refresh_token = create_refresh_token(token_data)

    set_auth_cookies(response, new_access_token, new_refresh_token)

    return {
        "access_token":  new_access_token,
        "refresh_token": new_refresh_token,
        "token_type":    "bearer",
    }


# ─────────────────────────────────────────────────────────────────────────────
# SEND OTP
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/send_otp")
async def send_otp(
    email:   str = Form(...),
    purpose: str = Form("signup", description="signup | forgot_password | login"),
):
    email   = _normalize_email(email)
    await detect_input_type(email)
    purpose = purpose.strip().lower()

    user = await User.get_or_none(email=email)

    allowed_purposes = {"signup", "forgot_password", "login"}
    if purpose not in allowed_purposes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid OTP purpose")

    if purpose == "signup" and user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    if purpose in {"forgot_password", "login"} and not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Do not allow standalone OTP login for accounts that don't have 2FA
    if purpose == "login" and user and not getattr(user, "is_active_2fa", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OTP login is not enabled for this user",
        )

    otp = await generate_otp(email, purpose)

    return {
        "status":  "success",
        "message": (
            f"OTP sent to {email}"
            + (f" (DEBUG MODE: OTP is {otp})" if settings.DEBUG else "")
        ),
        "purpose": purpose,
    }


# ─────────────────────────────────────────────────────────────────────────────
# SIGNUP
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/signup")
async def signup(
    first_name: str      = Form(...),
    last_name:  str      = Form(...),
    email:      str      = Form(...),
    password:   str      = Form(...),
    otp_value:  str      = Form(...),
    role:       UserSignupRole = Form(None),
):
    email      = _normalize_email(email)
    await detect_input_type(email)

    first_name = first_name.strip()
    last_name  = last_name.strip()
    password   = password.strip()
    otp_value  = otp_value.strip()

    if not first_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="First name is required")
    if not last_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Last name is required")
    if not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password is required")

    await verify_otp(email, otp_value, "signup")

    if await User.get_or_none(email=email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    # New signups start as pending (§3.3) — payment validation promotes to membre
    user = await User.create(
        first_name=first_name,
        last_name=last_name,
        email=email,
        password=User.set_password(password),
        role=role,
        status=UserStatus.PENDING,
        is_active=True,
    )

    token_data = _build_token_data(user)
    return {
        "message":       "User created successfully. Awaiting payment validation.",
        "access_token":  create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type":    "bearer",
        "role":          user.role,
        "status":        user.status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# VERIFY OTP
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/verify_otp")
async def verify_otp_route(
    email:     str = Form(...),
    otp_value: str = Form(...),
    purpose:   str = Form(...),
):
    email = _normalize_email(email)
    await detect_input_type(email)
    session_key = await verify_otp(email, otp_value, purpose)

    return {
        "status":     "success",
        "sessionKey": session_key,
    }


# ─────────────────────────────────────────────────────────────────────────────
# RESET PASSWORD (logged-in user)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/reset_password")
async def reset_password(
    user:         User = Depends(get_current_user),
    old_password: str  = Form(...),
    new_password: str  = Form(...),
):
    new_password = new_password.strip()
    if not user.verify_password(old_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid old password")
    if not new_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password is required")
    if old_password == new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from old password",
        )

    user.password = User.set_password(new_password)
    await user.save()
    return {"message": "Password updated successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# FORGOT PASSWORD
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/forgot_password")
async def forgot_password(
    email:       str = Form(...),
    password:    str = Form(...),
    session_key: str = Form(...),
):
    email       = _normalize_email(email)
    await detect_input_type(email)
    password    = password.strip()
    session_key = session_key.strip()

    user = await User.get_or_none(email=email)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if not password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password is required")

    await verify_session_key(email, session_key, "forgot_password")

    user.password = User.set_password(password)
    await user.save()
    return {"message": "Password reset successfully"}


# ─────────────────────────────────────────────────────────────────────────────
# VERIFY TOKEN  (also returns refreshed tokens when rotation happened)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/verify-token")
async def verify_token(request: Request, user: User = Depends(get_current_user)):
    response_data = {
        "id":           str(user.id),
        "email":        user.email,
        "first_name":   user.first_name,
        "last_name":    user.last_name,
        "role":         user.role,
        "status":       user.status,
        "is_active":    user.is_active,
        "is_superuser": user.is_superuser,
        "avatar_url":   user.avatar_url,   # ← was incorrectly 'photo' in the original
    }
    if hasattr(request.state, "new_tokens"):
        response_data["new_tokens"] = request.state.new_tokens
    return response_data