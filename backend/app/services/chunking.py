"""Paragraph-aware text chunking (~500-800 tokens, ~15% overlap).

Token counts are estimated at ~4 characters per token (no tokenizer dependency),
which is accurate enough for English prose at these sizes.
"""

import re

CHARS_PER_TOKEN = 4
MAX_CHARS = 800 * CHARS_PER_TOKEN       # hard cap per chunk (~800 tokens)
MIN_CHARS = 500 * CHARS_PER_TOKEN       # try to reach this before closing a chunk
OVERLAP_CHARS = int(0.15 * 650 * CHARS_PER_TOKEN)  # ~15% of a typical chunk

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_long(unit: str) -> list[str]:
    """Break an oversized paragraph into sentences, then hard-wrap if still too long."""
    pieces: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(unit):
        while len(sentence) > MAX_CHARS:
            cut = sentence.rfind(" ", 0, MAX_CHARS)
            cut = cut if cut > 0 else MAX_CHARS
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            pieces.append(sentence)
    return pieces


def _tail(unit: str) -> str:
    """Trailing sentence(s) of `unit` totalling at most OVERLAP_CHARS."""
    picked: list[str] = []
    total = 0
    for sentence in reversed(_SENTENCE_SPLIT.split(unit)):
        if total + len(sentence) > OVERLAP_CHARS:
            break
        picked.insert(0, sentence)
        total += len(sentence) + 1
    if picked:
        return " ".join(picked)
    # A single sentence longer than the budget: take its last words.
    cut = unit[-OVERLAP_CHARS:]
    space = cut.find(" ")
    return cut[space + 1 :] if space != -1 else cut


def chunk_text(text: str) -> list[str]:
    units: list[str] = []
    for para in re.split(r"\n+", text):
        para = para.strip()
        if not para:
            continue
        units.extend([para] if len(para) <= MAX_CHARS else _split_long(para))

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    has_new = False  # current holds something beyond carried-over overlap

    def emit() -> None:
        nonlocal current, size, has_new
        chunks.append("\n\n".join(current))
        # Carry the tail of this chunk into the next one as overlap.
        overlap: list[str] = []
        total = 0
        for u in reversed(current):
            if total + len(u) > OVERLAP_CHARS:
                break
            overlap.insert(0, u)
            total += len(u) + 2
        if not overlap:
            # Last paragraph alone exceeds the overlap budget: carry its trailing sentences.
            tail = _tail(current[-1])
            overlap, total = [tail], len(tail) + 2
        current, size, has_new = overlap, total, False

    for unit in units:
        if current:
            too_big = size + len(unit) + 2 > MAX_CHARS
            # Close the chunk at a paragraph boundary once it's big enough, or if the
            # next unit wouldn't fit. Never emit a chunk made only of carried overlap.
            if has_new and (too_big or size >= MIN_CHARS):
                emit()
            if current and size + len(unit) + 2 > MAX_CHARS:
                current, size, has_new = [], 0, False  # overlap + unit too big: drop overlap
        current.append(unit)
        size += len(unit) + 2
        has_new = True

    if current and has_new:
        chunks.append("\n\n".join(current))
    return chunks
