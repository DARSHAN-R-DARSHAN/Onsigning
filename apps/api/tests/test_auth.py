"""
Tests for Supabase Auth JWT validation, organization authorization, and tenant isolation.
"""

import uuid
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import UnboundExecutionError

from apps.api.core.auth import create_mock_jwt, get_tenant_context
from apps.api.core.database import (
    User, Organization, OrganizationMembership, RiskPlaybook,
    Document, ExtractedClause, AuditRun, RiskAlert, AuditLog,
    PortableUUID,
    set_user_context, set_tenant_context, async_session_factory,
    is_sqlite, _get_session_dialect_name, _is_sqlite_session
)
from sqlalchemy.dialects import postgresql, sqlite
from apps.api.core.exceptions import AuthorizationError


@pytest.mark.asyncio
async def test_valid_auth_token_accesses_playbooks(client: AsyncClient, test_tenant_data: dict):
    """A valid Supabase JWT and correct X-Organization-Id header successfully authorizes requests."""
    res = await client.get("/api/v1/playbooks", headers=test_tenant_data["headers"])
    assert res.status_code == 200
    playbooks = res.json()
    assert len(playbooks) >= 1
    assert playbooks[0]["name"] == "Standard B2B SaaS Playbook"


@pytest.mark.asyncio
async def test_missing_auth_header_rejected(client: AsyncClient, test_tenant_data: dict):
    """Requests without an Authorization header receive HTTP 401 Unauthorized."""
    res = await client.get("/api/v1/playbooks", headers={"X-Organization-Id": test_tenant_data["org_id"]})
    assert res.status_code == 401
    assert "Authorization header is missing" in res.json()["message"]


@pytest.mark.asyncio
async def test_invalid_auth_token_rejected(client: AsyncClient, test_tenant_data: dict):
    """Requests with forged or expired JWTs receive HTTP 401 Unauthorized."""
    invalid_headers = {
        "Authorization": "Bearer not-a-real-jwt-token.signature.invalid",
        "X-Organization-Id": test_tenant_data["org_id"]
    }
    res = await client.get("/api/v1/playbooks", headers=invalid_headers)
    assert res.status_code == 401
    assert "Invalid authentication token" in res.json()["message"]


@pytest.mark.asyncio
async def test_missing_organization_header_rejected(client: AsyncClient, test_tenant_data: dict):
    """Requests without X-Organization-Id workspace header receive HTTP 403 Forbidden."""
    headers = {"Authorization": f"Bearer {test_tenant_data['token']}"}
    res = await client.get("/api/v1/playbooks", headers=headers)
    assert res.status_code == 403
    assert "Missing required 'X-Organization-Id'" in res.json()["message"]


@pytest.mark.asyncio
async def test_cross_organization_access_forbidden(client: AsyncClient, test_tenant_data: dict, db_session: AsyncSession):
    """
    A valid user requesting access to an organization where they have NO membership
    is strictly blocked with HTTP 403.
    """
    other_org_id = str(uuid.uuid4())
    other_org = Organization(
        id=other_org_id,
        name="Other Corp",
        slug="other-corp"
    )
    db_session.add(other_org)
    await db_session.commit()

    cross_headers = {
        "Authorization": f"Bearer {test_tenant_data['token']}",
        "X-Organization-Id": other_org_id
    }
    res = await client.get("/api/v1/playbooks", headers=cross_headers)
    assert res.status_code == 403
    assert "User is not authorized for the requested organization" in res.json()["message"]


@pytest.mark.asyncio
async def test_jit_user_provisioning(client: AsyncClient, db_session: AsyncSession):
    """
    Supabase authenticated user gets provisioned just-in-time into public.users
    when presenting a valid signed JWT for the first time.
    """
    new_user_id = str(uuid.uuid4())
    new_email = "jit.attorney@acme.com"
    token = create_mock_jwt(user_id=new_user_id, email=new_email)

    org_id = str(uuid.uuid4())
    org = Organization(id=org_id, name="Legal Org", slug="legal-org")
    db_session.add(org)
    await db_session.flush()

    # Pre-authorize membership
    membership = OrganizationMembership(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        user_id=new_user_id,
        role="LEGAL_REVIEWER"
    )
    db_session.add(membership)
    await db_session.commit()

    # User does NOT yet exist in users table
    stmt = select(User).where(User.id == new_user_id)
    assert (await db_session.execute(stmt)).scalar_one_or_none() is None

    # Make request
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": org_id
    }
    res = await client.get("/api/v1/playbooks", headers=headers)
    assert res.status_code == 200

    # User now exists via JIT provisioning
    persisted = (await db_session.execute(stmt)).scalar_one_or_none()
    assert persisted is not None
    assert persisted.email == new_email


@pytest.mark.asyncio
async def test_malformed_organization_header_rejected(client: AsyncClient, test_tenant_data: dict):
    """Requests with non-UUID X-Organization-Id receive HTTP 403 Forbidden."""
    headers = {
        "Authorization": f"Bearer {test_tenant_data['token']}",
        "X-Organization-Id": "not-a-valid-uuid"
    }
    res = await client.get("/api/v1/playbooks", headers=headers)
    assert res.status_code == 403
    assert "Invalid 'X-Organization-Id' UUID format" in res.json()["message"]


@pytest.mark.asyncio
async def test_multi_org_membership_context_switching(client: AsyncClient, db_session: AsyncSession):
    """
    A user who belongs to multiple organizations can access each organization
    independently by supplying the corresponding X-Organization-Id header.
    """
    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        email="multi.member@domain.com",
        full_name="Multi Org Member",
        external_auth_id=user_id,
        auth_provider="supabase"
    )
    db_session.add(user)

    # Org 1
    org_1_id = str(uuid.uuid4())
    org_1 = Organization(id=org_1_id, name="Org Alpha", slug="org-alpha")
    db_session.add(org_1)
    db_session.add(OrganizationMembership(
        id=str(uuid.uuid4()),
        organization_id=org_1_id,
        user_id=user_id,
        role="ADMIN"
    ))
    db_session.add(RiskPlaybook(
        id=str(uuid.uuid4()),
        organization_id=org_1_id,
        name="Alpha Playbook",
        version=1,
        rules="[]"
    ))

    # Org 2
    org_2_id = str(uuid.uuid4())
    org_2 = Organization(id=org_2_id, name="Org Beta", slug="org-beta")
    db_session.add(org_2)
    db_session.add(OrganizationMembership(
        id=str(uuid.uuid4()),
        organization_id=org_2_id,
        user_id=user_id,
        role="VIEWER"
    ))
    db_session.add(RiskPlaybook(
        id=str(uuid.uuid4()),
        organization_id=org_2_id,
        name="Beta Playbook",
        version=1,
        rules="[]"
    ))

    # Org 3 (unrelated, user has NO membership)
    org_3_id = str(uuid.uuid4())
    org_3 = Organization(id=org_3_id, name="Org Gamma", slug="org-gamma")
    db_session.add(org_3)

    await db_session.commit()

    token = create_mock_jwt(user_id=user_id, email=user.email)

    # Request Org 1 -> Success (Admin)
    res_1 = await client.get("/api/v1/playbooks", headers={
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": org_1_id
    })
    assert res_1.status_code == 200
    playbooks_1 = res_1.json()
    assert len(playbooks_1) == 1
    assert playbooks_1[0]["name"] == "Alpha Playbook"

    # Request Org 2 -> Success (Viewer)
    res_2 = await client.get("/api/v1/playbooks", headers={
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": org_2_id
    })
    assert res_2.status_code == 200
    playbooks_2 = res_2.json()
    assert len(playbooks_2) == 1
    assert playbooks_2[0]["name"] == "Beta Playbook"

    # Request Org 3 -> Forbidden (No membership)
    res_3 = await client.get("/api/v1/playbooks", headers={
        "Authorization": f"Bearer {token}",
        "X-Organization-Id": org_3_id
    })
    assert res_3.status_code == 403
    assert "User is not authorized for the requested organization" in res_3.json()["message"]


@pytest.mark.asyncio
async def test_set_user_context_validation(db_session: AsyncSession):
    """Verifies input validation on set_user_context."""
    with pytest.raises(ValueError, match="user_id must be provided"):
        await set_user_context(db_session, user_id=None)

    with pytest.raises(ValueError, match="user_id must be provided"):
        await set_user_context(db_session, user_id="")

    with pytest.raises(ValueError, match="Invalid user_id UUID"):
        await set_user_context(db_session, user_id="not-a-uuid")

    valid_uuid = uuid.uuid4()
    await set_user_context(db_session, user_id=valid_uuid)
    assert db_session.info["last_applied_user_id"] == str(valid_uuid)
    # Organization context must NOT be established
    assert db_session.info.get("current_organization_id") is None


@pytest.mark.asyncio
async def test_user_only_bootstrap_does_not_expose_other_user_membership(db_session: AsyncSession):
    """
    Verifies that establishing user-only context for User A allows querying only User A's
    membership, and filtering by requested organization ensures isolation.
    """
    user_a_id = str(uuid.uuid4())
    user_b_id = str(uuid.uuid4())
    org_a_id = str(uuid.uuid4())
    org_b_id = str(uuid.uuid4())

    db_session.add(User(id=user_a_id, email="a@test.com", external_auth_id=user_a_id, auth_provider="supabase"))
    db_session.add(User(id=user_b_id, email="b@test.com", external_auth_id=user_b_id, auth_provider="supabase"))
    db_session.add(Organization(id=org_a_id, name="Org A", slug="org-a"))
    db_session.add(Organization(id=org_b_id, name="Org B", slug="org-b"))

    # User A is member of Org A only; User B is member of Org B only
    db_session.add(OrganizationMembership(id=str(uuid.uuid4()), organization_id=org_a_id, user_id=user_a_id, role="OWNER"))
    db_session.add(OrganizationMembership(id=str(uuid.uuid4()), organization_id=org_b_id, user_id=user_b_id, role="OWNER"))
    await db_session.commit()

    # Bootstrap User A context
    await set_user_context(db_session, user_a_id)

    # User A querying Org A membership -> Found
    stmt_a = select(OrganizationMembership).where(
        OrganizationMembership.organization_id == org_a_id,
        OrganizationMembership.user_id == user_a_id
    )
    membership_a = (await db_session.execute(stmt_a)).scalar_one_or_none()
    assert membership_a is not None
    assert membership_a.role == "OWNER"

    # User A querying Org B membership -> None (User A is NOT a member of Org B)
    stmt_b = select(OrganizationMembership).where(
        OrganizationMembership.organization_id == org_b_id,
        OrganizationMembership.user_id == user_a_id
    )
    membership_b = (await db_session.execute(stmt_b)).scalar_one_or_none()
    assert membership_b is None

    # Org context must remain uninitialized on the session
    assert db_session.info.get("current_organization_id") is None


@pytest.mark.asyncio
async def test_session_isolation_tenant_and_user_context(db_session: AsyncSession):
    """
    Verifies that user and tenant context established on one session
    does NOT leak into an independent newly opened session.
    """
    org_uuid = uuid.uuid4()
    user_uuid = uuid.uuid4()

    # Session 1: configure full tenant context
    await set_user_context(db_session, user_uuid)
    await set_tenant_context(db_session, user_id=user_uuid, organization_id=org_uuid)
    assert db_session.info["current_organization_id"] == str(org_uuid)
    assert db_session.info["current_user_id"] == str(user_uuid)

    # Session 2: completely fresh session
    async with async_session_factory() as session_2:
        assert session_2.info.get("current_organization_id") is None
        assert session_2.info.get("current_user_id") is None
        assert session_2.info.get("last_applied_user_id") is None
        assert session_2.info.get("last_applied_org_id") is None


@pytest.mark.asyncio
async def test_tenant_context_continuity_after_commit_and_rollback(db_session: AsyncSession):
    """
    Verifies that established tenant context persists across both commit and rollback
    boundaries via the after_begin hook without needing re-invocation.
    """
    org_uuid = uuid.uuid4()
    user_uuid = uuid.uuid4()

    await set_tenant_context(db_session, user_id=user_uuid, organization_id=org_uuid)
    initial_reapplied = db_session.info.get("tenant_context_reapplied_count", 0)

    # Transaction 1: Commit boundary
    await db_session.execute(select(1))
    await db_session.commit()
    assert db_session.info.get("tenant_context_reapplied_count") == initial_reapplied + 1
    assert db_session.info.get("current_organization_id") == str(org_uuid)

    # Transaction 2: Rollback boundary
    await db_session.execute(select(2))
    await db_session.rollback()
    assert db_session.info.get("tenant_context_reapplied_count") == initial_reapplied + 2
    assert db_session.info.get("current_organization_id") == str(org_uuid)

    # Transaction 3: Read after rollback retains context
    res = await db_session.execute(select(3))
    assert res.scalar() == 3
    assert db_session.info.get("tenant_context_reapplied_count") == initial_reapplied + 3


@pytest.mark.asyncio
async def test_set_user_context_resets_prior_organization_context(db_session: AsyncSession):
    """
    Task 5.10 Regression:
    Verifies that calling set_user_context() on a session that previously held an
    authorized organization context explicitly resets the organization context from session.info
    before any SQL execution, preventing stale tenant retention.
    """
    old_org_uuid = uuid.uuid4()
    old_user_uuid = uuid.uuid4()
    new_user_uuid = uuid.uuid4()

    # Pre-condition: Session has active Tenant A context
    await set_tenant_context(db_session, user_id=old_user_uuid, organization_id=old_org_uuid)
    assert db_session.info.get("current_organization_id") == str(old_org_uuid)
    assert db_session.info.get("current_user_id") == str(old_user_uuid)
    assert db_session.info.get("last_applied_org_id") == str(old_org_uuid)

    # Action: Initialize user-only context for User B
    await set_user_context(db_session, user_id=new_user_uuid)

    # Assertion: Organization context is completely eradicated
    assert db_session.info.get("current_organization_id") is None
    assert db_session.info.get("current_user_id") is None
    assert db_session.info.get("last_applied_org_id") == ""
    assert db_session.info.get("last_applied_user_id") == str(new_user_uuid)
    assert db_session.info.get("tenant_context_reapplied_count") is None


@pytest.mark.asyncio
async def test_after_begin_listener_does_not_reapply_old_org_after_reset(db_session: AsyncSession):
    """
    Task 5.10 Regression:
    Verifies that when a session with prior organization context is reset via set_user_context(),
    subsequent new transactions (which trigger Session.after_begin) do NOT reapply the old organization.
    """
    old_org_uuid = uuid.uuid4()
    old_user_uuid = uuid.uuid4()
    new_user_uuid = uuid.uuid4()

    # 1. Establish initial context and commit
    await set_tenant_context(db_session, user_id=old_user_uuid, organization_id=old_org_uuid)
    await db_session.execute(select(1))
    await db_session.commit()
    assert db_session.info.get("current_organization_id") == str(old_org_uuid)

    # 2. Reset context via set_user_context
    await set_user_context(db_session, user_id=new_user_uuid)

    # 3. Commit/start new transaction -> triggers after_begin hook
    await db_session.execute(select(2))
    await db_session.commit()

    # 4. Listener must NOT have reapplied the old organization
    assert db_session.info.get("current_organization_id") is None
    assert db_session.info.get("last_applied_org_id") == ""
    assert db_session.info.get("tenant_context_reapplied_count") is None


@pytest.mark.asyncio
async def test_set_user_context_clears_postgresql_org_context_statements():
    """
    Task 5.10 Regression:
    Verifies that in PostgreSQL dialect mode, set_user_context() executes:
    1. SELECT set_config('app.current_user_id', :uid, true);
    2. SELECT set_config('app.current_organization_id', '', true);
    ensuring PostgreSQL transaction-local organization setting is cleared with is_local=true.
    """
    test_user_id = uuid.uuid4()
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.info = {
        "current_organization_id": str(uuid.uuid4()),
        "current_user_id": str(uuid.uuid4()),
        "last_applied_org_id": str(uuid.uuid4()),
        "tenant_context_reapplied_count": 5
    }

    executed_queries = []
    async def fake_execute(statement, params=None):
        executed_queries.append((str(statement), params))
        return AsyncMock()

    mock_session.execute = fake_execute

    with patch("apps.api.core.database.is_sqlite", False):
        await set_user_context(mock_session, user_id=test_user_id)

    # Verify session.info cleared before SQL
    assert mock_session.info.get("current_organization_id") is None
    assert mock_session.info.get("current_user_id") is None
    assert mock_session.info.get("last_applied_org_id") is None
    assert mock_session.info.get("tenant_context_reapplied_count") is None

    # Verify exact SQL statements executed
    assert len(executed_queries) == 2
    # Statement 1: sets user ID
    stmt_1, params_1 = executed_queries[0]
    assert "set_config('app.current_user_id'" in stmt_1
    assert params_1 == {"uid": str(test_user_id)}
    # Statement 2: clears organization ID
    stmt_2, params_2 = executed_queries[1]
    assert "set_config('app.current_organization_id', '', true)" in stmt_2


@pytest.mark.asyncio
async def test_failed_membership_authorization_cleanses_session_for_subsequent_operations(
    db_session: AsyncSession
):
    """
    Task 5.10 Regression:
    Verifies that when a session with prior organization context attempts an unauthorized
    workspace access (membership not found), the prior organization context is eradicated
    and cannot leak into subsequent operations on that session.
    """
    user_1_id = str(uuid.uuid4())
    user_2_id = str(uuid.uuid4())
    org_1_id = str(uuid.uuid4())
    org_2_id = str(uuid.uuid4())

    db_session.add(User(id=user_1_id, email="u1@test.com", external_auth_id=user_1_id, auth_provider="supabase"))
    db_session.add(User(id=user_2_id, email="u2@test.com", external_auth_id=user_2_id, auth_provider="supabase"))
    db_session.add(Organization(id=org_1_id, name="Org 1", slug="org-1"))
    db_session.add(Organization(id=org_2_id, name="Org 2", slug="org-2"))
    # User 1 is member of Org 1 only; User 2 has NO membership in Org 2
    db_session.add(OrganizationMembership(id=str(uuid.uuid4()), organization_id=org_1_id, user_id=user_1_id, role="OWNER"))
    await db_session.commit()

    # Pre-condition: Reused session holds Org 1 context
    await set_tenant_context(db_session, user_id=user_1_id, organization_id=org_1_id)
    assert db_session.info.get("current_organization_id") == str(org_1_id)

    # Action: User 2 requests Org 2 via get_tenant_context (unauthorized)
    mock_request = AsyncMock()
    user_2 = (await db_session.execute(select(User).where(User.id == user_2_id))).scalar_one()

    with pytest.raises(AuthorizationError, match="User is not authorized for the requested organization"):
        await get_tenant_context(
            request=mock_request,
            user=user_2,
            x_organization_id=org_2_id,
            db=db_session
        )

    # Assertion: Org 1 is NOT retained in session.info, and Org 2 was never set
    assert db_session.info.get("current_organization_id") is None
    assert db_session.info.get("last_applied_org_id") == ""

    # Subsequent transaction on this session must NOT reapply Org 1
    await db_session.execute(select(1))
    await db_session.commit()
    assert db_session.info.get("current_organization_id") is None
    assert db_session.info.get("last_applied_org_id") == ""


@pytest.mark.asyncio
async def test_subsequent_authorized_tenant_selection_establishes_correct_new_context(
    db_session: AsyncSession
):
    """
    Task 5.10 Regression:
    Verifies that after context reset, a subsequent authorized organization request
    cleanly establishes the new tenant context without residual traces of the old tenant.
    """
    user_id = str(uuid.uuid4())
    org_1_id = str(uuid.uuid4())
    org_2_id = str(uuid.uuid4())

    user = User(id=user_id, email="switch@test.com", external_auth_id=user_id, auth_provider="supabase")
    db_session.add(user)
    db_session.add(Organization(id=org_1_id, name="Prior Org", slug="prior-org"))
    db_session.add(Organization(id=org_2_id, name="Target Org", slug="target-org"))
    db_session.add(OrganizationMembership(id=str(uuid.uuid4()), organization_id=org_1_id, user_id=user_id, role="VIEWER"))
    db_session.add(OrganizationMembership(id=str(uuid.uuid4()), organization_id=org_2_id, user_id=user_id, role="ADMIN"))
    await db_session.commit()

    # 1. Prior context: Org 1
    await set_tenant_context(db_session, user_id=user_id, organization_id=org_1_id)
    assert db_session.info.get("current_organization_id") == str(org_1_id)

    # 2. Switch context to Org 2 via get_tenant_context
    mock_request = AsyncMock()
    context = await get_tenant_context(
        request=mock_request,
        user=user,
        x_organization_id=org_2_id,
        db=db_session
    )

    # 3. Context is correctly updated to Org 2 (Admin)
    assert context.organization.id == str(org_2_id)
    assert context.role == "ADMIN"
    assert db_session.info.get("current_organization_id") == str(org_2_id)
    assert db_session.info.get("current_user_id") == str(user_id)

    # 4. Subsequent transaction preserves Org 2
    await db_session.execute(select(1))
    await db_session.commit()
    assert db_session.info.get("current_organization_id") == str(org_2_id)


# ==============================================================================
# Task 5.12.26 Regression Tests: Dialect-Aware Tenant & User Context
# ==============================================================================

@pytest.mark.asyncio
async def test_sqlite_bound_session_retains_behavior_and_emits_no_set_config(
    db_session: AsyncSession
):
    """
    Task 5.12.26 Regression (Requirement 1):
    Proves that a real SQLite-bound AsyncSession:
    - Resolves its dialect dynamically as 'sqlite'.
    - Updates session.info tracking fields (last_applied_user_id, last_applied_org_id).
    - Does NOT attempt to execute raw PostgreSQL set_config statements (which would fail on SQLite).
    """
    assert _get_session_dialect_name(db_session) == "sqlite"
    assert _is_sqlite_session(db_session) is True

    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    # 1. Establish tenant context on SQLite
    await set_tenant_context(db_session, user_id=user_id, organization_id=org_id)
    assert db_session.info["current_organization_id"] == str(org_id)
    assert db_session.info["current_user_id"] == str(user_id)
    assert db_session.info["last_applied_org_id"] == str(org_id)
    assert db_session.info["last_applied_user_id"] == str(user_id)

    # 2. Reset context via set_user_context on SQLite
    new_user_id = str(uuid.uuid4())
    await set_user_context(db_session, user_id=new_user_id)
    assert db_session.info.get("current_organization_id") is None
    assert db_session.info.get("current_user_id") is None
    assert db_session.info.get("last_applied_org_id") == ""
    assert db_session.info.get("last_applied_user_id") == str(new_user_id)


@pytest.mark.asyncio
async def test_postgresql_bound_session_executes_set_config_when_global_is_sqlite_true():
    """
    Task 5.12.26 Regression (Requirement 2):
    Proves that a PostgreSQL-bound session executes transaction-local set_config SQL
    even when the module-level is_sqlite flag remains True.
    """
    assert is_sqlite is True, "Precondition: default module-level is_sqlite must be True in unit test env."

    mock_session = AsyncMock(spec=AsyncSession)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.get_bind.return_value = mock_bind
    mock_session.in_transaction.return_value = True
    mock_session.info = {}

    executed_queries = []
    async def fake_execute(statement, params=None):
        executed_queries.append((str(statement), params))
        return AsyncMock()

    mock_session.execute = fake_execute

    # Dialect detection must resolve postgresql despite global is_sqlite=True
    assert _get_session_dialect_name(mock_session) == "postgresql"
    assert _is_sqlite_session(mock_session) is False

    test_user_id = uuid.uuid4()
    test_org_id = uuid.uuid4()

    # set_tenant_context in active transaction
    await set_tenant_context(mock_session, user_id=test_user_id, organization_id=test_org_id)

    assert len(executed_queries) == 2
    stmt_user, params_user = executed_queries[0]
    assert "set_config('app.current_user_id'" in stmt_user
    assert params_user == {"uid": str(test_user_id)}

    stmt_org, params_org = executed_queries[1]
    assert "set_config('app.current_organization_id'" in stmt_org
    assert params_org == {"org_id": str(test_org_id)}


@pytest.mark.asyncio
async def test_postgresql_bound_session_set_user_context_clears_stale_org_context():
    """
    Task 5.12.26 Regression (Requirement 3):
    Proves that set_user_context() on a PostgreSQL-bound session cleanses session.info
    and executes SQL:
    1. SELECT set_config('app.current_user_id', :uid, true);
    2. SELECT set_config('app.current_organization_id', '', true);
    """
    mock_session = AsyncMock(spec=AsyncSession)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.get_bind.return_value = mock_bind
    mock_session.info = {
        "current_organization_id": str(uuid.uuid4()),
        "current_user_id": str(uuid.uuid4()),
        "last_applied_org_id": str(uuid.uuid4()),
        "tenant_context_reapplied_count": 3,
    }

    executed_queries = []
    async def fake_execute(statement, params=None):
        executed_queries.append((str(statement), params))
        return AsyncMock()

    mock_session.execute = fake_execute

    test_user_id = uuid.uuid4()
    await set_user_context(mock_session, user_id=test_user_id)

    # 1. session.info is cleansed
    assert mock_session.info.get("current_organization_id") is None
    assert mock_session.info.get("current_user_id") is None
    assert mock_session.info.get("last_applied_org_id") is None
    assert mock_session.info.get("tenant_context_reapplied_count") is None

    # 2. Both GUCs are set/cleared in PostgreSQL
    assert len(executed_queries) == 2
    stmt_user, params_user = executed_queries[0]
    assert "set_config('app.current_user_id'" in stmt_user
    assert params_user == {"uid": str(test_user_id)}

    stmt_org, _ = executed_queries[1]
    assert "set_config('app.current_organization_id', '', true)" in stmt_org


@pytest.mark.asyncio
async def test_postgresql_bound_session_set_tenant_context_in_active_transaction():
    """
    Task 5.12.26 Regression (Requirement 4):
    Proves that set_tenant_context() applies context immediately via set_config when
    a transaction is active, and defers execution to after_begin when no transaction is active.
    Also verifies system-actor policy (actor_user_id=None sets current_user_id to '').
    """
    mock_session = AsyncMock(spec=AsyncSession)
    mock_bind = MagicMock()
    mock_bind.dialect.name = "postgresql"
    mock_session.get_bind.return_value = mock_bind
    mock_session.info = {}

    executed_queries = []
    async def fake_execute(statement, params=None):
        executed_queries.append((str(statement), params))
        return AsyncMock()

    mock_session.execute = fake_execute

    test_org_id = uuid.uuid4()

    # Case A: in_transaction is False -> does NOT execute SQL immediately; populates session.info
    mock_session.in_transaction.return_value = False
    await set_tenant_context(mock_session, user_id=None, organization_id=test_org_id)
    assert len(executed_queries) == 0
    assert mock_session.info["current_organization_id"] == str(test_org_id)
    assert mock_session.info["current_user_id"] is None

    # Case B: in_transaction is True -> executes set_config immediately with system actor (empty user string)
    mock_session.in_transaction.return_value = True
    await set_tenant_context(mock_session, user_id=None, organization_id=test_org_id)
    assert len(executed_queries) == 2
    stmt_user, params_user = executed_queries[0]
    assert "set_config('app.current_user_id'" in stmt_user
    assert params_user == {"uid": ""}
    stmt_org, params_org = executed_queries[1]
    assert "set_config('app.current_organization_id'" in stmt_org
    assert params_org == {"org_id": str(test_org_id)}


@pytest.mark.asyncio
async def test_unbound_session_dialect_resolution_and_fallback():
    """
    Task 5.12.26 Regression (Requirement 5):
    Proves that:
    1. An unbound session raising UnboundExecutionError deliberately falls back to the configured default.
    2. A mock session lacking a concrete dialect string deliberately falls back to the configured default.
    3. The fallback is documented and explicit, not a broad silent exception swallow.
    """
    # Case 1: Unbound session raising UnboundExecutionError
    unbound_session = AsyncMock(spec=AsyncSession)
    unbound_session.get_bind.side_effect = UnboundExecutionError("Session is not bound to an Engine.")
    assert _get_session_dialect_name(unbound_session) == ("sqlite" if is_sqlite else "postgresql")

    # Case 2: Generic mock session whose get_bind().dialect.name is a MagicMock (not a str)
    generic_mock = AsyncMock(spec=AsyncSession)
    assert not isinstance(generic_mock.get_bind().dialect.name, str)
    assert _get_session_dialect_name(generic_mock) == ("sqlite" if is_sqlite else "postgresql")

    # Case 3: Explicit patching of is_sqlite works deterministically on unbound sessions
    with patch("apps.api.core.database.is_sqlite", False):
        assert _get_session_dialect_name(unbound_session) == "postgresql"
        assert _is_sqlite_session(unbound_session) is False

    with patch("apps.api.core.database.is_sqlite", True):
        assert _get_session_dialect_name(unbound_session) == "sqlite"
        assert _is_sqlite_session(unbound_session) is True


# ==============================================================================
# Task 5.12.31: PortableUUID Regression Tests
# ==============================================================================

def test_portable_uuid_load_dialect_impl():
    """
    Task 5.12.31 Regression (Requirement 1):
    Proves PortableUUID loads PG_UUID on PostgreSQL dialect and String(36) on SQLite dialect.
    """
    uuid_type = PortableUUID()
    pg_impl = uuid_type.load_dialect_impl(postgresql.dialect())
    assert isinstance(pg_impl, postgresql.UUID)
    assert pg_impl.as_uuid is True

    sqlite_impl = uuid_type.load_dialect_impl(sqlite.dialect())
    assert sqlite_impl.python_type is str
    assert sqlite_impl.length == 36


def test_portable_uuid_bind_and_result_processing():
    """
    Task 5.12.31 Regression (Requirements 2, 3, 4, 5):
    Proves that:
    1. PostgreSQL dialect binds both str and uuid.UUID as uuid.UUID objects.
    2. SQLite dialect binds both str and uuid.UUID as 36-char string representations.
    3. Both dialects normalize result values to strings (the canonical API/test contract).
    4. Nullable values (None) are preserved as None across both dialects.
    5. Invalid UUID inputs raise ValueError on bind.
    """
    uuid_type = PortableUUID()
    pg_d = postgresql.dialect()
    sq_d = sqlite.dialect()

    test_uuid = uuid.uuid4()
    test_str = str(test_uuid)

    # 1. PostgreSQL bind processing
    pg_from_str = uuid_type.process_bind_param(test_str, pg_d)
    assert isinstance(pg_from_str, uuid.UUID)
    assert pg_from_str == test_uuid

    pg_from_uuid = uuid_type.process_bind_param(test_uuid, pg_d)
    assert isinstance(pg_from_uuid, uuid.UUID)
    assert pg_from_uuid == test_uuid

    assert uuid_type.process_bind_param(None, pg_d) is None

    with pytest.raises(ValueError, match="Invalid UUID value"):
        uuid_type.process_bind_param("not-a-valid-uuid", pg_d)

    # 2. SQLite bind processing
    sq_from_str = uuid_type.process_bind_param(test_str, sq_d)
    assert isinstance(sq_from_str, str)
    assert sq_from_str == test_str

    sq_from_uuid = uuid_type.process_bind_param(test_uuid, sq_d)
    assert isinstance(sq_from_uuid, str)
    assert sq_from_uuid == test_str

    assert uuid_type.process_bind_param(None, sq_d) is None

    with pytest.raises(ValueError, match="Invalid UUID value"):
        uuid_type.process_bind_param("malformed-uuid", sq_d)

    # 3. Result processing (normalization to str)
    assert uuid_type.process_result_value(test_uuid, pg_d) == test_str
    assert uuid_type.process_result_value(test_str, pg_d) == test_str
    assert uuid_type.process_result_value(None, pg_d) is None

    assert uuid_type.process_result_value(test_str, sq_d) == test_str
    assert uuid_type.process_result_value(None, sq_d) is None


def test_portable_uuid_statement_compilation_and_param_types():
    """
    Task 5.12.31 Regression:
    Proves that compiling a query with PostgreSQL dialect generates $1::UUID (not VARCHAR),
    avoiding the PostgreSQL 42883 'operator does not exist: uuid = character varying' failure.
    """
    test_id = str(uuid.uuid4())
    stmt = select(Document).where(Document.id == test_id)

    # PostgreSQL compilation
    pg_compiled = stmt.compile(dialect=postgresql.dialect())
    compiled_str = str(pg_compiled)
    assert "documents.id = :id_1" in compiled_str or "documents.id =" in compiled_str

    # Type check on the compiled bound parameter
    bind_param = pg_compiled.params["id_1"]
    assert bind_param == test_id
    # Underlying column type compiles to native UUID on PostgreSQL
    assert isinstance(Document.id.type, PortableUUID)
    assert isinstance(Document.id.type.load_dialect_impl(postgresql.dialect()), postgresql.UUID)

    # SQLite compilation
    sq_compiled = stmt.compile(dialect=sqlite.dialect())
    assert isinstance(Document.id.type.load_dialect_impl(sqlite.dialect()).python_type, type(str))


@pytest.mark.asyncio
async def test_portable_uuid_live_roundtrip_and_nullability(db_session: AsyncSession):
    """
    Task 5.12.31 Regression (Requirement 6):
    Proves that in an active database session (SQLite):
    1. Entities can be inserted and queried by both string UUIDs and uuid.UUID instances.
    2. Read results consistently produce strings.
    3. Nullable UUID foreign keys accept and store None cleanly.
    4. Nullable UUID foreign keys can be updated with uuid.UUID objects and retrieved as strings.
    """
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    doc_id = str(uuid.uuid4())
    vendor_uuid = uuid.uuid4()

    org = Organization(id=org_id, name="UUID Test Org", slug=f"uuid-test-{uuid.uuid4().hex[:8]}")
    user = User(id=user_id, email=f"uuid-{uuid.uuid4().hex[:8]}@example.com")
    db_session.add_all([org, user])
    await db_session.commit()

    # Insert Document with None for nullable UUID fields (contract_family_id, vendor_id, created_by)
    doc = Document(
        id=doc_id,
        organization_id=org_id,
        title="UUID Test Doc",
        original_filename="test.pdf",
        storage_key="keys/test",
        sha256=f"sha256_{uuid.uuid4().hex}",
        mime_type="application/pdf",
        byte_size=1024,
        contract_family_id=None,
        vendor_id=None,
        created_by=None
    )
    db_session.add(doc)
    await db_session.commit()

    # Query 1: Query by str
    res1 = (await db_session.execute(select(Document).where(Document.id == doc_id))).scalar_one()
    assert res1.id == doc_id
    assert isinstance(res1.id, str)
    assert res1.organization_id == org_id
    assert isinstance(res1.organization_id, str)
    assert res1.contract_family_id is None
    assert res1.vendor_id is None
    assert res1.created_by is None

    # Query 2: Query by uuid.UUID object
    res2 = (await db_session.execute(select(Document).where(Document.id == uuid.UUID(doc_id)))).scalar_one()
    assert res2.id == doc_id

    # Update nullable UUID field with uuid.UUID instance
    res2.vendor_id = vendor_uuid
    res2.created_by = uuid.UUID(user_id)
    await db_session.commit()

    # Expire session identity map to force fresh database row retrieval through process_result_value
    db_session.expire_all()

    # Query 3: Re-query from database and verify normalized string output
    res3 = (await db_session.execute(select(Document).where(Document.id == doc_id))).scalar_one()
    assert res3.vendor_id == str(vendor_uuid)
    assert isinstance(res3.vendor_id, str)
    assert res3.created_by == user_id
    assert isinstance(res3.created_by, str)


def test_portable_uuid_pk_fk_mapping_consistency():
    """
    Task 5.12.31 Regression (Requirement 7):
    Verifies that all 9 ORM models declare PortableUUID on all canonical UUID PK and FK columns,
    ensuring foreign-key type compatibility with referenced primary keys.
    """
    expected_uuid_columns = {
        User: ["id"],
        Organization: ["id"],
        OrganizationMembership: ["id", "organization_id", "user_id"],
        RiskPlaybook: ["id", "organization_id", "created_by"],
        Document: ["id", "organization_id", "contract_family_id", "vendor_id", "created_by"],
        ExtractedClause: ["id", "organization_id", "contract_family_id", "document_id"],
        AuditRun: ["id", "organization_id", "document_id", "playbook_id", "created_by"],
        RiskAlert: ["id", "organization_id", "audit_run_id", "extracted_clause_id", "reviewed_by"],
        AuditLog: ["id", "organization_id", "actor_user_id", "entity_id"],
    }

    for model, col_names in expected_uuid_columns.items():
        for col_name in col_names:
            col = getattr(model, col_name)
            assert isinstance(col.type, PortableUUID), (
                f"Expected {model.__name__}.{col_name} to be PortableUUID, got {type(col.type)}"
            )
