from typing import List, Optional
from uuid import UUID
import uuid
from datetime import datetime, timezone as UTC
from passlib.hash import bcrypt

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status, Query
from tortoise.transactions import in_transaction
from tortoise.expressions import Q
from pydantic import BaseModel, EmailStr, Field

from app.auth import login_required, role_required
from app.token import get_current_user
from app.utils.file_manager import delete_file, update_file
from applications.user.models import  Group, Permission, User, UserRole, UserStatus, MembershipCategory, ActivityActionType, ActivityLog


router = APIRouter()

class UserOut(BaseModel):
    id:                   uuid.UUID
    email:                str
    first_name:           str
    last_name:            str
    phone:                str | None
    city:                 str | None
    role:                 UserRole
    status:               UserStatus
    is_payment_validated: bool
    member_since:         datetime | None
    created_at:           datetime
 
    class Config:
        from_attributes = True

class UserCreate(BaseModel):
    email:       EmailStr
    password:    str = Field(min_length=8)
    first_name:  str
    last_name:   str
    phone:       str | None = None
    role:        UserRole   = UserRole.AUDITEUR
    membership_category_id: uuid.UUID | None = None
 
class UserUpdate(BaseModel):
    first_name:     str | None = None
    last_name:      str | None = None
    phone:          str | None = None
    street_address: str | None = None
    city:           str | None = None
    postal_code:    str | None = None
    country:        str | None = None
 
class UserAdminUpdate(UserUpdate):
    role:                    UserRole | None   = None
    status:                  UserStatus | None = None
    is_payment_validated:    bool | None       = None
    membership_category_id:  uuid.UUID | None  = None


# def hash_password(plain: str) -> str:
#     return bcrypt.hash(plain)

async def log_activity(user: User, action: ActivityActionType, target_type: str | None = None,
                       target_id: uuid.UUID | None = None, description: str | None = None) -> None:
    await ActivityLog.create(
        user=user, action_type=action,
        target_type=target_type, target_id=target_id, description=description,
    )


@router.get("/users", tags=["Members"])
async def list_users(
    search:      str | None = None,
    role:        UserRole | None = None,
    status:      UserStatus | None = None,
    category_id: uuid.UUID | None = None,
    alpha:       str | None = Query(None, max_length=1),
    page:        int = Query(1, ge=1),
    page_size:   int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """
    Member directory — only active + payment-validated members for non-admins.
    Admins see all members.
    """
    qs = User.filter(is_deleted=False)
 
    if current_user.role not in (UserRole.ADMIN, UserRole.MODERATOR):
        qs = qs.filter(status=UserStatus.ACTIVE, is_payment_validated=True)
 
    if search:
        qs = qs.filter(
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search)  |
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
 
    total  = await qs.count()
    users  = await qs.offset((page - 1) * page_size).limit(page_size).prefetch_related("membership_category")
    return {"total": total, "page": page, "results": [UserOut.model_validate(u) for u in users]}
 
 
@router.post("/users", tags=["Members"], status_code=201)
async def create_user(body: UserCreate, current_user: User = Depends(role_required(UserRole.ADMIN))):
    """Create a new member account (admin only)."""
    if await User.filter(email=body.email).exists():
        raise HTTPException(status_code=409, detail="Email already registered.")
    hashed_password = bcrypt.hash(body.password)
    user = await User.create(
        email=body.email,
        password=User.set_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        phone=body.phone,
        role=body.role,
        membership_category_id=body.membership_category_id,
        member_since=datetime.now(UTC.utc),
    )
    await log_activity(current_user, ActivityActionType.USER_REGISTERED, "user", user.id)
    return UserOut.model_validate(user)
 
 
@router.get("/users/me", response_model=UserOut, tags=["Members"])
async def get_me(current_user: User = Depends(get_current_user)):
    """Current authenticated user profile."""
    return UserOut.model_validate(current_user)
 
 
@router.patch("/users/me", response_model=UserOut, tags=["Members"])
async def update_me(body: UserUpdate, current_user: User = Depends(get_current_user)):
    """Update own profile fields."""
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(current_user, field, value)
    await current_user.save()
    return UserOut.model_validate(current_user)
 
 
@router.get("/users/{user_id}", response_model=UserOut, tags=["Members"])
async def get_user(user_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    """Get a single member profile."""
    user = await User.get_or_none(id=user_id, is_deleted=False)
    if not user:
        raise HTTPException(status_code=404, detail="Member not found.")
    # Non-admin can only see active+validated members
    if current_user.role not in (UserRole.ADMIN, UserRole.MODERATOR):
        if not (user.status == UserStatus.ACTIVE and user.is_payment_validated):
            raise HTTPException(status_code=404, detail="Member not found.")
    return UserOut.model_validate(user)
 
 
@router.patch("/users/{user_id}", response_model=UserOut, tags=["Members"])
async def update_user(user_id: uuid.UUID, body: UserAdminUpdate, current_user: User = Depends(role_required(UserRole.ADMIN))):
    """Update any member's details (admin only)."""
    user = await User.get_or_none(id=user_id, is_deleted=False)
    if not user:
        raise HTTPException(status_code=404, detail="Member not found.")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(user, field, value)
    await user.save()
    return UserOut.model_validate(user)
 
 
@router.patch("/users/{user_id}/validate", response_model=UserOut, tags=["Members"])
async def validate_payment(user_id: uuid.UUID, current_user: User = Depends(role_required(UserRole.ADMIN))):
    """Toggle payment validation (admin only). Upgrades role to membre."""
    user = await User.get_or_none(id=user_id, is_deleted=False)
    if not user:
        raise HTTPException(status_code=404, detail="Member not found.")
    user.is_payment_validated = not user.is_payment_validated
    if user.is_payment_validated:
        user.role        = UserRole.MEMBRE
        user.status      = UserStatus.ACTIVE
        user.validated_by_id = current_user.id
        user.validated_at    = datetime.now(UTC.utc)
        if not user.member_since:
            user.member_since = datetime.now(UTC.utc)
    await user.save()
    await log_activity(current_user, ActivityActionType.USER_VALIDATED, "user", user.id,
                       f"Payment validated for {user.full_name}")
    return UserOut.model_validate(user)
 
 
@router.delete("/users/{user_id}", status_code=204, tags=["Members"])
async def delete_user(user_id: uuid.UUID, current_user: User = Depends(role_required(UserRole.ADMIN))):
    """Soft-delete a member account (admin only)."""
    user = await User.get_or_none(id=user_id, is_deleted=False)
    if not user:
        raise HTTPException(status_code=404, detail="Member not found.")
    user.is_deleted = True
    user.status     = UserStatus.SUSPENDED
    await user.save(update_fields=["is_deleted", "status"])
