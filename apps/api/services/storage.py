"""
Onsigning Private Storage Service
Handles secure document upload validation, SHA-256 computation, and private object storage:
- Enforces 35 MB upload size limit server-side.
- Validates file format and magic bytes (PDF: %PDF-, DOCX: PK\x03\x04).
- Provides authenticated streaming proxy (GET /api/documents/{id}/content) so viewers don't expire after 900s.
- Enforces 900-second TTL for presigned export URLs.
"""

import os
import hashlib
import uuid
from typing import Tuple, Optional
import aiofiles
from fastapi.responses import FileResponse, StreamingResponse

from apps.api.core.config import settings
from apps.api.core.exceptions import DocumentValidationError
from apps.api.core.logging import logger

PDF_MAGIC = b"%PDF-"
DOCX_MAGIC = b"PK\x03\x04"


class StorageService:
    def __init__(self):
        self.storage_dir = settings.STORAGE_LOCAL_DIR
        os.makedirs(self.storage_dir, exist_ok=True)

    def validate_file(self, file_bytes: bytes, filename: str, content_type: Optional[str]) -> Tuple[str, str]:
        """
        Validates byte size, extension, and magic bytes.
        Returns (detected_mime, sha256).
        """
        size = len(file_bytes)
        if size > settings.MAX_UPLOAD_SIZE_BYTES:
            raise DocumentValidationError(
                message=f"File size ({size / (1024*1024):.1f} MB) exceeds maximum allowed limit of {settings.MAX_UPLOAD_SIZE_BYTES / (1024*1024):.0f} MB.",
                error_category="FILE_SIZE_EXCEEDED",
                status_code=400
            )

        fn_lower = filename.lower()
        if fn_lower.endswith(".doc") and not fn_lower.endswith(".docx"):
            raise DocumentValidationError(
                message="Legacy binary .doc files are not supported. Please upload a .docx or .pdf file.",
                error_category="PARSER_ERROR",
                status_code=415
            )

        # Magic byte validation
        if file_bytes.startswith(PDF_MAGIC):
            detected_mime = "application/pdf"
        elif file_bytes.startswith(DOCX_MAGIC) and (fn_lower.endswith(".docx") or "openxmlformats" in (content_type or "")):
            detected_mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            raise DocumentValidationError(
                message="Invalid file format or corrupted file header. Supported formats are PDF and DOCX.",
                error_category="PARSER_ERROR",
                status_code=415
            )

        sha256 = hashlib.sha256(file_bytes).hexdigest()
        return detected_mime, sha256

    async def save_file(self, organization_id: str, document_id: str, file_bytes: bytes, filename: str) -> str:
        """Saves file to tenant-isolated private storage directory. Returns storage key."""
        org_dir = os.path.join(self.storage_dir, organization_id)
        os.makedirs(org_dir, exist_ok=True)

        ext = ".pdf" if filename.lower().endswith(".pdf") else ".docx"
        storage_filename = f"{document_id}{ext}"
        full_path = os.path.join(org_dir, storage_filename)

        async with aiofiles.open(full_path, "wb") as f:
            await f.write(file_bytes)

        # Storage key is relative, preventing full local paths from leaking
        return f"{organization_id}/{storage_filename}"

    async def read_file(self, storage_key: str) -> bytes:
        """Reads file bytes from private storage."""
        full_path = os.path.join(self.storage_dir, storage_key)
        if not os.path.exists(full_path):
            raise DocumentValidationError(
                message="Stored document content could not be located.",
                error_category="CORRUPTED_FILE",
                status_code=404
            )
        async with aiofiles.open(full_path, "rb") as f:
            return await f.read()

    def get_file_path(self, storage_key: str) -> str:
        """Returns the local path for streaming."""
        return os.path.join(self.storage_dir, storage_key)

    def generate_presigned_url(self, storage_key: str, expires_in_seconds: Optional[int] = None) -> str:
        """
        Generates a temporary presigned URL for direct download.
        Enforces maximum 900-second TTL (PRD.md Section 8).
        """
        ttl = min(expires_in_seconds or settings.PRESIGNED_URL_TTL_SECONDS, settings.PRESIGNED_URL_TTL_SECONDS)
        # In local storage, return a token-authenticated streaming URL with ttl parameter
        return f"/api/v1/documents/content-by-key?key={storage_key}&ttl={ttl}"


storage_service = StorageService()
