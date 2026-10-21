"""
Pytest Fixtures for PostgreSQL Row Level Security (RLS) Integration Testing.
Provides isolated, ephemeral PostgreSQL databases and sessions under the runtime
onsigning_app role (NOBYPASSRLS) and admin fixture role (BYPASSRLS).

Execution Guard:
- When RUN_POSTGRES_RLS is false/unset, all postgres_rls marked tests are cleanly skipped.
- When RUN_POSTGRES_RLS is true, failure to connect or provision fails closed with an error.
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator, Optional
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession, AsyncEngine

from apps.api.tests.postgres_bootstrap import PostgresConfig, PostgresHarness

RUN_POSTGRES_RLS = os.getenv("RUN_POSTGRES_RLS", "false").lower() in ("true", "1", "yes")


def pytest_configure(config):
    """Registers the postgres_rls custom marker."""
    config.addinivalue_line(
        "markers",
        "postgres_rls: Real PostgreSQL 16+ Row Level Security integration tests"
    )


@pytest_asyncio.fixture(scope="session")
async def postgres_harness() -> AsyncGenerator[Optional[PostgresHarness], None]:
    """
    Session-scoped fixture managing ephemeral PostgreSQL database lifecycle.
    Only boots when RUN_POSTGRES_RLS is explicitly enabled.
    """
    if not RUN_POSTGRES_RLS:
        yield None
        return

    config = PostgresConfig()
    harness = PostgresHarness(config)

    # 1. Connect to maintenance DB and provision roles if needed
    m_conn = await harness.connect_maintenance()
    try:
        await harness.provision_roles_if_needed(m_conn)
        await harness.create_ephemeral_database(m_conn)
    finally:
        await m_conn.close()

    # 2. Install extensions, run migration, apply grants, verify catalog security
    await harness.install_extensions()
    await harness.run_migration_and_grants()
    await harness.verify_privileges_and_catalogs()

    try:
        yield harness
    finally:
        await harness.teardown()


@pytest_asyncio.fixture(scope="function")
async def clean_postgres_db(postgres_harness: Optional[PostgresHarness]) -> AsyncGenerator[None, None]:
    """
    Function-scoped fixture ensuring a clean database before and after each test.
    Executes TRUNCATE TABLE organizations, users CASCADE in <20ms under onsigning_admin.
    """
    if postgres_harness is None:
        pytest.skip("PostgreSQL RLS suite skipped: RUN_POSTGRES_RLS is not enabled.")

    await postgres_harness.reset_fixtures()
    try:
        yield
    finally:
        await postgres_harness.reset_fixtures()


@pytest_asyncio.fixture(scope="function")
async def pg_admin_conn(postgres_harness: Optional[PostgresHarness], clean_postgres_db) -> AsyncGenerator[Any, None]:
    """Direct asyncpg connection under onsigning_admin (BYPASSRLS) for fixture creation."""
    if postgres_harness is None:
        pytest.skip("PostgreSQL RLS suite skipped: RUN_POSTGRES_RLS is not enabled.")

    conn = await postgres_harness.connect_as_admin()
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="function")
async def pg_app_conn(postgres_harness: Optional[PostgresHarness], clean_postgres_db) -> AsyncGenerator[Any, None]:
    """Direct asyncpg connection under onsigning_app (NOBYPASSRLS) for raw RLS assertions."""
    if postgres_harness is None:
        pytest.skip("PostgreSQL RLS suite skipped: RUN_POSTGRES_RLS is not enabled.")

    conn = await postgres_harness.connect_as_app()
    try:
        yield conn
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="function")
async def pg_sa_engine(postgres_harness: Optional[PostgresHarness]) -> AsyncGenerator[Optional[AsyncEngine], None]:
    """
    Async SQLAlchemy engine connected to the ephemeral database as onsigning_app.
    RLS is strictly enforced on this engine.
    """
    if postgres_harness is None:
        yield None
        return

    url = postgres_harness.config.get_sa_url(
        user=postgres_harness.config.app_role,
        password=postgres_harness.config.app_password,
        database=postgres_harness.db_name
    )
    engine = create_async_engine(
        url,
        pool_size=5,
        max_overflow=10,
        echo=False
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def pg_db_session(pg_sa_engine: Optional[AsyncEngine], clean_postgres_db) -> AsyncGenerator[AsyncSession, None]:
    """
    AsyncSession connected as onsigning_app for testing SQLAlchemy ORM & session event listeners.
    """
    if pg_sa_engine is None:
        pytest.skip("PostgreSQL RLS suite skipped: RUN_POSTGRES_RLS is not enabled.")

    session_factory = async_sessionmaker(
        bind=pg_sa_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False
    )
    async with session_factory() as session:
        yield session
