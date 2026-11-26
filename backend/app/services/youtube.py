"""YouTube helpers: link parsing, timestamp parsing, transcript fallback, sectioning.

Extraction strategy (see ingest.process_youtube_source):
  1. Primary: hand the video URL to Gemini, get sections with start times back.
  2. Fallback: fetch captions with youtube-transcript-api and group them into
     time-based sections. Needs no Gemini video quota, but YouTube blocks most
     cloud-provider IPs, so it works best when running locally or via a proxy.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx

log = logging.getLogger(__name__)

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
             "youtube-nocookie.com", "www.youtube-nocookie.com", "youtu.be", "www.youtu.be"}

# Transcript grouping: aim for ~2 minute sections, never more than ~4 minutes.
TARGET_SECONDS = 120
MAX_SECONDS = 240
PAUSE_SECONDS = 1.5
MAX_SECTION_CHARS = 3000


class YouTubeError(Exception):
    """A YouTube-specific step failed; message is safe to show to the user."""


@dataclass
class Segment:
    start_seconds: int
    text: str
    heading: str | None = None


@dataclass
class ParsedYouTube:
    is_youtube: bool
    video_id: str | None = None


def parse_youtube_url(url: str) -> ParsedYouTube:
    """Recognise YouTube links. `is_youtube` is True for any YouTube host; `video_id`
    is set only when the link points at one specific video."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in _YT_HOSTS:
        return ParsedYouTube(False)

    candidate: str | None = None
    parts = [p for p in parsed.path.split("/") if p]
    if host.endswith("youtu.be"):
        candidate = parts[0] if parts else None
    elif parts and parts[0] == "watch":
        candidate = (parse_qs(parsed.query).get("v") or [None])[0]
    elif len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
        candidate = parts[1]

    if candidate and _VIDEO_ID.match(candidate):
        return ParsedYouTube(True, candidate)
    return ParsedYouTube(True, None)


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


_TS = re.compile(r"^\s*\[?(?:(\d{1,2}):)?(\d{1,3}):(\d{2})\]?\s*$")


def parse_timestamp(value: str) -> int | None:
    """'1:02:03' / '12:34' / '[12:34]' / '754' -> seconds, or None if unparseable."""
    value = (value or "").strip()
    if value.isdigit():
        return int(value)
    m = _TS.match(value)
    if not m:
        return None
    hours, minutes, seconds = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    if seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def format_timestamp(seconds: int) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fetch_title(video_id: str) -> str | None:
    """Video title via YouTube's public oEmbed endpoint (best effort)."""
    try:
        r = httpx.get(
            "https://www.youtube.com/oembed",
            params={"url": canonical_url(video_id), "format": "json"},
            timeout=10,
        )
        if r.status_code == 200:
            return (r.json().get("title") or "").strip() or None
    except (httpx.HTTPError, ValueError):
        pass
    return None


# ---------------------------------------------------------------------------
# Fallback: captions via youtube-transcript-api
# ---------------------------------------------------------------------------
def group_snippets(snippets: list[tuple[float, float, str]]) -> list[Segment]:
    """Group (start, duration, text) caption snippets into time-based sections.

    A section closes after TARGET_SECONDS at a natural break (sentence end or a
    pause in speech), or unconditionally at MAX_SECONDS / MAX_SECTION_CHARS.
    """
    segments: list[Segment] = []
    cur: list[str] = []
    seg_start = 0.0
    chars = 0

    def close() -> None:
        nonlocal cur, chars
        text = " ".join(cur).strip()
        if text:
            segments.append(Segment(int(seg_start), text))
        cur, chars = [], 0

    for i, (start, dur, raw) in enumerate(snippets):
        text = re.sub(r"\s+", " ", raw.replace("\n", " ")).strip()
        if not text:
            continue
        if not cur:
            seg_start = start
        cur.append(text)
        chars += len(text) + 1

        end = start + dur
        elapsed = end - seg_start
        next_start = snippets[i + 1][0] if i + 1 < len(snippets) else None
        natural_break = text[-1] in ".?!" or (next_start is not None and next_start - end >= PAUSE_SECONDS)
        if (elapsed >= TARGET_SECONDS and natural_break) or elapsed >= MAX_SECONDS or chars >= MAX_SECTION_CHARS:
            close()
    close()
    return segments


def fetch_transcript_segments(video_id: str) -> list[Segment]:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api import _errors as yt_errors

    api = YouTubeTranscriptApi()
    try:
        transcripts = api.list(video_id)
        try:
            transcript = transcripts.find_transcript(["en", "en-US", "en-GB"])
        except yt_errors.NoTranscriptFound:
            transcript = next(iter(transcripts))  # any available language
        fetched = transcript.fetch()
    except yt_errors.TranscriptsDisabled:
        raise YouTubeError("captions are disabled for this video") from None
    except (yt_errors.RequestBlocked, yt_errors.IpBlocked):
        raise YouTubeError(
            "YouTube blocked the caption request from this server's IP address"
        ) from None
    except yt_errors.AgeRestricted:
        raise YouTubeError("the video is age-restricted") from None
    except yt_errors.VideoUnavailable:
        raise YouTubeError("the video is unavailable (private, removed or region-locked)") from None
    except (yt_errors.NoTranscriptFound, StopIteration):
        raise YouTubeError("the video has no captions") from None
    except Exception as e:  # noqa: BLE001
        log.exception("Transcript fetch failed for %s", video_id)
        raise YouTubeError(f"captions could not be fetched ({type(e).__name__})") from None

    segments = group_snippets([(s.start, s.duration, s.text) for s in fetched])
    if not segments:
        raise YouTubeError("the video's captions are empty")
    return segments
