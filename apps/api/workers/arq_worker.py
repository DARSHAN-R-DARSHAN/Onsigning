"""
Onsigning ARQ Background Worker
Enforces:
- 300-second maximum worker timeout
- Tenant isolation (session-level RLS context)
- Redacted, structured logging (no contract quotes or token leakage)
- Bounded retries (transient errors only)
- Safe failure recording with user-facing error classification
- Strict system-actor policy conforming to SCHEMA.md and ARCHITECTURE.md
"""

import asyncio
import uuid
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from sqlalchemy import select

from apps.api.core.config import settings
from apps.api.core.database import (
    async_session_factory, set_tenant_context, Document, OrganizationMembership
)
from apps.api.core.logging import logger
from apps.api.ai_pipeline.schemas import DocProcessingStatus, DocErrorCategory
from apps.api.services.audit_service import audit_service


async def process_document_audit_job(
    ctx: Dict[str, Any],
    organization_id: str,
    document_id: str,
    playbook_id: Optional[str] = None,
    actor_user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    ARQ task for asynchronous document audit processing.
    Bounded by WORKER_TIMEOUT_SECONDS (300 seconds).

    System-Actor Policy:
    - If actor_user_id is provided, verifies user membership in organization_id.
    - If actor_user_id is None, executes under SYSTEM_WORKER policy (RLS tenant context
      is set to organization_id with user context cleared; no fake user UUID is fabricated).
    - Verifies document tenant boundary before processing.
    """
    # 1. Parameter Validation
    try:
        org_uuid = uuid.UUID(str(organization_id))
        doc_uuid = uuid.UUID(str(document_id))
        actor_uuid = uuid.UUID(str(actor_user_id)) if actor_user_id else None
    except (ValueError, TypeError, AttributeError) as err:
        logger.error(
            f"Worker rejected job with invalid identifier: {err}",
            extra={"document_id": str(document_id), "organization_id": str(organization_id)}
        )
        raise ValueError(f"Invalid UUID in job parameters: {err}") from err

    logger.info(
        f"Worker picked up audit job: doc={doc_uuid}, org={org_uuid}, actor={actor_uuid or 'SYSTEM_WORKER'}",
        extra={"document_id": str(doc_uuid), "organization_id": str(org_uuid)}
    )

    async with async_session_factory() as session:
        # 2. Establish tenant context for RLS (correct argument order: user_id, organization_id)
        await set_tenant_context(session, user_id=actor_uuid, organization_id=org_uuid)

        # 3. If a human actor is specified, verify their membership in the organization
        if actor_uuid:
            membership_stmt = select(OrganizationMembership).where(
                OrganizationMembership.organization_id == str(org_uuid),
                OrganizationMembership.user_id == str(actor_uuid)
            )
            membership_res = await session.execute(membership_stmt)
            if not membership_res.scalar_one_or_none():
                logger.error(
                    f"Worker authorization failed: actor {actor_uuid} is not a member of {org_uuid}",
                    extra={"organization_id": str(org_uuid), "actor_user_id": str(actor_uuid)}
                )
                raise PermissionError(f"Actor {actor_uuid} is not authorized for organization {org_uuid}")

        # 4. Verify tenant boundary: ensure document belongs to claimed organization
        doc = await session.get(Document, str(doc_uuid))
        if not doc or str(doc.organization_id) != str(org_uuid):
            logger.error(
                f"Tenant boundary violation: doc {doc_uuid} not found or tenant mismatch with {org_uuid}",
                extra={"document_id": str(doc_uuid), "organization_id": str(org_uuid)}
            )
            raise ValueError(f"Document {doc_uuid} does not belong to organization {org_uuid} or does not exist.")

        try:
            # Enforce 300s timeout strictly
            result = await asyncio.wait_for(
                audit_service.run_document_audit(
                    db=session,
                    organization_id=str(org_uuid),
                    document_id=str(doc_uuid),
                    playbook_id=playbook_id,
                    actor_user_id=str(actor_uuid) if actor_uuid else None
                ),
                timeout=float(settings.WORKER_TIMEOUT_SECONDS)
            )
            logger.info(
                f"Worker completed audit job successfully: doc={doc_uuid}",
                extra={"document_id": str(doc_uuid), "organization_id": str(org_uuid)}
            )
            return result

        except asyncio.TimeoutError:
            logger.error(
                f"Worker audit job timed out after {settings.WORKER_TIMEOUT_SECONDS}s: doc={doc_uuid}",
                extra={"document_id": str(doc_uuid), "organization_id": str(org_uuid)}
            )
            try:
                # Update document status to FAILED with TIMEOUT category
                failed_doc = await session.get(Document, str(doc_uuid))
                if failed_doc:
                    failed_doc.processing_status = DocProcessingStatus.FAILED.value
                    failed_doc.error_category = DocErrorCategory.TIMEOUT_EXCEEDED.value
                    failed_doc.error_message = f"Processing timed out after {settings.WORKER_TIMEOUT_SECONDS} seconds."
                    failed_doc.failed_at = datetime.now(timezone.utc)
                    await session.commit()
            except Exception as e:
                logger.error(f"Failed to record timeout error state: {str(e)}")
            raise

        except Exception as exc:
            logger.error(
                f"Worker audit job failed: doc={doc_uuid}, error={str(exc)}",
                extra={"document_id": str(doc_uuid), "organization_id": str(org_uuid)}
            )
            try:
                failed_doc = await session.get(Document, str(doc_uuid))
                if failed_doc and failed_doc.processing_status != DocProcessingStatus.FAILED.value:
                    failed_doc.processing_status = DocProcessingStatus.FAILED.value
                    failed_doc.error_category = getattr(exc, "error_category", DocErrorCategory.PROCESSING_CRASH.value)
                    failed_doc.error_message = str(exc)
                    failed_doc.failed_at = datetime.now(timezone.utc)
                    await session.commit()
            except Exception as rec_err:
                logger.error(f"Failed to record failure state: {str(rec_err)}")
            raise


class WorkerSettings:
    """ARQ Worker configuration conforming to ARCHITECTURE.md."""
    functions = [process_document_audit_job]
    redis_settings = settings.REDIS_URL
    job_timeout = settings.WORKER_TIMEOUT_SECONDS
    max_tries = 2  # Bounded retries for transient failures only
