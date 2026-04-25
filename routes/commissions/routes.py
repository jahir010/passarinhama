from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
import uuid

from app.auth import role_required
from app.token import get_current_user
from app.utils.helper_functions import log_activity

from applications.commissions.models import Commission, CommissionMember, CommissionRole
from applications.user.models import User, UserRole, UserStatus, ActivityActionType
from applications.forums.models import Forum


router = APIRouter()


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────────────────────────────────────────────────

class CommissionCreate(BaseModel):
    name:         str
    description:  str | None  = None
    president_id: uuid.UUID | None = None
    forum_id:     uuid.UUID | None = None


class CommissionUpdate(BaseModel):
    """All fields optional — proper PATCH semantics."""
    name:         str | None  = None
    description:  str | None  = None
    status:       str | None  = None   # "active" | "inactive"
    president_id: uuid.UUID | None = None
    forum_id:     uuid.UUID | None = None


class CommissionMemberAdd(BaseModel):
    user_id: uuid.UUID
    role:    CommissionRole = CommissionRole.MEMBER


class CommissionMemberUpdate(BaseModel):
    role: CommissionRole


# ──────────────────────────────────────────────────────────────────────────────
# Shared serialiser
# ──────────────────────────────────────────────────────────────────────────────

async def _serialize_commission(commission: Commission, current_user: User | None = None) -> dict:
    """
    Build the response dict the UI commission card needs:
      - president name
      - members_count
      - forum_id  (frontend navigates to /forums/{id})
      - is_member (so UI can show commission-specific actions to members)
    """
    president = None
    if commission.president_id:
        p = await commission.president
        president = {
            "id":         str(p.id),
            "first_name": p.first_name,
            "last_name":  p.last_name,
        }

    members_count = await CommissionMember.filter(commission=commission).count()

    is_member = False
    if current_user:
        is_member = await CommissionMember.filter(
            commission=commission, user=current_user
        ).exists()

    forum_id = str(commission.forum_id) if commission.forum_id else None

    return {
        "id":           str(commission.id),
        "name":         commission.name,
        "description":  commission.description,
        "status":       commission.status,
        "president":    president,
        "forum_id":     forum_id,       # frontend uses this to navigate to the forum
        "members_count": members_count,
        "is_member":    is_member,
        "created_at":   commission.created_at.isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# COMMISSIONS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/commissions", tags=["Commissions"])
async def list_commissions(
    current_user: User = Depends(get_current_user),
):
    """
    List all active commissions.
    All authenticated users can view. Each card shows president,
    members_count, forum_id, and whether current user is a member.
    Spec ref: §11.1, §11.3
    """
    commissions = await Commission.filter(status="active").order_by("name")
    return [await _serialize_commission(c, current_user) for c in commissions]


@router.get("/commissions/{commission_id}", tags=["Commissions"])
async def get_commission(
    commission_id: uuid.UUID,
    current_user:  User = Depends(get_current_user),
):
    """
    Single commission detail — needed for the commission page header.
    Spec ref: §11.1
    """
    commission = await Commission.get_or_none(id=commission_id)
    if not commission:
        raise HTTPException(status_code=404, detail="Commission not found.")
    return await _serialize_commission(commission, current_user)


@router.post("/commissions", tags=["Commissions"], status_code=201)
async def create_commission(
    body:         CommissionCreate,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """
    Create a new commission (admin only).

    If president_id is provided:
      - validates the user exists and is active
      - creates the commission
      - automatically adds that user as a CommissionMember with role=PRESIDENT
        to keep the FK and the junction table in sync.
    Spec ref: §11.2, §15.4
    """
    president = None
    if body.president_id:
        president = await User.get_or_none(
            id=body.president_id,
            is_deleted=False,
            status=UserStatus.ACTIVE,
        )
        if not president:
            raise HTTPException(status_code=404, detail="President user not found or not active.")

    forum = None
    if body.forum_id:
        forum = await Forum.get_or_none(id=body.forum_id)
        if not forum:
            raise HTTPException(status_code=404, detail="Forum not found.")

    commission = await Commission.create(
        name=body.name,
        description=body.description,
        president=president,
        forum=forum,
    )

    # Sync: if a president is set, ensure they appear in the member table
    # with role=PRESIDENT so the junction table is always the source of truth.
    if president:
        await _sync_president_membership(commission, president)

    await log_activity(current_user, ActivityActionType.COMMISSION_CREATED, "commission", commission.id, body.name)
    return await _serialize_commission(commission, current_user)


@router.patch("/commissions/{commission_id}", tags=["Commissions"])
async def update_commission(
    commission_id: uuid.UUID,
    body:          CommissionUpdate,
    current_user:  User = Depends(role_required(UserRole.ADMIN)),
):
    """
    Update a commission (admin only).
    When president_id changes:
      - old president's CommissionMember role is downgraded to MEMBER
      - new president's CommissionMember role is set to PRESIDENT (created if needed)
    This keeps the FK and junction table in sync at all times.
    Spec ref: §15.4
    """
    commission = await Commission.get_or_none(id=commission_id)
    if not commission:
        raise HTTPException(status_code=404, detail="Commission not found.")

    data = body.model_dump(exclude_none=True)

    # Handle president change with sync
    if "president_id" in data:
        new_president_id = data.pop("president_id")

        # Downgrade old president to member in junction table
        if commission.president_id and commission.president_id != new_president_id:
            await CommissionMember.filter(
                commission=commission,
                user_id=commission.president_id,
                role=CommissionRole.PRESIDENT,
            ).update(role=CommissionRole.MEMBER)

        if new_president_id is None:
            commission.president_id = None
        else:
            new_president = await User.get_or_none(
                id=new_president_id, is_deleted=False, status=UserStatus.ACTIVE
            )
            if not new_president:
                raise HTTPException(status_code=404, detail="New president user not found or not active.")
            commission.president_id = new_president_id
            await _sync_president_membership(commission, new_president)

    # Handle forum change
    if "forum_id" in data:
        forum_id = data.pop("forum_id")
        if forum_id is None:
            commission.forum_id = None
        else:
            forum = await Forum.get_or_none(id=forum_id)
            if not forum:
                raise HTTPException(status_code=404, detail="Forum not found.")
            commission.forum_id = forum_id

    for field, value in data.items():
        setattr(commission, field, value)

    await commission.save()
    return await _serialize_commission(commission, current_user)


@router.delete("/commissions/{commission_id}", status_code=204, tags=["Commissions"])
async def delete_commission(
    commission_id: uuid.UUID,
    current_user:  User = Depends(role_required(UserRole.ADMIN)),
):
    """
    Delete a commission (admin only).
    Cascades to CommissionMember rows via DB cascade.
    Spec ref: §15.4
    """
    commission = await Commission.get_or_none(id=commission_id)
    if not commission:
        raise HTTPException(status_code=404, detail="Commission not found.")
    await commission.delete()


# ── Members ──────────────────────────────────────────────────────────────────

@router.get("/commissions/{commission_id}/members", tags=["Commissions"])
async def list_commission_members(
    commission_id: uuid.UUID,
    current_user:  User = Depends(get_current_user),
):
    """
    List all members of a commission with their role.
    Spec ref: §11.2
    """
    commission = await Commission.get_or_none(id=commission_id)
    if not commission:
        raise HTTPException(status_code=404, detail="Commission not found.")

    members = await CommissionMember.filter(commission=commission).prefetch_related("user")
    return {
        "commission_id":   str(commission.id),
        "commission_name": commission.name,
        "total":           len(members),
        "members": [
            {
                "id":         str(m.id),
                "role":       m.role,
                "joined_at":  m.joined_at.isoformat(),
                "user": {
                    "id":         str(m.user_id),
                    "first_name": m.user.first_name,
                    "last_name":  m.user.last_name,
                    "email":      m.user.email,
                },
            }
            for m in members
        ],
    }


@router.post("/commissions/{commission_id}/members", tags=["Commissions"], status_code=201)
async def add_commission_member(
    commission_id: uuid.UUID,
    body:          CommissionMemberAdd,
    current_user:  User = Depends(role_required(UserRole.ADMIN)),
):
    """
    Add a user to a commission (admin only).

    Rules enforced:
      - User must be active and payment-validated (spec §11.2)
      - If role=PRESIDENT: old president is downgraded to MEMBER and
        Commission.president FK is updated — single-president constraint.
      - If already a member: role is updated (idempotent upsert).
    Spec ref: §11.2, §15.4
    """
    commission = await Commission.get_or_none(id=commission_id)
    if not commission:
        raise HTTPException(status_code=404, detail="Commission not found.")

    # FIX: validate user is active and payment-validated per spec §11.2
    user = await User.get_or_none(
        id=body.user_id,
        is_deleted=False,
        status=UserStatus.ACTIVE,
        is_payment_validated=True,
    )
    if not user:
        raise HTTPException(
            status_code=400,
            detail="User must be an active, payment-validated member to join a commission.",
        )

    # FIX: enforce single president — demote current president first
    if body.role == CommissionRole.PRESIDENT:
        await _enforce_single_president(commission, new_president=user)

    member, created = await CommissionMember.get_or_create(
        commission=commission,
        user=user,
        defaults={"role": body.role},
    )
    if not created and member.role != body.role:
        member.role = body.role
        await member.save(update_fields=["role"])

    await log_activity(current_user, ActivityActionType.COMMISSION_MEMBER_ADDED, "commission", commission.id, user.id)

    return {
        "id":        str(member.id),
        "role":      member.role,
        "joined_at": member.joined_at.isoformat(),
        "user": {
            "id":         str(user.id),
            "first_name": user.first_name,
            "last_name":  user.last_name,
        },
    }


@router.patch("/commissions/{commission_id}/members/{user_id}", tags=["Commissions"])
async def update_commission_member_role(
    commission_id: uuid.UUID,
    user_id:       uuid.UUID,
    body:          CommissionMemberUpdate,
    current_user:  User = Depends(role_required(UserRole.ADMIN)),
):
    """
    Update the role of a commission member (admin only).
    Promoting to PRESIDENT demotes the current president to MEMBER
    and updates Commission.president FK.
    Spec ref: §15.4
    """
    commission = await Commission.get_or_none(id=commission_id)
    if not commission:
        raise HTTPException(status_code=404, detail="Commission not found.")

    member = await CommissionMember.get_or_none(commission=commission, user_id=user_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not in commission.")

    if body.role == CommissionRole.PRESIDENT:
        user = await User.get(id=user_id)
        await _enforce_single_president(commission, new_president=user)

    member.role = body.role
    await member.save(update_fields=["role"])

    return {"id": str(member.id), "user_id": str(user_id), "role": member.role}


@router.delete("/commissions/{commission_id}/members/{user_id}", status_code=204, tags=["Commissions"])
async def remove_commission_member(
    commission_id: uuid.UUID,
    user_id:       uuid.UUID,
    current_user:  User = Depends(role_required(UserRole.ADMIN)),
):
    """
    Remove a user from a commission (admin only).
    If the removed user is the current president, Commission.president is cleared.
    Spec ref: §15.4
    """
    member = await CommissionMember.get_or_none(commission_id=commission_id, user_id=user_id)
    if not member:
        raise HTTPException(status_code=404, detail="Member not in commission.")

    await member.delete()

    # If removed user was the president, clear the FK
    commission = await Commission.get(id=commission_id)
    if str(commission.president_id) == str(user_id):
        commission.president_id = None
        await commission.save(update_fields=["president_id"])

    await log_activity(current_user, ActivityActionType.COMMISSION_MEMBER_REMOVED, "commission", commission_id, user_id)


# ──────────────────────────────────────────────────────────────────────────────
# Private helpers
# ──────────────────────────────────────────────────────────────────────────────

async def _sync_president_membership(commission: Commission, president: User) -> None:
    """
    Ensure `president` exists in CommissionMember with role=PRESIDENT.
    Creates the row if missing; upgrades role if they're already a MEMBER.
    Call this any time Commission.president FK is set.
    """
    member, created = await CommissionMember.get_or_create(
        commission=commission,
        user=president,
        defaults={"role": CommissionRole.PRESIDENT},
    )
    if not created and member.role != CommissionRole.PRESIDENT:
        member.role = CommissionRole.PRESIDENT
        await member.save(update_fields=["role"])


async def _enforce_single_president(commission: Commission, new_president: User) -> None:
    """
    Enforce the single-president constraint:
      1. Downgrade old president's CommissionMember row to MEMBER.
      2. Update Commission.president FK to the new president.
    Call this before assigning the new president row.
    """
    # Downgrade whoever currently holds PRESIDENT role in the junction table
    await CommissionMember.filter(
        commission=commission,
        role=CommissionRole.PRESIDENT,
    ).exclude(user=new_president).update(role=CommissionRole.MEMBER)

    # Update the FK on Commission itself
    commission.president_id = new_president.id
    await commission.save(update_fields=["president_id"])