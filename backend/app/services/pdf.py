from dataclasses import dataclass

import pymupdf


class PDFError(Exception):
    pass


class PDFQualityError(Exception):
    pass


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    section: str | None = None


class PDFService:
    def __init__(
        self,
        min_extracted_chars: int = 30,
        min_text_page_ratio: float = 0.5,
        max_replacement_ratio: float = 0.05,
    ):
        self.min_extracted_chars = min_extracted_chars
        self.min_text_page_ratio = min_text_page_ratio
        self.max_replacement_ratio = max_replacement_ratio

    def page_count(self, data: bytes) -> int:
        try:
            with pymupdf.open(stream=data, filetype="pdf") as document:
                if document.needs_pass:
                    raise PDFError("PDF is encrypted and cannot be read")
                if document.page_count == 0:
                    raise PDFError("PDF contains no pages")
                return document.page_count
        except (pymupdf.FileDataError, pymupdf.EmptyFileError, ValueError) as exc:
            raise PDFError("PDF cannot be opened") from exc

    def extract(self, data: bytes) -> list[ExtractedPage]:
        try:
            with pymupdf.open(stream=data, filetype="pdf") as document:
                if document.needs_pass:
                    raise PDFError("PDF is encrypted and cannot be read")
                if document.page_count == 0:
                    raise PDFError("PDF contains no pages")
                return [
                    ExtractedPage(page_number=index + 1, text=page.get_text("text"))
                    for index, page in enumerate(document)
                ]
        except (pymupdf.FileDataError, pymupdf.EmptyFileError, ValueError, RuntimeError) as exc:
            raise PDFError("PDF text extraction failed") from exc

    def validate_quality(self, pages: list[ExtractedPage]) -> None:
        text = "".join(page.text for page in pages)
        useful = sum(character.isalnum() for character in text)
        if useful < self.min_extracted_chars:
            raise PDFQualityError("Insufficient extractable text; PDF may be scanned")
        text_pages = sum(any(character.isalnum() for character in page.text) for page in pages)
        if text_pages / len(pages) < self.min_text_page_ratio:
            raise PDFQualityError("Too many pages have no extractable text")
        if text.count("\ufffd") / max(len(text), 1) > self.max_replacement_ratio:
            raise PDFQualityError("Extracted text contains too many replacement characters")
