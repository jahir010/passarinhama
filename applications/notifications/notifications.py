
from enum import Enum
import uuid

from passlib.context import CryptContext
from tortoise import fields, models




class NotificationType(str, Enum):
    NEW_ARTICLE  = "new_article"
    NEW_POST     = "new_post"
    NEW_EVENT    = "new_event"
    NEW_TRAINING = "new_training"
    POST_REPLY   = "post_reply"
    POST_REJECTED = "post_rejected"
    ACCOUNT_APPROVED = "account_approved"



# ─────────────────────────────────────────
# 5. NotificationPreference
# ─────────────────────────────────────────
 
class NotificationPreference(models.Model):
    """
    Per-user per-type email opt-in/out.
    One row per (user, notification_type) pair.
    Nullable forum_id supports per-forum subscriptions.
    """
    id                = fields.UUIDField(pk=True, default=uuid.uuid4)
    user              = fields.ForeignKeyField("models.User", related_name="notification_preferences", on_delete=fields.CASCADE)
    notification_type = fields.CharEnumField(NotificationType)
    forum             = fields.ForeignKeyField(
        "models.Forum",
        related_name="notification_preferences",
        null=True,
        on_delete=fields.CASCADE,
    )
    email_enabled     = fields.BooleanField(default=True)
    updated_at        = fields.DatetimeField(auto_now=True)
 
    class Meta:
        table         = "notification_preferences"
        unique_together = [("user", "notification_type", "forum")]
 
 
# ─────────────────────────────────────────
# 6. NotificationLog
# ─────────────────────────────────────────
 
class NotificationLog(models.Model):
    """Audit log of every email dispatched by the platform."""
    id                = fields.UUIDField(pk=True, default=uuid.uuid4)
    recipient         = fields.ForeignKeyField("models.User", related_name="notification_logs", on_delete=fields.CASCADE)
    notification_type = fields.CharEnumField(NotificationType)
    target_type       = fields.CharField(max_length=50)   # "article" | "post" | "event" | "training"
    target_id         = fields.UUIDField(null=True)
    is_read           = fields.BooleanField(default=False)
    sent_at           = fields.DatetimeField(auto_now_add=True)
 
    class Meta:
        table    = "notification_logs"
        ordering = ["-sent_at"]