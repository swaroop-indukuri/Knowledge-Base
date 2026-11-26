import io
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.auth import get_current_user
from app.services import gemini, ingest, storage


def make_text_pdf(lines: list[str]) -> bytes:
    """Build a minimal valid one-page PDF containing the given hard-wrapped lines."""
    esc = lambda s: s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    body = "BT /F1 10 Tf 40 760 Td 12 TL " + " T* ".join(f"({esc(l)}) Tj" for l in lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(body)} >>\nstream\n{body}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def blank_pdf(password: str | None = None) -> bytes:
    w = PdfWriter()
    w.add_blank_page(612, 792)
    if password:
        w.encrypt(password)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


SENTENCES = [
    "Knowledge management is the practice of capturing and sharing what an",
    "organization knows so that teams can reuse it. Good systems reduce dupli-",
    "cated work and speed up onboarding for new employees across many departments.",
    "Search quality matters because people only reuse knowledge they can actually find",
    "when they need it, which is why embeddings are useful for retrieval tasks.",
]


# ---------- extraction ----------
def test_extract_pdf_reflows_wrapped_lines():
    text, _ = ingest.extract_pdf(make_text_pdf(SENTENCES))
    assert "Knowledge management is the practice of capturing and sharing what an organization knows" in text
    assert "duplicated work" in text  # de-hyphenated across the line break


def test_scanned_pdf_gets_clear_error():
    with pytest.raises(ingest.IngestError, match="scanned or image-only"):
        ingest.extract_pdf(blank_pdf())


def test_corrupt_pdf_gets_clear_error():
    with pytest.raises(ingest.IngestError, match="corrupted"):
        ingest.extract_pdf(b"%PDF-1.4\nthis is not really a pdf")


def test_encrypted_pdf_gets_clear_error():
    with pytest.raises(ingest.IngestError, match="password-protected"):
        ingest.extract_pdf(blank_pdf(password="secret"))


def test_reflow_paragraph_rules():
    out = ingest._reflow("first line of a sen-\ntence that wraps.\nNext sentence here.\n\nNew para")
    assert out.split("\n") == ["first line of a sentence that wraps.", "Next sentence here.", "New para"]


# ---------- pipeline reuse ----------
def test_pdf_pipeline_runs_shared_flow(monkeypatch):
    calls = SimpleNamespace(updates=[], meta=None, ref=None)
    monkeypatch.setattr(ingest, "_update_source", lambda sid, **f: calls.updates.append(f))
    monkeypatch.setattr(ingest, "_insert_chunks", lambda sid, c, e, meta: setattr(calls, "meta", meta))
    monkeypatch.setattr(ingest, "_delete_chunks", lambda sid: None)
    monkeypatch.setattr(gemini, "embed_texts", lambda t, **k: [[0.1] * 768 for _ in t])

    def fake_meta(text, ref):
        calls.ref = ref
        return gemini.SourceMetadata(title="T", summary="S", category="C", tags=[])

    monkeypatch.setattr(gemini, "generate_metadata", fake_meta)
    ingest.process_pdf_source("sid", make_text_pdf(SENTENCES * 3), "notes.pdf")
    assert calls.updates[-1]["status"] == "ready" and calls.updates[-1]["title"] == "T"
    assert calls.meta[0] == {"filename": "notes.pdf", "page": 1} and calls.ref == "PDF file: notes.pdf"


def test_scanned_pdf_pipeline_marks_failed(monkeypatch):
    updates = []
    monkeypatch.setattr(ingest, "_update_source", lambda sid, **f: updates.append(f))
    monkeypatch.setattr(ingest, "_delete_chunks", lambda sid: None)
    ingest.process_pdf_source("sid", blank_pdf(), "scan.pdf")
    assert updates[-1]["status"] == "failed" and "scanned" in updates[-1]["error"]


# ---------- endpoints ----------
@pytest.fixture
def client():
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: "user-1"
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def stubs(monkeypatch):
    s = SimpleNamespace(uploaded=[], deleted=[], created=[], processed=[])
    monkeypatch.setattr(storage, "upload_pdf", lambda path, data: s.uploaded.append((path, data)))
    monkeypatch.setattr(storage, "delete_file", lambda path: s.deleted.append(path))

    def create(user_id, **kw):
        s.created.append((user_id, kw))
        return {"id": kw["source_id"], "type": kw["type"], "title": kw["title"], "status": "processing",
                "created_at": "2026-01-01T00:00:00+00:00"}

    monkeypatch.setattr(ingest, "create_source", create)
    monkeypatch.setattr(ingest, "process_pdf_source", lambda sid, data, name: s.processed.append((sid, name)))
    return s


def upload(client, name="doc.pdf", data=b"%PDF-1.4 hello", ctype="application/pdf"):
    return client.post("/sources/upload", files={"file": (name, data, ctype)})


def test_upload_requires_auth():
    from app.main import app

    assert TestClient(app).post("/sources/upload", files={"file": ("a.pdf", b"%PDF-", "application/pdf")}).status_code == 401


def test_upload_success(client, stubs):
    r = upload(client, name="My Report.PDF")
    assert r.status_code == 202
    body = r.json()
    assert body["type"] == "pdf" and body["status"] == "processing" and body["title"] == "My Report"
    (path, data), = stubs.uploaded
    assert path == f"user-1/{body['id']}.pdf" and data == b"%PDF-1.4 hello"
    user_id, kw = stubs.created[0]
    assert user_id == "user-1" and kw["storage_path"] == path and kw["type"] == "pdf"
    assert stubs.processed == [(body["id"], "My Report.PDF")]


@pytest.mark.parametrize(
    "name,data",
    [("notes.txt", b"%PDF-1.4"), ("fake.pdf", b"<html>not a pdf</html>"), ("noext", b"%PDF-1.4")],
)
def test_upload_rejects_non_pdf_before_storing(client, stubs, name, data):
    r = upload(client, name=name, data=data)
    assert r.status_code == 400 and "PDF" in r.json()["detail"]
    assert stubs.uploaded == [] and stubs.created == []


@pytest.mark.parametrize("extra", [1000, 2 * 1024 * 1024])  # just over the cap / well over
def test_upload_rejects_oversize_before_storing(client, stubs, extra):
    r = upload(client, data=b"%PDF-" + b"0" * (20 * 1024 * 1024 + extra))
    assert r.status_code == 413
    assert stubs.uploaded == []


def test_upload_cleans_up_file_if_row_insert_fails(client, stubs, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(ingest, "create_source", boom)
    r = upload(client)
    assert r.status_code == 500 and r.json() == {"detail": "Internal server error"}  # clean JSON, no crash
    assert stubs.deleted == [stubs.uploaded[0][0]]  # the orphaned file is removed


def test_upload_storage_failure_is_502(client, stubs, monkeypatch):
    def fail(path, data):
        raise storage.StorageError("Could not store the uploaded file.")

    monkeypatch.setattr(storage, "upload_pdf", fail)
    r = upload(client)
    assert r.status_code == 502 and stubs.created == []


# ---------- GET /sources/{id}/file ----------
class FakeQuery:
    def __init__(self, data):
        self.data, self.filters = data, []

    def __getattr__(self, name):
        def method(*a, **k):
            if name == "eq":
                self.filters.append(a)
            return self

        return method

    def execute(self):
        return SimpleNamespace(data=self.data)


def test_file_url_scopes_query_to_owner(client, monkeypatch):
    q = FakeQuery([{"storage_path": "user-1/abc.pdf"}])
    monkeypatch.setattr("app.routers.sources.get_supabase", lambda: SimpleNamespace(table=lambda t: q))
    monkeypatch.setattr(storage, "signed_url", lambda path: f"https://signed.example/{path}?token=t")
    sid = uuid.uuid4()
    r = client.get(f"/sources/{sid}/file")
    assert r.status_code == 200
    assert r.json() == {"url": "https://signed.example/user-1/abc.pdf?token=t", "expires_in": 300}
    assert ("user_id", "user-1") in q.filters and ("id", str(sid)) in q.filters


def test_file_url_404_for_other_users_or_url_sources(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.sources.get_supabase", lambda: SimpleNamespace(table=lambda t: FakeQuery([]))
    )
    assert client.get(f"/sources/{uuid.uuid4()}/file").status_code == 404
    monkeypatch.setattr(
        "app.routers.sources.get_supabase",
        lambda: SimpleNamespace(table=lambda t: FakeQuery([{"storage_path": None}])),
    )
    r = client.get(f"/sources/{uuid.uuid4()}/file")
    assert r.status_code == 404 and "no uploaded file" in r.json()["detail"]
