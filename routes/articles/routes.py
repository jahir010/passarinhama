
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status, Query, BackgroundTasks
from tortoise.transactions import in_transaction
from tortoise.expressions import Q
from pydantic import BaseModel, EmailStr, Field
import uuid
from datetime import datetime, timezone as UTC

from app.auth import login_required, role_required
from app.token import get_current_user
from app.utils.file_manager import delete_file, update_file
from applications.articles.models import Article, ArticleCategory, ArticleStatus
from applications.user.models import  Group, Permission, User, UserRole, UserStatus, MembershipCategory, ActivityActionType, ActivityLog
from applications.notifications.notifications import NotificationType, NotificationLog, NotificationPreference
from routes.user.routes import log_activity
from app.utils.send_email import send_email



router = APIRouter()


# ══════════════════════════════════════════════════════════════════════════════
# ARTICLES
# ══════════════════════════════════════════════════════════════════════════════

class ArticleCreate(BaseModel):
    title:             str
    category_id:       uuid.UUID
    excerpt:           str | None = None
    body:              str | None = None
    youtube_url:       str | None = None
    structured_fields: dict | None = None
 
class ArticleUpdate(ArticleCreate):
    title:       str | None = None
    category_id: uuid.UUID | None = None
 
class ArticleOut(BaseModel):
    id:           uuid.UUID
    title:        str
    excerpt:      str | None
    status:       ArticleStatus
    published_at: datetime | None
    created_at:   datetime
 
    class Config:
        from_attributes = True
 
@router.get("/articles", tags=["Articles"])
async def list_articles(
    status:      ArticleStatus | None = None,
    category_id: uuid.UUID | None = None,
    search:      str | None = None,
    page:        int = Query(1, ge=1),
    page_size:   int = Query(20, ge=1, le=100),
    current_user: User | None = Depends(get_current_user),
):
    """
    List articles. Unauthenticated → published only.
    Admin/Moderator → all statuses.
    """
    qs = Article.filter()
 
    if not current_user or current_user.role not in (UserRole.ADMIN, UserRole.MODERATOR):
        qs = qs.filter(status=ArticleStatus.PUBLISHED)
    elif status:
        qs = qs.filter(status=status)
 
    if category_id:
        qs = qs.filter(category_id=category_id)
    if search:
        qs = qs.filter(Q(title__icontains=search) | Q(excerpt__icontains=search))
 
    total    = await qs.count()
    articles = await qs.offset((page - 1) * page_size).limit(page_size).prefetch_related("author", "category")
    return {"total": total, "page": page, "results": articles}
 
 
@router.post("/articles", tags=["Articles"], status_code=201)
async def create_article(body: ArticleCreate, current_user: User = Depends(role_required(UserRole.ADMIN, UserRole.MODERATOR))):
    """Create a new article (admin/moderator)."""
    category = await ArticleCategory.get_or_none(id=body.category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found.")
    article = await Article.create(
        title=body.title, category=category, excerpt=body.excerpt,
        body=body.body, youtube_url=body.youtube_url,
        structured_fields=body.structured_fields, author=current_user,
    )
    await log_activity(current_user, ActivityActionType.ARTICLE_PUBLISHED, "article", article.id, body.title)
    return article


@router.get("/my-articles", tags=["Articles"])
async def list_my_articles(
    status: ArticleStatus | None = None,
    author_id: uuid.UUID | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user)
):
    """List articles authored by the current user. Optional status filter.
    """
    if author_id:
        qs = Article.filter(author_id=author_id)
    else:
        qs = Article.filter(author=current_user)
    if status:
        qs = qs.filter(status=status)
    total    = await qs.count()
    articles = await qs.offset((page - 1) * page_size).limit(page_size).prefetch_related("category")
    return {"total": total, "page": page, "results": articles}
 
 
@router.get("/articles/{article_id}", tags=["Articles"])
async def get_article(article_id: uuid.UUID, current_user: User | None = Depends(get_current_user)):
    article = await Article.get_or_none(id=article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found.")
    if article.status == ArticleStatus.DRAFT:
        if not current_user or current_user.role not in (UserRole.ADMIN, UserRole.MODERATOR):
            if not current_user or current_user.id != article.author_id:
                raise HTTPException(status_code=403, detail="Draft articles are restricted.")
    return article
 
 
@router.patch("/articles/{article_id}", tags=["Articles"])
async def update_article(article_id: uuid.UUID, body: ArticleUpdate, current_user: User = Depends(role_required(UserRole.ADMIN, UserRole.MODERATOR))):
    article = await Article.get_or_none(id=article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found.")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(article, field, value)
    await article.save()
    return article
 
 
@router.post("/articles/{article_id}/publish", tags=["Articles"])
async def publish_article(
    article_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(role_required(UserRole.ADMIN, UserRole.MODERATOR)),
):
    """Toggle article status between draft and published."""
    article = await Article.get_or_none(id=article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found.")
    if article.status == ArticleStatus.DRAFT:
        article.status       = ArticleStatus.PUBLISHED
        article.published_at = datetime.now(UTC.utc)
        await article.save(update_fields=["status", "published_at"])
        # Notify all subscribed users
        users = await User.filter(status=UserStatus.ACTIVE, is_payment_validated=True, is_deleted=False)
        for user in users:
            await send_email(subject="New Article Published: " + article.title, to=user.email, message=f'"user": {user}, "article": {article}')
    else:
        article.status = ArticleStatus.DRAFT
        await article.save(update_fields=["status"])
    return {"status": article.status}
 
 
@router.delete("/articles/{article_id}", status_code=204, tags=["Articles"])
async def delete_article(article_id: uuid.UUID, current_user: User = Depends(role_required(UserRole.ADMIN))):
    article = await Article.get_or_none(id=article_id)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found.")
    await article.delete()
 
 
# ── Article Categories ────────────────────
 
@router.get("/article-categories", tags=["Articles"])
async def list_article_categories(current_user: User = Depends(get_current_user)):
    return await ArticleCategory.all()
 
 
@router.post("/article-categories", tags=["Articles"], status_code=201)
async def create_article_category(name: str, color_code: str = "#FFD600", current_user: User = Depends(role_required(UserRole.ADMIN))):
    return await ArticleCategory.create(name=name, color_code=color_code)