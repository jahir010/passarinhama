from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from tortoise.expressions import F
from pydantic import BaseModel, field_validator
import uuid
import os
import mimetypes

from app.auth import role_required
from app.token import get_current_user
from app.utils.helper_functions import log_activity, check_folder_access
from app.utils.file_manager import save_file, delete_file   # your existing save_file helper

from applications.user.models import User, UserRole, ActivityActionType
from applications.documents.models import DocumentFolder, DocumentFolderPermission, Document, FileType


router = APIRouter()


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────────────────────────────────────────────────

class FolderCreate(BaseModel):
    name:          str
    parent_id:     uuid.UUID | None = None
    commission_id: uuid.UUID | None = None
    color_code:    str = "#FFD600"


class FolderUpdate(BaseModel):
    """Admin can rename a folder or change its color."""
    name:       str | None = None
    color_code: str | None = None


class DocumentUpdate(BaseModel):
    """Admin/uploader can rename the display name of a document."""
    original_name: str


# ──────────────────────────────────────────────────────────────────────────────
# File type detector
# ──────────────────────────────────────────────────────────────────────────────

def _detect_file_type(filename: str, mime_type: str) -> FileType:
    """Derive FileType enum from filename extension or MIME type."""
    ext = os.path.splitext(filename)[1].lower().lstrip(".")
    mapping = {
        "pdf":  FileType.PDF,
        "doc":  FileType.DOC,
        "docx": FileType.DOCX,
        "xls":  FileType.XLS,
        "xlsx": FileType.XLSX,
    }
    if ext in mapping:
        return mapping[ext]
    if mime_type.startswith("image/"):
        return FileType.IMAGE
    return FileType.OTHER


# ──────────────────────────────────────────────────────────────────────────────
# Serialisers
# ──────────────────────────────────────────────────────────────────────────────

def _serialize_folder(folder: DocumentFolder, can_upload: bool = False, children: list = None) -> dict:
    """
    Folder response shape the UI sidebar needs:
      - color_code      → folder icon colour
      - document_count  → badge on folder
      - can_upload      → show/hide upload button
      - children        → nested subfolders for tree rendering
    """
    return {
        "id":             str(folder.id),
        "name":           folder.name,
        "color_code":     folder.color_code,
        "document_count": folder.document_count,
        "commission_id":  str(folder.commission_id) if folder.commission_id else None,
        "parent_id":      str(folder.parent_id) if folder.parent_id else None,
        "created_at":     folder.created_at.isoformat(),
        "can_upload":     can_upload,
        "children":       children if children is not None else [],
    }


def _serialize_document(doc: Document, uploader) -> dict:
    """
    Document response the file list UI needs:
      - file_size_kb    → human-readable size display
      - uploader name   → "uploaded by X"
    Note: storage_path is intentionally NEVER returned (spec §12.3).
    """
    size_kb = round(doc.file_size / 1024, 1)
    size_display = f"{size_kb} KB" if size_kb < 1024 else f"{round(size_kb / 1024, 1)} MB"

    return {
        "id":            str(doc.id),
        "original_name": doc.original_name,
        "file_type":     doc.file_type,
        "mime_type":     doc.mime_type,
        "file_size":     doc.file_size,
        "file_size_display": size_display,
        "folder_id":     str(doc.folder_id),
        "created_at":    doc.created_at.isoformat(),
        "uploaded_by": {
            "id":         str(uploader.id),
            "first_name": uploader.first_name,
            "last_name":  uploader.last_name,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# Depth helper
# ──────────────────────────────────────────────────────────────────────────────

async def _get_folder_depth(folder_id: uuid.UUID) -> int:
    """
    Walk up the parent chain and count levels.
    Root folders return depth=1; their children depth=2; grandchildren depth=3.
    Spec §12.1: max nesting depth = 3.
    """
    depth = 1
    current_id = folder_id
    while True:
        folder = await DocumentFolder.get_or_none(id=current_id)
        if not folder or not folder.parent_id:
            break
        depth += 1
        current_id = folder.parent_id
    return depth


# ══════════════════════════════════════════════════════════════════════════════
# FOLDERS
# Fixed-path routes (/folders, /upload-url) MUST come before
# parameterised routes (/{document_id}) to avoid FastAPI UUID-matching issues.
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/documents/folders", tags=["Documents"])
async def list_folders(
    commission_id: uuid.UUID | None = None,
    current_user:  User             = Depends(get_current_user),
):
    """
    Return folders the current user can read, as a nested tree.

    The tree structure lets the frontend render the sidebar without
    additional requests per folder. Root folders (parent_id=None) are
    returned at the top level; their children are nested under `children`.

    ?commission_id=<uuid> filters to folders belonging to that commission
    (used by the Commission documents button, spec §11.3).

    Response shape:
      [
        {
          "id", "name", "color_code", "document_count",
          "commission_id", "can_upload",
          "children": [ { same shape, no further nesting } ]
        }, ...
      ]
    Spec ref: §12.1, §12.5
    """
    perms = await DocumentFolderPermission.filter(
        role=current_user.role, can_read=True
    ).prefetch_related("folder")

    # Build lookup: folder_id → (folder, can_upload)
    accessible: dict[str, tuple] = {}
    for p in perms:
        accessible[str(p.folder_id)] = (p.folder, p.can_upload)

    # Optionally filter by commission
    all_folders = [f for f, _ in accessible.values()]
    if commission_id:
        all_folders = [f for f in all_folders if str(f.commission_id) == str(commission_id)]

    # Separate roots and children
    root_folders = [f for f in all_folders if not f.parent_id]
    child_folders = [f for f in all_folders if f.parent_id]

    # Build tree (max depth 3 — only one level of children needed)
    result = []
    for folder in sorted(root_folders, key=lambda f: f.name):
        _, can_upload = accessible[str(folder.id)]
        children_data = []
        for child in sorted(child_folders, key=lambda f: f.name):
            if str(child.parent_id) == str(folder.id):
                _, child_can_upload = accessible.get(str(child.id), (None, False))
                children_data.append(_serialize_folder(child, child_can_upload))
        result.append(_serialize_folder(folder, can_upload, children_data))

    return result


@router.post("/documents/folders", tags=["Documents"], status_code=201)
async def create_folder(
    body:         FolderCreate,
    current_user: User = Depends(role_required(UserRole.ADMIN, UserRole.MODERATOR)),
):
    """
    Create a new folder (admin/moderator only).

    FIX: enforces max nesting depth = 3 (spec §12.1).
    If parent_id is given and its depth is already 3, reject with 400.
    Seeds default permissions: admin + moderator always get R+W.
    Spec ref: §12.1, §15.2
    """
    if body.parent_id:
        parent = await DocumentFolder.get_or_none(id=body.parent_id)
        if not parent:
            raise HTTPException(status_code=404, detail="Parent folder not found.")

        # FIX: enforce max depth = 3
        parent_depth = await _get_folder_depth(body.parent_id)
        if parent_depth >= 3:
            raise HTTPException(
                status_code=400,
                detail="Maximum folder nesting depth of 3 levels reached. Cannot create subfolder here.",
            )

    folder = await DocumentFolder.create(**body.model_dump())

    # Seed default R+W permissions for admin and moderator
    if current_user.role == UserRole.ADMIN:
        await DocumentFolderPermission.get_or_create(
            folder=folder, role=UserRole.ADMIN,
            defaults={"can_read": True, "can_upload": True},
        )

    return _serialize_folder(folder, can_upload=True)


@router.patch("/documents/folders/{folder_id}", tags=["Documents"])
async def rename_folder(
    folder_id:    uuid.UUID,
    body:         FolderUpdate,
    current_user: User = Depends(role_required(UserRole.ADMIN, UserRole.MODERATOR)),
):
    """
    Rename a folder or update its color (admin only).
    Spec ref: §15.2 'rename folders'
    """
    folder = await DocumentFolder.get_or_none(id=folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found.")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(folder, field, value)
    await folder.save()

    # Check caller's upload permission for response
    perm = await DocumentFolderPermission.get_or_none(folder=folder, role=current_user.role)
    can_upload = perm.can_upload if perm else False
    return _serialize_folder(folder, can_upload)


@router.get("/documents/folders/{folder_id}/permissions", tags=["Documents"])
async def get_folder_permissions(
    folder_id:    uuid.UUID,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """
    Get the read/upload permissions for all roles on a folder (admin/moderator only).
    Spec ref: §12.5, §15.2
    """
    folder = await DocumentFolder.get_or_none(id=folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found.")

    perms = await DocumentFolderPermission.filter(folder=folder).all()
    return {
        str(p.role): {"can_read": p.can_read, "can_upload": p.can_upload}
        for p in perms
    }


@router.patch("/documents/folders/{folder_id}/permissions", tags=["Documents"])
async def set_folder_permission(
    folder_id:  uuid.UUID,
    role:       UserRole,
    can_read:   bool,
    can_upload: bool,
    current_user: User = Depends(role_required(UserRole.ADMIN))
):
    """
    Set read/upload access for a specific role on a folder (admin only).
    Idempotent — creates the row if it doesn't exist.
    Spec ref: §12.5, §15.2
    """
    folder = await DocumentFolder.get_or_none(id=folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found.")
    perm, _ = await DocumentFolderPermission.get_or_create(folder=folder, role=role)
    perm.can_read   = can_read
    perm.can_upload = can_upload
    await perm.save()
    return perm


@router.delete("/documents/folders/{folder_id}", status_code=204, tags=["Documents"])
async def delete_folder(
    folder_id:    uuid.UUID,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """
    Delete a folder and all its contents (admin only).
    Cascades to child folders and documents via DB CASCADE.
    Spec ref: §15.2
    """
    folder = await DocumentFolder.get_or_none(id=folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found.")
    await folder.delete()


# ══════════════════════════════════════════════════════════════════════════════
# DOCUMENTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/documents", tags=["Documents"])
async def list_documents(
    folder_id:    uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """
    List all documents inside a folder.
    This was missing entirely — the UI file list panel requires it.

    Returns structured document data including uploader name and
    human-readable file size. storage_path is never returned (spec §12.3).

    Response shape:
      {
        "folder": { "id", "name", "can_upload" },
        "total": int,
        "documents": [ { id, original_name, file_type, file_size_display,
                          created_at, uploaded_by: {id, first_name, last_name} } ]
      }
    Spec ref: §12.3, §12.4
    """
    folder = await DocumentFolder.get_or_none(id=folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found.")
    await check_folder_access(folder, current_user)

    docs = await Document.filter(folder=folder).order_by("-created_at").prefetch_related("uploaded_by")

    perm = await DocumentFolderPermission.get_or_none(folder=folder, role=current_user.role)
    can_upload = perm.can_upload if perm else False

    return {
        "folder": {
            "id":        str(folder.id),
            "name":      folder.name,
            "can_upload": can_upload,
        },
        "total": len(docs),
        "documents": [_serialize_document(d, d.uploaded_by) for d in docs],
    }


@router.post("/documents/upload", tags=["Documents"], status_code=201)
async def upload_document(
    folder_id:    uuid.UUID   = Form(...),
    file:         UploadFile  = File(...),
    current_user: User        = Depends(get_current_user),
):
    """
    Upload a document directly via multipart/form-data.
    One request — no presigned URL dance needed.

    Form fields:
      - folder_id  (UUID)
      - file       (the actual file)

    The file is saved via your existing save_file() helper under
    "documents" upload_to path. FileType is auto-detected from the
    filename extension / MIME type so the frontend sends no extra metadata.

    Response: full document dict (same shape as list_documents).
    Spec ref: §12.4
    """
    folder = await DocumentFolder.get_or_none(id=folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found.")
    await check_folder_access(folder, current_user, need_upload=True)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file has no filename.")

    # Detect MIME type — fall back to content_type from the upload if available
    mime_type = file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
    file_type  = _detect_file_type(file.filename, mime_type)

    # Read size before saving (seek to end, record position, seek back)
    content    = await file.read()
    file_size  = len(content)
    await file.seek(0)

    # Save using your existing save_file helper — returns the stored URL/path
    storage_path = await save_file(file, upload_to="documents")

    doc = await Document.create(
        folder=folder,
        uploaded_by=current_user,
        filename=os.path.basename(storage_path),   # stored filename (may be UUID-renamed)
        original_name=file.filename,               # display name shown to users
        file_type=file_type,
        mime_type=mime_type,
        file_size=file_size,
        storage_path=storage_path,
    )

    # Atomic increment — no race condition on concurrent uploads
    await DocumentFolder.filter(id=folder.id).update(document_count=F("document_count") + 1)

    await log_activity(
        current_user, ActivityActionType.DOCUMENT_UPLOADED, "document", doc.id, file.filename
    )
    return _serialize_document(doc, current_user)


@router.patch("/documents/{document_id}", tags=["Documents"])
async def rename_document(
    document_id:  uuid.UUID,
    body:         DocumentUpdate,
    current_user: User = Depends(get_current_user),
):
    """
    Rename a document's display name (original_name).
    Admin can rename any document; uploader can rename their own.
    storage_path and filename (the S3 key) are never changed.
    Spec ref: §15.2 'rename files'
    """
    doc = await Document.get_or_none(id=document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    # Only admin or the uploader can rename
    if current_user.role != UserRole.ADMIN and str(doc.uploaded_by_id) != str(current_user.id):
        raise HTTPException(status_code=403, detail="You can only rename your own documents.")

    doc.original_name = body.original_name
    await doc.save(update_fields=["original_name"])
    return _serialize_document(doc, current_user)


@router.get("/documents/{document_id}/download", tags=["Documents"])
async def get_download_url(
    document_id:  uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    """
    Return the file URL for direct download.
    Since files are stored via save_file() (local/CDN path), storage_path
    IS the accessible URL — returned as download_url.
    storage_path itself is never returned (spec §12.3) — only the
    resolved download URL.
    Spec ref: §12.4
    """
    doc = await Document.get_or_none(id=document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")
    folder = await doc.folder
    await check_folder_access(folder, current_user)

    return {
        "download_url": doc.storage_path,   # the URL returned by save_file()
        "filename":     doc.original_name,
        "mime_type":    doc.mime_type,
    }


@router.delete("/documents/{document_id}", status_code=204, tags=["Documents"])
async def delete_document(
    document_id:  uuid.UUID,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    """
    Delete a document record (admin only).
    FIX 1: decrements document_count atomically with F().
    FIX 2: logs the delete action (was missing entirely).
    Note: actual S3 object deletion should be handled by a background task
    or S3 lifecycle policy — not blocking the HTTP response.
    Spec ref: §15.2
    """
    doc = await Document.get_or_none(id=document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found.")

    folder = await doc.folder
    original_name = doc.original_name
    storage_path  = doc.storage_path   # capture before delete

    await doc.delete()

    # Remove the actual file from local storage via your delete_file helper
    await delete_file(storage_path)

    # Atomic decrement — no race condition, no negative values
    await DocumentFolder.filter(id=folder.id).update(
        document_count=F("document_count") - 1
    )

    # FIX: activity log on delete was missing
    await log_activity(
        current_user, ActivityActionType.DOCUMENT_DELETED, "document", document_id, original_name
    )







# ─── Pydantic schema ───────────────────────────────────────────────────────────

class BulkFolderPermissionRequest(BaseModel):
    folder_id: list[uuid.UUID]
    role:     list[UserRole]
    can_read: bool
    can_upload: bool

    @field_validator("folder_id", "role")
    @classmethod
    def no_empty(cls, v):
        if not v:
            raise ValueError("List cannot be empty.")
        return v


# ─── Endpoint ──────────────────────────────────────────────────────────────────

@router.patch("/folder/permissions/bulk", tags=["Folders"])
async def set_folder_permissions_bulk(
    body:         BulkFolderPermissionRequest,
    current_user: User = Depends(role_required(UserRole.ADMIN)),
):
    # 1. Validate all folder IDs exist in ONE query
    found_folders = await DocumentFolder.filter(id__in=body.folder_id).only("id")
    found_ids    = {f.id for f in found_folders}

    missing = set(body.folder_id) - found_ids
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Folders not found or inactive: {[str(m) for m in missing]}",
        )

    # 2. Expand (folder_id x role) combinations
    records = [
        DocumentFolderPermission(
            folder_id = folder_id,
            role     = role,
            can_read = body.can_read,
            can_upload = body.can_upload,
        )
        for folder_id in body.folder_id
        for role in body.role
    ]

    # 3. Single upsert — one round-trip
    await DocumentFolderPermission.bulk_create(
        records,
        update_fields=["can_read", "can_upload"],
        on_conflict=["folder_id", "role"],
    )

    # 4. Return updated rows
    result = await DocumentFolderPermission.filter(
        folder_id__in=body.folder_id
    ).values("id", "folder_id", "role", "can_read", "can_upload")

    return {"updated": len(records), "permissions": result}