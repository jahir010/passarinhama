from fastapi import Depends, Header, HTTPException, Request, status
from .token import get_current_user, oauth2_scheme
from applications.user.models import User, UserRole, UserStatus


# ─────────────────────────────────────────────────────────────────────────────
# Basic role guards
# ─────────────────────────────────────────────────────────────────────────────

async def superuser_required(current_user: User = Depends(get_current_user)) -> User:
    """Allows only users whose is_superuser flag is True."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superuser access required")
    return current_user


async def admin_required(current_user: User = Depends(get_current_user)) -> User:
    """Allows only users with role=admin (or superuser). §3.1"""
    if not current_user.is_superuser and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


async def staff_required(current_user: User = Depends(get_current_user)) -> User:
    """
    Allows admin and moderator roles. §3.1
    NOTE: previously this guard incorrectly *rejected* superusers. Fixed.
    """
    staff_roles = {UserRole.ADMIN, UserRole.MODERATOR}
    if not current_user.is_superuser and current_user.role not in staff_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff access required")
    return current_user


async def login_required(current_user: User = Depends(get_current_user)) -> User:
    """Any authenticated, active user."""
    return current_user


async def membre_required(current_user: User = Depends(get_current_user)) -> User:
    """
    Requires a fully validated membre (payment confirmed, status active). §3.3
    Admin/moderator/superuser always pass through.
    """
    privileged = {UserRole.ADMIN, UserRole.MODERATOR}
    if current_user.is_superuser or current_user.role in privileged:
        return current_user

    if current_user.role != UserRole.MEMBRE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Membre access required")

    if not current_user.is_payment_validated:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your membership payment has not been validated yet.",
        )

    if current_user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is not active.",
        )

    return current_user


# ─────────────────────────────────────────────────────────────────────────────
# Optional user (returns None when unauthenticated instead of raising 401)
# ─────────────────────────────────────────────────────────────────────────────

async def get_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    refresh_token: str | None = Header(default=None, alias="refresh-token"),
) -> User | None:
    """
    Soft dependency: returns the authenticated User or None.
    Useful for endpoints that serve different content to guests vs. members.
    """
    try:
        current_user = await get_current_user(
            request=request,
            token=token,
            refresh_token=refresh_token,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            return None
        raise

    return current_user


# ─────────────────────────────────────────────────────────────────────────────
# Fine-grained permission dependency (§4.4)
# ─────────────────────────────────────────────────────────────────────────────

def permission_required(codename: str):
    """
    Factory that returns a FastAPI dependency enforcing a specific permission
    codename.  Superusers bypass the check (handled inside has_permission).
    """
    async def wrapper(current_user: User = Depends(get_current_user)) -> User:
        allowed = await current_user.has_permission(codename)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied.",
            )
        return current_user

    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# Role-based dependency factory (§3.1)
# ─────────────────────────────────────────────────────────────────────────────

def role_required(*roles: UserRole, allow_superuser: bool = True):
    """
    Factory that restricts an endpoint to one or more specific roles.

    Parameters
    ----------
    *roles:
        One or more UserRole values that are permitted.
    allow_superuser:
        When True (default), superusers always pass regardless of their role.
        Set to False only when you explicitly want to exclude superusers.
    """
    async def wrapper(current_user: User = Depends(get_current_user)) -> User:
        if allow_superuser and current_user.is_superuser:
            return current_user
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied.",
            )
        return current_user

    return wrapper