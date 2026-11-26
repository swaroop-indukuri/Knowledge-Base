import logging
import re
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from app.auth import get_current_user
from app.db import get_supabase
from app.models import FileUrlOut, SourceCreate, SourceOut
from app.services import ingest, storage, youtube

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sources", tags=["sources"])

# Columns returned to clients (user_id and storage_path stay server-side).
COLUMNS = "id,type,url,title,summary,category,status,error,created_at"

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
# Multipart framing adds a little on top of the file itself.
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 64 * 1024


@router.post("", response_model=SourceOut, status_code=status.HTTP_202_ACCEPTED)
def create_source(
    body: SourceCreate,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_current_user),
) -> dict:
    """Create a 'processing' source and return it immediately; ingestion continues
    in-process after the response is sent. Poll GET /sources/{id} for the result.
    YouTube links become type='youtube'; everything else is a web article."""
    url = str(body.url)

    yt = youtube.parse_youtube_url(url)
    if yt.is_youtube:
        if not yt.video_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "That YouTube link doesn't point to a single video "
                "(playlists, channels and search pages aren't supported)",
            )
        canonical = youtube.canonical_url(yt.video_id)
        row = ingest.create_source(user_id, type="youtube", url=canonical)
        background_tasks.add_task(ingest.process_youtube_source, row["id"], yt.video_id)
        return row

    row = ingest.create_url_source(user_id, url)
    background_tasks.add_task(ingest.process_url_source, row["id"], url)
    return row


@router.post("/upload", response_model=SourceOut, status_code=status.HTTP_202_ACCEPTED)
def upload_source(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user),
) -> dict:
    """Upload a PDF (multipart/form-data, field name 'file'). Validated before anything
    is stored; returns the 'processing' row immediately like POST /sources."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_REQUEST_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File is too large (max 20 MB)")

    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only PDF files are supported")

    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File is too large (max 20 MB)")
    if not data.startswith(b"%PDF-"):  # don't trust the extension or Content-Type
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "That file isn't a valid PDF")

    source_id = ingest.new_source_id()
    path = storage.pdf_path(user_id, source_id)
    display_name = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)[:200] or "Untitled PDF"

    try:
        storage.upload_pdf(path, data)
    except storage.StorageError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from None

    try:
        row = ingest.create_source(
            user_id,
            type="pdf",
            source_id=source_id,
            storage_path=path,
            title=display_name,  # replaced by the generated title when processing finishes
        )
    except Exception:
        storage.delete_file(path)  # don't orphan the file if the row couldn't be created
        raise

    background_tasks.add_task(ingest.process_pdf_source, source_id, data, filename)
    return row


@router.get("", response_model=list[SourceOut])
def list_sources(user_id: str = Depends(get_current_user)) -> list[dict]:
    res = (
        get_supabase()
        .table("sources")
        .select(COLUMNS)
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return res.data


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: UUID, user_id: str = Depends(get_current_user)) -> dict:
    res = (
        get_supabase()
        .table("sources")
        .select(COLUMNS)
        .eq("id", str(source_id))
        .eq("user_id", user_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    return res.data[0]


@router.get("/{source_id}/file", response_model=FileUrlOut)
def get_source_file(source_id: UUID, user_id: str = Depends(get_current_user)) -> FileUrlOut:
    """Short-lived signed URL to the original PDF."""
    res = (
        get_supabase()
        .table("sources")
        .select("storage_path")
        .eq("id", str(source_id))
        .eq("user_id", user_id)  # ownership check: never sign another user's file
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Source not found")
    path = res.data[0].get("storage_path")
    if not path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This source has no uploaded file")
    try:
        url = storage.signed_url(path)
    except storage.StorageError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from None
    return FileUrlOut(url=url, expires_in=storage.SIGNED_URL_TTL_SECONDS)
