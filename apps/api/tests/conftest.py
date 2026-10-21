"""
Pytest configuration and fixtures for Onsigning API tests.
Provides isolated in-memory test database, test tenants, and authenticated HTTP clients.
"""

import asyncio
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import StaticPool

from apps.api.main import app
from apps.api.core.config import settings
from apps.api.core.database import Base, get_db, User, Organization, OrganizationMembership, RiskPlaybook
from apps.api.core.auth import create_mock_jwt
from apps.api.services.evaluator import get_baseline_saas_playbook
import json

pytest_plugins = ["apps.api.tests.conftest_postgres"]

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

test_engine = create_async_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)


@pytest_asyncio.fixture(scope="function")
async def db_session():
    """Provides a fresh isolated database for each test function."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with TestingSessionLocal() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession):
    """FastAPI AsyncClient overriding get_db dependency to use test session."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def test_tenant_data(db_session: AsyncSession):
    """Provisions a test organization, admin user, and membership."""
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())

    user = User(
        id=user_id,
        email="legal.lead@acme-corp.com",
        full_name="Sarah Legal Lead",
        external_auth_id=user_id,
        auth_provider="supabase"
    )
    db_session.add(user)

    org = Organization(
        id=org_id,
        name="Acme Corporation",
        slug="acme-corp"
    )
    db_session.add(org)
    await db_session.flush()

    membership = OrganizationMembership(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        user_id=user_id,
        role="ADMIN"
    )
    db_session.add(membership)

    # Seed baseline SaaS playbook
    saas_cfg = get_baseline_saas_playbook()
    playbook = RiskPlaybook(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        name=saas_cfg.playbook_name,
        version=saas_cfg.version,
        description=saas_cfg.description,
        rules=json.dumps([r.model_dump() for r in saas_cfg.rules]),
        is_active=True,
        created_by=user_id
    )
    db_session.add(playbook)

    await db_session.commit()

    token = create_mock_jwt(user_id=user_id, email=user.email)

    return {
        "user_id": user_id,
        "org_id": org_id,
        "token": token,
        "playbook_id": playbook.id,
        "headers": {
            "Authorization": f"Bearer {token}",
            "X-Organization-Id": org_id
        }
    }
