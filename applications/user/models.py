from enum import Enum
import uuid

from passlib.context import CryptContext
from tortoise import fields, models

pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")


# ─────────────────────────────────────────
# Enums
# ─────────────────────────────────────────

class UserRole(str, Enum):
    ADMIN                   = "admin"
    MODERATOR               = "moderator"
    MEMBRE                  = "membre"
    AUDITEUR                = "auditeur"
    MEMBRE_ARCHITECTES      = "membre_architectes"
    MEMBRES_TECHNICOPRO     = "membres_technicopro"
    MEMBRE_COMITE           = "membre_comite"
    MEMBRE_DHONNEUR         = "membre_dhonneur"
    VISITEUR                = "visiteur"
    PARTENAIRE              = "partenaire"

class FEATURES(str, Enum):
    USER                   = "user"
    FORUM                  = "forum"
    ARTICLE                = "article"
    TRAINING               = "training"
    EVENT                  = "event"
    DOCUMENT               = "document"
    




class UserStatus(str, Enum):
    PENDING   = "pending"
    ACTIVE    = "active"
    SUSPENDED = "suspended"


class ActivityActionType(str, Enum):
    USER_REGISTERED           = "user_registered"
    USER_VALIDATED            = "user_validated"
    ARTICLE_PUBLISHED         = "article_published"
    ARTICLE_UPDATED           = "article_updated"
    POST_CREATED              = "post_created"
    POST_APPROVED             = "post_approved"
    POST_REJECTED             = "post_rejected"
    POST_FLAGGED              = "post_flagged"
    POST_UPDATED              = "post_updated"
    POST_DELETED              = "post_deleted"
    TOPIC_CREATED             = "topic_created"
    TOPIC_UPDATED             = "topic_updated"
    TOPIC_DELETED             = "topic_deleted"
    FORUM_UPDATED             = "forum_updated"
    FORUM_DELETED             = "forum_deleted"
    EVENT_CREATED             = "event_created"
    TRAINING_REGISTERED       = "training_registered"
    TRAINING_CREATED          = "training_created"
    DOCUMENT_UPLOADED         = "document_uploaded"
    DOCUMENT_DELETED          = "document_deleted"
    MODERATION_FLAG           = "moderation_flag"
    POST_FORWARDED            = "post_forwarded"
    COMMISSION_CREATED        = "commission_created"
    COMMISSION_MEMBER_ADDED   = "commission_member_added"
    COMMISSION_MEMBER_REMOVED = "commission_member_removed"
    PROFILE_UPDATED           = "profile_updated"
    PASSWORD_CHANGED          = "password_changed"


# ─────────────────────────────────────────
# 1. MembershipCategory
# ─────────────────────────────────────────

class MembershipCategory(models.Model):
    """
    Lookup table: Category A (Senior), Category B (Member), Category C (Associate).
    Configurable by admin.
    """
    id          = fields.UUIDField(pk=True, default=uuid.uuid4)
    name        = fields.CharField(max_length=100, unique=True)
    code        = fields.CharField(max_length=20, unique=True)
    description = fields.TextField(null=True)
    created_at  = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table    = "membership_categories"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


# ─────────────────────────────────────────
# 2. Permission & Group (RBAC helpers)
# ─────────────────────────────────────────

class Permission(models.Model):
    id       = fields.IntField(pk=True, readonly=True)
    name     = fields.CharField(max_length=100, unique=True)
    codename = fields.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.codename


class Group(models.Model):
    id   = fields.IntField(pk=True)
    name = fields.CharField(max_length=100, unique=True)

    permissions: fields.ManyToManyRelation["Permission"] = fields.ManyToManyField(
        "models.Permission",
        related_name="groups",
        through="group_permissions",
    )

    def __str__(self):
        return self.name


# ─────────────────────────────────────────
# 3. User
# ─────────────────────────────────────────

class User(models.Model):
    """
    Core member account. Covers all roles: admin, moderator, membre,
    auditeur, partenaires_technicopro, comite_coproprietaire.
    """
    id                   = fields.UUIDField(pk=True, default=uuid.uuid4)
    email                = fields.CharField(max_length=255, unique=True)
    password             = fields.CharField(max_length=255)

    # ── Personal info ─────────────────────────────────────────────────────
    first_name           = fields.CharField(max_length=100)
    last_name            = fields.CharField(max_length=100)
    phone                = fields.CharField(max_length=30, null=True)   # landline / office
    mobile               = fields.CharField(max_length=30, null=True)   # mobile — profile §16 "MOBILE" field
    avatar_url           = fields.CharField(max_length=500, null=True)

    # ── Email verification (§4 / profile "Vérifié" badge) ─────────────────
    is_email_verified    = fields.BooleanField(default=False)
    email_verified_at    = fields.DatetimeField(null=True)

    # ── Société / Company info (profile "Société" tab) ────────────────────
    company_name         = fields.CharField(max_length=255, null=True)
    company_role         = fields.CharField(max_length=100, null=True)  # job title inside company
    company_website      = fields.CharField(max_length=255, null=True)
    company_siret        = fields.CharField(max_length=20, null=True)

    # ── Address ───────────────────────────────────────────────────────────
    street_address       = fields.CharField(max_length=255, null=True)
    city                 = fields.CharField(max_length=100, null=True)
    postal_code          = fields.CharField(max_length=20, null=True)
    country              = fields.CharField(max_length=100, default="France")

    # ── Role & status ─────────────────────────────────────────────────────
    role                 = fields.CharEnumField(UserRole, default=UserRole.MEMBRE)
    status               = fields.CharEnumField(UserStatus, default=UserStatus.PENDING)
    membership_category  = fields.ForeignKeyField(
        "models.MembershipCategory",
        related_name="members",
        null=True,
        on_delete=fields.SET_NULL,
    )
    is_active            = fields.BooleanField(default=True)
    is_superuser         = fields.BooleanField(default=False)

    # ── Two-factor authentication (§4.2) ──────────────────────────────────
    is_active_2fa        = fields.BooleanField(default=False)

    # ── Payment validation (§3.3) ─────────────────────────────────────────
    is_payment_validated = fields.BooleanField(default=False)
    validated_by         = fields.ForeignKeyField(
        "models.User",
        related_name="validated_members",
        null=True,
        on_delete=fields.SET_NULL,
    )
    validated_at         = fields.DatetimeField(null=True)

    # ── Soft delete ───────────────────────────────────────────────────────
    is_deleted           = fields.BooleanField(default=False)

    # ── Online presence ───────────────────────────────────────────────────
    # Updated on every authenticated request (middleware) or at login.
    # "Online" = last_seen_at within the last 5 minutes.
    last_seen_at         = fields.DatetimeField(null=True)

    # Set once on successful login; drives "Dernier accès" column in member list.
    last_login_at        = fields.DatetimeField(null=True)

    # ── Timestamps ────────────────────────────────────────────────────────
    member_since         = fields.DatetimeField(null=True)
    created_at           = fields.DatetimeField(auto_now_add=True)
    updated_at           = fields.DatetimeField(auto_now=True)

    # ── RBAC relations (§4.4) ─────────────────────────────────────────────
    groups: fields.ManyToManyRelation["Group"] = fields.ManyToManyField(
        "models.Group",
        related_name="users",
        through="user_groups",
    )
    user_permissions: fields.ManyToManyRelation["Permission"] = fields.ManyToManyField(
        "models.Permission",
        related_name="users",
        through="user_permissions",
    )

    class Meta:
        table    = "users"
        ordering = ["last_name", "first_name"]
        indexes  = [
            ("status", "is_payment_validated"),
            ("last_seen_at",),   # fast online-presence queries
        ]

    # ── helpers ──────────────────────────────────────────────────────────

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def initials(self) -> str:
        return f"{self.first_name[:1]}{self.last_name[:1]}".upper()

    @property
    def is_online(self) -> bool:
        """True when the user was seen in the last 5 minutes."""
        print (f"Checking online status for user {self.id} — last_seen_at: {self.last_seen_at}")
        from datetime import datetime, timezone, timedelta
        if not self.last_seen_at:
            print (f"User {self.id} has no last_seen_at timestamp")
            return False
        return (datetime.now(timezone.utc) - self.last_seen_at) < timedelta(minutes=5)

    @property
    def membership_year(self) -> int | None:
        """Year extracted from validated_at — drives 'Actif 2025' badge."""
        if self.validated_at:
            return self.validated_at.year
        if self.member_since:
            return self.member_since.year
        return None

    @classmethod
    def set_password(cls, password: str) -> str:
        return pwd_context.hash(password)

    def verify_password(self, password: str) -> bool:
        if not self.password:
            return False
        try:
            return pwd_context.verify(password, self.password)
        except Exception:
            return False

    async def has_permission(self, codename: str) -> bool:
        """
        Returns True when the user holds the given permission, either directly
        or via a group (§4.4). Superusers bypass all checks.
        """
        if self.is_superuser:
            return True

        await self.fetch_related("user_permissions", "groups__permissions")

        for perm in self.user_permissions:
            if perm.codename == codename:
                return True

        for group in self.groups:
            for perm in group.permissions:
                if perm.codename == codename:
                    return True

        return False

    def __str__(self) -> str:
        return self.full_name


# ─────────────────────────────────────────
# 4. UserSession  (§4 "Sessions" profile tab)
# ─────────────────────────────────────────

class UserSession(models.Model):
    """
    Tracks active login sessions per user.
    Drives the "Sessions" tab on the profile page and enables
    per-device logout. One row per issued refresh token.
    """
    id           = fields.UUIDField(pk=True, default=uuid.uuid4)
    user         = fields.ForeignKeyField("models.User", related_name="sessions", on_delete=fields.CASCADE)

    # Opaque token stored hashed; the raw token is returned to the client once.
    token_hash   = fields.CharField(max_length=255, unique=True)

    # Device / browser metadata (populated from User-Agent + IP on login)
    device_name  = fields.CharField(max_length=200, null=True)   # e.g. "Chrome 124 — Windows"
    ip_address   = fields.CharField(max_length=45, null=True)    # IPv4 or IPv6
    user_agent   = fields.TextField(null=True)

    is_active    = fields.BooleanField(default=True)
    created_at   = fields.DatetimeField(auto_now_add=True)       # login time
    last_used_at = fields.DatetimeField(auto_now_add=True)       # updated on each refresh
    expires_at   = fields.DatetimeField(null=True)

    class Meta:
        table    = "user_sessions"
        ordering = ["-last_used_at"]

    def __str__(self) -> str:
        return f"{self.user_id} — {self.device_name or 'unknown device'}"


# ─────────────────────────────────────────
# 5. ActivityLog
# ─────────────────────────────────────────

class ActivityLog(models.Model):
    """
    Platform-wide activity feed (polymorphic: target_type + target_id).
    Drives the dashboard Recent Activity widget (§5.2).

    target_type allowed values:
        "user" | "article" | "post" | "topic" | "event" |
        "training" | "document" | "commission"
    """
    id          = fields.UUIDField(pk=True, default=uuid.uuid4)
    user        = fields.ForeignKeyField(
        "models.User", related_name="activity_logs", on_delete=fields.CASCADE
    )
    action_type = fields.CharEnumField(ActivityActionType, max_length=100)
    target_type = fields.CharField(max_length=50, null=True)
    target_id   = fields.UUIDField(null=True)
    description = fields.TextField(null=True)
    created_at  = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table    = "activity_logs"
        ordering = ["-created_at"]