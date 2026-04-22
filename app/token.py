from fastapi import Depends, HTTPException, status, Request, Header, Response
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError, ExpiredSignatureError
from datetime import datetime, timedelta, timezone
from tortoise.exceptions import DoesNotExist
from app.config import settings

from applications.user.models import User

# =========================
# JWT SETTINGS
# =========================
def _safe_int_setting(setting_name: str, default_value: int) -> int:
    value = getattr(settings, setting_name, default_value)
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default_value
    except (TypeError, ValueError):
        return default_value


def _safe_bool(value, default_value: bool) -> bool:
    if value is None:
        return default_value
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default_value


SECRET_KEY          = settings.SECRET_KEY or "dev-secret-key-change-me"
REFRESH_SECRET_KEY  = getattr(settings, "REFRESH_SECRET_KEY", None) or f"{SECRET_KEY}:refresh"
ALGORITHM           = getattr(settings, "JWT_ALGORITHM", "HS256")

# §4.2: access token 30 min, refresh token 30 days
ACCESS_TOKEN_EXPIRE_MINUTES = _safe_int_setting("ACCESS_TOKEN_EXPIRE_MINUTES", 30)
REFRESH_TOKEN_EXPIRE_DAYS   = _safe_int_setting("REFRESH_TOKEN_EXPIRE_DAYS", 30)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login_auth2/", auto_error=False)

ACCESS_COOKIE_NAME  = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"


# =========================
# REDIS BLOCKLIST HELPERS  (§4.2 — server-side refresh token invalidation)
# =========================

def _get_redis():
    """Return the Redis client or raise 503."""
    try:
        from app.redis import get_redis
        return get_redis()
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth service is temporarily unavailable.",
        )


def _blocklist_key(jti: str) -> str:
    return f"refresh_blocklist:{jti}"


async def blocklist_refresh_token(jti: str, ttl_seconds: int) -> None:
    """Add a refresh token JTI to the Redis blocklist until it naturally expires."""
    redis = _get_redis()
    await redis.set(_blocklist_key(jti), "1", ex=ttl_seconds)


async def is_refresh_token_blocked(jti: str) -> bool:
    redis = _get_redis()
    return bool(await redis.get(_blocklist_key(jti)))


# =========================
# TOKEN HELPERS
# =========================

import secrets as _secrets


def _new_jti() -> str:
    return _secrets.token_urlsafe(32)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    # Embed a unique JTI so individual tokens can be blocklisted on logout (§4.2)
    to_encode.update({"exp": expire, "type": "refresh", "jti": _new_jti()})
    return jwt.encode(to_encode, REFRESH_SECRET_KEY, algorithm=ALGORITHM)


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    secure = not settings.DEBUG
    response.set_cookie(
        key=ACCESS_COOKIE_NAME,
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        path="/",
    )


def _normalize_token(token_value: str | None) -> str | None:
    if not token_value:
        return None
    token_value = token_value.strip()
    if token_value.lower().startswith("bearer "):
        token_value = token_value[7:].strip()
    return token_value or None


# =========================
# AUTH HELPERS
# =========================

async def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    refresh_token: str | None = Header(default=None, alias="refresh-token"),
) -> User:
    cookie_access_token = _normalize_token(request.cookies.get(ACCESS_COOKIE_NAME))
    token               = _normalize_token(token)
    refresh_token       = _normalize_token(refresh_token)
    if not refresh_token:
        refresh_token = _normalize_token(request.cookies.get(REFRESH_COOKIE_NAME))

    candidate_tokens: list[str] = []
    if cookie_access_token:
        candidate_tokens.append(cookie_access_token)
    if token and token != cookie_access_token:
        candidate_tokens.append(token)

    if not candidate_tokens:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload      = None
    token_expired = False

    try:
        for candidate_token in candidate_tokens:
            try:
                payload = jwt.decode(candidate_token, SECRET_KEY, algorithms=[ALGORITHM])
                if payload.get("type") != "access":
                    payload = None
                    continue
                break
            except ExpiredSignatureError:
                token_expired = True
            except JWTError:
                continue

        if payload is None and token_expired:
            if not refresh_token:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Access token expired. Refresh token required.",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            try:
                refresh_payload = jwt.decode(
                    refresh_token, REFRESH_SECRET_KEY, algorithms=[ALGORITHM]
                )
                if refresh_payload.get("type") != "refresh":
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid refresh token type",
                    )

                # §4.2 — reject blocklisted (logged-out) refresh tokens
                jti = refresh_payload.get("jti")
                if jti and await is_refresh_token_blocked(jti):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Refresh token has been revoked. Please log in again.",
                    )

                token_data = {
                    "sub":          refresh_payload.get("sub"),
                    "email":        refresh_payload.get("email") or "",
                    "role":         refresh_payload.get("role"),
                    "language":     refresh_payload.get("language") or "en",
                    "is_active":    _safe_bool(refresh_payload.get("is_active"), True),
                    "is_superuser": _safe_bool(refresh_payload.get("is_superuser"), False),
                }
                if not token_data["sub"]:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid refresh token payload",
                    )

                new_access_token  = create_access_token(token_data)
                new_refresh_token = create_refresh_token(token_data)

                request.state.new_tokens = {
                    "access_token":  new_access_token,
                    "refresh_token": new_refresh_token,
                }

                payload = jwt.decode(new_access_token, SECRET_KEY, algorithms=[ALGORITHM])

            except ExpiredSignatureError:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Refresh token expired. Please log in again.",
                )
            except JWTError:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid refresh token",
                )

        if payload is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid access token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    except HTTPException:
        raise
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user = await User.get(id=payload.get("sub"))
    except DoesNotExist:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")

    # §4.2 — reject suspended/pending users at the token level
    from applications.user.models import UserStatus
    if user.status == UserStatus.SUSPENDED:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account has been suspended.",
        )

    return user