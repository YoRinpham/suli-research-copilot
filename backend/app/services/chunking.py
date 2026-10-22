import re
from dataclasses import dataclass

from app.services.pdf import ExtractedPage


@dataclass(frozen=True)
class ChunkDraft:
    page_number: int
    chunk_index: int
    section: str | None
    text: str


SENTENCE_PATTERN = re.compile(r"[^.!?。！？]+[.!?。！？]?\s*", re.S)


def _sentence_spans(text: str, chunk_size: int) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in SENTENCE_PATTERN.finditer(text):
        start, end = match.span()
        if not match.group().strip():
            continue
        while end - start > chunk_size:
            window = text[start:start + chunk_size]
            boundaries = [space.end() for space in re.finditer(r"\s+", window)]
            cut = start + (boundaries[-1] if boundaries and window[:boundaries[-1]].strip() else chunk_size)
            spans.append((start, cut))
            start = cut
        if text[start:end].strip():
            spans.append((start, end))
    return spans


def chunk_pages(pages: list[ExtractedPage], chunk_size: int, overlap: int) -> list[ChunkDraft]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Invalid chunk size or overlap")
    chunks: list[ChunkDraft] = []
    for page in pages:
        spans = _sentence_spans(page.text, chunk_size)
        cursor = 0
        page_index = 0
        while cursor < len(spans):
            start = cursor
            end = cursor + 1
            while end < len(spans) and spans[end][1] - spans[start][0] <= chunk_size:
                end += 1
            text = page.text[spans[start][0]:spans[end - 1][1]].strip()
            chunks.append(ChunkDraft(page.page_number, page_index, page.section, text))
            page_index += 1
            if end == len(spans):
                break
            next_cursor = end
            if overlap and end - start > 1:
                next_cursor = end - 1
                while next_cursor > start + 1 and spans[end - 1][1] - spans[next_cursor][0] < overlap:
                    next_cursor -= 1
            cursor = next_cursor
    return chunks
