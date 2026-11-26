"""Supabase Storage helpers for original uploaded files (private bucket)."""

import logging

from app.db import get_supabase

log = logging.getLogger(__name__)

BUCKET = "sources"
SIGNED_URL_TTL_SECONDS = 300


class StorageError(Exception):
    """Storage operation failed; message is safe to show to the user."""


def pdf_path(user_id: str, source_id: str) -> str:
    # Namespaced per user: {user_id}/{source_id}.pdf
    return f"{user_id}/{source_id}.pdf"


def upload_pdf(path: str, data: bytes) -> None:
    try:
        get_supabase().storage.from_(BUCKET).upload(
            path, data, {"content-type": "application/pdf"}
        )
    except Exception as e:  # noqa: BLE001
        log.exception("Storage upload failed for %s", path)
        raise StorageError(
            "Could not store the uploaded file. Check that the 'sources' storage bucket exists."
        ) from e


def delete_file(path: str) -> None:
    try:
        get_supabase().storage.from_(BUCKET).remove([path])
    except Exception:  # noqa: BLE001
        log.exception("Could not delete stored file %s", path)


def signed_url(path: str) -> str:
    try:
        res = get_supabase().storage.from_(BUCKET).create_signed_url(path, SIGNED_URL_TTL_SECONDS)
    except Exception as e:  # noqa: BLE001
        log.exception("Could not sign URL for %s", path)
        raise StorageError("Could not create a link to the original file") from e
    url = res.get("signedURL") or res.get("signedUrl")
    if not url:
        raise StorageError("Could not create a link to the original file")
    return url
