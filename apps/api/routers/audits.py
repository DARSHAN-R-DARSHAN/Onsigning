"""
Onsigning Playbook Audit Router
Endpoints for executing contract playbook evaluations, retrieving findings reports,
and submitting human review decisions (confirm, override, dismiss).
"""

from typing import Optional, Dict, Any, List
import uuid
import json
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from apps.api.core.database import get_db, AuditRun, RiskAlert, ExtractedClause, Document, AuditLog
from apps.api.core.auth import get_tenant_context, TenantContext, require_role
from apps.api.core.exceptions import ResourceNotFoundError, AuthorizationError
from apps.api.services.audit_service import audit_service
from apps.api.ai_pipeline.schemas import ReviewStatus
from apps.api.core.logging import logger

router = APIRouter(prefix="/api/v1/audits", tags=["Playbook Audits"])


class RunAuditRequest(BaseModel):
    document_id: str
    playbook_id: Optional[str] = None


class ReviewAlertRequest(BaseModel):
    review_status: ReviewStatus
    review_note: Optional[str] = Field(default=None, description="Mandatory note for overrides or dismissals")


@router.post("/run")
async def run_audit(
    payload: RunAuditRequest,
    context: TenantContext = Depends(require_role(["OWNER", "ADMIN", "LEGAL_REVIEWER"])),
    db: AsyncSession = Depends(get_db)
):
    """Executes a synchronous playbook audit run for a document."""
    result = await audit_service.run_document_audit(
        db=db,
        organization_id=str(context.organization.id),
        document_id=payload.document_id,
        playbook_id=payload.playbook_id,
        actor_user_id=str(context.user.id)
    )
    return result


@router.get("/document/{document_id}/latest")
async def get_latest_document_audit(
    document_id: str,
    context: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """Retrieves the latest audit run report and findings for a document."""
    org_id = str(context.organization.id)

    # Fetch latest audit run
    run_stmt = (
        select(AuditRun)
        .where(AuditRun.document_id == document_id, AuditRun.organization_id == org_id)
        .order_by(AuditRun.started_at.desc())
    )
    run_res = await db.execute(run_stmt)
    audit_run = run_res.scalars().first()

    if not audit_run:
        raise ResourceNotFoundError("AuditRun for document", document_id)

    # Fetch alerts
    alerts_stmt = (
        select(RiskAlert)
        .where(RiskAlert.audit_run_id == audit_run.id, RiskAlert.organization_id == org_id)
        .order_by(RiskAlert.created_at.asc())
    )
    alerts_res = await db.execute(alerts_stmt)
    alerts = alerts_res.scalars().all()

    # Fetch extracted clauses
    clauses_stmt = (
        select(ExtractedClause)
        .where(ExtractedClause.document_id == document_id, ExtractedClause.organization_id == org_id)
    )
    clauses_res = await db.execute(clauses_stmt)
    clauses = clauses_res.scalars().all()

    return {
        "audit_run_id": audit_run.id,
        "document_id": audit_run.document_id,
        "playbook_id": audit_run.playbook_id,
        "playbook_version": audit_run.playbook_version,
        "status": audit_run.status,
        "completed_at": audit_run.completed_at.isoformat() if audit_run.completed_at else None,
        "summary": json.loads(audit_run.summary) if isinstance(audit_run.summary, str) else audit_run.summary,
        "alerts": [
            {
                "id": a.id,
                "rule_id": a.rule_id,
                "severity": a.severity,
                "title": a.title,
                "explanation": a.explanation,
                "review_status": a.review_status,
                "reviewed_by": a.reviewed_by,
                "reviewed_at": a.reviewed_at.isoformat() if a.reviewed_at else None,
                "review_note": a.review_note,
                "evidence": json.loads(a.evidence_payload) if isinstance(a.evidence_payload, str) else a.evidence_payload
            }
            for a in alerts
        ],
        "extracted_clauses": [
            {
                "id": c.id,
                "clause_category": c.clause_category,
                "presence_status": c.presence_status,
                "section_reference": c.section_reference,
                "page_number": c.page_number,
                "page_end": c.page_end,
                "verbatim_quote": c.verbatim_quote,
                "citation_status": c.citation_status,
                "is_evidence_verified": c.is_evidence_verified,
                "factual_summary": c.factual_summary,
                "cap_structure": c.cap_structure,
                "cap_fixed_amount_cents": c.cap_fixed_amount_cents,
                "cap_currency": c.cap_currency,
                "notice_period_days": c.notice_period_days,
                "governing_law_jurisdiction": c.governing_law_jurisdiction
            }
            for c in clauses
        ]
    }


@router.patch("/alerts/{alert_id}/review")
async def review_alert(
    alert_id: str,
    payload: ReviewAlertRequest,
    context: TenantContext = Depends(require_role(["OWNER", "ADMIN", "LEGAL_REVIEWER"])),
    db: AsyncSession = Depends(get_db)
):
    """
    Submits a human review decision (Confirm, Override, Dismiss) for a risk alert.
    Audits the decision in audit_logs table (PRD.md Section 4).
    """
    org_id = str(context.organization.id)

    stmt = select(RiskAlert).where(
        RiskAlert.id == alert_id,
        RiskAlert.organization_id == org_id
    )
    res = await db.execute(stmt)
    alert = res.scalar_one_or_none()

    if not alert:
        raise ResourceNotFoundError("RiskAlert", alert_id)

    old_status = alert.review_status
    new_status = payload.review_status.value

    # Overrides and dismissals require a justification note
    if new_status in (ReviewStatus.HUMAN_OVERRIDDEN.value, ReviewStatus.DISMISSED_FALSE_POSITIVE.value):
        if not payload.review_note or not payload.review_note.strip():
            raise HTTPException(
                status_code=400,
                detail=f"A review note is required when marking a finding as '{new_status}'."
            )

    alert.review_status = new_status
    alert.reviewed_by = str(context.user.id)
    alert.reviewed_at = datetime.now(timezone.utc)
    alert.review_note = payload.review_note

    # Record immutable audit event
    log_entry = AuditLog(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        actor_user_id=str(context.user.id),
        actor_type="USER",
        entity_table="risk_alerts",
        entity_id=alert_id,
        action="REVIEW_STATUS_UPDATED",
        previous_state=json.dumps({"review_status": old_status}),
        new_state=json.dumps({"review_status": new_status, "note": payload.review_note})
    )
    db.add(log_entry)

    await db.commit()

    return {
        "alert_id": alert.id,
        "review_status": alert.review_status,
        "reviewed_by": alert.reviewed_by,
        "reviewed_at": alert.reviewed_at.isoformat(),
        "review_note": alert.review_note
    }
