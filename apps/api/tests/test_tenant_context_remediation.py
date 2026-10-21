"""
Regression Tests for Tenant Context, System-Actor Policy, and Array Columns
Tests fixes for:
1. set_tenant_context argument order and system-actor policy (actor_user_id=None).
2. Worker tenant isolation, boundary checks, and membership validation.
3. API background audit execution tenant context and authorization checks.
4. Database array types (unreadable_pages, uncapped_carveouts) round-trip & DDL.
"""

import uuid
from contextlib import asynccontextmanager
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.database import (
    set_tenant_context, Document, ExtractedClause, OrganizationMembership,
    PortableIntArray, PortableStringArray
)
from apps.api.workers.arq_worker import process_document_audit_job
from apps.api.routers.documents import _run_audit_background


def create_session_factory(session: AsyncSession):
    """Helper to return an async context manager yielding the active test session."""
    @asynccontextmanager
    async def _factory():
        yield session
    return _factory


@pytest.mark.asyncio
async def test_set_tenant_context_valid_user_and_org(db_session: AsyncSession):
    """Verifies that set_tenant_context sets both user and organization context correctly."""
    user_uuid = uuid.uuid4()
    org_uuid = uuid.uuid4()

    await set_tenant_context(db_session, user_id=user_uuid, organization_id=org_uuid)

    assert db_session.info["current_user_id"] == str(user_uuid)
    assert db_session.info["current_organization_id"] == str(org_uuid)


@pytest.mark.asyncio
async def test_set_tenant_context_system_actor_policy(db_session: AsyncSession):
    """
    Verifies the system-actor policy:
    When actor_user_id is None or empty string, no fake user UUID is fabricated;
    user context is None (evaluating to SQL NULL in RLS), while organization context is set.
    """
    org_uuid = uuid.uuid4()

    # None user
    await set_tenant_context(db_session, user_id=None, organization_id=org_uuid)
    assert db_session.info["current_user_id"] is None
    assert db_session.info["current_organization_id"] == str(org_uuid)

    # Empty string user
    await set_tenant_context(db_session, user_id="", organization_id=org_uuid)
    assert db_session.info["current_user_id"] is None
    assert db_session.info["current_organization_id"] == str(org_uuid)


@pytest.mark.asyncio
async def test_set_tenant_context_rejects_invalid_identifiers(db_session: AsyncSession):
    """Verifies that malformed or missing UUIDs raise ValueError immediately."""
    org_uuid = uuid.uuid4()

    with pytest.raises(ValueError, match="organization_id must be provided"):
        await set_tenant_context(db_session, user_id=None, organization_id="")

    with pytest.raises(ValueError, match="Invalid organization_id UUID"):
        await set_tenant_context(db_session, user_id=None, organization_id="not-a-valid-uuid")

    with pytest.raises(ValueError, match="Invalid user_id UUID"):
        await set_tenant_context(db_session, user_id="bad-user-uuid", organization_id=org_uuid)


@pytest.mark.asyncio
async def test_worker_tenant_context_with_valid_user(test_tenant_data: dict, db_session: AsyncSession):
    """Verifies that the ARQ worker establishes tenant context with correct argument order."""
    org_id = test_tenant_data["org_id"]
    user_id = test_tenant_data["user_id"]
    doc_id = str(uuid.uuid4())

    doc = Document(
        id=doc_id,
        organization_id=org_id,
        title="MSA Agreement",
        original_filename="msa.pdf",
        storage_key="test/msa.pdf",
        sha256="b" * 64,
        mime_type="application/pdf",
        byte_size=1024,
        hierarchy_type="MASTER_AGREEMENT",
        processing_status="UPLOADED",
        created_by=user_id
    )
    db_session.add(doc)
    await db_session.commit()

    factory = create_session_factory(db_session)

    with patch("apps.api.workers.arq_worker.async_session_factory", factory), \
         patch("apps.api.workers.arq_worker.audit_service.run_document_audit", new_callable=AsyncMock) as mock_audit:

        mock_audit.return_value = {"status": "SUCCESS", "document_id": doc_id}

        result = await process_document_audit_job(
            ctx={},
            organization_id=org_id,
            document_id=doc_id,
            actor_user_id=user_id
        )

        assert result["status"] == "SUCCESS"
        mock_audit.assert_awaited_once()
        # Verify call arguments passed to audit_service
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["organization_id"] == org_id
        assert call_kwargs["document_id"] == doc_id
        assert call_kwargs["actor_user_id"] == user_id


@pytest.mark.asyncio
async def test_worker_tenant_context_system_actor(test_tenant_data: dict, db_session: AsyncSession):
    """Verifies that the ARQ worker runs with actor_user_id=None under the system-actor policy."""
    org_id = test_tenant_data["org_id"]
    doc_id = str(uuid.uuid4())

    doc = Document(
        id=doc_id,
        organization_id=org_id,
        title="Automated Audit Doc",
        original_filename="auto.pdf",
        storage_key="test/auto.pdf",
        sha256="c" * 64,
        mime_type="application/pdf",
        byte_size=2048,
        hierarchy_type="ORDER_FORM",
        processing_status="UPLOADED"
    )
    db_session.add(doc)
    await db_session.commit()

    factory = create_session_factory(db_session)

    with patch("apps.api.workers.arq_worker.async_session_factory", factory), \
         patch("apps.api.workers.arq_worker.audit_service.run_document_audit", new_callable=AsyncMock) as mock_audit:

        mock_audit.return_value = {"status": "SUCCESS", "document_id": doc_id}

        result = await process_document_audit_job(
            ctx={},
            organization_id=org_id,
            document_id=doc_id,
            actor_user_id=None
        )

        assert result["status"] == "SUCCESS"
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["actor_user_id"] is None


@pytest.mark.asyncio
async def test_worker_rejects_cross_tenant_document_mismatch(test_tenant_data: dict, db_session: AsyncSession):
    """Verifies that the worker strictly rejects execution when document does not belong to organization."""
    org_a = test_tenant_data["org_id"]
    org_b = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())

    doc = Document(
        id=doc_id,
        organization_id=org_a,
        title="Org A Confidential",
        original_filename="doc.pdf",
        storage_key="test/doc.pdf",
        sha256="d" * 64,
        mime_type="application/pdf",
        byte_size=512,
        processing_status="UPLOADED"
    )
    db_session.add(doc)
    await db_session.commit()

    factory = create_session_factory(db_session)

    with patch("apps.api.workers.arq_worker.async_session_factory", factory), \
         patch("apps.api.workers.arq_worker.audit_service.run_document_audit", new_callable=AsyncMock) as mock_audit:

        # Attempt to process Org A document under Org B context
        with pytest.raises(ValueError, match="does not belong to organization"):
            await process_document_audit_job(
                ctx={},
                organization_id=org_b,
                document_id=doc_id,
                actor_user_id=None
            )

        mock_audit.assert_not_called()


@pytest.mark.asyncio
async def test_worker_rejects_unauthorized_user_actor(test_tenant_data: dict, db_session: AsyncSession):
    """Verifies that the worker rejects execution if a provided user is not a member of the organization."""
    org_id = test_tenant_data["org_id"]
    doc_id = str(uuid.uuid4())
    intruder_user_id = str(uuid.uuid4())

    doc = Document(
        id=doc_id,
        organization_id=org_id,
        title="Protected Document",
        original_filename="prot.pdf",
        storage_key="test/prot.pdf",
        sha256="e" * 64,
        mime_type="application/pdf",
        byte_size=1024,
        processing_status="UPLOADED"
    )
    db_session.add(doc)
    await db_session.commit()

    factory = create_session_factory(db_session)

    with patch("apps.api.workers.arq_worker.async_session_factory", factory), \
         patch("apps.api.workers.arq_worker.audit_service.run_document_audit", new_callable=AsyncMock) as mock_audit:

        with pytest.raises(PermissionError, match="is not authorized for organization"):
            await process_document_audit_job(
                ctx={},
                organization_id=org_id,
                document_id=doc_id,
                actor_user_id=intruder_user_id
            )

        mock_audit.assert_not_called()


@pytest.mark.asyncio
async def test_worker_rejects_malformed_identifiers():
    """Verifies that worker validates and rejects malformed UUID identifiers."""
    with pytest.raises(ValueError, match="Invalid UUID in job parameters"):
        await process_document_audit_job(
            ctx={},
            organization_id="not-a-uuid",
            document_id="also-not-a-uuid"
        )


@pytest.mark.asyncio
async def test_background_audit_runner_tenant_context_and_authorization(test_tenant_data: dict, db_session: AsyncSession):
    """Verifies that _run_audit_background initializes tenant context and verifies authorization."""
    org_id = test_tenant_data["org_id"]
    user_id = test_tenant_data["user_id"]
    doc_id = str(uuid.uuid4())
    unauth_user_id = str(uuid.uuid4())

    doc = Document(
        id=doc_id,
        organization_id=org_id,
        title="Background Ingestion Doc",
        original_filename="bg.pdf",
        storage_key="test/bg.pdf",
        sha256="f" * 64,
        mime_type="application/pdf",
        byte_size=1024,
        processing_status="UPLOADED"
    )
    db_session.add(doc)
    await db_session.commit()

    factory = create_session_factory(db_session)

    with patch("apps.api.routers.documents.async_session_factory", factory), \
         patch("apps.api.routers.documents.audit_service.run_document_audit", new_callable=AsyncMock) as mock_audit:

        # 1. Valid member triggers background audit -> succeeds
        await _run_audit_background(org_id=org_id, doc_id=doc_id, user_id=user_id)
        mock_audit.assert_awaited_once_with(
            db=pytest.approx(mock_audit.call_args.kwargs["db"]),
            organization_id=org_id,
            document_id=doc_id,
            actor_user_id=user_id
        )

        mock_audit.reset_mock()

        # 2. Unauthorized user -> rejected before invoking audit pipeline
        await _run_audit_background(org_id=org_id, doc_id=doc_id, user_id=unauth_user_id)
        mock_audit.assert_not_called()

        # 3. Cross-tenant mismatch -> rejected
        other_org_id = str(uuid.uuid4())
        await _run_audit_background(org_id=other_org_id, doc_id=doc_id, user_id=user_id)
        mock_audit.assert_not_called()

        # 4. Malformed IDs -> aborted safely without exception leakage
        await _run_audit_background(org_id="malformed", doc_id="malformed", user_id="malformed")
        mock_audit.assert_not_called()


@pytest.mark.asyncio
async def test_array_columns_roundtrip(db_session: AsyncSession, test_tenant_data: dict):
    """
    Verifies that unreadable_pages and uncapped_carveouts are persisted and retrieved
    as native Python lists (both empty and populated), and validates PostgreSQL DDL types.
    """
    org_id = test_tenant_data["org_id"]
    doc_id = str(uuid.uuid4())
    clause_id = str(uuid.uuid4())

    # 1. Test Document unreadable_pages
    doc = Document(
        id=doc_id,
        organization_id=org_id,
        title="Array Test Doc",
        original_filename="array.pdf",
        storage_key="test/array.pdf",
        sha256="1" * 64,
        mime_type="application/pdf",
        byte_size=500,
        unreadable_pages=[3, 7, 12]
    )
    db_session.add(doc)

    # 2. Test ExtractedClause uncapped_carveouts
    clause = ExtractedClause(
        id=clause_id,
        organization_id=org_id,
        document_id=doc_id,
        clause_category="LIMITATION_OF_LIABILITY",
        presence_status="EXPLICIT_PRESENT",
        factual_summary="Test carveouts",
        uncapped_carveouts=["gross_negligence", "willful_misconduct", "ip_infringement"]
    )
    db_session.add(clause)
    await db_session.commit()

    # Query back
    fetched_doc = await db_session.get(Document, doc_id)
    assert isinstance(fetched_doc.unreadable_pages, list)
    assert fetched_doc.unreadable_pages == [3, 7, 12]

    fetched_clause = await db_session.get(ExtractedClause, clause_id)
    assert isinstance(fetched_clause.uncapped_carveouts, list)
    assert fetched_clause.uncapped_carveouts == ["gross_negligence", "willful_misconduct", "ip_infringement"]

    # Test empty list default
    doc_empty = Document(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        title="Empty Array Doc",
        original_filename="empty.pdf",
        storage_key="test/empty.pdf",
        sha256="2" * 64,
        mime_type="application/pdf",
        byte_size=500
    )
    db_session.add(doc_empty)
    await db_session.commit()

    fetched_empty = await db_session.get(Document, doc_empty.id)
    assert fetched_empty.unreadable_pages == []

    # 3. Verify PostgreSQL DDL compilation matches 0001_mvp_schema.sql
    pg_ddl_doc = str(CreateTable(Document.__table__).compile(dialect=postgresql.dialect()))
    assert "unreadable_pages INTEGER[]" in pg_ddl_doc

    pg_ddl_clause = str(CreateTable(ExtractedClause.__table__).compile(dialect=postgresql.dialect()))
    assert "uncapped_carveouts TEXT[]" in pg_ddl_clause


@pytest.mark.asyncio
async def test_tenant_context_reapplication_across_commits(db_session: AsyncSession):
    """
    Verifies that tenant context is automatically reapplied to new transactions
    created after intermediate commits, ensuring RLS continuity across multi-stage pipelines.
    """
    user_uuid = uuid.uuid4()
    org_uuid = uuid.uuid4()

    await set_tenant_context(db_session, user_id=user_uuid, organization_id=org_uuid)
    initial_count = db_session.info.get("tenant_context_reapplied_count", 0)

    # Transaction 1: Execute statement and commit
    await db_session.execute(select(1))
    await db_session.commit()
    assert db_session.info.get("tenant_context_reapplied_count") == initial_count + 1
    assert db_session.info.get("last_applied_org_id") == str(org_uuid)
    assert db_session.info.get("last_applied_user_id") == str(user_uuid)

    # Transaction 2: Execute statement in subsequent transaction on same session
    await db_session.execute(select(2))
    await db_session.commit()
    assert db_session.info.get("tenant_context_reapplied_count") == initial_count + 2
    assert db_session.info.get("last_applied_org_id") == str(org_uuid)
    assert db_session.info.get("last_applied_user_id") == str(user_uuid)


@pytest.mark.asyncio
async def test_tenant_context_reapplication_after_rollback(db_session: AsyncSession):
    """
    Verifies that tenant context is automatically reapplied to new transactions
    created after a rollback occurs.
    """
    user_uuid = uuid.uuid4()
    org_uuid = uuid.uuid4()

    await set_tenant_context(db_session, user_id=user_uuid, organization_id=org_uuid)
    initial_count = db_session.info.get("tenant_context_reapplied_count", 0)

    # Transaction 1: Execute statement and rollback
    await db_session.execute(select(1))
    await db_session.rollback()
    assert db_session.info.get("tenant_context_reapplied_count") == initial_count + 1

    # Transaction 2: Next transaction after rollback re-establishes tenant context
    await db_session.execute(select(2))
    await db_session.commit()
    assert db_session.info.get("tenant_context_reapplied_count") == initial_count + 2
    assert db_session.info.get("last_applied_org_id") == str(org_uuid)
    assert db_session.info.get("last_applied_user_id") == str(user_uuid)


@pytest.mark.asyncio
async def test_fresh_session_has_no_tenant_context_and_no_leakage(test_tenant_data: dict, db_session: AsyncSession):
    """
    Verifies that tenant context does not leak between sessions or pool checkouts.
    An unconfigured session must not have any tenant context or trigger the tenant hook.
    """
    org_id = test_tenant_data["org_id"]
    user_id = test_tenant_data["user_id"]

    # Session A is configured for Org A
    await set_tenant_context(db_session, user_id=user_id, organization_id=org_id)
    await db_session.execute(select(1))
    await db_session.commit()
    assert db_session.info.get("current_organization_id") == org_id

    # Session B is a fresh unconfigured session
    from apps.api.tests.conftest import TestingSessionLocal
    async with TestingSessionLocal() as session_b:
        # Before any statement
        assert session_b.info.get("current_organization_id") is None
        assert session_b.info.get("tenant_context_reapplied_count") is None

        # Execute statement without calling set_tenant_context
        await session_b.execute(select(1))
        await session_b.commit()

        # Context hook must NOT fire for unconfigured session
        assert session_b.info.get("current_organization_id") is None
        assert session_b.info.get("tenant_context_reapplied_count") is None


@pytest.mark.asyncio
async def test_system_worker_context_reapplication_no_user_fabricated(db_session: AsyncSession):
    """
    Verifies system-worker policy during transaction reapplication:
    When user_id is None, user context remains empty string (SQL NULL in RLS),
    and no fake user UUID is fabricated across multiple transactions.
    """
    org_uuid = uuid.uuid4()

    await set_tenant_context(db_session, user_id=None, organization_id=org_uuid)
    initial_count = db_session.info.get("tenant_context_reapplied_count", 0)

    # Transaction 1
    await db_session.execute(select(1))
    await db_session.commit()
    assert db_session.info.get("tenant_context_reapplied_count") == initial_count + 1
    assert db_session.info.get("last_applied_org_id") == str(org_uuid)
    assert db_session.info.get("last_applied_user_id") == ""
    assert db_session.info.get("current_user_id") is None

    # Transaction 2
    await db_session.execute(select(2))
    await db_session.commit()
    assert db_session.info.get("tenant_context_reapplied_count") == initial_count + 2
    assert db_session.info.get("last_applied_org_id") == str(org_uuid)
    assert db_session.info.get("last_applied_user_id") == ""


@pytest.mark.asyncio
async def test_after_begin_hook_uses_supplied_connection_without_session_recursion(db_session: AsyncSession):
    """
    Verifies that the after_begin hook executes directly on the supplied connection
    without initiating nested transactions or recursive session operations.
    """
    org_uuid = uuid.uuid4()
    user_uuid = uuid.uuid4()

    await set_tenant_context(db_session, user_id=user_uuid, organization_id=org_uuid)

    # Track in_transaction state
    assert not db_session.in_transaction()
    await db_session.execute(select(100))
    # Transaction opened and statement executed cleanly without deadlocks or recursion
    assert db_session.in_transaction()
    await db_session.commit()
    assert not db_session.in_transaction()
