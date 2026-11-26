import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.services import gemini, ingest, youtube

VID = "UF8uR6Z6KLc"


# ---------- URL parsing ----------
@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"https://youtube.com/watch?v={VID}&list=PL123&t=42s",
        f"https://m.youtube.com/watch?v={VID}",
        f"https://youtu.be/{VID}?si=tracking",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/embed/{VID}",
        f"https://www.youtube.com/live/{VID}?feature=share",
        f"https://music.youtube.com/watch?v={VID}",
    ],
)
def test_recognises_video_links(url):
    p = youtube.parse_youtube_url(url)
    assert p.is_youtube and p.video_id == VID


@pytest.mark.parametrize(
    "url",
    ["https://www.youtube.com/playlist?list=PL1", "https://www.youtube.com/@somechannel",
     "https://www.youtube.com/watch?v=short", "https://www.youtube.com/"],
)
def test_youtube_links_without_a_video(url):
    p = youtube.parse_youtube_url(url)
    assert p.is_youtube and p.video_id is None


@pytest.mark.parametrize(
    "url",
    [f"https://example.com/watch?v={VID}", f"https://notyoutube.com/watch?v={VID}",
     f"https://youtube.com.evil.example/watch?v={VID}"],
)
def test_non_youtube_hosts_not_matched(url):
    assert not youtube.parse_youtube_url(url).is_youtube


# ---------- timestamps ----------
@pytest.mark.parametrize(
    "text,expected",
    [("00:00", 0), ("00:56", 56), ("5:38", 338), ("1:02:03", 3723), ("[12:34]", 754), ("754", 754),
     ("12:75", None), ("abc", None), ("", None)],
)
def test_parse_timestamp(text, expected):
    assert youtube.parse_timestamp(text) == expected


def test_format_timestamp_round_trip():
    assert youtube.format_timestamp(142) == "2:22"
    assert youtube.format_timestamp(3723) == "1:02:03"
    assert youtube.parse_timestamp(youtube.format_timestamp(3723)) == 3723


# ---------- caption grouping ----------
def snippets(n, step=4.0):
    return [(i * step, step, f"caption line number {i}") for i in range(n)]


def test_group_snippets_makes_time_based_sections():
    segs = youtube.group_snippets(snippets(150))  # 600 seconds, no punctuation
    assert 3 <= len(segs) <= 6
    starts = [s.start_seconds for s in segs]
    assert starts == sorted(starts) and starts[0] == 0
    # sections cap out at MAX_SECONDS even with no natural break
    assert all(b - a <= youtube.MAX_SECONDS + 4 for a, b in zip(starts, starts[1:]))
    assert all(len(s.text) <= youtube.MAX_SECTION_CHARS + 100 for s in segs)


def test_group_snippets_prefers_sentence_breaks_after_target():
    snips = [(i * 10.0, 10.0, "words go here" + ("." if i == 15 else "")) for i in range(30)]
    segs = youtube.group_snippets(snips)
    # first section should end at the sentence end (index 15 -> 160s), not mid-sentence at 120s
    assert segs[1].start_seconds == 160


def test_group_snippets_ignores_blank_and_handles_empty():
    assert youtube.group_snippets([]) == []
    assert youtube.group_snippets([(0.0, 2.0, "  \n ")]) == []


# ---------- extraction: native first, captions as fallback ----------
def _vt():
    return gemini.VideoTranscript(
        title="Great Talk",
        sections=[
            gemini.VideoSection(start="05:38", heading="Second", text="second section text"),
            gemini.VideoSection(start="00:00", heading="Intro", text="intro text"),
            gemini.VideoSection(start="bogus", heading="Bad", text="dropped, unparseable time"),
        ],
    )


def test_native_path_sections_sorted_with_timestamps(monkeypatch):
    monkeypatch.setattr(gemini, "transcribe_youtube", lambda url: _vt())
    ex = ingest.extract_youtube(VID)
    assert [m["timestamp_seconds"] for _, m in ex.chunks] == [0, 338]
    assert ex.chunks[0][0].startswith("Intro\nintro text")
    assert all(m["extraction"] == "gemini" and m["video_id"] == VID for _, m in ex.chunks)
    assert ex.title == "Great Talk"


def test_falls_back_to_captions_when_gemini_fails(monkeypatch):
    def boom(url):
        raise gemini.GeminiError("403 no permission")

    monkeypatch.setattr(gemini, "transcribe_youtube", boom)
    monkeypatch.setattr(youtube, "fetch_title", lambda v: "Caption Title")
    monkeypatch.setattr(
        youtube, "fetch_transcript_segments", lambda v: [youtube.Segment(7, "hello"), youtube.Segment(132, "world")]
    )
    ex = ingest.extract_youtube(VID)
    assert [m["timestamp_seconds"] for _, m in ex.chunks] == [7, 132]
    assert all(m["extraction"] == "captions" for _, m in ex.chunks) and ex.title == "Caption Title"


def test_falls_back_when_gemini_sections_unusable(monkeypatch):
    bad = gemini.VideoTranscript(
        title="x", sections=[gemini.VideoSection(start="??", heading="h", text="t")]
    )
    monkeypatch.setattr(gemini, "transcribe_youtube", lambda url: bad)
    monkeypatch.setattr(youtube, "fetch_title", lambda v: None)
    monkeypatch.setattr(youtube, "fetch_transcript_segments", lambda v: [youtube.Segment(0, "cap")])
    assert ingest.extract_youtube(VID).chunks[0][1]["extraction"] == "captions"


def test_both_paths_failing_gives_combined_clear_error(monkeypatch):
    def boom(url):
        raise gemini.GeminiError("video is private")

    def no_caps(v):
        raise youtube.YouTubeError("captions are disabled for this video")

    monkeypatch.setattr(gemini, "transcribe_youtube", boom)
    monkeypatch.setattr(youtube, "fetch_transcript_segments", no_caps)
    with pytest.raises(ingest.IngestError) as e:
        ingest.extract_youtube(VID)
    assert "video is private" in str(e.value) and "captions are disabled" in str(e.value)


def test_long_section_is_split_but_keeps_its_timestamp():
    long_text = "Sentence about things. " * 400  # ~9k chars
    ex = ingest._segments_to_extracted([youtube.Segment(90, long_text, "Big")], VID, "gemini", None)
    assert len(ex.chunks) >= 3
    assert all(m["timestamp_seconds"] == 90 for _, m in ex.chunks)
    assert all(len(c) <= ingest._MAX_SECTION for c, _ in ex.chunks)


def test_youtube_pipeline_stamps_chunk_meta_and_finishes(monkeypatch):
    calls = SimpleNamespace(updates=[], metas=None)
    monkeypatch.setattr(ingest, "_update_source", lambda sid, **f: calls.updates.append(f))
    monkeypatch.setattr(ingest, "_insert_chunks", lambda sid, c, e, metas: setattr(calls, "metas", metas))
    monkeypatch.setattr(ingest, "_delete_chunks", lambda sid: None)
    monkeypatch.setattr(gemini, "transcribe_youtube", lambda url: _vt())
    monkeypatch.setattr(gemini, "embed_texts", lambda t, **k: [[0.1] * 768 for _ in t])
    monkeypatch.setattr(
        gemini, "generate_metadata",
        lambda text, ref: gemini.SourceMetadata(title="T", summary="S", category="Talks", tags=[]),
    )
    ingest.process_youtube_source("sid", VID)
    assert calls.updates[-1]["status"] == "ready"
    assert calls.metas[0]["timestamp_seconds"] == 0 and calls.metas[0]["url"].endswith(VID)


# ---------- endpoint ----------
@pytest.fixture
def client():
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: "user-1"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_post_youtube_link_creates_youtube_source(client, monkeypatch):
    seen = {}

    def create(user_id, **kw):
        seen["kw"] = kw
        return {"id": str(uuid.uuid4()), "type": kw["type"], "url": kw["url"], "status": "processing",
                "created_at": "2026-01-01T00:00:00+00:00"}

    monkeypatch.setattr(ingest, "create_source", create)
    monkeypatch.setattr(ingest, "process_youtube_source", lambda sid, vid: seen.update(vid=vid))
    monkeypatch.setattr(ingest, "process_url_source", lambda *a: pytest.fail("must not use the article path"))
    r = client.post("/sources", json={"url": f"https://youtu.be/{VID}?si=x"})
    assert r.status_code == 202
    assert r.json()["type"] == "youtube" and r.json()["url"] == f"https://www.youtube.com/watch?v={VID}"
    assert seen["vid"] == VID and seen["kw"]["type"] == "youtube"


def test_post_playlist_link_is_400(client):
    r = client.post("/sources", json={"url": "https://www.youtube.com/playlist?list=PL1"})
    assert r.status_code == 400 and "single video" in r.json()["detail"]
