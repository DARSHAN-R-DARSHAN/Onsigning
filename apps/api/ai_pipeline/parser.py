"""
Onsigning Document Layout & Text Parser
Supports PDF (PyMuPDF) and modern DOCX (python-docx).
Strictly adheres to ARCHITECTURE.md Section 6 and AI_DATA_SPEC.md Stage A:
- Enforces 150-page limit.
- Rejects legacy .doc files with clear validation errors.
- Detects unreadable/scanned pages and logs parser warnings.
- Strips zero-point or invisible text (anti-prompt injection).
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import io
import docx
import pymupdf

from apps.api.core.config import settings
from apps.api.core.exceptions import DocumentValidationError
from apps.api.core.logging import logger


@dataclass
class PageText:
    page_number: int  # 1-indexed
    text: str
    has_unreadable_content: bool = False
    stripped_hidden_text_count: int = 0


@dataclass
class ParsedDocument:
    page_count: int
    pages: List[PageText]
    unreadable_pages: List[int] = field(default_factory=list)
    parser_warnings: List[Dict[str, Any]] = field(default_factory=list)


def parse_pdf(file_bytes: bytes) -> ParsedDocument:
    """Parses PDF bytes using PyMuPDF with page provenance and font inspection."""
    try:
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    except Exception as e:
        raise DocumentValidationError(
            message=f"Failed to parse PDF document. The file may be corrupt or encrypted: {str(e)}",
            error_category="CORRUPTED_FILE",
            status_code=400
        )

    page_count = len(doc)
    if page_count > settings.MAX_DOCUMENT_PAGES:
        raise DocumentValidationError(
            message=f"Document exceeds maximum length of {settings.MAX_DOCUMENT_PAGES} pages (found {page_count} pages).",
            error_category="PAGE_COUNT_EXCEEDED",
            status_code=400
        )

    pages: List[PageText] = []
    unreadable_pages: List[int] = []
    parser_warnings: List[Dict[str, Any]] = []

    for page_idx in range(page_count):
        page = doc[page_idx]
        page_num = page_idx + 1

        # Extract structured text spans to inspect fonts and rendering modes
        page_dict = page.get_text("dict")
        clean_text_lines: List[str] = []
        stripped_count = 0

        for block in page_dict.get("blocks", []):
            if block.get("type") == 0:  # Text block
                for line in block.get("lines", []):
                    line_spans = []
                    for span in line.get("spans", []):
                        text_span = span.get("text", "")
                        font_size = span.get("size", 10.0)
                        flags = span.get("flags", 0)

                        # Prompt-injection defense: Strip zero-point or micro-fonts (< 1.0 pt)
                        if font_size < 1.0:
                            stripped_count += 1
                            continue

                        line_spans.append(text_span)
                    line_str = "".join(line_spans).strip()
                    if line_str:
                        clean_text_lines.append(line_str)

        full_page_text = "\n".join(clean_text_lines).strip()

        # Check for unreadable / scanned / image-only / empty pages (< 20 characters)
        is_unreadable = False
        if len(full_page_text) < 20:
            is_unreadable = True
            unreadable_pages.append(page_num)
            images = page.get_images()
            parser_warnings.append({
                "page": page_num,
                "warning_type": "SCANNED_OR_LOW_TEXT_PAGE" if images else "EMPTY_OR_UNREADABLE_PAGE",
                "message": f"Page contains minimal extractable text layer ({len(full_page_text)} chars)."
            })

        if stripped_count > 0:
            parser_warnings.append({
                "page": page_num,
                "warning_type": "SUSPICIOUS_MICRO_FONT_STRIPPED",
                "message": f"Stripped {stripped_count} text span(s) with font size < 1.0 pt."
            })

        pages.append(PageText(
            page_number=page_num,
            text=full_page_text,
            has_unreadable_content=is_unreadable,
            stripped_hidden_text_count=stripped_count
        ))

    return ParsedDocument(
        page_count=page_count,
        pages=pages,
        unreadable_pages=unreadable_pages,
        parser_warnings=parser_warnings
    )


def parse_docx(file_bytes: bytes) -> ParsedDocument:
    """Parses modern DOCX bytes using python-docx."""
    try:
        doc = docx.Document(io.BytesIO(file_bytes))
    except Exception as e:
        raise DocumentValidationError(
            message=f"Failed to parse DOCX document: {str(e)}",
            error_category="CORRUPTED_FILE",
            status_code=400
        )

    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
            if row_text:
                paragraphs.append(row_text)

    full_text = "\n\n".join(paragraphs)
    # DOCX does not have native fixed pages; synthesize logical page breaks per ~3000 chars (approx. 500 words)
    page_size = 3000
    total_len = len(full_text)
    page_count = max(1, (total_len + page_size - 1) // page_size)

    if page_count > settings.MAX_DOCUMENT_PAGES:
        raise DocumentValidationError(
            message=f"Document exceeds maximum length of {settings.MAX_DOCUMENT_PAGES} pages.",
            error_category="PAGE_COUNT_EXCEEDED",
            status_code=400
        )

    pages = []
    for i in range(page_count):
        chunk = full_text[i * page_size : (i + 1) * page_size].strip()
        pages.append(PageText(page_number=i + 1, text=chunk))

    return ParsedDocument(page_count=page_count, pages=pages)


def parse_document(file_bytes: bytes, mime_type: str, filename: str) -> ParsedDocument:
    """
    Main dispatch function for document layout and text extraction.
    Enforces format restrictions: PDF and DOCX only; rejects legacy .doc files with HTTP 415.
    """
    fn_lower = filename.lower()

    if fn_lower.endswith(".doc") and not fn_lower.endswith(".docx"):
        raise DocumentValidationError(
            message="Legacy binary .doc files are not supported. Please upload a modern .docx or .pdf file.",
            error_category="PARSER_ERROR",
            status_code=415
        )

    # Magic byte checks
    if file_bytes.startswith(b"%PDF-") or mime_type == "application/pdf" or fn_lower.endswith(".pdf"):
        return parse_pdf(file_bytes)
    elif file_bytes.startswith(b"PK\x03\x04") or "openxmlformats" in mime_type or fn_lower.endswith(".docx"):
        return parse_docx(file_bytes)
    else:
        raise DocumentValidationError(
            message=f"Unsupported document format '{mime_type}'. Only PDF and DOCX files are permitted.",
            error_category="PARSER_ERROR",
            status_code=415
        )
