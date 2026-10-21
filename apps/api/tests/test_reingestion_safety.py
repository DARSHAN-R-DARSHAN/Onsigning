"""
Tests for Rule 11: Re-ingestion Safety and Human Audit Decision Preservation.
Verifies that re-running playbook evaluation never silently wipes human decisions.
"""

import uuid
import json
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from apps.api.core.database import Document, AuditRun, RiskAlert, AuditLog
from apps.api.services.audit_service import audit_service
from apps.api.services.storage import storage_service
from apps.api.ai_pipeline.schemas import ReviewStatus, RiskSeverity


@pytest.mark.asyncio
async def test_human_confirmed_decision_preserved_on_reingestion(
    client: AsyncClient,
    test_tenant_data: dict,
    db_session: AsyncSession
):
    """
    CRITICAL AUDIT PRESERVATION RULE:
    When a legal reviewer confirms, overrides, or dismisses an alert,
    subsequent audit re-runs on that document MUST preserve the human review state.
    """
    org_id = test_tenant_data["org_id"]
    user_id = test_tenant_data["user_id"]
    doc_id = str(uuid.uuid4())

    # Create dummy PDF file in storage
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (50, 72),
        "Section 8. Limitation of Liability: The total aggregate liability shall not exceed five hundred thousand dollars ($500,000).",
        fontsize=11
    )
    file_bytes = doc.write()
    doc.close()

    storage_key = await storage_service.save_file(org_id, doc_id, file_bytes, "agreement.pdf")

    # Persist document row
    doc_row = Document(
        id=doc_id,
        organization_id=org_id,
        title="Vendor Agreement",
        original_filename="agreement.pdf",
        storage_key=storage_key,
        sha256="dummy-sha256",
        mime_type="application/pdf",
        byte_size=len(file_bytes),
        processing_status="READY",
        created_by=user_id
    )
    db_session.add(doc_row)
    await db_session.commit()

    # 1. Run initial audit
    init_res = await audit_service.run_document_audit(
        db=db_session,
        organization_id=org_id,
        document_id=doc_id,
        actor_user_id=user_id
    )
    assert init_res["status"] in ("READY", "READY_WITH_WARNINGS")

    # Fetch initial alerts
    stmt = select(RiskAlert).where(RiskAlert.organization_id == org_id)
    alerts = (await db_session.execute(stmt)).scalars().all()
    assert len(alerts) > 0
    target_alert = alerts[0]
    target_rule_id = target_alert.rule_id

    # 2. Human reviewer overrides / confirms the alert
    review_note = "Confirmed with GC; exception accepted under commercial terms."
    review_res = await client.patch(
        f"/api/v1/audits/alerts/{target_alert.id}/review",
        headers=test_tenant_data["headers"],
        json={
            "review_status": "HUMAN_CONFIRMED",
            "review_note": review_note
        }
    )
    assert review_res.status_code == 200
    assert review_res.json()["review_status"] == "HUMAN_CONFIRMED"

    # 3. Trigger re-audit on the exact same document
    re_audit_res = await audit_service.run_document_audit(
        db=db_session,
        organization_id=org_id,
        document_id=doc_id,
        actor_user_id=user_id
    )
    assert re_audit_res["status"] in ("READY", "READY_WITH_WARNINGS")

    # 4. Verify preserved state in the new audit run
    stmt_new = select(RiskAlert).where(
        RiskAlert.organization_id == org_id,
        RiskAlert.rule_id == target_rule_id
    ).order_by(RiskAlert.created_at.desc())

    latest_alert = (await db_session.execute(stmt_new)).scalars().first()
    assert latest_alert is not None
    # Crucial assertion: the human review status must be preserved!
    assert latest_alert.review_status == ReviewStatus.HUMAN_CONFIRMED.value
    assert latest_alert.reviewed_by == user_id
    assert latest_alert.review_note == review_note
