"""
Tests for Document Ingestion, Size Limits, Format Verification, and Parser Robustness.
"""

import io
import fitz  # PyMuPDF
import pytest
from httpx import AsyncClient

from apps.api.ai_pipeline.parser import parse_document
from apps.api.core.exceptions import DocumentValidationError


def create_minimal_pdf(pages_count: int = 1, text_prefix: str = "This is agreement text on page ") -> bytes:
    """Helper to generate a valid in-memory PDF with the specified number of pages."""
    doc = fitz.open()
    for i in range(pages_count):
        page = doc.new_page(width=612, height=792)
        page.insert_text((50, 72), f"{text_prefix} {i + 1}. Mutual Indemnification and Liability.", fontsize=11)
    pdf_bytes = doc.write()
    doc.close()
    return pdf_bytes


@pytest.mark.asyncio
async def test_upload_valid_pdf_succeeds(client: AsyncClient, test_tenant_data: dict):
    """Valid PDF under 35 MB with valid magic bytes uploads successfully."""
    pdf_bytes = create_minimal_pdf(pages_count=3)
    files = {
        "file": ("master_services_agreement.pdf", io.BytesIO(pdf_bytes), "application/pdf")
    }
    data = {"hierarchy_type": "MASTER_SERVICES_AGREEMENT", "auto_audit": "false"}

    res = await client.post(
        "/api/v1/documents/upload",
        headers=test_tenant_data["headers"],
        files=files,
        data=data
    )
    assert res.status_code == 200
    json_data = res.json()
    assert json_data["filename"] == "master_services_agreement.pdf"
    assert json_data["processing_status"] in ("UPLOADED", "READY")


@pytest.mark.asyncio
async def test_reject_legacy_doc_format_with_http_415(client: AsyncClient, test_tenant_data: dict):
    """
    Legacy binary Microsoft Word 97-2003 (.doc) files are rejected with HTTP 415
    per PRD and AI Data Spec requirements.
    """
    legacy_doc_bytes = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\x00" * 200
    files = {
        "file": ("legacy_agreement.doc", io.BytesIO(legacy_doc_bytes), "application/msword")
    }
    data = {"hierarchy_type": "OTHER", "auto_audit": "false"}

    res = await client.post(
        "/api/v1/documents/upload",
        headers=test_tenant_data["headers"],
        files=files,
        data=data
    )
    assert res.status_code == 415
    assert "Legacy binary .doc files are not supported" in res.json()["message"]


@pytest.mark.asyncio
async def test_reject_oversized_document_over_35mb(client: AsyncClient, test_tenant_data: dict):
    """Files exceeding the 35 MB operational baseline limit are rejected."""
    oversized_dummy = b"%PDF-1.4\n" + b"X" * (36 * 1024 * 1024)
    files = {
        "file": ("huge_contract.pdf", io.BytesIO(oversized_dummy), "application/pdf")
    }
    res = await client.post(
        "/api/v1/documents/upload",
        headers=test_tenant_data["headers"],
        files=files,
        data={"auto_audit": "false"}
    )
    assert res.status_code in (400, 413)


def test_parser_enforces_150_page_limit():
    """Document exceeding 150 pages triggers DocumentValidationError with PAGE_COUNT_EXCEEDED."""
    oversized_pdf = create_minimal_pdf(pages_count=151)
    with pytest.raises(DocumentValidationError) as exc_info:
        parse_document(oversized_pdf, "application/pdf", "oversized.pdf")
    assert exc_info.value.error_category == "PAGE_COUNT_EXCEEDED"
    assert "Document exceeds maximum length of 150 pages" in exc_info.value.message


def test_parser_under_150_pages_succeeds():
    """Document within 150 pages limit parses cleanly."""
    valid_pdf = create_minimal_pdf(pages_count=5)
    parsed = parse_document(valid_pdf, "application/pdf", "valid.pdf")
    assert parsed.page_count == 5
    assert len(parsed.pages) == 5
    assert parsed.pages[0].page_number == 1
    assert "Mutual Indemnification" in parsed.pages[0].text


def test_parser_flags_unreadable_pages():
    """Blank or scanned pages with 0 characters are recorded in unreadable_pages."""
    doc = fitz.open()
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((50, 72), "Page 1 has plenty of readable contract text.", fontsize=12)
    p2 = doc.new_page(width=612, height=792)  # Blank page (simulates unreadable/image-only)
    pdf_bytes = doc.write()
    doc.close()

    parsed = parse_document(pdf_bytes, "application/pdf", "partially_blank.pdf")
    assert parsed.page_count == 2
    assert 2 in parsed.unreadable_pages
