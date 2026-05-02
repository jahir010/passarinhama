from tortoise.contrib.pydantic import pydantic_model_creator
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Form, Request, Response
from pydantic import BaseModel, EmailStr
from fastapi.security import OAuth2PasswordRequestForm
import re

from app.config import settings
from applications.user.models import User, UserStatus
from app.token import (
    get_current_user,
    create_access_token,
    create_refresh_token,
    set_auth_cookies,
    blocklist_refresh_token,
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    REFRESH_SECRET_KEY,
    REFRESH_TOKEN_EXPIRE_DAYS,
    ALGORITHM,
    _normalize_token,
)

router = APIRouter(tags=["Swagger Authentication"])


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def detect_input_type(value: str) -> str:
    value = value.strip()
    if re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', value):
        return "email"
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Invalid email address.",
    )


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _build_token_data(user: User) -> dict:
    return {
        "sub":          str(user.id),
        "email":        user.email or "",
        "is_active":    user.is_active,
        "role":         user.role,
        "is_superuser": user.is_superuser,
    }


def _check_user_status(user: User) -> None:
    """Reject pending and suspended users with clear messages. §4.2"""
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


# ─────────────────────────────────────────────────────────────────────────────
# OAuth2 form helper
# ─────────────────────────────────────────────────────────────────────────────

class OAuth2EmailPasswordForm:
    def __init__(
        self,
        email:         EmailStr = Form(...),
        password:      str      = Form(...),
        scope:         str      = Form(""),
        client_id:     str      = Form(None),
        client_secret: str      = Form(None),
    ):
        self.email         = email
        self.password      = password
        self.scopes        = scope.split()
        self.client_id     = client_id
        self.client_secret = client_secret


User_Pydantic = pydantic_model_creator(User, name="User")


class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str


# ─────────────────────────────────────────────────────────────────────────────
# Swagger login (sets HTTP-only cookies)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/swagger_login_auth2", response_model=TokenResponse)
async def login_auth2(
    response:  Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    email = _normalize_email(form_data.username)
    await detect_input_type(email)

    user = await User.get_or_none(email=email)
    if not user or not user.verify_password(form_data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    _check_user_status(user)

    token_data    = _build_token_data(user)
    access_token  = create_access_token(token_data)
    refresh_token = create_refresh_token(token_data)

    set_auth_cookies(response, access_token, refresh_token)

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Token refresh helper (Swagger / browser)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/swagger-auth-token")
async def swagger_auth_token(
    request:  Request,
    response: Response,
    user:     User = Depends(get_current_user),
):
    access_token  = request.cookies.get(ACCESS_COOKIE_NAME)
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)

    if hasattr(request.state, "new_tokens"):
        access_token  = request.state.new_tokens["access_token"]
        refresh_token = request.state.new_tokens["refresh_token"]
        set_auth_cookies(response, access_token, refresh_token)

    return {"access_token": access_token, "refresh_token": refresh_token}


# ─────────────────────────────────────────────────────────────────────────────
# Logout — invalidates refresh token server-side (§4.2)
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/logout")
async def logout(
    request:  Request,
    response: Response,
    refresh_token_form: Optional[str] = Form(None),
):
    """
    Clears auth cookies and blocklists the refresh token in Redis so it
    cannot be reused even if someone still holds the raw token value. §4.2
    """
    from jose import jwt, JWTError

    # Prefer form-submitted token; fall back to cookie
    raw_refresh = _normalize_token(refresh_token_form) or _normalize_token(
        request.cookies.get(REFRESH_COOKIE_NAME)
    )

    if raw_refresh:
        try:
            payload = jwt.decode(raw_refresh, REFRESH_SECRET_KEY, algorithms=[ALGORITHM])
            jti = payload.get("jti")
            if jti:
                ttl = int(REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60)
                await blocklist_refresh_token(jti, ttl)
        except JWTError:
            # Token is already invalid; nothing to blocklist
            pass

    # Clear cookies regardless of token validity
    cookie_secure = not settings.DEBUG
    for cookie_name in (ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME):
        response.delete_cookie(key=cookie_name, path="/")
        response.set_cookie(
            key=cookie_name,
            value="",
            max_age=0,
            expires=0,
            path="/",
            secure=cookie_secure,
            httponly=True,
            samesite="lax",
        )

    response.headers["Clear-Site-Data"] = '"cookies", "storage"'
    return {"status": "success", "message": "Logged out successfully"}