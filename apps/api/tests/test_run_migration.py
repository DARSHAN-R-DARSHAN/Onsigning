"""
Unit and Integration Tests for packages/database/run_migration.py
Validates pre-flight fail-closed guards, secret-safe logging, transactional rollback,
and post-commit verification failure handling without connecting to any persistent database.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from packages.database.run_migration import (
    EXIT_SUCCESS,
    EXIT_PREFLIGHT_FAILURE,
    EXIT_MIGRATION_FAILURE,
    EXIT_VERIFICATION_FAILURE,
    mask_dsn,
    sanitize_log,
    parse_args,
    validate_preflight_config,
    execute_and_verify,
    main,
)


@pytest.fixture
def valid_env():
    """Returns a valid environment dictionary for staging migration execution."""
    return {
        "ONSIGNING_MIGRATION_OPT_IN": "1",
        "ONSIGNING_STAGING_PROJECT_REF": "xyzstgproject",
        "ONSIGNING_MIGRATION_DSN": "postgresql://postgres:mysecretpassword123@db.xyzstgproject.supabase.co:5432/postgres",
        "ONSIGNING_RUNNER_TEST_MODE": "true",
    }


@pytest.fixture
def valid_args():
    """Returns valid parsed CLI arguments."""
    return argparse.Namespace(
        target="staging",
        confirm_staging=True,
        project_ref="xyzstgproject",
        expected_db="postgres",
        ca_path=None,
        timeout=60.0,
    )


# ==============================================================================
# 1. SECRET MASKING & SANITIZATION TESTS
# ==============================================================================

def test_mask_dsn_hides_password():
    dsn = "postgresql://postgres:SuperSecretPassword123!@db.projectref.supabase.co:5432/postgres"
    masked = mask_dsn(dsn)
    assert "SuperSecretPassword123!" not in masked
    assert "postgresql://postgres:***@db.projectref.supabase.co:5432/postgres" == masked


def test_sanitize_log_removes_credentials():
    raw_log = "Connection failed for user postgres:SecretPass456@db.supabase.co with token=eyJhbGciOi..."
    sanitized = sanitize_log(raw_log)
    assert "SecretPass456" not in sanitized
    assert "eyJhbGciOi" not in sanitized
    assert "token=***" in sanitized


# ==============================================================================
# 2. PRE-FLIGHT VALIDATION FAIL-CLOSED TESTS
# ==============================================================================

def test_preflight_missing_opt_in_fails_closed(valid_args, valid_env):
    valid_env.pop("ONSIGNING_MIGRATION_OPT_IN")
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert not ok
    assert "Operator opt-in missing" in msg
    assert config is None


def test_preflight_missing_confirm_staging_fails_closed(valid_args, valid_env):
    valid_args.confirm_staging = False
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert not ok
    assert "Missing confirmation: flag --confirm-staging is required" in msg
    assert config is None


def test_preflight_missing_trusted_project_ref_fails_closed(valid_args, valid_env):
    valid_env.pop("ONSIGNING_STAGING_PROJECT_REF")
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert not ok
    assert "Trusted staging project reference is not configured" in msg
    assert config is None


def test_preflight_project_ref_mismatch_fails_closed(valid_args, valid_env):
    valid_args.project_ref = "differentref"
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert not ok
    assert "Project reference mismatch" in msg
    assert config is None


def test_preflight_missing_dsn_fails_closed(valid_args, valid_env):
    valid_env.pop("ONSIGNING_MIGRATION_DSN")
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert not ok
    assert "Connection configuration missing" in msg
    assert config is None


def test_preflight_rejects_transaction_pooler_port_6543(valid_args, valid_env):
    valid_env["ONSIGNING_MIGRATION_DSN"] = (
        "postgresql://postgres:secret@db.xyzstgproject.supabase.co:6543/postgres"
    )
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert not ok
    assert "Transaction pooler port (6543) detected" in msg
    assert config is None


def test_preflight_rejects_pooler_hostname_even_on_port_5432(valid_args, valid_env):
    valid_env["ONSIGNING_MIGRATION_DSN"] = (
        "postgresql://postgres:secret@xyzstgproject.pooler.supabase.com:5432/postgres"
    )
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert not ok
    assert "is a Supabase connection pooler" in msg
    assert "strictly requires direct connection" in msg
    assert config is None


def test_preflight_endpoint_hostname_mismatch_fails_closed(valid_args, valid_env):
    valid_env["ONSIGNING_MIGRATION_DSN"] = (
        "postgresql://postgres:secret@db.wronghost.supabase.co:5432/postgres"
    )
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert not ok
    assert "Connection endpoint mismatch" in msg
    assert config is None


def test_preflight_database_name_mismatch_fails_closed(valid_args, valid_env):
    valid_env["ONSIGNING_MIGRATION_DSN"] = (
        "postgresql://postgres:secret@db.xyzstgproject.supabase.co:5432/wrong_db"
    )
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert not ok
    assert "Database name mismatch" in msg
    assert config is None


def test_preflight_username_not_postgres_fails_closed(valid_args, valid_env):
    valid_env["ONSIGNING_MIGRATION_DSN"] = (
        "postgresql://onsigning_admin:secret@db.xyzstgproject.supabase.co:5432/postgres"
    )
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert not ok
    assert "Invalid DSN role" in msg
    assert config is None


def test_preflight_pytest_guard_rejects_execution_without_test_mode(valid_args, valid_env):
    valid_env.pop("ONSIGNING_RUNNER_TEST_MODE")
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert not ok
    assert "migration runner must not be executed within pytest" in msg
    assert config is None


def test_preflight_success_with_valid_config(valid_args, valid_env):
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert ok
    assert "Pre-flight validation passed" in msg
    assert config is not None
    assert config["hostname"] == "db.xyzstgproject.supabase.co"
    assert config["port"] == 5432
    assert config["db_name"] == "postgres"
    assert config["trusted_project_ref"] == "xyzstgproject"
    assert "0003_security_hardening.sql" in str(config["migration_file"])
    assert len(config["migration_sha256"]) == 64


# ==============================================================================
# 3. SERVER ASSERTION & TRANSACTION EXECUTION MOCK TESTS
# ==============================================================================

class MockAsyncTransaction:
    """Mock async context manager mimicking asyncpg Transaction."""
    def __init__(self, should_raise=False):
        self.should_raise = should_raise

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.should_raise:
            return False
        return None


@pytest.mark.asyncio
async def test_server_role_mismatch_fails_closed(valid_args, valid_env):
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert ok

    mock_conn = AsyncMock()
    # Server returns wrong role (e.g. onsigning_admin instead of postgres)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres",
        "user_name": "onsigning_admin",
        "server_ip": "10.0.0.1",
    }

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_PREFLIGHT_FAILURE


@pytest.mark.asyncio
async def test_server_database_mismatch_fails_closed(valid_args, valid_env):
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert ok

    mock_conn = AsyncMock()
    # Server returns different db name
    mock_conn.fetchrow.return_value = {
        "db_name": "other_database",
        "user_name": "postgres",
        "server_ip": "10.0.0.1",
    }

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_PREFLIGHT_FAILURE


@pytest.mark.asyncio
async def test_migration_execution_rolls_back_on_sql_error(valid_args, valid_env, capsys):
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres",
        "user_name": "postgres",
        "server_ip": "10.0.0.1",
    }

    # In asyncpg, conn.transaction() is a synchronous method returning an async context manager
    mock_tx = MockAsyncTransaction()
    mock_conn.transaction = MagicMock(return_value=mock_tx)
    # Simulate SQL execution failure inside transaction
    mock_conn.execute.side_effect = RuntimeError("Simulated syntax or permission violation (SQLSTATE 42501)")

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_MIGRATION_FAILURE

    captured = capsys.readouterr()
    assert "Transaction automatically rolled back" in captured.err
    assert "Database remains in pre-migration state" in captured.err


# ==============================================================================
# 4. POST-COMMIT VERIFICATION TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_post_commit_verification_failure_flags_committed_status(valid_args, valid_env, capsys):
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres",
        "user_name": "postgres",
        "server_ip": "10.0.0.1",
    }

    mock_tx = MockAsyncTransaction()
    mock_conn.transaction = MagicMock(return_value=mock_tx)
    mock_conn.execute.return_value = "DO"

    # Simulate verification query returning an unexpected privilege for anon
    mock_conn.fetch.side_effect = [
        # Query 1: PostgREST roles - anon has SELECT!
        [
            {
                "rolname": "anon",
                "tablename": "users",
                "can_select": True,  # VIOLATION!
                "can_insert": False,
                "can_update": False,
                "can_delete": False,
                "can_truncate": False,
            }
        ],
        # Query 2: onsigning_app roles - valid
        [
            {
                "tablename": "users",
                "can_select": True,
                "can_insert": True,
                "can_update": True,
                "can_delete": True,
                "can_truncate": False,
            },
            {
                "tablename": "audit_logs",
                "can_select": True,
                "can_insert": True,
                "can_update": False,
                "can_delete": False,
                "can_truncate": False,
            },
        ],
        # Query 3: default ACLs - empty
        [],
    ]

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE

    captured = capsys.readouterr()
    assert "[CRITICAL ALERT] Post-commit security verification FAILED!" in captured.err
    assert "The migration transaction has ALREADY COMMITTED and was NOT rolled back" in captured.err
    assert "Do NOT attempt automated compensating migrations" in captured.err


@pytest.mark.asyncio
async def test_successful_execution_and_verification_workflow(valid_args, valid_env, capsys):
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres",
        "user_name": "postgres",
        "server_ip": "10.0.0.1",
    }

    mock_tx = MockAsyncTransaction()
    mock_conn.transaction = MagicMock(return_value=mock_tx)
    mock_conn.execute.return_value = "DO"

    # All verification checks pass cleanly
    mock_conn.fetch.side_effect = [
        # Query 1: PostgREST roles - all false
        [
            {
                "rolname": "anon",
                "tablename": "users",
                "can_select": False,
                "can_insert": False,
                "can_update": False,
                "can_delete": False,
                "can_truncate": False,
            },
            {
                "rolname": "anon",
                "tablename": "audit_logs",
                "can_select": False,
                "can_insert": False,
                "can_update": False,
                "can_delete": False,
                "can_truncate": False,
            },
        ],
        # Query 2: onsigning_app roles - exact expected privileges
        [
            {
                "tablename": "users",
                "can_select": True,
                "can_insert": True,
                "can_update": True,
                "can_delete": True,
                "can_truncate": False,
            },
            {
                "tablename": "audit_logs",
                "can_select": True,
                "can_insert": True,
                "can_update": False,
                "can_delete": False,
                "can_truncate": False,
            },
        ],
        # Query 3: default ACLs - no PostgREST grants
        [
            {
                "defaclobjtype": "r",
                "defaclacl_str": "{postgres=arwdDxt/postgres}",
            },
            {
                "defaclobjtype": "S",
                "defaclacl_str": "{postgres=rwU/postgres}",
            },
        ],
    ]

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_SUCCESS

    captured = capsys.readouterr()
    assert "[MIGRATION SUCCESS] Transaction committed successfully to catalog." in captured.out
    assert "[VERIFICATION SUCCESS] All post-commit security invariants verified." in captured.out
    assert "[RUNNER SUCCESS] Migration 0003 successfully applied and verified." in captured.out


@pytest.mark.asyncio
async def test_post_commit_verification_query_exception_flags_committed_status(valid_args, valid_env, capsys):
    """
    Task A: Proves that any unexpected exception during post-commit verification queries
    produces a sanitized [CRITICAL ALERT] stating the transaction ALREADY COMMITTED,
    does NOT claim rollback, and returns EXIT_VERIFICATION_FAILURE.
    """
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres",
        "user_name": "postgres",
        "server_ip": "10.0.0.1",
    }

    mock_tx = MockAsyncTransaction()
    mock_conn.transaction = MagicMock(return_value=mock_tx)
    mock_conn.execute.return_value = "DO"

    # Simulate network drop or query failure during verification query
    mock_conn.fetch.side_effect = RuntimeError("Database connection dropped during catalog assertion query")

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE

    captured = capsys.readouterr()
    assert "[CRITICAL ALERT] Post-commit security verification encountered an unexpected query error!" in captured.err
    assert "Verification exception: Database connection dropped during catalog assertion query" in captured.err
    assert "The migration transaction has ALREADY COMMITTED and was NOT rolled back." in captured.err
    assert "Do NOT attempt automated compensating migrations. Manual DBA inspection is required." in captured.err


@pytest.mark.asyncio
async def test_post_commit_default_acl_prohibited_grant_detected(valid_args, valid_env, capsys):
    """
    Task B: Proves that prohibited default ACL grants (e.g. anon=r/postgres) are accurately
    detected and flagged as post-commit verification failures.
    """
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres",
        "user_name": "postgres",
        "server_ip": "10.0.0.1",
    }

    mock_tx = MockAsyncTransaction()
    mock_conn.transaction = MagicMock(return_value=mock_tx)
    mock_conn.execute.return_value = "DO"

    mock_conn.fetch.side_effect = [
        # Query 1: PostgREST roles - all false
        [],
        # Query 2: onsigning_app roles - valid
        [
            {
                "tablename": "users",
                "can_select": True,
                "can_insert": True,
                "can_update": True,
                "can_delete": True,
                "can_truncate": False,
            },
            {
                "tablename": "audit_logs",
                "can_select": True,
                "can_insert": True,
                "can_update": False,
                "can_delete": False,
                "can_truncate": False,
            },
        ],
        # Query 3: default ACL contains PROHIBITED grant: anon=r/postgres
        [
            {
                "defaclobjtype": "r",
                "defaclacl_str": "{postgres=arwdDxt/postgres,anon=r/postgres}",
            }
        ],
    ]

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE

    captured = capsys.readouterr()
    assert "[CRITICAL ALERT] Post-commit security verification FAILED!" in captured.err
    assert "contains prohibited grant 'r' to 'anon'" in captured.err


@pytest.mark.asyncio
async def test_post_commit_default_acl_absent_rows_permitted(valid_args, valid_env, capsys):
    """
    Task B: Proves that absent default-ACL rows are distinguished from explicit grants
    and are permitted under PostgreSQL built-in default semantics.
    """
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres",
        "user_name": "postgres",
        "server_ip": "10.0.0.1",
    }

    mock_tx = MockAsyncTransaction()
    mock_conn.transaction = MagicMock(return_value=mock_tx)
    mock_conn.execute.return_value = "DO"

    mock_conn.fetch.side_effect = [
        # Query 1: PostgREST roles - all false
        [],
        # Query 2: onsigning_app roles - valid
        [
            {
                "tablename": "users",
                "can_select": True,
                "can_insert": True,
                "can_update": True,
                "can_delete": True,
                "can_truncate": False,
            },
            {
                "tablename": "audit_logs",
                "can_select": True,
                "can_insert": True,
                "can_update": False,
                "can_delete": False,
                "can_truncate": False,
            },
        ],
        # Query 3: default ACL rows absent (empty list) -> permitted!
        [],
    ]

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_SUCCESS

    captured = capsys.readouterr()
    assert "[VERIFICATION SUCCESS] All post-commit security invariants verified." in captured.out


def test_preflight_insecure_local_tls_rejected_for_non_localhost(valid_args, valid_env):
    """Proves that allow_insecure_local_tls is strictly rejected for any remote host."""
    valid_env["ONSIGNING_ALLOW_INSECURE_LOCAL_TEST_TLS"] = "true"
    # DSN targets a remote hostname, not localhost
    valid_env["ONSIGNING_MIGRATION_DSN"] = (
        "postgresql://postgres:secret@db.xyzstgproject.supabase.co:5432/postgres"
    )
    ok, msg, config = validate_preflight_config(valid_args, valid_env)
    assert ok
    # Even if flag was set in env, allow_insecure_local_tls must be False because hostname is not localhost
    assert config["allow_insecure_local_tls"] is False


# ==============================================================================
# 5. DISPOSABLE POSTGRESQL INTEGRATION TEST FIXTURE (TASK D)
# ==============================================================================

RUN_POSTGRES_RLS = os.getenv("RUN_POSTGRES_RLS", "false").lower() in ("true", "1", "yes")


@pytest.mark.postgres_rls
@pytest.mark.skipif(
    not RUN_POSTGRES_RLS,
    reason="Disposable PostgreSQL 16 engine unavailable in local environment (RUN_POSTGRES_RLS not enabled).",
)
@pytest.mark.asyncio
async def test_disposable_postgres_live_runner_integration():
    """
    Task D: Live Disposable PostgreSQL 16 Integration Test.
    Executes strictly when RUN_POSTGRES_RLS is true and a disposable PostgreSQL instance is active.
    Proves atomic rollback and post-commit verification against a real database engine.
    """
    import asyncpg
    import uuid

    host = os.getenv("POSTGRES_HOST", "localhost")
    port = int(os.getenv("POSTGRES_PORT", "5432"))
    superuser = os.getenv("POSTGRES_SUPERUSER", "postgres")
    superuser_password = os.getenv("POSTGRES_SUPERUSER_PASSWORD", "postgres_ci_password")

    roles_created_by_test: list[str] = []
    ephemeral_db: str | None = None

    try:
        # 1. Connect to maintenance database to inspect roles and provision ephemeral resources
        m_conn = await asyncpg.connect(
            host=host,
            port=port,
            user=superuser,
            password=superuser_password,
            database="postgres",
        )
        try:
            # Query pre-existing mock PostgREST roles to ensure cleanup only drops roles
            # created specifically by this test, preserving shared suite roles like onsigning_app.
            mock_roles = ["anon", "authenticated", "service_role"]
            existing_rows = await m_conn.fetch(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY($1::text[])",
                mock_roles,
            )
            pre_existing_roles = {r["rolname"] for r in existing_rows}
            roles_created_by_test = [r for r in mock_roles if r not in pre_existing_roles]

            # Provision required roles for migration 0003
            roles_sql = """
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'onsigning_app') THEN
                    CREATE ROLE onsigning_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
                    CREATE ROLE anon NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
                    CREATE ROLE authenticated NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
                END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
                    CREATE ROLE service_role NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
                END IF;
            END $$;
            """
            await m_conn.execute(roles_sql)

            run_token = uuid.uuid4().hex[:8]
            target_db = f"onsigning_test_runner_{run_token}"
            # Create isolated ephemeral database
            await m_conn.execute(f'CREATE DATABASE "{target_db}" OWNER "{superuser}";')
            ephemeral_db = target_db
        finally:
            await m_conn.close()

        # 2. Connect to ephemeral database and establish prerequisite schema (users & audit_logs)
        ephem_conn = await asyncpg.connect(
            host=host,
            port=port,
            user=superuser,
            password=superuser_password,
            database=ephemeral_db,
        )
        try:
            schema_prereqs_sql = """
            CREATE TABLE IF NOT EXISTS public.users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                email VARCHAR(255) NOT NULL UNIQUE,
                full_name VARCHAR(255),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS public.audit_logs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                organization_id UUID NOT NULL,
                actor_user_id UUID,
                actor_type VARCHAR(50) NOT NULL DEFAULT 'USER',
                entity_table VARCHAR(100) NOT NULL,
                entity_id UUID NOT NULL,
                action VARCHAR(50) NOT NULL,
                previous_state JSONB,
                new_state JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
            await ephem_conn.execute(schema_prereqs_sql)
        finally:
            await ephem_conn.close()

        # 3. Configure runner test environment
        test_env = {
            "ONSIGNING_MIGRATION_OPT_IN": "1",
            "ONSIGNING_STAGING_PROJECT_REF": "disposable-test-ref",
            "ONSIGNING_STAGING_HOST_OVERRIDE": host,
            "ONSIGNING_STAGING_DB_NAME": ephemeral_db,
            "ONSIGNING_MIGRATION_DSN": f"postgresql://{superuser}:{superuser_password}@{host}:{port}/{ephemeral_db}",
            "ONSIGNING_RUNNER_TEST_MODE": "true",
            "ONSIGNING_ALLOW_INSECURE_LOCAL_TEST_TLS": "true",
        }
        test_args = argparse.Namespace(
            target="staging",
            confirm_staging=True,
            project_ref="disposable-test-ref",
            expected_db=ephemeral_db,
            ca_path=None,
            timeout=30.0,
        )

        ok, msg, config = validate_preflight_config(test_args, test_env)
        assert ok, f"Pre-flight failed: {msg}"
        assert config is not None

        # 4. Execute runner against real disposable PostgreSQL engine
        exit_code = await execute_and_verify(config)
        assert exit_code == EXIT_SUCCESS, f"Live runner execution failed with exit code: {exit_code}"

        # 5. Prove atomic rollback with a deliberate test-only failure inside a transaction
        test_conn = await asyncpg.connect(
            host=host,
            port=port,
            user=superuser,
            password=superuser_password,
            database=ephemeral_db,
        )
        try:
            # Create a test canary table
            await test_conn.execute("CREATE TABLE public._rollback_canary (id int);")

            # Execute transactional block with deliberate error
            rollback_triggered = False
            try:
                async with test_conn.transaction():
                    await test_conn.execute("INSERT INTO public._rollback_canary VALUES (42);")
                    # Deliberate syntax/catalog failure
                    await test_conn.execute("SELECT * FROM non_existent_table_for_rollback_proof;")
            except Exception:
                rollback_triggered = True

            assert rollback_triggered, "Expected transaction to fail on deliberate SQL error."
            canary_count = await test_conn.fetchval("SELECT count(*) FROM public._rollback_canary;")
            assert canary_count == 0, f"Rollback failed: canary count is {canary_count}, expected 0."
        finally:
            await test_conn.close()

    finally:
        # Preserve original test failure (do not mask with cleanup error)
        exc_type, exc_val, exc_tb = sys.exc_info()
        cleanup_errors: list[Exception] = []

        try:
            m_conn_cleanup = await asyncpg.connect(
                host=host,
                port=port,
                user=superuser,
                password=superuser_password,
                database="postgres",
            )
            try:
                # Step 1: Drop ephemeral database first.
                # Must precede role cleanup because default ACLs and permissions in the database
                # depend on the mock roles. Dropping the database removes all database-local catalog entries.
                if ephemeral_db:
                    assert ephemeral_db.startswith("onsigning_test_runner_") and ephemeral_db != "postgres", (
                        f"Refusing to drop potentially non-ephemeral database: {ephemeral_db}"
                    )
                    assert ephemeral_db not in ("postgres", "template0", "template1"), (
                        f"Refusing to drop system database: {ephemeral_db}"
                    )
                    try:
                        await m_conn_cleanup.execute(
                            """
                            SELECT pg_terminate_backend(pid)
                            FROM pg_stat_activity
                            WHERE datname = $1 AND pid <> pg_backend_pid();
                            """,
                            ephemeral_db,
                        )
                        await m_conn_cleanup.execute(f'DROP DATABASE IF EXISTS "{ephemeral_db}" WITH (FORCE);')
                    except Exception as db_err:
                        cleanup_errors.append(db_err)

                # Step 2: Drop only mock PostgREST roles created specifically by this fixture.
                # Never drops onsigning_app or pre-existing cluster roles.
                for role_name in roles_created_by_test:
                    if role_name not in ("anon", "authenticated", "service_role"):
                        continue
                    try:
                        # Check whether another cluster resource or database depends on this role
                        has_dependents = await m_conn_cleanup.fetchval(
                            """
                            SELECT EXISTS (
                                SELECT 1 FROM pg_shdepend s
                                JOIN pg_roles r ON r.oid = s.refobjid
                                WHERE r.rolname = $1
                            );
                            """,
                            role_name,
                        )
                        if has_dependents:
                            continue

                        await m_conn_cleanup.execute(f'DROP ROLE IF EXISTS "{role_name}";')
                    except asyncpg.exceptions.DependentObjectsStillExistError:
                        # Role has dependencies in other test resources; leave intact safely
                        continue
                    except Exception as role_err:
                        cleanup_errors.append(role_err)
            finally:
                await m_conn_cleanup.close()
        except Exception as conn_err:
            cleanup_errors.append(conn_err)

        if cleanup_errors:
            if exc_val is None:
                raise RuntimeError(f"Live integration cleanup failed: {cleanup_errors}")
            else:
                sys.stderr.write(f"\n[CLEANUP WARNING] Secondary error during teardown: {cleanup_errors}\n")
