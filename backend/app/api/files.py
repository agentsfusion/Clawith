"""File management API routes for agent workspaces."""

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.core.permissions import check_agent_access
from app.core.security import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.storage.factory import get_storage
from app.services.storage.mime_types import guess_mime_type, is_inline_displayable
from app.services.storage.interface import (
    FileNotFoundError as StorageFileNotFoundError,
    StorageError,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/agents/{agent_id}/files", tags=["files"])


class FileInfo(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: int = 0
    modified_at: str = ""
    version_token: str | None = None
    url: str | None = None


class FileContent(BaseModel):
    path: str
    content: str
    version_token: str | None = None


class FileWrite(BaseModel):
    content: str
    autosave: bool = False
    session_id: str | None = None
    expected_version_token: str | None = None


class FileLockBody(BaseModel):
    path: str
    session_id: str | None = None


class RestoreRevisionBody(BaseModel):
    revision_id: uuid.UUID
    expected_version_token: str | None = None


TEXT_PREVIEW_EXTENSIONS = {
    ".bat",
    ".bash",
    ".c",
    ".cfg",
    ".clj",
    ".cpp",
    ".cs",
    ".css",
    ".dart",
    ".env",
    ".go",
    ".h",
    ".hpp",
    ".ini",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".less",
    ".lua",
    ".m",
    ".mm",
    ".php",
    ".pl",
    ".pm",
    ".properties",
    ".py",
    ".r",
    ".rb",
    ".rs",
    ".sass",
    ".scala",
    ".scss",
    ".sh",
    ".sql",
    ".swift",
    ".toml",
    ".ts",
    ".tsx",
    ".vue",
    ".xml",
    ".yaml",
    ".yml",
    ".zsh",
}

TEXT_PREVIEW_FILENAMES = {
    ".dockerignore",
    ".env",
    ".env.example",
    ".gitignore",
    ".npmrc",
    ".prettierrc",
    "dockerfile",
    "makefile",
}


def _validate_path(rel_path: str) -> None:
    """Ensure the path doesn't contain traversal components."""
    if ".." in rel_path.split("/"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Path traversal not allowed",
        )


def _visible_path(agent_id: uuid.UUID, rel_path: str, tenant_id: uuid.UUID | None) -> tuple[Path, Path, bool]:
    """Resolve an agent-visible path, including virtual enterprise_info/."""
    try:
        resolved = resolve_agent_visible_path(
            _agent_base_dir(agent_id),
            rel_path,
            workspace_root=Path(settings.AGENT_DATA_DIR),
            tenant_id=str(tenant_id) if tenant_id else None,
        )
    except WorkspacePathError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return resolved.path, resolved.relative_root, resolved.is_enterprise


def _is_enterprise_visible_path(rel_path: str) -> bool:
    normalized = (rel_path or "").strip().strip("/")
    return normalized == "enterprise_info" or normalized.startswith("enterprise_info/")


def _visible_storage_key(agent_id: uuid.UUID, rel_path: str, tenant_id: uuid.UUID | None) -> tuple[str, bool]:
    normalized = (rel_path or "").strip().strip("/")
    if _is_enterprise_visible_path(normalized):
        if not tenant_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No tenant associated")
        sub_path = normalized[len("enterprise_info"):].lstrip("/")
        return _enterprise_storage_key(str(tenant_id), sub_path), True
    return _agent_storage_key(agent_id, normalized), False


async def _require_agent_file_delete_access(
    db: AsyncSession,
    current_user: User,
    agent_id: uuid.UUID,
) -> None:
    """Allow destructive workspace file operations only for managers/admins."""
    _agent, access_level = await check_agent_access(db, current_user, agent_id)
    if access_level == "manage" or current_user.role in ("platform_admin", "org_admin", "super_admin"):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Only agent managers or admins can delete files",
    )


@router.get("/", response_model=list[FileInfo])
async def list_files(
    agent_id: uuid.UUID,
    path: str = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List files and directories in an agent's file system."""
    await check_agent_access(db, current_user, agent_id)
    if path:
        _validate_path(path)

    storage = get_storage()
    prefix = f"{agent_id}/{path}" if path else str(agent_id)
    agent_prefix = f"{agent_id}/"

    items = await storage.list(prefix)
    result = []
    for item in items:
        rel = item.path.removeprefix(agent_prefix)
        result.append(FileInfo(
            name=item.name,
            path=rel,
            is_dir=item.is_dir,
            size=item.size,
            modified_at=item.modified_at,
            url=f"/api/agents/{agent_id}/files/download?path={rel}" if not item.is_dir else None,
        ))
    return result


@router.get("/content", response_model=FileContent)
async def read_file(
    agent_id: uuid.UUID,
    path: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read the content of a file."""
    await check_agent_access(db, current_user, agent_id)
    _validate_path(path)

    storage = get_storage()
    key = f"{agent_id}/{path}"
    try:
        content = await storage.read(key)
    except StorageFileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    except UnicodeDecodeError:
        raw = await storage.read_bytes(key)
        return FileContent(path=path, content=f"[Binary file: {Path(path).name}, {len(raw)} bytes]")
    return FileContent(path=path, content=content)


@router.get("/download")
async def download_file(
    agent_id: uuid.UUID,
    path: str,
    token: str = "",
    inline: bool = False,
    credentials: HTTPAuthorizationCredentials | None = Depends(HTTPBearer(auto_error=False)),
    db: AsyncSession = Depends(get_db),
):
    """Download / serve a file from the agent workspace (browser-friendly).

    Auth via Bearer header OR `token` query parameter (for <img> tags).
    """
    from app.core.security import decode_access_token

    # Resolve JWT token from either Bearer header or query param
    jwt_token = None
    if credentials:
        jwt_token = credentials.credentials
    elif token:
        jwt_token = token

    if not jwt_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    payload = decode_access_token(jwt_token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    await check_agent_access(db, user, agent_id)
    _validate_path(path)

    storage = get_storage()
    key = f"{agent_id}/{path}"
    filename = Path(path).name

    # Try presigned URL first (works for S3/cloud backends)
    try:
        url = await storage.get_presigned_url(key)
        return RedirectResponse(url=url)
    except StorageError:
        pass

    # Fallback: read bytes and return as response (works for local backend)
    try:
        data = await storage.read_bytes(key)
    except StorageFileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    mime_type = guess_mime_type(filename)
    headers: dict[str, str] = {}
    if not is_inline_displayable(mime_type):
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(
        content=data,
        media_type=mime_type,
        headers=headers,
    )


@router.put("/content")
async def write_file(
    agent_id: uuid.UUID,
    path: str,
    data: FileWrite,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Write content to a file (create or overwrite)."""
    await check_agent_access(db, current_user, agent_id)
    _validate_path(path)

    storage = get_storage()
    key = f"{agent_id}/{path}"
    await storage.write(key, data.content)


@router.post("/locks")
async def lock_file(
    agent_id: uuid.UUID,
    data: FileLockBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Acquire or refresh a short-lived human editing lock for a file."""
    await check_agent_access(db, current_user, agent_id)
    if is_focus_file_path(data.path):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Focus is stored in the system database.")
    lock = await acquire_edit_lock(
        db,
        agent_id=agent_id,
        path=data.path,
        user_id=current_user.id,
        session_id=data.session_id,
    )
    await db.commit()
    return {"status": "ok", "path": lock.path, "expires_at": lock.expires_at.isoformat()}


@router.delete("/locks")
async def unlock_file(
    agent_id: uuid.UUID,
    path: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Release the current user's edit lock for a file."""
    await check_agent_access(db, current_user, agent_id)
    await release_edit_lock(db, agent_id=agent_id, path=path, user_id=current_user.id)
    await db.commit()
    return {"status": "ok", "path": path}


@router.get("/revisions")
async def get_file_revisions(
    agent_id: uuid.UUID,
    path: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List version history for the currently opened Workspace file."""
    await check_agent_access(db, current_user, agent_id)
    if is_focus_file_path(path):
        return []
    if path.startswith("enterprise_info"):
        return []
    revisions = await list_revisions(db, agent_id=agent_id, path=path)
    return [
        {
            "id": str(rev.id),
            "path": rev.path,
            "operation": rev.operation,
            "actor_type": rev.actor_type,
            "actor_id": str(rev.actor_id) if rev.actor_id else None,
            "session_id": rev.session_id,
            "before_content": rev.before_content,
            "after_content": rev.after_content,
            "created_at": rev.created_at.isoformat() if rev.created_at else None,
            "updated_at": rev.updated_at.isoformat() if rev.updated_at else None,
        }
        for rev in revisions
    ]


@router.post("/restore")
async def restore_file_revision(
    agent_id: uuid.UUID,
    data: RestoreRevisionBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Restore a file to a previous revision's after-content."""
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(WorkspaceFileRevision).where(
            WorkspaceFileRevision.id == data.revision_id,
            WorkspaceFileRevision.agent_id == agent_id,
        )
    )
    revision = result.scalar_one_or_none()
    if not revision:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Revision not found")
    if revision.after_content is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot restore an empty/deleted revision")

    restored = await write_workspace_file(
        db,
        agent_id=agent_id,
        base_dir=_agent_base_dir(agent_id),
        path=revision.path,
        content=revision.after_content,
        actor_type="user",
        actor_id=current_user.id,
        operation="restore",
        enforce_human_lock=False,
        expected_version_token=data.expected_version_token,
    )
    if not restored.ok:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=restored.message)
    await db.commit()
    return {"status": "ok", "path": revision.path, "revision_id": restored.revision_id}


@router.delete("/content")
async def delete_file(
    agent_id: uuid.UUID,
    path: str,
    expected_version_token: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a file or directory."""
    await check_agent_access(db, current_user, agent_id)
    _validate_path(path)

    storage = get_storage()
    key = f"{agent_id}/{path}"

    # Check if it's a file
    if await storage.exists(key):
        await storage.delete(key)
    else:
        # Check if it's a directory (prefix with children)
        items = await storage.list(key)
        if items:
            await storage.delete_prefix(key)
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    return {"status": "ok", "path": path}


class ImportSkillBody(BaseModel):
    skill_id: str


class ImportSkillsBatchBody(BaseModel):
    skill_ids: list[str]


@router.post("/import-skill")
async def import_skill_to_agent(
    agent_id: uuid.UUID,
    body: ImportSkillBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import a global skill into this agent's skills/ workspace folder.

    Copies all files from the global skill registry into
    <agent_workspace>/skills/<folder_name>/.
    """
    await check_agent_access(db, current_user, agent_id)

    from sqlalchemy.orm import selectinload
    from app.models.skill import Skill, SkillFile
    from app.api.skills import _apply_skill_scope

    q = select(Skill).where(Skill.id == body.skill_id).options(selectinload(Skill.files))
    result = await db.execute(_apply_skill_scope(q, current_user))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    if not skill.files:
        raise HTTPException(status_code=400, detail="Skill has no files")

    # Write each file into the agent's workspace via storage
    storage = get_storage()
    written = []
    for f in skill.files:
        # Skip paths with traversal
        if ".." in f.path.split("/"):
            continue
        key = f"{agent_id}/skills/{skill.folder_name}/{f.path}"
        await storage.write(key, f.content)
        written.append(f.path)

    return {
        "status": "ok",
        "skill_name": skill.name,
        "folder_name": skill.folder_name,
        "files_written": len(written),
        "files": written,
    }


@router.post("/import-skills-batch")
async def import_skills_batch_to_agent(
    agent_id: uuid.UUID,
    body: ImportSkillsBatchBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import multiple global skills into this agent's skills/ workspace folder.

    Accepts an array of skill IDs and imports each one sequentially.
    Returns per-skill results so partial failures are visible to the caller.
    """
    await check_agent_access(db, current_user, agent_id)

    from sqlalchemy.orm import selectinload
    from app.models.skill import Skill
    from app.api.skills import _apply_skill_scope

    if not body.skill_ids:
        return {"results": []}

    storage = get_storage()
    results = []

    for sid in body.skill_ids:
        result_row: dict = {"skill_id": sid, "status": "ok"}

        try:
            q = select(Skill).where(Skill.id == sid).options(selectinload(Skill.files))
            db_result = await db.execute(_apply_skill_scope(q, current_user))
            skill = db_result.scalar_one_or_none()

            if not skill:
                result_row["status"] = "error"
                result_row["error"] = "Skill not found"
                results.append(result_row)
                continue

            result_row["skill_name"] = skill.name
            result_row["folder_name"] = skill.folder_name

            if not skill.files:
                result_row["status"] = "error"
                result_row["error"] = "Skill has no files"
                results.append(result_row)
                continue

            written = []
            for f in skill.files:
                if ".." in f.path.split("/"):
                    continue
                key = f"{agent_id}/skills/{skill.folder_name}/{f.path}"
                await storage.write(key, f.content)
                written.append(f.path)

            result_row["files_written"] = len(written)
            result_row["files"] = written
        except Exception as exc:
            result_row["status"] = "error"
            result_row["error"] = str(exc)

        results.append(result_row)

    return {"results": results}


# Separate router for file uploads (binary) since we need UploadFile
from fastapi import File as FastFile, UploadFile as UploadFileType


upload_router = APIRouter(prefix="/agents/{agent_id}/files", tags=["files"])
DEFAULT_UPLOAD_DIR = "workspace/uploads"


@upload_router.post("/upload")
async def upload_file_to_workspace(
    agent_id: uuid.UUID,
    file: UploadFileType = FastFile(...),
    path: str = "workspace/knowledge_base",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a binary file to agent workspace."""
    await check_agent_access(db, current_user, agent_id)

    normalized_path = (path or "").strip().strip("/")
    if not normalized_path or normalized_path == ".":
        normalized_path = DEFAULT_UPLOAD_DIR

    # Validate path prefix
    if not path.startswith(("workspace/", "skills/")):
        raise HTTPException(status_code=400, detail="Can only upload to workspace/ or skills/ directories")

    _validate_path(path)
    filename = file.filename or "unnamed"
    # Sanitize filename
    filename = filename.replace("/", "_").replace("\\", "_")

    storage = get_storage()
    key = f"{agent_id}/{path}/{filename}"
    content = await file.read()
    await storage.write_bytes(key, content)

    # Auto-extract text from non-text files
    extracted_path = None
    from app.services.text_extractor import needs_extraction, extract_text
    if needs_extraction(filename):
        text = extract_text(content, filename)
        if text and text.strip():
            stem = Path(filename).stem
            txt_key = f"{agent_id}/{path}/{stem}.txt"
            await storage.write(txt_key, text)
            extracted_path = f"{path}/{stem}.txt"

    return {
        "status": "ok",
        "path": f"{normalized_path}/{filename}",
        "url": f"/api/agents/{agent_id}/files/download?path={normalized_path}/{filename}",
        "filename": filename,
        "size": len(content),
        "extracted_text_path": extracted_path,
    }


# ─── Enterprise Knowledge Base ─────────────────────────────────

enterprise_kb_router = APIRouter(prefix="/enterprise/knowledge-base", tags=["enterprise"])


@enterprise_kb_router.get("/files")
async def list_enterprise_kb_files(
    path: str = "",
    current_user: User = Depends(get_current_user),
):
    """List files in enterprise knowledge base (tenant-scoped)."""
    if not current_user.tenant_id:
        return []

    tenant_id = str(current_user.tenant_id)
    if path:
        _validate_path(path)

    storage = get_storage()
    prefix = f"enterprise_info_{tenant_id}/{path}" if path else f"enterprise_info_{tenant_id}"
    ep_prefix = f"enterprise_info_{tenant_id}/"

    items = await storage.list(prefix)
    result = []
    for item in items:
        rel = item.path.removeprefix(ep_prefix)
        result.append({
            "name": item.name,
            "path": rel,
            "is_dir": item.is_dir,
            "size": item.size,
            "url": f"/api/enterprise/knowledge-base/download?path={rel}" if not item.is_dir else None,
        })
    return result


@enterprise_kb_router.post("/upload")
async def upload_enterprise_kb_file(
    file: UploadFileType = FastFile(...),
    sub_path: str = "",
    current_user: User = Depends(get_current_user),
):
    """Upload a file to enterprise knowledge base (tenant-scoped)."""
    # Only admin can upload to enterprise KB
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=403, detail="Only admins can upload to enterprise knowledge base")
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="No tenant associated")

    if sub_path:
        _validate_path(sub_path)

    tenant_id = str(current_user.tenant_id)
    filename = file.filename or "unnamed"
    filename = filename.replace("/", "_").replace("\\", "_")

    storage = get_storage()
    key = f"enterprise_info_{tenant_id}/{sub_path}/{filename}" if sub_path else f"enterprise_info_{tenant_id}/{filename}"
    content = await file.read()
    await storage.write_bytes(key, content)

    # Auto-extract text from non-text files
    extracted_path = None
    from app.services.text_extractor import needs_extraction, extract_text
    if needs_extraction(filename):
        text = extract_text(content, filename)
        if text and text.strip():
            stem = Path(filename).stem
            if sub_path:
                txt_key = f"enterprise_info_{tenant_id}/{sub_path}/{stem}.txt"
            else:
                txt_key = f"enterprise_info_{tenant_id}/{stem}.txt"
            await storage.write(txt_key, text)
            extracted_path = f"{sub_path}/{stem}.txt" if sub_path else f"{stem}.txt"

    rel_path = f"{sub_path}/{filename}" if sub_path else filename
    return {
        "status": "ok",
        "path": rel_path,
        "url": f"/api/enterprise/knowledge-base/download?path={rel_path}",
        "filename": filename,
        "size": len(content),
        "extracted_text_path": extracted_path,
    }


@enterprise_kb_router.get("/content")
async def read_enterprise_file(
    path: str,
    current_user: User = Depends(get_current_user),
):
    """Read content of an enterprise knowledge base file (tenant-scoped)."""
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="No tenant associated")

    _validate_path(path)
    tenant_id = str(current_user.tenant_id)

    storage = get_storage()
    key = f"enterprise_info_{tenant_id}/{path}"
    try:
        content = await storage.read(key)
    except StorageFileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except UnicodeDecodeError:
        raw = await storage.read_bytes(key)
        return {"path": path, "content": f"[Binary file: {Path(path).name}, {len(raw)} bytes]"}
    return {"path": path, "content": content}


@enterprise_kb_router.put("/content")
async def write_enterprise_file(
    path: str,
    data: FileWrite,
    current_user: User = Depends(get_current_user),
):
    """Write content to an enterprise file (tenant-scoped)."""
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=403, detail="Only admins can edit enterprise knowledge base")
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="No tenant associated")

    _validate_path(path)
    tenant_id = str(current_user.tenant_id)

    storage = get_storage()
    key = f"enterprise_info_{tenant_id}/{path}"
    await storage.write(key, data.content)
    return {"status": "ok", "path": path}


@enterprise_kb_router.delete("/content")
async def delete_enterprise_file(
    path: str,
    current_user: User = Depends(get_current_user),
):
    """Delete an enterprise knowledge base file (tenant-scoped)."""
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=403, detail="Only admins can delete enterprise knowledge base files")
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="No tenant associated")

    _validate_path(path)
    tenant_id = str(current_user.tenant_id)

    storage = get_storage()
    key = f"enterprise_info_{tenant_id}/{path}"

    if await storage.exists(key):
        await storage.delete(key)
    else:
        items = await storage.list(key)
        if items:
            await storage.delete_prefix(key)
        else:
            raise HTTPException(status_code=404, detail="File not found")
    return {"status": "ok", "path": path}


# ─── Agent-level ClawHub / URL Skill Import ─────────────────

class ClawhubImportBody(BaseModel):
    slug: str

class UrlImportBody(BaseModel):
    url: str


@router.post("/import-from-clawhub")
async def agent_import_from_clawhub(
    agent_id: uuid.UUID,
    body: ClawhubImportBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import a skill from ClawHub directly into this agent's skills/ workspace."""
    await check_agent_access(db, current_user, agent_id)

    from app.api.skills import (
        _fetch_clawhub_skill_archive, _fetch_clawhub_skill_meta, _get_clawhub_key,
    )

    slug = body.slug
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    api_key = await _get_clawhub_key(tenant_id)

    # 1. Fetch metadata from ClawHub
    try:
        meta, meta_base = await _fetch_clawhub_skill_meta(slug, api_key=api_key)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Failed to connect to ClawHub: {e}")

    skill_info = meta.get("skill", {})

    # 2. Fetch files from the ClawHub archive
    files, _ = await _fetch_clawhub_skill_archive(slug, api_key=api_key, preferred_base=meta_base)

    # 3. Write to agent workspace via storage
    storage = get_storage()
    folder_name = slug

    written = []
    for f in files:
        if ".." in f["path"].split("/"):
            continue
        key = f"{agent_id}/skills/{folder_name}/{f['path']}"
        await storage.write(key, f["content"])
        written.append(f["path"])

    return {
        "status": "ok",
        "skill_name": skill_info.get("displayName", slug),
        "folder_name": folder_name,
        "files_written": len(written),
        "files": written,
    }


@router.post("/import-from-url")
async def agent_import_from_url(
    agent_id: uuid.UUID,
    body: UrlImportBody,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Import a skill from a GitHub URL directly into this agent's skills/ workspace."""
    await check_agent_access(db, current_user, agent_id)

    from app.api.skills import _parse_github_url, _fetch_github_directory, _get_github_token

    parsed = _parse_github_url(body.url)
    if not parsed:
        raise HTTPException(400, "Invalid GitHub URL")

    owner, repo, branch, path = parsed["owner"], parsed["repo"], parsed["branch"], parsed["path"]
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    token = await _get_github_token(tenant_id)
    files = await _fetch_github_directory(owner, repo, path, branch, token)
    if not files:
        raise HTTPException(404, "No files found")

    # Derive folder name
    folder_name = path.rstrip("/").split("/")[-1] if path else repo

    # Write to agent workspace via storage
    storage = get_storage()
    written = []
    for f in files:
        if ".." in f["path"].split("/"):
            continue
        key = f"{agent_id}/skills/{folder_name}/{f['path']}"
        await storage.write(key, f["content"])
        written.append(f["path"])

    return {
        "status": "ok",
        "folder_name": folder_name,
        "files_written": len(written),
        "files": written,
    }
