from typing import List, Optional
from uuid import UUID
import uuid
from datetime import datetime, timezone as UTC, timedelta


from fastapi import APIRouter, Depends, HTTPException, Query, Request
from tortoise.expressions import Q
from pydantic import BaseModel, EmailStr, Field, computed_field

from app.auth import role_required
from app.token import get_current_user
from applications.user.models import (
    Group, Permission, User, UserRole, UserStatus,
    MembershipCategory, ActivityActionType, ActivityLog, UserSession,
)

router = APIRouter()

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

ONLINE_THRESHOLD_MINUTES = 5   # seconds since last_seen_at → "online"


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ─────────────────────────────────────────────────────────────────────────────

class MembershipCategoryOut(BaseModel):
    id:   uuid.UUID
    name: str
    code: str

    class Config:
        from_attributes = True


class UserOut(BaseModel):
    """
    Full user response — aligns with every UI column and profile field.
    """
    id:                   uuid.UUID
    email:                str
    first_name:           str
    last_name:            str
    initials:             str                      # avatar fallback
    avatar_url:           str | None

    # Contact
    phone:                str | None
    mobile:               str | None               # "MOBILE" field on profile

    # Role / status
    role:                 UserRole
    status:               UserStatus
    is_payment_validated: bool
    membership_category:  MembershipCategoryOut | None
    membership_year:      int | None               # drives "Actif 2025" badge

    # Email verification badge ("Vérifié")
    is_email_verified:    bool

    # Online presence — "all users show online when connected"
    is_online:            bool
    last_seen_at:         datetime | None

    # Member list columns
    member_since:         datetime | None          # "INSCRIPTION"
    last_login_at:        datetime | None          # "DERNIER ACCÈS"
    created_at:           datetime

    # Soft delete flag (admin views)
    is_deleted:           bool

    class Config:
        from_attributes = True


class UserProfileOut(UserOut):
    """
    Extended response for profile page — includes Société fields and address.
    """
    # Address (§16.1)
    street_address:  str | None
    city:            str | None
    postal_code:     str | None
    country:         str

    # Société tab
    company_name:    str | None
    company_role:    str | None
    company_website: str | None
    company_siret:   str | None

    # Profile stats header (248 MEMBRES / 34 ARTICLES / 3 ANNÉES)
    years_as_member: int | None  # computed in route

    class Config:
        from_attributes = True


class SessionOut(BaseModel):
    """Sessions tab on profile page."""
    id:           uuid.UUID
    device_name:  str | None
    ip_address:   str | None
    is_active:    bool
    created_at:   datetime
    last_used_at: datetime
    expires_at:   datetime | None

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    email:                  EmailStr
    password:               str = Field(min_length=8)
    first_name:             str
    last_name:              str
    phone:                  str | None = None
    mobile:                 str | None = None
    role:                   UserRole   = UserRole.AUDITEUR
    membership_category_id: uuid.UUID | None = None


class UserUpdate(BaseModel):
    """Self-service profile update (§16.1)."""
    first_name:      str | None = None
    last_name:       str | None = None
    phone:           str | None = None
    mobile:          str | None = None
    street_address:  str | None = None
    city:            str | None = None
    postal_code:     str | None = None
    country:         str | None = None
    # Société tab
    company_name:    str | None = None
    company_role:    str | None = None
    company_website: str | None = None
    company_siret:   str | None = None


class UserAdminUpdate(UserUpdate):
    """Admin-only fields on top of self-service fields."""
    role:                    UserRole | None   = None
    status:                  UserStatus | None = None
    is_payment_validated:    bool | None       = None
    membership_category_id:  uuid.UUID | None  = None
    avatar_url:              str | None        = None


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def log_activity(
    user: User,
    action: ActivityActionType,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    description: str | None = None,
) -> None:
    await ActivityLog.create(
        user=user,
        action_type=action,
        target_type=target_type,
        target_id=target_id,
        description=description,
    )


def _is_online(user: User) -> bool:
    """True when user was seen within the last ONLINE_THRESHOLD_MINUTES."""
    print(f"Checking online status for user {user.first_name} — last_seen_at: {user.last_seen_at}")
    if not user.last_seen_at:
        return False
    result = (datetime.now(UTC.utc) - user.last_seen_at) < timedelta(minutes=ONLINE_THRESHOLD_MINUTES)
    print(f"result of online check: {result}")
    print(f"User {user.first_name} is {'online' if result else 'offline'}")
    return result




# def _is_online(user: User) -> bool:
#     if not user.last_seen_at:
#         return False

#     last_seen = user.last_seen_at
#     if last_seen.tzinfo is None:
#         last_seen = last_seen.replace(tzinfo=timezone.utc)  # ← timezone.utc
#     last_seen_utc = last_seen.astimezone(timezone.utc)       # ← timezone.utc

#     threshold = datetime.now(timezone.utc) - timedelta(minutes=ONLINE_THRESHOLD_MINUTES)

#     return last_seen_utc >= threshold


def _membership_year(user: User) -> int | None:
    if user.validated_at:
        return user.validated_at.year
    if user.member_since:
        return user.member_since.year
    return None


def _years_as_member(user: User) -> int | None:
    ref = user.member_since or user.validated_at
    if not ref:
        return None
    return max(1, (datetime.now(UTC.utc) - ref).days // 365)


def _serialize_user(user: User) -> dict:
    """Build a UserOut-compatible dict from a User ORM instance."""
    cat = None
    if hasattr(user, "membership_category") and user.membership_category_id:
        mc = user.membership_category
        # mc may be the FK int (not prefetched) — guard with hasattr
        if mc and hasattr(mc, "name"):
            cat = MembershipCategoryOut.model_validate(mc).model_dump()

    return {
        "id":                   user.id,
        "email":                user.email,
        "first_name":           user.first_name,
        "last_name":            user.last_name,
        "initials":             user.initials,
        "avatar_url":           user.avatar_url,
        "phone":                user.phone,
        "mobile":               user.mobile,
        "role":                 user.role,
        "status":               user.status,
        "is_payment_validated": user.is_payment_validated,
        "membership_category":  cat,
        "membership_year":      _membership_year(user),
        "is_email_verified":    user.is_email_verified,
        "is_online":            _is_online(user),
        "last_seen_at":         user.last_seen_at,
        "member_since":         user.member_since,
        "last_login_at":        user.last_login_at,
        "created_at":           user.created_at,
        "is_deleted":           user.is_deleted,
    }


def _serialize_profile(user: User) -> dict:
    base = _serialize_user(user)
    base.update({
        "street_address":  user.street_address,
        "city":            user.city,
        "postal_code":     user.postal_code,
        "country":         user.country,
        "company_name":    user.company_name,
        "company_role":    user.company_role,
        "company_website": user.company_website,
        "company_siret":   user.company_siret,
        "years_as_member": _years_as_member(user),
    })
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Online presence middleware helper
# Call this from your auth middleware on every authenticated request.
# ─────────────────────────────────────────────────────────────────────────────

async def touch_last_seen(user: User) -> None:
    """
    Update last_seen_at to now.
    Call this in your JWT auth dependency so every authenticated
    request keeps the user's online status fresh.
    Uses update() to avoid triggering auto_now on updated_at unnecessarily.
    """
    await User.filter(id=user.id).update(last_seen_at=datetime.now(UTC.utc))


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Member Directory
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/users", tags=["Members"])
async def list_users(
    search:      str | None = None,
    role:        UserRole | None = None,
    status:      UserStatus | None = None,
    category_id: uuid.UUID | None = None,
    alpha:       str | None = Query(None, max_length=1),
    year:        int | None = None,          # "Toutes les années" dropdown
    archived:    bool = False,               # "Archivés" tab → is_deleted=True
    page:        int = Query(1, ge=1),
    page_size:   int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """
    Member directory — §6.
    - Archived tab: pass ?archived=true to list soft-deleted members (admin only).
    - Non-admins only see active + payment-validated members.
    - year filter drives the "Toutes les années" dropdown.
    """
    if archived:
        # Only admins/moderators may browse archived members
        if current_user.role not in (UserRole.ADMIN, UserRole.MODERATOR):
            raise HTTPException(status_code=403, detail="Not authorised.")
        qs = User.filter(is_deleted=True)
    else:
        qs = User.filter(is_deleted=False)
        if current_user.role not in (UserRole.ADMIN, UserRole.MODERATOR):
            qs = qs.filter(status=UserStatus.ACTIVE, is_payment_validated=True)

    if search:
        qs = qs.filter(
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search)  |
            Q(email__icontains=search)      |
            Q(city__icontains=search)
        )
    if role:
        qs = qs.filter(role=role)
    if status:
        qs = qs.filter(status=status)
    if category_id:
        qs = qs.filter(membership_category_id=category_id)
    if alpha:
        qs = qs.filter(last_name__istartswith=alpha)
    if year:
        # Filter by the year the membership was validated / activated
        qs = qs.filter(
            Q(validated_at__year=year) | Q(member_since__year=year)
        )

    total = await qs.count()
    users = (
        await qs
        .offset((page - 1) * page_size)
        .limit(page_size)
        .prefetch_related("membership_category")
    )

    # Stat counts for status tabs (returned alongside results so the
    # frontend can render the tab badges in one request)
    if current_user.role in (UserRole.ADMIN, UserRole.MODERATOR):
        counts = {
            "all":       await User.filter(is_deleted=False).count(),
            "active":    await User.filter(is_deleted=False, status=UserStatus.ACTIVE).count(),
            "pending":   await User.filter(is_deleted=False, status=UserStatus.PENDING).count(),
            "suspended": await User.filter(is_deleted=False, status=UserStatus.SUSPENDED).count(),
            "archived":  await User.filter(is_deleted=True).count(),
        }
    else:
        counts = None

    return {
        "total":    total,
        "page":     page,
        "counts":   counts,
        "results":  [_serialize_user(u) for u in users],
    }


@router.post("/users", tags=["Members"], status_code=201)
async def create_user(
    body: UserCreate,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """Create a new member account (admin only)."""
    if await User.filter(email=body.email).exists():
        raise HTTPException(status_code=409, detail="Email already registered.")

    user = await User.create(
        email=body.email,
        password=User.set_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        phone=body.phone,
        mobile=body.mobile,
        role=body.role,
        membership_category_id=body.membership_category_id,
        member_since=datetime.now(UTC.utc),
    )
    await log_activity(current_user, ActivityActionType.USER_REGISTERED, "user", user.id,
                       f"New member created: {user.full_name}")
    return _serialize_user(user)


@router.get("/users/online", tags=["Members"])
async def list_online_users(
    current_user: User = Depends(get_current_user),
):
    """
    Returns users seen in the last 5 minutes — drives the 'EN LIGNE' sidebar.
    Response includes: id, first_name, last_name, initials, avatar_url, last_seen_at
    """
    threshold = datetime.now(UTC.utc) - timedelta(minutes=ONLINE_THRESHOLD_MINUTES)
    users = (
        await User.filter(
            is_deleted=False,
            last_seen_at__gte=threshold,
        )
        .order_by("-last_seen_at")
        .limit(50)
    )
    return {
        "count": len(users),
        "users": [
            {
                "id":           str(u.id),
                "first_name":   u.first_name,
                "last_name":    u.last_name,
                "initials":     u.initials,
                "avatar_url":   u.avatar_url,
                "last_seen_at": u.last_seen_at,
            }
            for u in users
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Own Profile  (§16)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/users/me", tags=["Members"])
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Current authenticated user — full profile response including
    Société fields, address, and stats.
    """
    await current_user.fetch_related("membership_category")
    return _serialize_profile(current_user)


@router.patch("/users/me", tags=["Members"])
async def update_me(
    body: UserUpdate,
    current_user: User = Depends(get_current_user),
):
    """Update own profile (§16.1 + Société tab)."""
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(current_user, field, value)
    await current_user.save()
    await current_user.fetch_related("membership_category")
    await log_activity(current_user, ActivityActionType.PROFILE_UPDATED, "user", current_user.id)
    return _serialize_profile(current_user)


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Sessions  (§4 "Sessions" profile tab)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/users/me/sessions", tags=["Members"])
async def list_my_sessions(current_user: User = Depends(get_current_user)):
    """List all active sessions for the current user (profile → Sessions tab)."""
    sessions = await UserSession.filter(user=current_user, is_active=True).all()
    return [SessionOut.model_validate(s) for s in sessions]


@router.delete("/users/me/sessions/{session_id}", status_code=204, tags=["Members"])
async def revoke_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """Revoke (log out) a specific session."""
    session = await UserSession.get_or_none(id=session_id, user=current_user)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found.")
    session.is_active = False
    await session.save(update_fields=["is_active"])


@router.delete("/users/me/sessions", status_code=204, tags=["Members"])
async def revoke_all_sessions(current_user: User = Depends(get_current_user)):
    """Revoke all sessions except the current one (logout everywhere)."""
    await UserSession.filter(user=current_user, is_active=True).update(is_active=False)


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Single Member
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/users/{user_id}", tags=["Members"])
async def get_user(
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """Get a single member profile."""
    user = await User.get_or_none(id=user_id, is_deleted=False)
    if not user:
        raise HTTPException(status_code=404, detail="Member not found.")

    # Non-admin can only see active + validated members
    if current_user.role not in (UserRole.ADMIN, UserRole.MODERATOR):
        if not (user.status == UserStatus.ACTIVE and user.is_payment_validated):
            raise HTTPException(status_code=404, detail="Member not found.")

    await user.fetch_related("membership_category")

    # Return full profile when it's the user themselves or an admin
    if current_user.id == user.id or current_user.role in (UserRole.ADMIN, UserRole.MODERATOR):
        return _serialize_profile(user)
    return _serialize_user(user)


@router.patch("/users/{user_id}", tags=["Members"])
async def update_user(
    user_id: uuid.UUID,
    body: UserAdminUpdate,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """Update any member's details (admin only)."""
    user = await User.get_or_none(id=user_id, is_deleted=False)
    if not user:
        raise HTTPException(status_code=404, detail="Member not found.")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(user, field, value)
    await user.save()
    await user.fetch_related("membership_category")
    return _serialize_profile(user)


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Payment Validation
# ─────────────────────────────────────────────────────────────────────────────

@router.patch("/users/{user_id}/validate", tags=["Members"])
async def validate_payment(
    user_id: uuid.UUID,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """Toggle payment validation (admin only). Upgrades role to membre on confirmation."""
    user = await User.get_or_none(id=user_id, is_deleted=False)
    if not user:
        raise HTTPException(status_code=404, detail="Member not found.")

    user.is_payment_validated = not user.is_payment_validated
    if user.is_payment_validated and user.role in (UserRole.AUDITEUR, UserRole.MEMBRE):
        user.role              = UserRole.MEMBRE
        user.status            = UserStatus.ACTIVE
        user.validated_by_id   = current_user.id
        user.validated_at      = datetime.now(UTC.utc)
        if not user.member_since:
            user.member_since  = datetime.now(UTC.utc)
    

    await user.save()
    await user.fetch_related("membership_category")
    await log_activity(
        current_user, ActivityActionType.USER_VALIDATED, "user", user.id,
        f"Payment {'validated' if user.is_payment_validated else 'revoked'} for {user.full_name}",
    )
    return _serialize_profile(user)


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Delete (soft)
# ─────────────────────────────────────────────────────────────────────────────

@router.delete("/users/{user_id}", status_code=204, tags=["Members"])
async def delete_user(
    user_id: uuid.UUID,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """Soft-delete a member account (admin only). Appears under 'Archivés' tab."""
    user = await User.get_or_none(id=user_id, is_deleted=False)
    if not user:
        raise HTTPException(status_code=404, detail="Member not found.")

    user.is_deleted = True
    user.status     = UserStatus.SUSPENDED
    # Invalidate all sessions on archive
    await UserSession.filter(user=user, is_active=True).update(is_active=False)
    await user.save(update_fields=["is_deleted", "status"])


@router.patch("/users/{user_id}/restore", tags=["Members"])
async def restore_user(
    user_id: uuid.UUID,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """Restore a soft-deleted (archived) member account."""
    user = await User.get_or_none(id=user_id, is_deleted=True)
    if not user:
        raise HTTPException(status_code=404, detail="Archived member not found.")

    user.is_deleted = False
    user.status     = UserStatus.PENDING
    await user.save(update_fields=["is_deleted", "status"])
    await user.fetch_related("membership_category")
    return _serialize_profile(user)
    


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Dashboard stats helpers  (§5)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/users/stats/roles", tags=["Dashboard"])
async def role_distribution(current_user: User = Depends(role_required(UserRole.ADMIN))):
    """
    Returns per-role member counts — drives the 'Répartition des rôles'
    bar chart on the dashboard (§5).
    """
    from tortoise.functions import Count

    rows = (
        await User.filter(is_deleted=False, status=UserStatus.ACTIVE)
        .group_by("role")
        .annotate(count=Count("id"))
        .values("role", "count")
    )
    return {row["role"]: row["count"] for row in rows}


@router.get("/users/stats/online", tags=["Dashboard"])
async def online_count(current_user: User = Depends(get_current_user)):
    """Count of users seen within the last 5 minutes."""
    threshold = datetime.now(UTC.utc) - timedelta(minutes=ONLINE_THRESHOLD_MINUTES)
    count = await User.filter(is_deleted=False, last_seen_at__gte=threshold).count()
    return {"online": count}








