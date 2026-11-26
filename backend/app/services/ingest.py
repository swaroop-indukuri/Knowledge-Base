"""Ingestion pipeline: extract -> chunk -> embed -> summarize -> save.

Both source types share `_run_pipeline`; they differ only in how text is extracted:
  - URL: fetch the page, extract the article body with trafilatura
  - PDF: extract text with pypdf

trafilatura (URLs) is purpose-built for pulling the main article out of
boilerplate-heavy pages. pypdf (PDFs) is pure Python, permissively licensed and
fast; it handles text-based PDFs, and scanned PDFs are detected and rejected with
a clear message (no OCR).
"""

import io
import ipaddress
import logging
import re
import socket
import uuid
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from pypdf import PdfReader

from app.db import get_supabase
from app.services import gemini, youtube
from app.services.chunking import MAX_CHARS, chunk_text

log = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 5_000_000
MAX_REDIRECTS = 5
FETCH_TIMEOUT = 20.0
MIN_TEXT_CHARS = 200
MAX_CHUNKS = 150  # caps embedding cost on very long documents
MAX_PDF_PAGES = 500
DB_BATCH = 50
# Many sites (news sites, Wikipedia) 403 obviously-automated user agents.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 KnowledgeBase/0.1"
)


class IngestError(Exception):
    """Ingestion failed; the message is safe to show to the user."""


# ---------------------------------------------------------------------------
# URL: fetching (with SSRF protection: the URL is user-supplied, fetched server-side)
# ---------------------------------------------------------------------------
def assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise IngestError("Only http(s) URLs are supported")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise IngestError(f"Could not resolve host '{parsed.hostname}'") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise IngestError("That URL points to a private or internal address")


def fetch_html(url: str) -> tuple[bytes, str]:
    """Download a page, validating every redirect hop. Returns (body, final_url)."""
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        with httpx.Client(follow_redirects=False, timeout=FETCH_TIMEOUT, headers=headers) as client:
            for _ in range(MAX_REDIRECTS + 1):
                assert_public_url(url)
                with client.stream("GET", url) as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            raise IngestError("Page redirected without a destination")
                        url = urljoin(url, location)
                        continue
                    if resp.status_code >= 400:
                        raise IngestError(f"The page returned HTTP {resp.status_code}")
                    ctype = resp.headers.get("content-type", "").lower()
                    if ctype and not any(t in ctype for t in ("html", "xml", "text/plain")):
                        raise IngestError(f"Unsupported content type '{ctype.split(';')[0]}'")
                    body = bytearray()
                    for part in resp.iter_bytes():
                        body.extend(part)
                        if len(body) > MAX_DOWNLOAD_BYTES:
                            raise IngestError("Page is too large to process")
                    return bytes(body), url
            raise IngestError("Too many redirects")
    except httpx.TimeoutException:
        raise IngestError("Timed out fetching the page") from None
    except httpx.HTTPError as e:
        raise IngestError(f"Could not fetch the page: {e}") from None


def extract_article(html: bytes, url: str) -> tuple[str, str | None]:
    """Return (clean_text, page_title)."""
    text = trafilatura.extract(
        html, url=url, include_comments=False, include_tables=True, output_format="txt"
    )
    if not text or len(text.strip()) < MIN_TEXT_CHARS:
        raise IngestError("Couldn't extract readable article text from that page")
    meta = trafilatura.extract_metadata(html, default_url=url)
    return text.strip(), (meta.title if meta else None)


# ---------------------------------------------------------------------------
# PDF: text extraction
# ---------------------------------------------------------------------------
_SENTENCE_END = re.compile(r"[.!?:\"'”’)\]]$")


def _reflow(raw: str) -> str:
    """PDF text comes hard-wrapped at line width. Re-join wrapped lines into
    paragraphs (a blank line, or a line ending in sentence punctuation, ends one)."""
    paragraphs: list[str] = []
    buf = ""
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            if buf:
                paragraphs.append(buf)
                buf = ""
            continue
        if buf.endswith("-") and line[:1].islower():
            buf = buf[:-1] + line  # de-hyphenate "knowl-\nedge"
        else:
            buf = f"{buf} {line}" if buf else line
        if _SENTENCE_END.search(buf):
            paragraphs.append(buf)
            buf = ""
    if buf:
        paragraphs.append(buf)
    return "\n".join(paragraphs)


def extract_pdf_with_pages(data: bytes) -> tuple[str, str | None, list[tuple[int, int]]]:
    """Return (clean_text, pdf_title, page_offsets) where page_offsets is an ascending
    list of (character_offset_in_text, page_number). Raises IngestError for unreadable,
    encrypted or scanned/image-only PDFs."""
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise IngestError("This PDF is password-protected and can't be read")
        num_pages = len(reader.pages)
        if num_pages > MAX_PDF_PAGES:
            raise IngestError(f"This PDF has {num_pages} pages; the limit is {MAX_PDF_PAGES}")
        pages = [page.extract_text() or "" for page in reader.pages]
        title = reader.metadata.title if reader.metadata else None
    except IngestError:
        raise
    except Exception:  # noqa: BLE001 - pypdf raises many types on corrupt files
        log.exception("pypdf failed to read a PDF")
        raise IngestError("Couldn't read this PDF - the file may be corrupted") from None

    parts: list[str] = []
    offsets: list[tuple[int, int]] = []
    pos = 0
    for page_number, page_text in enumerate(pages, start=1):
        reflowed = _reflow(page_text)
        if not reflowed:
            continue
        offsets.append((pos, page_number))
        parts.append(reflowed)
        pos += len(reflowed) + 1  # +1 for the "\n" joining pages
    text = "\n".join(parts)

    # Scanned PDFs yield little or no text; scale the bar with page count.
    if len(text) < max(MIN_TEXT_CHARS, 20 * num_pages):
        raise IngestError(
            "This PDF looks scanned or image-only, so there's no text to extract. "
            "Scanned documents (OCR) aren't supported yet - try a text-based PDF."
        )
    return text, (title.strip() if title and title.strip() else None), offsets


def extract_pdf(data: bytes) -> tuple[str, str | None]:
    text, title, _ = extract_pdf_with_pages(data)
    return text, title


def page_for_chunks(text: str, chunks: list[str], offsets: list[tuple[int, int]]) -> list[int]:
    """Page number each chunk starts on, found by locating its opening text."""
    starts = [o for o, _ in offsets]
    pages: list[int] = []
    search_from = 0
    last = offsets[0][1] if offsets else 1
    for chunk in chunks:
        probe = chunk.split("\n\n")[0][:80]
        idx = text.find(probe, search_from)
        if idx != -1:
            search_from = idx + 1
            last = offsets[max(bisect_right(starts, idx) - 1, 0)][1]
        pages.append(last)
    return pages


# ---------------------------------------------------------------------------
# Database helpers (service-role client: every query must be scoped by user/source)
# ---------------------------------------------------------------------------
def create_source(
    user_id: str,
    *,
    type: str,
    source_id: str | None = None,
    url: str | None = None,
    storage_path: str | None = None,
    title: str | None = None,
) -> dict:
    row = {"user_id": user_id, "type": type, "status": "processing"}
    optional = {"id": source_id, "url": url, "storage_path": storage_path, "title": title}
    row.update({k: v for k, v in optional.items() if v is not None})
    return get_supabase().table("sources").insert(row).execute().data[0]


def create_url_source(user_id: str, url: str) -> dict:
    return create_source(user_id, type="url", url=url)


def new_source_id() -> str:
    return str(uuid.uuid4())


def _update_source(source_id: str, **fields) -> None:
    get_supabase().table("sources").update(fields).eq("id", source_id).execute()


def _insert_chunks(
    source_id: str, chunks: list[str], embeddings: list[list[float]], metas: list[dict]
) -> None:
    rows = [
        {
            "source_id": source_id,
            "content": content,
            # pgvector text input format; works for halfvec columns via PostgREST.
            "embedding": "[" + ",".join(f"{x:.7g}" for x in vec) + "]",
            "meta": {**meta, "chunk_index": i},
        }
        for i, (content, vec, meta) in enumerate(zip(chunks, embeddings, metas))
    ]
    table = get_supabase().table("chunks")
    for i in range(0, len(rows), DB_BATCH):
        table.insert(rows[i : i + DB_BATCH]).execute()


def _delete_chunks(source_id: str) -> None:
    get_supabase().table("chunks").delete().eq("source_id", source_id).execute()


# ---------------------------------------------------------------------------
# Shared pipeline
# ---------------------------------------------------------------------------
@dataclass
class Extracted:
    """Result of an extraction step."""

    text: str  # full clean text (used for the summary)
    title: str | None = None  # fallback title if Gemini doesn't produce one
    # Pre-built chunks as (content, meta), e.g. YouTube sections with timestamps.
    # None means: split `text` with the generic paragraph chunker.
    chunks: list[tuple[str, dict]] | None = None


def _run_pipeline(
    source_id: str,
    extract: Callable[[], Extracted],
    ref: str,
    base_meta: dict,
) -> None:
    """Run the pipeline for an existing 'processing' row.

    `extract` returns an Extracted. `ref` labels the source in the Gemini prompt.
    Always ends with the row 'ready' or 'failed' - never left 'processing'.
    """
    try:
        extracted = extract()

        if extracted.chunks is not None:
            pieces = extracted.chunks
        else:
            pieces = [(c, {}) for c in chunk_text(extracted.text)]
        pieces = pieces[:MAX_CHUNKS]
        if not pieces:
            raise IngestError("The document contained no usable text")
        chunks = [content for content, _ in pieces]
        metas = [{**base_meta, **m} for _, m in pieces]

        embeddings = gemini.embed_texts(chunks)
        _insert_chunks(source_id, chunks, embeddings, metas)

        meta = gemini.generate_metadata(extracted.text, ref)
        _update_source(
            source_id,
            title=meta.title or extracted.title or ref,
            summary=meta.summary,
            category=meta.category,
            status="ready",
            error=None,
        )
    except Exception as e:  # noqa: BLE001 - we must record every failure
        if isinstance(e, (IngestError, gemini.GeminiError, youtube.YouTubeError)):
            message = str(e)
        else:
            log.exception("Unexpected error ingesting source %s", source_id)
            message = f"Unexpected error: {type(e).__name__}"
        try:
            _delete_chunks(source_id)  # don't leave a half-embedded source behind
        except Exception:  # noqa: BLE001
            log.exception("Could not clean up chunks for %s", source_id)
        try:
            _update_source(source_id, status="failed", error=message[:1000])
        except Exception:  # noqa: BLE001
            log.exception("Could not mark source %s as failed", source_id)


def process_url_source(source_id: str, url: str) -> None:
    def extract() -> Extracted:
        html, final_url = fetch_html(url)
        text, title = extract_article(html, final_url)
        return Extracted(text, title)

    _run_pipeline(source_id, extract, ref=f"URL: {url}", base_meta={"url": url})


def process_pdf_source(source_id: str, data: bytes, filename: str) -> None:
    def extract() -> Extracted:
        text, title, offsets = extract_pdf_with_pages(data)
        chunks = chunk_text(text)
        pages = page_for_chunks(text, chunks, offsets)
        # Each chunk records the page it starts on, so answers can cite "p. 12".
        return Extracted(text, title, chunks=[(c, {"page": p}) for c, p in zip(chunks, pages)])

    _run_pipeline(source_id, extract, ref=f"PDF file: {filename}", base_meta={"filename": filename})


# ---------------------------------------------------------------------------
# YouTube
# ---------------------------------------------------------------------------
_MAX_SECTION = MAX_CHARS  # same per-chunk size cap as other types


def _segments_to_extracted(
    segments: list[youtube.Segment], video_id: str, method: str, title: str | None
) -> Extracted:
    """One chunk per section (split only if a section exceeds the size cap), each
    stamped with the section's start time so citations can deep-link into the video."""
    pieces: list[tuple[str, dict]] = []
    for seg in sorted(segments, key=lambda s: s.start_seconds):
        content = f"{seg.heading}\n{seg.text}" if seg.heading else seg.text
        parts = [content] if len(content) <= _MAX_SECTION else chunk_text(content)
        meta = {
            "timestamp_seconds": seg.start_seconds,
            "video_id": video_id,
            "extraction": method,
            **({"section": seg.heading} if seg.heading else {}),
        }
        pieces.extend((p, dict(meta)) for p in parts)
    text = "\n\n".join(c for c, _ in pieces)
    return Extracted(text=text, title=title, chunks=pieces)


def _gemini_segments(video_id: str) -> tuple[list[youtube.Segment], str | None]:
    vt = gemini.transcribe_youtube(youtube.canonical_url(video_id))
    segments = []
    for s in vt.sections:
        start = youtube.parse_timestamp(s.start)
        if start is None or not s.text.strip():
            continue
        segments.append(youtube.Segment(start, s.text.strip(), s.heading.strip() or None))
    if not segments:
        raise youtube.YouTubeError("Gemini's sections had no usable timestamps")
    return segments, vt.title.strip() or None


def extract_youtube(video_id: str) -> Extracted:
    """Primary: Gemini watches the video. Fallback: captions via youtube-transcript-api."""
    try:
        segments, title = _gemini_segments(video_id)
        return _segments_to_extracted(segments, video_id, "gemini", title or youtube.fetch_title(video_id))
    except (gemini.GeminiError, youtube.YouTubeError) as native_err:
        log.warning("Gemini couldn't process video %s (%s); trying captions", video_id, native_err)
        try:
            segments = youtube.fetch_transcript_segments(video_id)
        except youtube.YouTubeError as caption_err:
            raise IngestError(
                f"Couldn't process this video. Gemini failed ({native_err}) and "
                f"the caption fallback failed too: {caption_err}."
            ) from None
        return _segments_to_extracted(segments, video_id, "captions", youtube.fetch_title(video_id))


def process_youtube_source(source_id: str, video_id: str) -> None:
    url = youtube.canonical_url(video_id)
    _run_pipeline(
        source_id,
        lambda: extract_youtube(video_id),
        ref=f"YouTube video: {url}",
        base_meta={"url": url, "video_id": video_id},
    )
