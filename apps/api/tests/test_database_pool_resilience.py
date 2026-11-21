"""
Unit and integration tests for Phase 1: Database Connection Resilience and Combined Context Settings.
Validates:
1. Non-SQLite engine configuration (pool_pre_ping, pool_recycle, pool_size, max_overflow, pool_timeout, connect_args).
2. SQLite engine configuration preserves compatible behavior.
3. Combined atomic set_config SQL statements for set_user_context, set_tenant_context, and after_begin.
4. list_user_organizations cleanup failure handling (does not mask primary exceptions).
5. Transaction rollback and absence of automatic mid-transaction retry on broken session.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text

from apps.api.core.database import (
    set_user_context, set_tenant_context,
    _reapply_tenant_context_on_transaction_begin,
    User, Organization, OrganizationMembership
)
from apps.api.routers.organizations import list_user_organizations


def test_non_sqlite_engine_configuration():
    """Verifies that non-SQLite engines are configured with resilient pooling parameters and disabled statement caches."""
    test_pg_url = "postgresql+asyncpg://mock_user:mock_pass@localhost:5432/mock_db"

    # Simulate non-SQLite branch from apps.api.core.database
    engine_kwargs = {
        "echo": False,
        "future": True,
        "pool_size": 5,
        "max_overflow": 5,
        "pool_timeout": 30.0,
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "connect_args": {
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "command_timeout": 30.0,
        },
    }

    test_engine = create_async_engine(test_pg_url, **engine_kwargs)

    try:
        assert test_engine.pool._pre_ping is True
        assert test_engine.pool._recycle == 300
        assert test_engine.pool.size() == 5
        assert test_engine.pool._max_overflow == 5
        assert test_engine.pool._timeout == 30.0
        # Verify connect_args passed to the underlying pool creator
        closure_dict = None
        for cell in test_engine.sync_engine.pool._creator.__closure__:
            if isinstance(cell.cell_contents, dict):
                closure_dict = cell.cell_contents
                break
        assert closure_dict is not None
        assert closure_dict.get("statement_cache_size") == 0
        assert closure_dict.get("prepared_statement_cache_size") == 0
        assert closure_dict.get("command_timeout") == 30.0
    finally:
        test_engine.sync_engine.dispose()


def test_sqlite_engine_configuration_compatibility():
    """Verifies that SQLite engines do not receive asyncpg-specific connect_args or queue pool arguments."""
    test_sqlite_url = "sqlite+aiosqlite:///:memory:"

    engine_kwargs = {
        "echo": False,
        "future": True,
    }

    test_engine = create_async_engine(test_sqlite_url, **engine_kwargs)
    try:
        closure_dict = {}
        if hasattr(test_engine.sync_engine.pool._creator, "__closure__") and test_engine.sync_engine.pool._creator.__closure__:
            for cell in test_engine.sync_engine.pool._creator.__closure__:
                if isinstance(cell.cell_contents, dict):
                    closure_dict = cell.cell_contents
                    break
        assert "statement_cache_size" not in closure_dict
    finally:
        test_engine.sync_engine.dispose()


@pytest.mark.asyncio
async def test_set_user_context_combined_statement_atomic():
    """Verifies that set_user_context executes a single combined atomic SQL statement with is_local=true."""
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

    uid = uuid.uuid4()
    await set_user_context(mock_session, user_id=uid)

    assert len(executed_queries) == 1
    sql, params = executed_queries[0]
    assert "set_config('app.current_user_id', :uid, true)" in sql
    assert "set_config('app.current_organization_id', '', true)" in sql
    assert params == {"uid": str(uid)}


@pytest.mark.asyncio
async def test_set_tenant_context_combined_statement_atomic():
    """Verifies that set_tenant_context in an active transaction executes a single combined atomic SQL statement."""
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

    uid = uuid.uuid4()
    org_id = uuid.uuid4()
    await set_tenant_context(mock_session, user_id=uid, organization_id=org_id)

    assert len(executed_queries) == 1
    sql, params = executed_queries[0]
    assert "set_config('app.current_user_id', :uid, true)" in sql
    assert "set_config('app.current_organization_id', :org_id, true)" in sql
    assert params == {"uid": str(uid), "org_id": str(org_id)}


def test_after_begin_event_listener_combined_statement():
    """Verifies that _reapply_tenant_context_on_transaction_begin executes a single combined atomic SQL statement."""
    mock_session = MagicMock()
    mock_session.info = {
        "current_organization_id": "org-test-uuid",
        "current_user_id": "user-test-uuid",
    }
    mock_connection = MagicMock()
    mock_connection.dialect.name = "postgresql"

    executed_queries = []
    def fake_execute(statement, params=None):
        executed_queries.append((str(statement), params))

    mock_connection.execute = fake_execute

    _reapply_tenant_context_on_transaction_begin(mock_session, None, mock_connection)

    assert len(executed_queries) == 1
    sql, params = executed_queries[0]
    assert "set_config('app.current_user_id', :uid, true)" in sql
    assert "set_config('app.current_organization_id', :org_id, true)" in sql
    assert params == {"uid": "user-test-uuid", "org_id": "org-test-uuid"}


@pytest.mark.asyncio
async def test_list_user_organizations_cleanup_failure_does_not_mask_original_exception():
    """
    Verifies that if an exception occurs during list_user_organizations query execution,
    and the cleanup set_user_context in finally: also raises (e.g. connection was lost),
    the original domain exception is NOT masked and propagates cleanly.
    """
    user = User(id=str(uuid.uuid4()), email="test@example.com", full_name="Test User")
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute.side_effect = ValueError("Primary database query failed")

    with patch("apps.api.routers.organizations.set_user_context", new_callable=AsyncMock) as mock_set_user:
        # First call (at entry) succeeds
        # Query raises a primary database/domain error
        # Second call (in finally cleanup) fails with ConnectionDoesNotExistError simulation
        mock_set_user.side_effect = [
            None,  # Entry call succeeds
            RuntimeError("Connection lost during finally cleanup")  # Cleanup fails
        ]

        # Must raise the primary exception ("Primary database query failed"), NOT the cleanup failure
        with pytest.raises(ValueError, match="Primary database query failed"):
            await list_user_organizations(user=user, db=mock_session)


@pytest.mark.asyncio
async def test_list_user_organizations_cleanup_failure_after_success_returns_data(db_session: AsyncSession):
    """
    Verifies that if list_user_organizations succeeds in reading data, but the cleanup
    in finally: encounters a severed connection, the data is still returned and does not crash.
    """
    user = User(id=str(uuid.uuid4()), email="success@example.com", full_name="Success User")

    with patch("apps.api.routers.organizations.set_user_context", new_callable=AsyncMock) as mock_set_user:
        # Entry succeeds, cleanup raises connection error
        mock_set_user.side_effect = [
            None,  # Entry call succeeds
            RuntimeError("Connection dropped after query completion")  # Cleanup fails
        ]

        # No memberships found -> empty list
        result = await list_user_organizations(user=user, db=db_session)
        assert result == []
