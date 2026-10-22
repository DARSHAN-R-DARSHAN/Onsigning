"""
Regression tests for Playbook JSON Loading in AuditService (Task 5.12.52).
Verifies that playbook names with double quotes, backslashes, and control characters
do not break JSON parsing, and that rules supplied as serialized JSON strings or
native lists/dicts are properly validated without fragile f-string formatting.
"""

import json
import uuid
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.database import Document, RiskPlaybook
from apps.api.services.audit_service import audit_service
from apps.api.services.storage import storage_service
from apps.api.services.evaluator import get_baseline_saas_playbook


async def _create_test_document(org_id: str, user_id: str, db_session: AsyncSession) -> str:
    """Helper to create and persist a valid test PDF document."""
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

    doc_id = str(uuid.uuid4())
    storage_key = await storage_service.save_file(org_id, doc_id, file_bytes, "agreement.pdf")

    doc_row = Document(
        id=doc_id,
        organization_id=org_id,
        title="Test Vendor Agreement",
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
    return doc_id


@pytest.mark.asyncio
async def test_playbook_name_containing_double_quotes(test_tenant_data: dict, db_session: AsyncSession):
    """Cover 1: Playbook name containing double quotes must not cause JSONDecodeError in audit service."""
    org_id = test_tenant_data["org_id"]
    user_id = test_tenant_data["user_id"]
    doc_id = await _create_test_document(org_id, user_id, db_session)

    baseline = get_baseline_saas_playbook()
    playbook = RiskPlaybook(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        name='Standard "B2B SaaS" Playbook with Quotes',
        version=1,
        description="Playbook with double quotes in name",
        rules=json.dumps([r.model_dump() for r in baseline.rules]),
        is_active=True,
        created_by=user_id
    )
    db_session.add(playbook)
    await db_session.commit()

    result = await audit_service.run_document_audit(
        db=db_session,
        organization_id=org_id,
        document_id=doc_id,
        playbook_id=playbook.id,
        actor_user_id=user_id
    )
    assert result["status"] in ("READY", "READY_WITH_WARNINGS")
    assert result["alerts_count"] > 0


@pytest.mark.asyncio
async def test_playbook_name_containing_backslashes(test_tenant_data: dict, db_session: AsyncSession):
    """Cover 2: Playbook name containing backslashes must not break escape sequence parsing."""
    org_id = test_tenant_data["org_id"]
    user_id = test_tenant_data["user_id"]
    doc_id = await _create_test_document(org_id, user_id, db_session)

    baseline = get_baseline_saas_playbook()
    playbook = RiskPlaybook(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        name=r"Special \ Backslash \ Enterprise Playbook",
        version=1,
        description="Playbook with backslashes in name",
        rules=json.dumps([r.model_dump() for r in baseline.rules]),
        is_active=True,
        created_by=user_id
    )
    db_session.add(playbook)
    await db_session.commit()

    result = await audit_service.run_document_audit(
        db=db_session,
        organization_id=org_id,
        document_id=doc_id,
        playbook_id=playbook.id,
        actor_user_id=user_id
    )
    assert result["status"] in ("READY", "READY_WITH_WARNINGS")
    assert result["alerts_count"] > 0


@pytest.mark.asyncio
async def test_playbook_name_containing_control_characters(test_tenant_data: dict, db_session: AsyncSession):
    """Cover 3: Playbook name containing newline, carriage-return, and tab characters."""
    org_id = test_tenant_data["org_id"]
    user_id = test_tenant_data["user_id"]
    doc_id = await _create_test_document(org_id, user_id, db_session)

    baseline = get_baseline_saas_playbook()
    playbook = RiskPlaybook(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        name="Control\nChars\rPlaybook\tCustom",
        version=1,
        description="Playbook with control characters in name",
        rules=json.dumps([r.model_dump() for r in baseline.rules]),
        is_active=True,
        created_by=user_id
    )
    db_session.add(playbook)
    await db_session.commit()

    result = await audit_service.run_document_audit(
        db=db_session,
        organization_id=org_id,
        document_id=doc_id,
        playbook_id=playbook.id,
        actor_user_id=user_id
    )
    assert result["status"] in ("READY", "READY_WITH_WARNINGS")
    assert result["alerts_count"] > 0


@pytest.mark.asyncio
async def test_playbook_rules_supplied_as_serialized_json_string(test_tenant_data: dict, db_session: AsyncSession):
    """Cover 4: Playbook rules supplied as a standard serialized JSON string."""
    org_id = test_tenant_data["org_id"]
    user_id = test_tenant_data["user_id"]
    doc_id = await _create_test_document(org_id, user_id, db_session)

    baseline = get_baseline_saas_playbook()
    serialized_rules = json.dumps([r.model_dump() for r in baseline.rules])
    playbook = RiskPlaybook(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        name="Serialized JSON Rules Playbook",
        version=1,
        description="Standard serialized JSON string rules",
        rules=serialized_rules,
        is_active=True,
        created_by=user_id
    )
    db_session.add(playbook)
    await db_session.commit()

    result = await audit_service.run_document_audit(
        db=db_session,
        organization_id=org_id,
        document_id=doc_id,
        playbook_id=playbook.id,
        actor_user_id=user_id
    )
    assert result["status"] in ("READY", "READY_WITH_WARNINGS")
    assert result["alerts_count"] > 0


@pytest.mark.asyncio
async def test_playbook_rules_supplied_as_native_list(test_tenant_data: dict, db_session: AsyncSession):
    """Cover 5: Playbook rules supplied as a native list of dictionaries."""
    org_id = test_tenant_data["org_id"]
    user_id = test_tenant_data["user_id"]
    doc_id = await _create_test_document(org_id, user_id, db_session)

    baseline = get_baseline_saas_playbook()
    native_rules_list = [r.model_dump() for r in baseline.rules]
    playbook = RiskPlaybook(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        name="Native List Rules Playbook",
        version=1,
        description="Native list of rule dictionaries",
        rules=native_rules_list,
        is_active=True,
        created_by=user_id
    )
    db_session.add(playbook)
    await db_session.commit()

    result = await audit_service.run_document_audit(
        db=db_session,
        organization_id=org_id,
        document_id=doc_id,
        playbook_id=playbook.id,
        actor_user_id=user_id
    )
    assert result["status"] in ("READY", "READY_WITH_WARNINGS")
    assert result["alerts_count"] > 0


@pytest.mark.asyncio
async def test_playbook_rules_with_invalid_json(test_tenant_data: dict, db_session: AsyncSession):
    """Cover 6: Invalid JSON in serialized rules must fail safely with JSONDecodeError/ValueError."""
    from datetime import datetime, timezone
    from sqlalchemy import text
    org_id = test_tenant_data["org_id"]
    user_id = test_tenant_data["user_id"]
    doc_id = await _create_test_document(org_id, user_id, db_session)

    playbook_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    await db_session.execute(
        text(
            "INSERT INTO risk_playbooks (id, organization_id, name, version, description, rules, is_active, created_by, created_at, updated_at) "
            "VALUES (:id, :org_id, :name, :version, :description, :rules, :is_active, :created_by, :created_at, :updated_at)"
        ),
        {
            "id": playbook_id,
            "org_id": org_id,
            "name": "Malformed JSON Playbook",
            "version": 1,
            "description": "Invalid JSON string payload",
            "rules": '{"invalid_json": unquoted_value}',
            "is_active": True,
            "created_by": user_id,
            "created_at": now,
            "updated_at": now
        }
    )
    await db_session.commit()

    with pytest.raises((json.JSONDecodeError, ValueError)):
        await audit_service.run_document_audit(
            db=db_session,
            organization_id=org_id,
            document_id=doc_id,
            playbook_id=playbook_id,
            actor_user_id=user_id
        )


@pytest.mark.asyncio
async def test_playbook_rules_with_structurally_invalid_data(test_tenant_data: dict, db_session: AsyncSession):
    """Cover 7: Structurally invalid rule data must fail safely with ValidationError."""
    org_id = test_tenant_data["org_id"]
    user_id = test_tenant_data["user_id"]
    doc_id = await _create_test_document(org_id, user_id, db_session)

    # Structurally invalid rules: missing required fields, invalid operator, invalid field
    invalid_rules = [
        {"not_a_valid_field": 12345, "invalid_operator": True}
    ]
    playbook = RiskPlaybook(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        name="Invalid Schema Playbook",
        version=1,
        description="Structurally invalid rule items",
        rules=json.dumps(invalid_rules),
        is_active=True,
        created_by=user_id
    )
    db_session.add(playbook)
    await db_session.commit()

    with pytest.raises(ValidationError):
        await audit_service.run_document_audit(
            db=db_session,
            organization_id=org_id,
            document_id=doc_id,
            playbook_id=playbook.id,
            actor_user_id=user_id
        )
