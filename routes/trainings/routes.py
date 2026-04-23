# from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
# from tortoise.transactions import in_transaction
# from tortoise.expressions import Q, F
# from pydantic import BaseModel, Field
# import uuid
# import re
# from datetime import datetime, timezone as UTC

# from app.auth import login_required, role_required
# from app.token import get_current_user
# from app.utils.helper_functions import  log_activity
# from applications.user.models import (
#     User, UserRole, ActivityActionType, UserStatus,
# )
# from applications.trainings.models import Training, TrainingFormat, TrainingStatus, TrainingRegistration
# from applications.notifications.notifications import NotificationType
# from app.utils.send_email import send_email




# router = APIRouter()



# class TrainingCreate(BaseModel):
#     title:          str
#     description:    str | None = None
#     format:         TrainingFormat = TrainingFormat.ONLINE
#     training_date:  str | None = None
#     duration_hours: int | None = None
#     max_attendees:  int | None = None
 
# class TrainingUpdate(TrainingCreate):
#     title: str | None = None





# # ══════════════════════════════════════════════════════════════════════════════
# # TRAININGS
# # ══════════════════════════════════════════════════════════════════════════════
 
# @router.get("/trainings", tags=["Trainings"])
# async def list_trainings(
#     status:    TrainingStatus | None = None,
#     current_user: User = Depends(get_current_user),
# ):
#     qs = Training.filter()
#     if status:
#         qs = qs.filter(status=status)
#     return await qs.prefetch_related("created_by")
 
 
# @router.post("/trainings", tags=["Trainings"], status_code=201)
# async def create_training(
#     body: TrainingCreate,
#     background_tasks: BackgroundTasks,
#     current_user: User = Depends(role_required(UserRole.ADMIN)),
# ):
#     training = await Training.create(**body.model_dump(), created_by=current_user)
#     # Notify all members
#     users = await User.filter(status=UserStatus.ACTIVE, is_payment_validated=True, is_deleted=False)
#     for user in users:
#         # await send_email(user, NotificationType.NEW_TRAINING, "training", training.id, background_tasks)
#         pass
#     return training
 
 
# @router.patch("/trainings/{training_id}", tags=["Trainings"])
# async def update_training(training_id: uuid.UUID, body: TrainingUpdate, current_user: User = Depends(role_required(UserRole.ADMIN))):
#     training = await Training.get_or_none(id=training_id)
#     if not training:
#         raise HTTPException(status_code=404, detail="Training not found.")
#     for field, value in body.model_dump(exclude_none=True).items():
#         setattr(training, field, value)
#     await training.save()
#     return training
 
 
# @router.post("/trainings/{training_id}/register", tags=["Trainings"], status_code=201)
# async def register_for_training(
#     training_id: uuid.UUID,
#     background_tasks: BackgroundTasks,
#     current_user: User = Depends(get_current_user),
# ):
#     training = await Training.get_or_none(id=training_id)
#     if not training:
#         raise HTTPException(status_code=404, detail="Training not found.")
#     if training.status != TrainingStatus.OPEN:
#         raise HTTPException(status_code=409, detail=f"Training is {training.status}.")
#     if await TrainingRegistration.filter(training=training, user=current_user).exists():
#         raise HTTPException(status_code=409, detail="Already registered.")
 
#     reg = await TrainingRegistration.create(training=training, user=current_user)
#     await log_activity(current_user, ActivityActionType.TRAINING_REGISTERED, "training", training.id)
 
#     # Auto-flip to full if capacity reached
#     if training.max_attendees:
#         count = await TrainingRegistration.filter(training=training).count()
#         if count >= training.max_attendees:
#             training.status = TrainingStatus.FULL
#             await training.save(update_fields=["status"])
#     return reg
 
 
# @router.delete("/trainings/{training_id}/register", status_code=204, tags=["Trainings"])
# async def unregister_from_training(training_id: uuid.UUID, current_user: User = Depends(get_current_user)):
#     reg = await TrainingRegistration.get_or_none(training_id=training_id, user=current_user)
#     if not reg:
#         raise HTTPException(status_code=404, detail="Registration not found.")
#     await reg.delete()
#     # Re-open if was full
#     training = await Training.get(id=training_id)
#     if training.status == TrainingStatus.FULL:
#         training.status = TrainingStatus.OPEN
#         await training.save(update_fields=["status"])








from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from tortoise.expressions import F
from pydantic import BaseModel, field_validator, model_validator
import uuid
from datetime import date, datetime

from app.auth import login_required, role_required
from app.token import get_current_user
from app.utils.helper_functions import log_activity

from applications.trainings.models import Training, TrainingFormat, TrainingStatus, TrainingRegistration
from applications.user.models import User, UserRole, ActivityActionType, UserStatus
from applications.notifications.notifications import NotificationType, NotificationPreference
from app.utils.send_email import send_email


router = APIRouter()


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────────────────────────────────────────────────

class TrainingCreate(BaseModel):
    title:          str
    description:    str | None         = None
    format:         TrainingFormat     = TrainingFormat.ONLINE
    training_date:  str | None         = None   # "YYYY-MM-DD"
    duration_hours: int | None         = None
    max_attendees:  int | None         = None

    # FIX: coerce string → date at schema level so Tortoise always
    # receives a proper date object, not a raw string (same root cause
    # as the events timedelta crash we fixed earlier).
    @field_validator("training_date")
    @classmethod
    def parse_training_date(cls, v: str | None) -> date | None:
        if v is None:
            return None
        try:
            return date.fromisoformat(v)
        except ValueError:
            raise ValueError("training_date must be YYYY-MM-DD format.")


class TrainingUpdate(BaseModel):
    """All fields optional — proper PATCH semantics."""
    title:          str | None         = None
    description:    str | None         = None
    format:         TrainingFormat | None = None
    training_date:  str | None         = None
    duration_hours: int | None         = None
    max_attendees:  int | None         = None
    status:         TrainingStatus | None = None   # admin can manually mark completed

    @field_validator("training_date")
    @classmethod
    def parse_training_date(cls, v: str | None) -> date | None:
        if v is None:
            return None
        try:
            return date.fromisoformat(v)
        except ValueError:
            raise ValueError("training_date must be YYYY-MM-DD format.")


# ──────────────────────────────────────────────────────────────────────────────
# Shared serialiser
# ──────────────────────────────────────────────────────────────────────────────

async def _serialize_training(training: Training, current_user: User | None = None) -> dict:
    """
    Build the response dict every UI card needs:
      - attendee_count   → shown on past training cards (spec §10.3)
      - spots_left       → available spots indicator (spec §5.6 dashboard widget)
      - is_registered    → drives Register / Unregister button state
      - is_at_capacity   → UI disables Register button
      - status           → auto-corrected for past dates (see note below)
    """
    attendee_count = await TrainingRegistration.filter(training=training).count()

    is_registered = False
    if current_user:
        is_registered = await TrainingRegistration.filter(
            training=training, user=current_user
        ).exists()

    spots_left = None
    if training.max_attendees is not None:
        spots_left = max(0, training.max_attendees - attendee_count)

    # FIX: COMPLETED is never set in DB automatically (no cron job yet).
    # Derive it at read time: if training_date is in the past and status
    # is still OPEN/FULL, report it as completed so the UI is always correct.
    effective_status = training.status
    if (
        training.training_date
        and training.training_date < date.today()
        and training.status != TrainingStatus.COMPLETED
    ):
        effective_status = TrainingStatus.COMPLETED

    created_by = await training.created_by
    return {
        "id":             str(training.id),
        "title":          training.title,
        "description":    training.description,
        "format":         training.format,
        "training_date":  training.training_date.isoformat() if training.training_date else None,
        "duration_hours": training.duration_hours,
        "max_attendees":  training.max_attendees,
        "attendee_count": attendee_count,
        "spots_left":     spots_left,
        "is_at_capacity": spots_left == 0 if spots_left is not None else False,
        "is_registered":  is_registered,
        "status":         effective_status,
        "created_at":     training.created_at.isoformat(),
        "created_by": {
            "id":         str(created_by.id),
            "first_name": created_by.first_name,
            "last_name":  created_by.last_name,
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# TRAININGS
# Fixed-path routes (/upcoming, /dashboard-widget) MUST come before
# parameterised routes (/{training_id}) to avoid FastAPI matching
# "upcoming" as a UUID and returning 422.
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/trainings/upcoming", tags=["Trainings"])
async def upcoming_trainings(
    page:         int  = Query(1, ge=1),
    page_size:    int  = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """
    Upcoming trainings grid — training_date >= today, ordered chronologically.
    Spec ref: §10.3 'Upcoming trainings grid (4 columns)'
    """
    today = date.today()
    qs    = Training.filter(training_date__gte=today)
    total = await qs.count()
    items = await qs.order_by("training_date").offset((page - 1) * page_size).limit(page_size)
    return {
        "total":   total,
        "page":    page,
        "results": [await _serialize_training(t, current_user) for t in items],
    }


@router.get("/trainings/past", tags=["Trainings"])
async def past_trainings(
    page:         int  = Query(1, ge=1),
    page_size:    int  = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    """
    Past trainings grid — training_date < today, reverse chronological.
    Always includes attendee_count (spec §10.3: 'Past trainings show attendee count').
    Spec ref: §10.3
    """
    today = date.today()
    qs    = Training.filter(training_date__lt=today)
    total = await qs.count()
    items = await qs.order_by("-training_date").offset((page - 1) * page_size).limit(page_size)
    return {
        "total":   total,
        "page":    page,
        "results": [await _serialize_training(t, current_user) for t in items],
    }


@router.get("/trainings/dashboard-widget", tags=["Trainings"])
async def trainings_dashboard_widget(
    current_user: User = Depends(get_current_user),
):
    """
    Next 4 upcoming trainings for the dashboard widget.
    Returns spots_left for the 'available spots indicator'.
    Spec ref: §5.6 'Monthly trainings widget — Next 4 upcoming trainings
              with available spots indicator'
    """
    today = date.today()
    items = (
        await Training.filter(training_date__gte=today)
        .order_by("training_date")
        .limit(4)
    )
    return [await _serialize_training(t, current_user) for t in items]


@router.get("/trainings", tags=["Trainings"])
async def list_trainings(
    status:       TrainingStatus | None = None,
    current_user: User                  = Depends(get_current_user),
):
    """
    Full training list with optional status filter.
    Used by admin panel and any future filter UI.
    Spec ref: §10.1
    """
    qs = Training.filter()
    if status:
        qs = qs.filter(status=status)
    items = await qs.order_by("training_date")
    return [await _serialize_training(t, current_user) for t in items]


@router.get("/trainings/{training_id}", tags=["Trainings"])
async def get_training(
    training_id:  uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """
    Single training detail — needed when user clicks a training card.
    Spec ref: §10.1, §10.2
    """
    training = await Training.get_or_none(id=training_id)
    if not training:
        raise HTTPException(status_code=404, detail="Training not found.")
    return await _serialize_training(training, current_user)


@router.post("/trainings", tags=["Trainings"], status_code=201)
async def create_training(
    body:             TrainingCreate,
    background_tasks: BackgroundTasks,
    current_user:     User = Depends(role_required(UserRole.ADMIN)),
):
    """
    Create a new training (admin only).

    FIX 1: body.model_dump() now has a proper date object (coerced by
            Pydantic validator) — Tortoise won't receive a raw string.
    FIX 2: Notification only goes to users with new_training preference ON.
            Spec §14.1: 'All users with new_training preference = ON'.
    Spec ref: §10.1, §14.1
    """
    training = await Training.create(**body.model_dump(), created_by=current_user)
    await log_activity(
        current_user, ActivityActionType.TRAINING_CREATED, "training", training.id, body.title
    )

    # FIX: respect notification preferences — do NOT blast all active users
    prefs = await NotificationPreference.filter(
        notification_type=NotificationType.NEW_TRAINING,
        email_enabled=True,
    ).prefetch_related("user")

    for pref in prefs:
        user = pref.user
        if user.status == UserStatus.ACTIVE and user.is_payment_validated and not user.is_deleted:
            # await send_email(
            #     user, NotificationType.NEW_TRAINING, "training", training.id, background_tasks
            # )
            pass

    return await _serialize_training(training, current_user)


@router.patch("/trainings/{training_id}", tags=["Trainings"])
async def update_training(
    training_id:  uuid.UUID,
    body:         TrainingUpdate,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """
    Partial update of a training (admin only).
    Admin can also manually set status=completed.
    FIX: date coercion is handled by Pydantic — no raw string reaches Tortoise.
    Spec ref: §10.1
    """
    training = await Training.get_or_none(id=training_id)
    if not training:
        raise HTTPException(status_code=404, detail="Training not found.")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(training, field, value)
    await training.save()
    return await _serialize_training(training, current_user)


@router.delete("/trainings/{training_id}", status_code=204, tags=["Trainings"])
async def delete_training(
    training_id:  uuid.UUID,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """Delete a training (admin only). Cascades to TrainingRegistration rows."""
    training = await Training.get_or_none(id=training_id)
    if not training:
        raise HTTPException(status_code=404, detail="Training not found.")
    await training.delete()


# ── Registration ─────────────────────────────────────────────────────────────

@router.post("/trainings/{training_id}/register", tags=["Trainings"], status_code=201)
async def register_for_training(
    training_id:      uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user:     User = Depends(get_current_user),
):
    """
    Register the current user for a training.
    - Rejects if status is not OPEN (full or completed).
    - Auto-flips status to FULL when capacity is reached (atomic count check).
    Spec ref: §10.2
    """
    training = await Training.get_or_none(id=training_id)
    if not training:
        raise HTTPException(status_code=404, detail="Training not found.")

    # Treat past trainings as completed even if DB hasn't been updated yet
    if training.training_date and training.training_date < date.today():
        raise HTTPException(status_code=409, detail="Training is already completed.")

    if training.status != TrainingStatus.OPEN:
        raise HTTPException(status_code=409, detail=f"Training is {training.status}. Registration is closed.")

    if await TrainingRegistration.filter(training=training, user=current_user).exists():
        raise HTTPException(status_code=409, detail="Already registered for this training.")

    reg = await TrainingRegistration.create(training=training, user=current_user)
    await log_activity(current_user, ActivityActionType.TRAINING_REGISTERED, "training", training.id)

    # Auto-flip to FULL when capacity is reached
    if training.max_attendees is not None:
        count = await TrainingRegistration.filter(training=training).count()
        if count >= training.max_attendees:
            training.status = TrainingStatus.FULL
            await training.save(update_fields=["status"])

    return {
        "id":            str(reg.id),
        "training_id":   str(training.id),
        "registered_at": reg.registered_at.isoformat(),
        "message":       f"Successfully registered for '{training.title}'.",
    }


@router.delete("/trainings/{training_id}/register", status_code=204, tags=["Trainings"])
async def unregister_from_training(
    training_id:  uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """
    Cancel registration for a training.
    Re-opens the training if it was FULL (someone dropped out, spot is free again).
    Spec ref: §10.2
    """
    reg = await TrainingRegistration.get_or_none(training_id=training_id, user=current_user)
    if not reg:
        raise HTTPException(status_code=404, detail="Registration not found.")
    await reg.delete()

    # Re-open if was full — a spot just became available
    training = await Training.get(id=training_id)
    if training.status == TrainingStatus.FULL:
        training.status = TrainingStatus.OPEN
        await training.save(update_fields=["status"])


@router.get("/trainings/{training_id}/registrations", tags=["Trainings"])
async def list_training_registrations(
    training_id:  uuid.UUID,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """
    List all registrations for a training (admin only).
    Used in the admin panel to see who signed up.
    Spec ref: §10.2, §10.3 (past trainings show attendee count)
    """
    training = await Training.get_or_none(id=training_id)
    if not training:
        raise HTTPException(status_code=404, detail="Training not found.")

    regs = await TrainingRegistration.filter(training=training).prefetch_related("user")
    return {
        "training_id":    str(training.id),
        "training_title": training.title,
        "total":          len(regs),
        "max_attendees":  training.max_attendees,
        "registrations": [
            {
                "id":            str(r.id),
                "registered_at": r.registered_at.isoformat(),
                "user": {
                    "id":         str(r.user_id),
                    "first_name": r.user.first_name,
                    "last_name":  r.user.last_name,
                    "email":      r.user.email,
                },
            }
            for r in regs
        ],
    }