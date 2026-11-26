import itertools
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.services import gemini, ingest, qa


# ---------- in-memory fake Supabase ----------
class FakeDB:
    def __init__(self):
        self.rows = {"conversations": [], "messages": []}
        self.counter = itertools.count(1)

    def table(self, name):
        return FakeTable(self, name)


class FakeTable:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self._reset()

    def _reset(self):
        self.op, self.row, self.filters, self.order_by, self.max = None, None, [], None, None

    def insert(self, row):
        self._reset()
        self.op, self.row = "insert", dict(row)
        return self

    def select(self, cols="*"):
        self._reset()
        self.op = "select"
        return self

    def eq(self, k, v):
        self.filters.append((k, v))
        return self

    def order(self, col, desc=False):
        self.order_by = (col, desc)
        return self

    def limit(self, n):
        self.max = n
        return self

    def execute(self):
        from types import SimpleNamespace

        table = self.db.rows[self.name]
        if self.op == "insert":
            n = next(self.db.counter)
            row = {"id": str(uuid.uuid4()), "created_at": f"2026-01-01T00:00:{n:02d}+00:00", "citations": [], **self.row}
            table.append(row)
            return SimpleNamespace(data=[row])
        rows = [r for r in table if all(r.get(k) == v for k, v in self.filters)]
        rows.sort(key=lambda r: r["created_at"], reverse=bool(self.order_by and self.order_by[1]))
        return SimpleNamespace(data=rows[: self.max] if self.max else rows)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr("app.routers.ask.get_supabase", lambda: fake)
    monkeypatch.setattr("app.routers.conversations.get_supabase", lambda: fake)
    return fake


@pytest.fixture
def client():
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: "user-1"
    yield TestClient(app)
    app.dependency_overrides.clear()


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


CHUNKS = [
    {"id": "c1", "source_id": "s-pdf", "content": "Revenue grew 10% in Q3.", "source_title": "Annual Report",
     "source_type": "pdf", "source_url": None, "meta": {"page": 7, "filename": "ar.pdf", "chunk_index": 3}},
    {"id": "c2", "source_id": "s-yt", "content": "We talk about margins.", "source_title": "Earnings Call",
     "source_type": "youtube", "source_url": "https://www.youtube.com/watch?v=UF8uR6Z6KLc",
     "meta": {"timestamp_seconds": 142, "video_id": "UF8uR6Z6KLc", "section": "Margins"}},
    {"id": "c3", "source_id": "s-web", "content": "Industry context.", "source_title": "Blog post",
     "source_type": "url", "source_url": "https://example.com/post", "meta": {"url": "https://example.com/post"}},
]


# ---------- citation helpers ----------
@pytest.mark.parametrize(
    "answer,expected",
    [
        ("Grew [1]. Margins [2].", [1, 2]),
        ("Both [2][1].", [2, 1]),
        ("Combined [1, 3].", [1, 3]),
        ("Repeated [1] and [1].", [1]),
        ("Bogus [0] [9] [12].", []),
        ("No citations here.", []),
        ("Array index like list[1] still counts only as bracket text [3]", [1, 3]),
    ],
)
def test_cited_numbers(answer, expected):
    assert qa.cited_numbers(answer, 3) == expected


def test_build_citations_maps_to_real_sources():
    cites = qa.build_citations("PDF [1] video [2] web [3]", CHUNKS)
    assert [c["n"] for c in cites] == [1, 2, 3]
    pdf, yt, web = cites
    assert pdf["source_id"] == "s-pdf" and pdf["title"] == "Annual Report" and pdf["meta"] == {"page": 7}
    assert yt["meta"] == {"timestamp_seconds": 142, "video_id": "UF8uR6Z6KLc", "section": "Margins"}
    assert web["source_url"] == "https://example.com/post" and web["meta"] == {}
    assert "chunk_index" not in pdf["meta"] and "filename" not in pdf["meta"]


def test_only_cited_sources_become_citations():
    assert [c["n"] for c in qa.build_citations("Only [3].", CHUNKS)] == [3]


def test_prompt_numbers_sources_with_locations():
    p = qa.build_prompt(CHUNKS, "What happened?")
    assert '[1] "Annual Report" (pdf, page 7)' in p
    assert '[2] "Earnings Call" (youtube, at 2:22)' in p
    assert '[3] "Blog post" (url)' in p and p.endswith("QUESTION: What happened?")


def test_page_for_chunks_uses_offsets():
    text = "Page one text here.\nStill page one.\nPage two begins now.\nPage three starts."
    offsets = [(0, 1), (text.index("Page two"), 2), (text.index("Page three"), 3)]
    chunks = ["Page one text here.\n\nStill page one.", "Page two begins now.", "Page three starts."]
    assert ingest.page_for_chunks(text, chunks, offsets) == [1, 2, 3]


# ---------- /ask ----------
@pytest.fixture
def llm(monkeypatch):
    calls = {}
    monkeypatch.setattr(qa, "retrieve", lambda uid, q: calls.setdefault("chunks", CHUNKS) and CHUNKS)

    def fake_stream(system, history, prompt):
        calls["system"], calls["history"], calls["prompt"] = system, history, prompt
        yield "Revenue grew 10% "
        yield "[1], and margins were discussed [2]."

    monkeypatch.setattr(gemini, "stream_answer", fake_stream)
    return calls


def test_ask_requires_auth():
    from app.main import app

    assert TestClient(app).post("/ask", json={"question": "hi"}).status_code == 401


def test_ask_streams_and_saves_both_messages(client, db, llm):
    r = client.post("/ask", json={"question": "How did revenue do?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    kinds = [k for k, _ in events]
    assert kinds == ["meta", "delta", "delta", "done"]

    cid = events[0][1]["conversation_id"]
    assert db.rows["conversations"][0]["id"] == cid and db.rows["conversations"][0]["user_id"] == "user-1"
    answer = "".join(d["text"] for k, d in events if k == "delta")
    assert answer == "Revenue grew 10% [1], and margins were discussed [2]."

    done = events[-1][1]
    assert [c["n"] for c in done["citations"]] == [1, 2]
    user_msg, asst_msg = db.rows["messages"]
    assert (user_msg["role"], user_msg["content"]) == ("user", "How did revenue do?")
    assert asst_msg["role"] == "assistant" and asst_msg["content"] == answer
    assert asst_msg["citations"] == done["citations"] and asst_msg["id"] == done["message_id"]
    assert user_msg["created_at"] < asst_msg["created_at"]
    assert llm["system"] == qa.SYSTEM_PROMPT and "ONLY" in qa.SYSTEM_PROMPT


def test_ask_continues_existing_conversation_with_history(client, db, llm):
    cid = client.post("/conversations").json()["id"]
    db.table("messages").insert({"conversation_id": cid, "role": "user", "content": "earlier q"}).execute()
    db.table("messages").insert({"conversation_id": cid, "role": "assistant", "content": "earlier a"}).execute()
    r = client.post("/ask", json={"question": "follow up", "conversation_id": cid})
    assert parse_sse(r.text)[0][1]["conversation_id"] == cid
    assert llm["history"] == [("user", "earlier q"), ("assistant", "earlier a")]
    assert len(db.rows["messages"]) == 4


def test_ask_rejects_other_users_conversation(client, db, llm):
    db.rows["conversations"].append(
        {"id": "11111111-1111-1111-1111-111111111111", "user_id": "someone-else", "created_at": "2026-01-01T00:00:00+00:00"}
    )
    r = client.post("/ask", json={"question": "hi", "conversation_id": "11111111-1111-1111-1111-111111111111"})
    assert r.status_code == 404 and db.rows["messages"] == []


def test_ask_validates_question(client, db):
    assert client.post("/ask", json={"question": ""}).status_code == 422
    assert client.post("/ask", json={"question": "x" * 2001}).status_code == 422


def test_ask_with_no_sources_skips_llm(client, db, monkeypatch):
    monkeypatch.setattr(qa, "retrieve", lambda uid, q: [])
    monkeypatch.setattr(gemini, "stream_answer", lambda *a: pytest.fail("LLM must not be called"))
    events = parse_sse(client.post("/ask", json={"question": "anything?"}).text)
    assert events[1][1]["text"] == qa.NO_SOURCES_ANSWER
    assert events[-1][0] == "done" and events[-1][1]["citations"] == []
    assert len(db.rows["messages"]) == 2


def test_gemini_failure_mid_stream_sends_error_and_saves_nothing(client, db, monkeypatch):
    monkeypatch.setattr(qa, "retrieve", lambda uid, q: CHUNKS)

    def broken(system, history, prompt):
        yield "partial answer "
        raise gemini.GeminiError("Gemini rate limit or quota exceeded while writing the answer.")

    monkeypatch.setattr(gemini, "stream_answer", broken)
    events = parse_sse(client.post("/ask", json={"question": "q"}).text)
    assert [k for k, _ in events] == ["meta", "delta", "error"]
    assert "rate limit" in events[-1][1]["message"]
    assert db.rows["messages"] == []


def test_retrieval_failure_is_reported_not_raised(client, db, monkeypatch):
    def boom(uid, q):
        raise RuntimeError("function hybrid_search does not exist")

    monkeypatch.setattr(qa, "retrieve", boom)
    events = parse_sse(client.post("/ask", json={"question": "q"}).text)
    assert events[-1][0] == "error" and "hybrid_search" not in events[-1][1]["message"]  # no internals leaked


# ---------- conversations ----------
def test_create_conversation_and_load_history(client, db, llm):
    created = client.post("/conversations")
    assert created.status_code == 201
    cid = created.json()["id"]
    assert client.get(f"/conversations/{cid}/messages").json() == []

    client.post("/ask", json={"question": "hello", "conversation_id": cid})
    msgs = client.get(f"/conversations/{cid}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["citations"][0]["source_id"] == "s-pdf"


def test_messages_404_for_unowned_conversation(client, db):
    db.rows["conversations"].append(
        {"id": "22222222-2222-2222-2222-222222222222", "user_id": "someone-else", "created_at": "2026-01-01T00:00:00+00:00"}
    )
    assert client.get("/conversations/22222222-2222-2222-2222-222222222222/messages").status_code == 404
    assert client.get(f"/conversations/{uuid.uuid4()}/messages").status_code == 404


def test_conversation_endpoints_require_auth():
    from app.main import app

    c = TestClient(app)
    assert c.post("/conversations").status_code == 401
    assert c.get(f"/conversations/{uuid.uuid4()}/messages").status_code == 401
