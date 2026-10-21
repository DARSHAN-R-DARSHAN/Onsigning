"""
Onsigning Document Management Router
Endpoints for uploading, inspecting, and streaming contract documents:
- Validates 35 MB upload size and file format (.pdf and .docx only).
- Rejects legacy binary .doc files with HTTP 415.
- Provides authenticated streaming proxy (GET /api/v1/documents/{id}/content) for the PDF.js viewer.
"""

from typing import List, Optional
import uuid
import json
from fastapi import APIRouter, Depends, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from apps.api.core.config import settings
from apps.api.core.database import (
    get_db, Document, set_tenant_context, OrganizationMembership, async_session_factory
)
from apps.api.core.auth import get_tenant_context, TenantContext, require_role
from apps.api.core.exceptions import DocumentValidationError, ResourceNotFoundError
from apps.api.services.storage import storage_service
from apps.api.services.audit_service import audit_service
from apps.api.core.logging import logger

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])


@router.post("/upload")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    hierarchy_type: str = Form("OTHER"),
    auto_audit: bool = Form(True),
    context: TenantContext = Depends(require_role(["OWNER", "ADMIN", "LEGAL_REVIEWER"])),
    db: AsyncSession = Depends(get_db)
):
    """
    Secure document upload endpoint.
    Enforces:
    - 35 MB maximum file size limit server-side.
    - PDF and DOCX only; rejects legacy .doc with HTTP 415.
    - Private object storage with SHA-256 integrity verification.
    """
    file_bytes = await file.read()
    filename = file.filename or "contract.pdf"

    # Validate size, extension, and magic bytes
    detected_mime, sha256 = storage_service.validate_file(
        file_bytes=file_bytes,
        filename=filename,
        content_type=file.content_type
    )

    doc_id = str(uuid.uuid4())
    org_id = str(context.organization.id)

    # Save to private storage
    storage_key = await storage_service.save_file(
        organization_id=org_id,
        document_id=doc_id,
        file_bytes=file_bytes,
        filename=filename
    )

    # Create document row in database
    document = Document(
        id=doc_id,
        organization_id=org_id,
        title=filename.rsplit(".", 1)[0],
        original_filename=filename,
        storage_key=storage_key,
        sha256=sha256,
        mime_type=detected_mime,
        byte_size=len(file_bytes),
        hierarchy_type=hierarchy_type,
        processing_status="UPLOADED",
        created_by=str(context.user.id)
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    logger.info(
        f"Document uploaded: doc_id={doc_id}, org_id={org_id}, bytes={len(file_bytes)}",
        extra={"organization_id": org_id, "document_id": doc_id}
    )

    # If auto-audit requested, trigger background audit execution
    if auto_audit:
        background_tasks.add_task(
            _run_audit_background,
            org_id=org_id,
            doc_id=doc_id,
            user_id=str(context.user.id)
        )

    return {
        "id": document.id,
        "title": document.title,
        "filename": document.original_filename,
        "mime_type": document.mime_type,
        "byte_size": document.byte_size,
        "processing_status": document.processing_status,
        "sha256": document.sha256
    }


async def _run_audit_background(org_id: str, doc_id: str, user_id: str):
    """
    Background task runner executing audit pipeline asynchronously.
    Enforces:
    - UUID validation.
    - Tenant RLS context initialization on the new session before running queries or writes.
    - Authorization check ensuring the actor belongs to the organization.
    - Strict document tenant boundary verification.
    - Redacted error logging preserving confidentiality.
    """
    try:
        org_uuid = uuid.UUID(str(org_id))
        doc_uuid = uuid.UUID(str(doc_id))
        user_uuid = uuid.UUID(str(user_id)) if user_id else None
    except (ValueError, TypeError, AttributeError) as err:
        logger.error(
            f"Background audit aborted: invalid UUID identifier: {err}",
            extra={"organization_id": str(org_id), "document_id": str(doc_id)}
        )
        return

    async with async_session_factory() as db:
        try:
            # 1. Establish tenant context for RLS
            await set_tenant_context(db, user_id=user_uuid, organization_id=org_uuid)

            # 2. Verify actor authorization within this tenant
            if user_uuid:
                membership_stmt = select(OrganizationMembership).where(
                    OrganizationMembership.organization_id == str(org_uuid),
                    OrganizationMembership.user_id == str(user_uuid)
                )
                membership_res = await db.execute(membership_stmt)
                if not membership_res.scalar_one_or_none():
                    logger.error(
                        f"Background audit unauthorized: user {user_uuid} is not a member of org {org_uuid}",
                        extra={"organization_id": str(org_uuid), "user_id": str(user_uuid)}
                    )
                    return

            # 3. Verify tenant boundary: ensure document belongs to claimed organization
            doc = await db.get(Document, str(doc_uuid))
            if not doc or str(doc.organization_id) != str(org_uuid):
                logger.error(
                    f"Background audit aborted: document {doc_uuid} not found or tenant mismatch with {org_uuid}",
                    extra={"organization_id": str(org_uuid), "document_id": str(doc_uuid)}
                )
                return

            # 4. Execute audit pipeline
            await audit_service.run_document_audit(
                db=db,
                organization_id=str(org_uuid),
                document_id=str(doc_uuid),
                actor_user_id=str(user_uuid) if user_uuid else None
            )
        except Exception as e:
            logger.error(
                f"Background audit failed for doc={doc_uuid}: {str(e)}",
                extra={"document_id": str(doc_uuid), "organization_id": str(org_uuid)}
            )


@router.get("")
async def list_documents(
    context: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """Lists all contracts within the authenticated workspace."""
    stmt = (
        select(Document)
        .where(Document.organization_id == str(context.organization.id))
        .order_by(Document.created_at.desc())
    )
    result = await db.execute(stmt)
    docs = result.scalars().all()

    return [
        {
            "id": d.id,
            "title": d.title,
            "original_filename": d.original_filename,
            "mime_type": d.mime_type,
            "byte_size": d.byte_size,
            "processing_status": d.processing_status,
            "page_count": d.page_count,
            "error_category": d.error_category,
            "error_message": d.error_message,
            "created_at": d.created_at.isoformat()
        }
        for d in docs
    ]


@router.get("/{document_id}")
async def get_document(
    document_id: str,
    context: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """Retrieves document details, parsing metadata, and error status."""
    stmt = select(Document).where(
        Document.id == document_id,
        Document.organization_id == str(context.organization.id)
    )
    result = await db.execute(stmt)
    document = result.scalar_one_or_none()
    if not document:
        raise ResourceNotFoundError("Document", document_id)

    # Format unreadable_pages and parser_warnings safely
    unreadable = document.unreadable_pages
    if isinstance(unreadable, str):
        try:
            unreadable = json.loads(unreadable)
        except Exception:
            unreadable = []
    elif not isinstance(unreadable, list):
        unreadable = []

    warnings = document.parser_warnings
    if isinstance(warnings, str):
        try:
            warnings = json.loads(warnings)
        except Exception:
            warnings = []
    elif not isinstance(warnings, list):
        warnings = []

    return {
        "id": document.id,
        "title": document.title,
        "original_filename": document.original_filename,
        "mime_type": document.mime_type,
        "byte_size": document.byte_size,
        "processing_status": document.processing_status,
        "page_count": document.page_count,
        "unreadable_pages": unreadable,
        "parser_warnings": warnings,
        "error_category": document.error_category,
        "error_message": document.error_message,
        "failed_at": document.failed_at.isoformat() if document.failed_at else None,
        "created_at": document.created_at.isoformat()
    }


@router.get("/{document_id}/content")
async def stream_document_content(
    document_id: str,
    context: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """
    Authenticated streaming proxy for contract documents.
    Allows Next.js PDF.js viewer to view documents over long review sessions (> 15 min)
    without expiring URLs or exposing cloud storage credentials.
    """
    stmt = select(Document).where(
        Document.id == document_id,
        Document.organization_id == str(context.organization.id)
    )
    result = await db.execute(stmt)
    document = result.scalar_one_or_none()
    if not document:
        raise ResourceNotFoundError("Document", document_id)

    file_path = storage_service.get_file_path(document.storage_key)
    return FileResponse(
        path=file_path,
        media_type=document.mime_type,
        filename=document.original_filename
    )
