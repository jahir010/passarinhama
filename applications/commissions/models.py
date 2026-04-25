from enum import Enum
import uuid

from passlib.context import CryptContext
from tortoise import fields, models
from applications.user.models import UserRole




class CommissionRole(str, Enum):
    MEMBER    = "member"
    PRESIDENT = "president"


# ─────────────────────────────────────────
# 17. Commission
# ─────────────────────────────────────────
 
class Commission(models.Model):
    """
    Association sub-group with dedicated president, forum workspace,
    and document folder.
    """
    id          = fields.UUIDField(pk=True, default=uuid.uuid4)
    name        = fields.CharField(max_length=200, unique=True)
    description = fields.TextField(null=True)
    status      = fields.CharField(max_length=20, default="active")  # active | inactive
    president   = fields.ForeignKeyField(
        "models.User",
        related_name="led_commissions",
        null=True,
        on_delete=fields.SET_NULL,
    )
    forum       = fields.ForeignKeyField(
        "models.Forum",
        related_name="commissions",
        null=True,
        on_delete=fields.SET_NULL,
    )
    created_at  = fields.DatetimeField(auto_now_add=True)
    updated_at  = fields.DatetimeField(auto_now=True)
 
    class Meta:
        table    = "commissions"
        ordering = ["name"]
 
    def __str__(self) -> str:
        return self.name
 
 
# ─────────────────────────────────────────
# 18. CommissionMember
# ─────────────────────────────────────────
 
class CommissionMember(models.Model):
    """Junction: User ↔ Commission with role inside commission."""
    id          = fields.UUIDField(pk=True, default=uuid.uuid4)
    commission  = fields.ForeignKeyField("models.Commission", related_name="members", on_delete=fields.CASCADE)
    user        = fields.ForeignKeyField("models.User", related_name="commission_memberships", on_delete=fields.CASCADE)
    role        = fields.CharEnumField(CommissionRole, default=CommissionRole.MEMBER)
    joined_at   = fields.DatetimeField(auto_now_add=True)
 
    class Meta:
        table           = "commission_members"
        unique_together = [("commission", "user")]