"""
Unit and integration tests for secure organization discovery (GET /api/v1/organizations).
Tests authentication, multi-tenant isolation, role resolution, and pre-tenant RLS safety.
"""

import uuid
import time
import jwt
from unittest.mock import patch
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from apps.api.core.config import settings
from apps.api.core.auth import create_mock_jwt
from apps.api.core.database import (
    User, Organization, OrganizationMembership,
    set_user_context, set_tenant_context
)


@pytest.mark.asyncio
async def test_unauthenticated_request_rejected(client: AsyncClient):
    """Requests without an Authorization header return HTTP 401 Unauthorized."""
    res = await client.get("/api/v1/organizations")
    assert res.status_code == 401
    assert "Authorization header is missing" in res.json()["message"]


@pytest.mark.asyncio
async def test_invalid_token_rejected(client: AsyncClient):
    """Requests with a forged or malformed JWT return HTTP 401 Unauthorized."""
    res = await client.get(
        "/api/v1/organizations",
        headers={"Authorization": "Bearer invalid.jwt.token"}
    )
    assert res.status_code == 401
    assert "Invalid authentication token" in res.json()["message"]


@pytest.mark.asyncio
async def test_expired_token_rejected(client: AsyncClient):
    """Requests with an expired JWT return HTTP 401 Unauthorized."""
    payload = {
        "sub": str(uuid.uuid4()),
        "email": "expired@example.com",
        "aud": settings.SUPABASE_AUDIENCE,
        "exp": int(time.time()) - 3600,
        "iat": int(time.time()) - 7200,
    }
    expired_token = jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm=settings.SUPABASE_JWT_ALGORITHM)

    res = await client.get(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert res.status_code == 401
    assert "token has expired" in res.json()["message"].lower()


@pytest.mark.asyncio
async def test_zero_memberships_returns_empty_list(client: AsyncClient, db_session: AsyncSession):
    """An authenticated user with zero organization memberships receives HTTP 200 and an empty list."""
    new_user_id = str(uuid.uuid4())
    token = create_mock_jwt(user_id=new_user_id, email="unaffiliated@example.com")

    # Send request without X-Organization-Id
    res = await client.get(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    assert res.json() == []


@pytest.mark.asyncio
async def test_single_membership_discovery(client: AsyncClient, test_tenant_data: dict):
    """
    An authenticated user with one membership discovers that organization without sending X-Organization-Id.
    """
    # Use the token from test_tenant_data without X-Organization-Id
    headers = {"Authorization": f"Bearer {test_tenant_data['token']}"}
    res = await client.get("/api/v1/organizations", headers=headers)
    assert res.status_code == 200

    data = res.json()
    assert len(data) == 1
    org = data[0]
    assert org["id"] == test_tenant_data["org_id"]
    assert org["name"] == "Acme Corporation"
    assert org["slug"] == "acme-corp"
    assert org["role"] == "ADMIN"
    assert "created_at" in org


@pytest.mark.asyncio
async def test_multiple_memberships_discovery(client: AsyncClient, db_session: AsyncSession):
    """
    A user belonging to multiple organizations discovers all of them with their respective roles.
    """
    user_id = str(uuid.uuid4())
    token = create_mock_jwt(user_id=user_id, email="multi.member@example.com")

    # Seed User
    user = User(id=user_id, email="multi.member@example.com", full_name="Multi Member")
    db_session.add(user)

    # Seed Organization 1 (User is OWNER)
    org1_id = str(uuid.uuid4())
    org1 = Organization(id=org1_id, name="First Org Inc", slug="first-org")
    db_session.add(org1)
    await db_session.flush()

    m1 = OrganizationMembership(
        id=str(uuid.uuid4()),
        organization_id=org1_id,
        user_id=user_id,
        role="OWNER"
    )
    db_session.add(m1)

    # Seed Organization 2 (User is LEGAL_REVIEWER)
    org2_id = str(uuid.uuid4())
    org2 = Organization(id=org2_id, name="Second Corp LLC", slug="second-corp")
    db_session.add(org2)
    await db_session.flush()

    m2 = OrganizationMembership(
        id=str(uuid.uuid4()),
        organization_id=org2_id,
        user_id=user_id,
        role="LEGAL_REVIEWER"
    )
    db_session.add(m2)

    await db_session.commit()

    res = await client.get(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200

    data = res.json()
    assert len(data) == 2

    org1_entry = next((o for o in data if o["id"] == org1_id), None)
    assert org1_entry is not None
    assert org1_entry["name"] == "First Org Inc"
    assert org1_entry["role"] == "OWNER"

    org2_entry = next((o for o in data if o["id"] == org2_id), None)
    assert org2_entry is not None
    assert org2_entry["name"] == "Second Corp LLC"
    assert org2_entry["role"] == "LEGAL_REVIEWER"


@pytest.mark.asyncio
async def test_cross_user_isolation(client: AsyncClient, db_session: AsyncSession):
    """
    Organizations belonging to User A are never returned to User B.
    """
    # User A in Org A
    user_a_id = str(uuid.uuid4())
    token_a = create_mock_jwt(user_id=user_a_id, email="alice@corp-a.com")
    user_a = User(id=user_a_id, email="alice@corp-a.com", full_name="Alice")
    db_session.add(user_a)

    org_a_id = str(uuid.uuid4())
    org_a = Organization(id=org_a_id, name="Alpha Corp", slug="alpha-corp")
    db_session.add(org_a)
    await db_session.flush()

    m_a = OrganizationMembership(
        id=str(uuid.uuid4()),
        organization_id=org_a_id,
        user_id=user_a_id,
        role="ADMIN"
    )
    db_session.add(m_a)

    # User B in Org B
    user_b_id = str(uuid.uuid4())
    token_b = create_mock_jwt(user_id=user_b_id, email="bob@corp-b.com")
    user_b = User(id=user_b_id, email="bob@corp-b.com", full_name="Bob")
    db_session.add(user_b)

    org_b_id = str(uuid.uuid4())
    org_b = Organization(id=org_b_id, name="Beta Corp", slug="beta-corp")
    db_session.add(org_b)
    await db_session.flush()

    m_b = OrganizationMembership(
        id=str(uuid.uuid4()),
        organization_id=org_b_id,
        user_id=user_b_id,
        role="VIEWER"
    )
    db_session.add(m_b)

    await db_session.commit()

    # User A queries organizations
    res_a = await client.get("/api/v1/organizations", headers={"Authorization": f"Bearer {token_a}"})
    assert res_a.status_code == 200
    data_a = res_a.json()
    assert len(data_a) == 1
    assert data_a[0]["id"] == org_a_id
    assert all(o["id"] != org_b_id for o in data_a)

    # User B queries organizations
    res_b = await client.get("/api/v1/organizations", headers={"Authorization": f"Bearer {token_b}"})
    assert res_b.status_code == 200
    data_b = res_b.json()
    assert len(data_b) == 1
    assert data_b[0]["id"] == org_b_id
    assert all(o["id"] != org_a_id for o in data_b)


@pytest.mark.asyncio
async def test_response_schema_exact_fields(client: AsyncClient, test_tenant_data: dict):
    """
    Verifies that response serialization contains exactly the required fields.
    """
    res = await client.get(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {test_tenant_data['token']}"}
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    entry = data[0]

    expected_keys = {"id", "name", "slug", "role", "created_at"}
    assert set(entry.keys()) == expected_keys
    assert isinstance(entry["id"], str)
    assert isinstance(entry["name"], str)
    assert isinstance(entry["slug"], str)
    assert isinstance(entry["role"], str)
    assert isinstance(entry["created_at"], str)


@pytest.mark.asyncio
async def test_exception_cleanup_restores_user_context(db_session: AsyncSession, test_tenant_data: dict):
    """
    Verifies that if an unexpected error occurs during organization fetching,
    the finally block guarantees context is reset to user-only, preventing tenant leakage.
    """
    from apps.api.routers.organizations import list_user_organizations
    from unittest.mock import AsyncMock

    user = User(id=test_tenant_data["user_id"], email="test@acme.com", full_name="Test User")

    with patch("apps.api.routers.organizations.set_tenant_context", new_callable=AsyncMock) as mock_set_tenant, \
         patch("apps.api.routers.organizations.set_user_context", new_callable=AsyncMock) as mock_set_user:
        mock_set_tenant.side_effect = RuntimeError("Simulated context failure")

        with pytest.raises(RuntimeError, match="Simulated context failure"):
            await list_user_organizations(user=user, db=db_session)

        # Confirm set_user_context was called at entry AND in finally block
        assert mock_set_user.call_count >= 2
        mock_set_user.assert_called_with(db_session, user.id)
