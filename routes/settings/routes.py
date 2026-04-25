from fastapi import APIRouter, Depends
import uuid
from pydantic import BaseModel

from applications.notifications.notifications import NotificationPreference, NotificationType
from applications.user.models import User

from app.auth import role_required
from app.token import get_current_user



router = APIRouter()


# ── Notifications ─────────────────────────
 
class NotificationPrefUpdate(BaseModel):
    notification_type: NotificationType
    forum_id:          uuid.UUID | None = None
    email_enabled:     bool


@router.get("/notifications/preferences", tags=["Notifications"])
async def get_notification_preferences(current_user: User = Depends(get_current_user)):
    return await NotificationPreference.filter(user=current_user).prefetch_related("forum")
 
 
@router.patch("/notifications/preferences", tags=["Notifications"])
async def update_notification_preference(body: NotificationPrefUpdate, current_user: User = Depends(get_current_user)):
    pref, _ = await NotificationPreference.get_or_create(
        user=current_user,
        notification_type=body.notification_type,
        forum_id=body.forum_id,
        defaults={"email_enabled": body.email_enabled},
    )
    pref.email_enabled = body.email_enabled
    await pref.save(update_fields=["email_enabled"])
    return pref