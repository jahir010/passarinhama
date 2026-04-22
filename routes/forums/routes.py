from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status, Query, BackgroundTasks
from tortoise.transactions import in_transaction
from tortoise.expressions import Q
from pydantic import BaseModel, EmailStr, Field
import uuid
from datetime import datetime, timezone as UTC

from app.auth import login_required, role_required
from app.token import get_current_user
from app.utils.helper_functions import check_forum_access, log_activity
from app.utils.file_manager import delete_file, update_file
from applications.forums.models import Forum, ForumRolePermission, ModerationStatus, ModerationAction, Topic, Post, ModerationLog
from applications.user.models import  Group, Permission, User, UserRole, UserStatus, MembershipCategory, ActivityActionType, ActivityLog
from applications.notifications.notifications import NotificationType, NotificationLog, NotificationPreference
from routes.user.routes import log_activity
from app.utils.send_email import send_email



router = APIRouter()


class TopicCreate(BaseModel):
    title: str
 
class PostCreate(BaseModel):
    content: str
 
class PostModerate(BaseModel):
    action:           ModerationAction
    rejection_reason: str | None = None
    forward_to:       uuid.UUID | None = None   # for forward action


# ══════════════════════════════════════════════════════════════════════════════
# FORUMS
# ══════════════════════════════════════════════════════════════════════════════
 
@router.get("/forums", tags=["Forums"])
async def list_forums(current_user: User = Depends(get_current_user)):
    """Return forums the current user has at least read access to."""
    perms = await ForumRolePermission.filter(role=current_user.role, can_read=True).prefetch_related("forum")
    return [p.forum for p in perms]
 
 
@router.post("/forums", tags=["Forums"], status_code=201)
async def create_forum(name: str, description: str | None = None,
                       forum_type: str = "general", current_user: User = Depends(role_required(UserRole.ADMIN))):
    slug = name.lower().replace(" ", "-")
    return await Forum.create(name=name, slug=slug, description=description, forum_type=forum_type)
 
 
@router.get("/forums/{forum_id}", tags=["Forums"])
async def get_forum(forum_id: uuid.UUID, current_user: User = Depends(get_current_user)):
    forum = await Forum.get_or_none(id=forum_id)
    if not forum:
        raise HTTPException(status_code=404, detail="Forum not found.")
    await check_forum_access(forum, current_user)
    return forum
 
 
@router.patch("/forums/{forum_id}/permissions", tags=["Forums"])
async def set_forum_permission(
    forum_id: uuid.UUID,
    role:     UserRole,
    can_read: bool,
    can_post: bool,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """Set read/post access for a specific role on a forum."""
    forum = await Forum.get_or_none(id=forum_id)
    if not forum:
        raise HTTPException(status_code=404, detail="Forum not found.")
    perm, _ = await ForumRolePermission.get_or_create(forum=forum, role=role)
    perm.can_read = can_read
    perm.can_post = can_post
    await perm.save()
    return perm
 
 
# ── Topics ─────────────────────────────────
 
@router.get("/forums/{forum_id}/topics", tags=["Forums"])
async def list_topics(
    forum_id:  uuid.UUID,
    page:      int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    forum = await Forum.get_or_none(id=forum_id)
    if not forum:
        raise HTTPException(status_code=404, detail="Forum not found.")
    await check_forum_access(forum, current_user)
    total  = await Topic.filter(forum=forum).count()
    topics = await Topic.filter(forum=forum).offset((page - 1) * page_size).limit(page_size).prefetch_related("author")
    return {"total": total, "page": page, "results": topics}
 
 
@router.post("/forums/{forum_id}/topics", tags=["Forums"], status_code=201)
async def create_topic(forum_id: uuid.UUID, body: TopicCreate, current_user: User = Depends(get_current_user)):
    forum = await Forum.get_or_none(id=forum_id)
    if not forum:
        raise HTTPException(status_code=404, detail="Forum not found.")
    await check_forum_access(forum, current_user, need_post=True)
    topic = await Topic.create(forum=forum, author=current_user, title=body.title)
    await log_activity(current_user, ActivityActionType.TOPIC_CREATED, "topic", topic.id, body.title)
    return topic
 
 
# ── Posts ──────────────────────────────────
 
@router.get("/topics/{topic_id}/posts", tags=["Forums"])
async def list_posts(
    topic_id:  uuid.UUID,
    page:      int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
):
    topic = await Topic.get_or_none(id=topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found.")
    forum = await topic.forum
    await check_forum_access(forum, current_user)
 
    # Increment view count asynchronously
    await Topic.filter(id=topic_id).update(view_count=topic.view_count + 1)
 
    qs     = Post.filter(topic=topic, moderation_status=ModerationStatus.APPROVED)
    total  = await qs.count()
    posts  = await qs.offset((page - 1) * page_size).limit(page_size).prefetch_related("author")
    return {"total": total, "page": page, "results": posts}
 
 
@router.post("/topics/{topic_id}/posts", tags=["Forums"], status_code=201)
async def create_post(
    topic_id: uuid.UUID,
    body: PostCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    topic = await Topic.get_or_none(id=topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found.")
    if topic.is_locked:
        raise HTTPException(status_code=403, detail="This topic is locked.")
    forum = await topic.forum
    await check_forum_access(forum, current_user, need_post=True)
 
    # Admin/moderator posts bypass moderation
    auto_approve = current_user.role in (UserRole.ADMIN, UserRole.MODERATOR)
    post = await Post.create(
        topic=topic, author=current_user, content=body.content,
        moderation_status=ModerationStatus.APPROVED if auto_approve else ModerationStatus.PENDING,
        moderated_at=datetime.now(UTC.utc) if auto_approve else None,
        moderated_by=current_user if auto_approve else None,
    )
    if auto_approve:
        await Topic.filter(id=topic_id).update(
            reply_count=topic.reply_count + 1,
            last_activity_at=datetime.now(UTC.utc),
        )
        # Notify topic author
        topic_author = await topic.author
        if topic_author.id != current_user.id:
            await send_email(
                topic_author, NotificationType.POST_REPLY, "post", post.id, background_tasks
            )
    await log_activity(current_user, ActivityActionType.POST_CREATED, "post", post.id)
    return post
 
 
@router.patch("/posts/{post_id}/moderate", tags=["Moderation"])
async def moderate_post(
    post_id: uuid.UUID,
    body: PostModerate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(role_required(UserRole.ADMIN, UserRole.MODERATOR)),
):
    """Approve / reject / flag / forward a post."""
    post = await Post.get_or_none(id=post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found.")
 
    post.moderated_by  = current_user
    post.moderated_at  = datetime.now(UTC.utc)
 
    if body.action == ModerationAction.APPROVE:
        post.moderation_status = ModerationStatus.APPROVED
        topic = await post.topic
        await Topic.filter(id=topic.id).update(
            reply_count=topic.reply_count + 1,
            last_activity_at=datetime.now(UTC.utc),
        )
        # Notify forum subscribers
        post_author = await post.author
        # await send_email(
        #     post_author, NotificationType.NEW_POST, "post", post.id, background_tasks
        # )
 
    elif body.action == ModerationAction.REJECT:
        post.moderation_status = ModerationStatus.REJECTED
        post.rejection_reason  = body.rejection_reason
        author = await post.author
        # await send_email(
        #     author, NotificationType.POST_REJECTED, "post", post.id, background_tasks
        # )
 
    elif body.action == ModerationAction.FLAG:
        post.moderation_status = ModerationStatus.FLAGGED
 
    await post.save()
 
    # Write moderation log
    await ModerationLog.create(
        moderator=current_user, post=post,
        action=body.action, reason=body.rejection_reason,
    )
    await log_activity(current_user, ActivityActionType.POST_APPROVED if body.action == ModerationAction.APPROVE
                       else ActivityActionType.POST_REJECTED, "post", post.id)
    return {"status": post.moderation_status}