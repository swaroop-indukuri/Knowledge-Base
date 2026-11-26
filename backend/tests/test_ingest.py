import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from app.auth import get_current_user
from app.services import chunking, gemini, ingest

ARTICLE = "\n".join(
    f"Paragraph {i}. " + ("This is a reasonably long sentence about knowledge management. " * 12)
    for i in range(40)
)


# ---------- chunking ----------
def test_chunks_respect_size_and_cover_text():
    chunks = chunking.chunk_text(ARTICLE)
    assert len(chunks) > 3
    assert all(len(c) <= chunking.MAX_CHARS for c in chunks)
    joined = "\n".join(chunks)
    for i in range(40):
        assert f"Paragraph {i}." in joined


def test_chunks_overlap_consecutive():
    chunks = chunking.chunk_text(ARTICLE)
    overlaps = sum(
        1 for a, b in zip(chunks, chunks[1:]) if b.split("\n\n")[0] in a
    )
    assert overlaps >= len(chunks) - 2  # nearly every boundary carries overlap


def test_oversized_paragraph_is_split():
    huge = "Word " * 3000  # one 15k-char paragraph, no sentence breaks
    chunks = chunking.chunk_text(huge)
    assert len(chunks) >= 2 and all(len(c) <= chunking.MAX_CHARS for c in chunks)


def test_short_text_single_chunk_and_empty():
    assert chunking.chunk_text("Hello world.") == ["Hello world."]
    assert chunking.chunk_text("  \n\n ") == []


# ---------- SSRF guard ----------
@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://localhost:8000/x", "http://169.254.169.254/latest/meta-data",
     "http://10.0.0.5/", "http://[::1]/", "ftp://example.com/file", "file:///etc/passwd"],
)
def test_private_and_non_http_urls_rejected(url):
    with pytest.raises(ingest.IngestError):
        ingest.assert_public_url(url)


# ---------- extraction ----------
def test_extract_article_from_html():
    body = "".join(f"<p>{'Knowledge management matters for teams. ' * 6}Point {i}.</p>" for i in range(8))
    html = (
        f"<html><head><title>My Article</title></head><body><nav>Home About</nav>"
        f"<article><h1>My Article</h1>{body}</article><footer>Copyright</footer></body></html>"
    ).encode()
    text, title = ingest.extract_article(html, "https://example.com/a")
    assert "Knowledge management matters" in text and "Copyright" not in text
    assert title == "My Article"


def test_extract_rejects_empty_page():
    with pytest.raises(ingest.IngestError):
        ingest.extract_article(b"<html><body><p>hi</p></body></html>", "https://example.com")


# ---------- pipeline ----------
@pytest.fixture
def fake_db(monkeypatch):
    calls = SimpleNamespace(updates=[], chunks=None, deleted=0)
    monkeypatch.setattr(ingest, "_update_source", lambda sid, **f: calls.updates.append(f))
    monkeypatch.setattr(
        ingest, "_insert_chunks", lambda sid, chunks, embs, metas: setattr(calls, "chunks", (chunks, embs))
    )
    monkeypatch.setattr(ingest, "_delete_chunks", lambda sid: setattr(calls, "deleted", calls.deleted + 1))
    return calls


def _patch_happy(monkeypatch):
    monkeypatch.setattr(ingest, "fetch_html", lambda url: (b"<html/>", url))
    monkeypatch.setattr(ingest, "extract_article", lambda html, url: (ARTICLE, "Page title"))
    monkeypatch.setattr(gemini, "embed_texts", lambda texts, **kw: [[0.1] * 768 for _ in texts])
    monkeypatch.setattr(
        gemini,
        "generate_metadata",
        lambda text, url: gemini.SourceMetadata(
            title="Great Title", summary="Two sentences.", category="Tech", tags=["a", "b"]
        ),
    )


def test_pipeline_success(monkeypatch, fake_db):
    _patch_happy(monkeypatch)
    ingest.process_url_source("sid", "https://example.com/a")
    assert fake_db.updates[-1] == {
        "title": "Great Title", "summary": "Two sentences.", "category": "Tech",
        "status": "ready", "error": None,
    }
    chunks, embs = fake_db.chunks
    assert len(chunks) == len(embs) > 0
    assert fake_db.deleted == 0


@pytest.mark.parametrize("failing", ["fetch_html", "embed_texts", "generate_metadata"])
def test_pipeline_failure_marks_failed_and_cleans_up(monkeypatch, fake_db, failing):
    _patch_happy(monkeypatch)

    def boom(*a, **k):
        raise (ingest.IngestError("fetch broke") if failing == "fetch_html"
               else gemini.GeminiError("gemini broke"))

    target = ingest if failing == "fetch_html" else gemini
    monkeypatch.setattr(target, failing, boom)
    ingest.process_url_source("sid", "https://example.com/a")
    last = fake_db.updates[-1]
    assert last["status"] == "failed" and "broke" in last["error"]
    assert fake_db.deleted == 1


def test_pipeline_unexpected_error_hides_internals(monkeypatch, fake_db):
    _patch_happy(monkeypatch)
    monkeypatch.setattr(ingest, "extract_article", lambda *a: 1 / 0)
    ingest.process_url_source("sid", "https://example.com/a")
    last = fake_db.updates[-1]
    assert last["status"] == "failed" and last["error"] == "Unexpected error: ZeroDivisionError"


# ---------- gemini retries ----------
def _api_error(code):
    return errors.APIError(code, {"error": {"code": code, "message": "nope", "status": "X"}})


def test_retry_then_success(monkeypatch):
    monkeypatch.setattr(gemini, "_sleep", lambda s: None)
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) < 3:
            raise _api_error(429)
        return "ok"

    assert gemini._call_with_retry(fn, "testing") == "ok" and len(attempts) == 3


def test_rate_limit_exhausted_gives_clear_error(monkeypatch):
    monkeypatch.setattr(gemini, "_sleep", lambda s: None)
    with pytest.raises(gemini.GeminiError, match="rate limit"):
        gemini._call_with_retry(lambda: (_ for _ in ()).throw(_api_error(429)), "embedding")


def test_non_retryable_error_not_retried(monkeypatch):
    monkeypatch.setattr(gemini, "_sleep", lambda s: pytest.fail("should not sleep"))
    with pytest.raises(gemini.GeminiError, match="400"):
        gemini._call_with_retry(lambda: (_ for _ in ()).throw(_api_error(400)), "embedding")


# ---------- endpoints ----------
@pytest.fixture
def client(monkeypatch):
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: "user-1"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_sources_require_auth():
    from app.main import app

    c = TestClient(app)
    assert c.post("/sources", json={"url": "https://example.com"}).status_code == 401
    assert c.get("/sources").status_code == 401
    assert c.get(f"/sources/{uuid.uuid4()}").status_code == 401


def test_post_source_returns_processing_row_and_schedules_work(client, monkeypatch):
    sid = str(uuid.uuid4())
    row = {"id": sid, "user_id": "user-1", "type": "url", "url": "https://example.com/a",
           "status": "processing", "created_at": "2026-01-01T00:00:00+00:00"}
    seen = {}
    monkeypatch.setattr(ingest, "create_url_source", lambda uid, url: seen.update(uid=uid) or row)
    monkeypatch.setattr(ingest, "process_url_source", lambda s, u: seen.update(ran=(s, u)))
    r = client.post("/sources", json={"url": "https://example.com/a"})
    assert r.status_code == 202
    body = r.json()
    assert body["id"] == sid and body["status"] == "processing" and "user_id" not in body
    assert seen["uid"] == "user-1" and seen["ran"] == (sid, "https://example.com/a")


def test_post_source_rejects_bad_url(client):
    assert client.post("/sources", json={"url": "not a url"}).status_code == 422
    assert client.post("/sources", json={"url": "ftp://example.com/x"}).status_code == 422
