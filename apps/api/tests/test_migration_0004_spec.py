"""
Unit & Contract Tests for Database Migration 0004: PostgREST Privilege Hardening & Supabase Model Reconciliation.
Validates SQL structure, table allowlists, fail-closed prerequisite validation,
zero-trust PostgREST boundary, and runtime least-privilege invariants without connecting to any database.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
from unittest.mock import AsyncMock, MagicMock
import pytest

from packages.database.run_migration import (
    EXIT_SUCCESS,
    EXIT_PREFLIGHT_FAILURE,
    EXIT_VERIFICATION_FAILURE,
    APPROVED_MIGRATION_0004_SHA256,
    APPROVED_MIGRATIONS,
    validate_preflight_config,
    execute_and_verify,
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "packages" / "database" / "migrations"
MIGRATION_0004_FILE = MIGRATIONS_DIR / "0004_postgrest_privilege_hardening.sql"

ALL_17_TABLES = [
    "users",
    "organizations",
    "organization_memberships",
    "vendors",
    "risk_playbooks",
    "contract_families",
    "documents",
    "document_relationships",
    "document_sections",
    "document_chunks",
    "extracted_clauses",
    "audit_runs",
    "risk_alerts",
    "qa_queries",
    "obligations",
    "obligation_notifications",
    "audit_logs",
]

STANDARD_16_TABLES = [t for t in ALL_17_TABLES if t != "audit_logs"]


def test_migration_0004_discovery_and_hash():
    """Asserts that 0004 exists and matches the approved SHA-256 digest exactly."""
    assert MIGRATION_0004_FILE.is_file(), f"Migration file missing at: {MIGRATION_0004_FILE}"
    sql_bytes = MIGRATION_0004_FILE.read_bytes()
    sha256 = hashlib.sha256(sql_bytes).hexdigest()
    assert sha256 == APPROVED_MIGRATION_0004_SHA256
    assert APPROVED_MIGRATIONS["0004_postgrest_privilege_hardening.sql"] == sha256


def test_migration_0004_fail_closed_prerequisites():
    """Asserts that 0004 validates onsigning_app and all 17 application tables in Section 1."""
    sql = MIGRATION_0004_FILE.read_text(encoding="utf-8")

    # Validates onsigning_app role
    assert "SELECT 1 FROM pg_roles WHERE rolname = 'onsigning_app'" in sql
    assert 'Prerequisite role missing: "onsigning_app"' in sql

    # Validates all 17 tables are in expected_tables array
    for table in ALL_17_TABLES:
        assert f"'{table}'" in sql, f"Table '{table}' missing from prerequisite check"


def test_migration_0004_universal_public_revocations():
    """Asserts that universal PUBLIC access is revoked from all 17 application tables in Section 2."""
    sql = MIGRATION_0004_FILE.read_text(encoding="utf-8")

    for table in ALL_17_TABLES:
        pattern = rf"public\.{table}\b"
        assert re.search(pattern, sql), f"Table public.{table} missing from PUBLIC revocation list"

    assert "FROM PUBLIC;" in sql


def test_migration_0004_runtime_application_grants():
    """Asserts that onsigning_app receives CRUD on 16 tables and append-only on audit_logs."""
    sql = MIGRATION_0004_FILE.read_text(encoding="utf-8")

    # 16 tables receive CRUD
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE" in sql
    assert "REVOKE TRUNCATE ON TABLE" in sql

    # audit_logs strictly append-only
    assert re.search(r"GRANT\s+SELECT,\s+INSERT\s+ON\s+TABLE\s+public\.audit_logs\s+TO\s+onsigning_app\s*;", sql)
    assert re.search(r"REVOKE\s+UPDATE,\s+DELETE,\s+TRUNCATE\s+ON\s+TABLE\s+public\.audit_logs\s+FROM\s+onsigning_app\s*;", sql)

    # Schema USAGE and sequence privileges
    assert "GRANT USAGE ON SCHEMA public TO onsigning_app;" in sql
    assert "REVOKE CREATE ON SCHEMA public FROM onsigning_app;" in sql
    assert "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO onsigning_app;" in sql


def test_migration_0004_postgrest_zero_trust_revocations():
    """Asserts that anon, authenticated, and service_role have ALL revoked on all 17 tables."""
    sql = MIGRATION_0004_FILE.read_text(encoding="utf-8")

    assert "ARRAY['anon', 'authenticated', 'service_role']" in sql
    assert "EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.%I FROM %I', t, r);" in sql


def test_migration_0004_default_privileges_reconciliation():
    """Asserts that default privileges for role postgres are hardened globally and in public schema."""
    sql = MIGRATION_0004_FILE.read_text(encoding="utf-8")

    # 1. Global PostgREST default revocations (defaclnamespace = 0)
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE ALL ON TABLES FROM %I" in sql
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE ALL ON SEQUENCES FROM %I" in sql

    # 2. Global PUBLIC default revocations (defaclnamespace = 0)
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE ALL ON TABLES FROM PUBLIC" in sql
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE ALL ON SEQUENCES FROM PUBLIC" in sql

    # 3. Schema public PostgREST default revocations (defaclnamespace = public)
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM %I" in sql
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I" in sql

    # 4. Schema public PUBLIC default revocations (defaclnamespace = public)
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC" in sql
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC" in sql

    # 5. onsigning_app granted postgres defaults in schema public
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO onsigning_app" in sql
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO onsigning_app" in sql

    # 6. Least privilege: TRUNCATE, REFERENCES, TRIGGER not granted
    assert "GRANT TRUNCATE" not in sql
    assert "GRANT REFERENCES" not in sql
    assert "GRANT TRIGGER" not in sql

    # Platform superuser untouched
    assert "FOR ROLE supabase_admin" not in sql


# ==============================================================================
# Runner Integration & Post-Commit Verification Tests for Migration 0004
# ==============================================================================

@pytest.fixture
def valid_0004_env():
    return {
        "ONSIGNING_MIGRATION_OPT_IN": "1",
        "ONSIGNING_STAGING_PROJECT_REF": "stgprojectref",
        "ONSIGNING_MIGRATION_DSN": "postgresql://postgres:secret123@db.stgprojectref.supabase.co:5432/postgres",
        "ONSIGNING_RUNNER_TEST_MODE": "true",
    }


@pytest.fixture
def valid_0004_args():
    return argparse.Namespace(
        target="staging",
        confirm_staging=True,
        project_ref="stgprojectref",
        expected_db="postgres",
        ca_path=None,
        timeout=60.0,
        migration="0004_postgrest_privilege_hardening.sql",
    )


def test_preflight_0004_hash_validation(valid_0004_args, valid_0004_env):
    """Proves preflight succeeds when 0004 is requested with matching hash."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok, f"Preflight failed: {msg}"
    assert config is not None
    assert config["migration_filename"] == "0004_postgrest_privilege_hardening.sql"
    assert config["migration_sha256"] == APPROVED_MIGRATION_0004_SHA256


def test_preflight_0004_tampered_hash_fails_closed(valid_0004_args, valid_0004_env, monkeypatch):
    """Proves tampered 0004 bytes are rejected before connecting."""
    real_read_bytes = Path.read_bytes

    def fake_read_bytes(self):
        if self.name == "0004_postgrest_privilege_hardening.sql":
            return b"-- tampered bytes\n"
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert not ok
    assert "Migration integrity check failed: SHA-256 digest" in msg
    assert config is None


class MockAsyncTransaction:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return None


def make_clean_0004_mock_responses():
    """Generates the 7 clean catalog query responses for 0004 post-commit verification."""
    # 1. Catalog table presence: all 17 tables exist
    presence_rows = [{"tablename": t} for t in ALL_17_TABLES]

    # 2. PostgREST role existence: anon, authenticated, service_role exist in cluster
    clean_postgrest_roles = [
        {"rolname": "anon"},
        {"rolname": "authenticated"},
        {"rolname": "service_role"},
    ]

    # 3. PostgREST roles: all 7 privileges False for anon, authenticated, service_role
    clean_role_rows = []
    for r in ("anon", "authenticated", "service_role"):
        for t in ALL_17_TABLES:
            clean_role_rows.append({
                "rolname": r, "tablename": t,
                "can_select": False, "can_insert": False,
                "can_update": False, "can_delete": False, "can_truncate": False,
                "can_references": False, "can_trigger": False,
            })

    # 4. PUBLIC acl entries via aclexplode: clean (empty list, no rows with grantee=0)
    clean_public_acl_rows = []

    # 5. onsigning_app: CRUD on 16 standard tables; append-only on audit_logs
    clean_app_rows = []
    for t in STANDARD_16_TABLES:
        clean_app_rows.append({
            "tablename": t,
            "can_select": True, "can_insert": True,
            "can_update": True, "can_delete": True, "can_truncate": False,
            "can_references": False, "can_trigger": False,
        })
    clean_app_rows.append({
        "tablename": "audit_logs",
        "can_select": True, "can_insert": True,
        "can_update": False, "can_delete": False, "can_truncate": False,
        "can_references": False, "can_trigger": False,
    })

    # 6. Default ACL entries for postgres in public (tables 'r' and sequences 'S')
    clean_default_entries = [
        {"defaclobjtype": "r", "defaclnamespace": 1234, "scope": "public", "defaclacl_str": "{postgres=arwdDxt/postgres,onsigning_app=arwd/postgres}"},
        {"defaclobjtype": "S", "defaclnamespace": 1234, "scope": "public", "defaclacl_str": "{postgres=rwU/postgres,onsigning_app=rU/postgres}"},
    ]

    # 7. Default ACL exploded privileges for onsigning_app
    clean_default_exploded = [
        {"defaclobjtype": "r", "scope": "public", "grantee": 5678, "grantee_name": "onsigning_app", "privilege_type": "SELECT", "is_grantable": False},
        {"defaclobjtype": "r", "scope": "public", "grantee": 5678, "grantee_name": "onsigning_app", "privilege_type": "INSERT", "is_grantable": False},
        {"defaclobjtype": "r", "scope": "public", "grantee": 5678, "grantee_name": "onsigning_app", "privilege_type": "UPDATE", "is_grantable": False},
        {"defaclobjtype": "r", "scope": "public", "grantee": 5678, "grantee_name": "onsigning_app", "privilege_type": "DELETE", "is_grantable": False},
        {"defaclobjtype": "S", "scope": "public", "grantee": 5678, "grantee_name": "onsigning_app", "privilege_type": "USAGE", "is_grantable": False},
        {"defaclobjtype": "S", "scope": "public", "grantee": 5678, "grantee_name": "onsigning_app", "privilege_type": "SELECT", "is_grantable": False},
    ]

    return [
        presence_rows,
        clean_postgrest_roles,
        clean_role_rows,
        clean_public_acl_rows,
        clean_app_rows,
        clean_default_entries,
        clean_default_exploded,
    ]


@pytest.mark.asyncio
async def test_0004_post_commit_verification_clean_success(valid_0004_args, valid_0004_env, capsys):
    """Proves 0004 post-commit verification passes and identifies 0004 in success output."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres",
        "user_name": "postgres",
        "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())
    mock_conn.fetch.side_effect = make_clean_0004_mock_responses()

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_SUCCESS

    captured = capsys.readouterr()
    assert "[VERIFICATION SUCCESS] All post-commit security invariants verified." in captured.out
    assert "[RUNNER SUCCESS] Migration 0004_postgrest_privilege_hardening.sql successfully applied and verified." in captured.out


@pytest.mark.asyncio
async def test_0004_post_commit_fails_if_service_role_has_table_privilege(valid_0004_args, valid_0004_env):
    """Proves verification fails closed if service_role retains privileges on any of the 17 tables."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres",
        "user_name": "postgres",
        "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())

    responses = make_clean_0004_mock_responses()
    # Modify query 3: service_role retains SELECT on documents
    dirty_role_rows = responses[2]
    for r in dirty_role_rows:
        if r["rolname"] == "service_role" and r["tablename"] == "documents":
            r["can_select"] = True

    mock_conn.fetch.side_effect = responses

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE


@pytest.mark.asyncio
async def test_0004_post_commit_fails_if_table_missing_from_schema(valid_0004_args, valid_0004_env, capsys):
    """Proves verification fails closed if an expected table is missing from schema public."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres", "user_name": "postgres", "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())

    responses = make_clean_0004_mock_responses()
    # Remove audit_logs from query 1 (table presence)
    responses[0] = [r for r in responses[0] if r["tablename"] != "audit_logs"]
    mock_conn.fetch.side_effect = responses

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE
    captured = capsys.readouterr()
    assert "Catalog inconsistency: expected table(s) missing from schema public: ['audit_logs']" in captured.err


@pytest.mark.asyncio
async def test_0004_post_commit_fails_if_public_retains_acl_entry(valid_0004_args, valid_0004_env, capsys):
    """Proves verification fails closed if PUBLIC pseudo-role retains an ACL grant on any table."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres", "user_name": "postgres", "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())

    responses = make_clean_0004_mock_responses()
    # Add PUBLIC grant to query 4 (public_acl_query)
    responses[3] = [{"tablename": "users", "privilege_type": "SELECT", "is_grantable": False}]
    mock_conn.fetch.side_effect = responses

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE
    captured = capsys.readouterr()
    assert "PUBLIC pseudo-role has prohibited privilege 'SELECT' on public.users." in captured.err


@pytest.mark.asyncio
async def test_0004_post_commit_fails_if_sequence_default_acl_missing(valid_0004_args, valid_0004_env, capsys):
    """Proves verification fails closed if sequence default ACL is missing even when table defaults exist."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres", "user_name": "postgres", "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())

    responses = make_clean_0004_mock_responses()
    # Query 6: Keep only table defaults ('r'), omit sequence defaults ('S')
    responses[5] = [r for r in responses[5] if r["defaclobjtype"] != "S"]
    mock_conn.fetch.side_effect = responses

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE
    captured = capsys.readouterr()
    assert "Default ACL for role postgres in schema public (sequences) was not found in pg_default_acl." in captured.err


@pytest.mark.asyncio
async def test_0004_post_commit_fails_if_table_default_acl_missing(valid_0004_args, valid_0004_env, capsys):
    """Proves verification fails closed if table default ACL is missing even when sequence defaults exist."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres", "user_name": "postgres", "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())

    responses = make_clean_0004_mock_responses()
    # Query 6: Keep only sequence defaults ('S'), omit table defaults ('r')
    responses[5] = [r for r in responses[5] if r["defaclobjtype"] != "r"]
    mock_conn.fetch.side_effect = responses

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE
    captured = capsys.readouterr()
    assert "Default ACL for role postgres in schema public (tables) was not found in pg_default_acl." in captured.err


@pytest.mark.asyncio
async def test_0004_post_commit_fails_if_forbidden_default_acl_grant_present(valid_0004_args, valid_0004_env, capsys):
    """Proves verification fails closed if any prohibited role has a default grant in pg_default_acl."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres", "user_name": "postgres", "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())

    responses = make_clean_0004_mock_responses()
    # Query 7: Add prohibited default grant to anon
    responses[6].append({
        "defaclobjtype": "r", "scope": "public", "grantee": 20, "grantee_name": "anon",
        "privilege_type": "SELECT", "is_grantable": False,
    })
    mock_conn.fetch.side_effect = responses

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE
    captured = capsys.readouterr()
    assert "contains prohibited grant 'SELECT' to 'anon'" in captured.err


@pytest.mark.asyncio
async def test_0004_post_commit_fails_if_prohibited_global_default_acl_grant_persists(valid_0004_args, valid_0004_env, capsys):
    """Proves verification fails closed if a prohibited grant exists in global scope (defaclnamespace = 0)."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres", "user_name": "postgres", "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())

    responses = make_clean_0004_mock_responses()
    # Query 7: Add prohibited default grant to service_role in GLOBAL scope
    responses[6].append({
        "defaclobjtype": "r", "scope": "global", "grantee": 22, "grantee_name": "service_role",
        "privilege_type": "SELECT", "is_grantable": False,
    })
    mock_conn.fetch.side_effect = responses

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE
    captured = capsys.readouterr()
    assert "Default ACL for role postgres in global (tables) contains prohibited grant 'SELECT' to 'service_role'" in captured.err


@pytest.mark.asyncio
async def test_0004_post_commit_fails_if_onsigning_app_has_elevated_privilege(valid_0004_args, valid_0004_env, capsys):
    """Proves verification fails closed if onsigning_app has forbidden TRUNCATE/REFERENCES/TRIGGER."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres", "user_name": "postgres", "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())

    responses = make_clean_0004_mock_responses()
    # Query 5: onsigning_app has TRUNCATE on organizations
    for r in responses[4]:
        if r["tablename"] == "organizations":
            r["can_truncate"] = True
    mock_conn.fetch.side_effect = responses

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE
    captured = capsys.readouterr()
    assert "onsigning_app unexpectedly has TRUNCATE privilege on public.organizations." in captured.err


@pytest.mark.asyncio
async def test_0004_post_commit_fails_closed_on_query_error(valid_0004_args, valid_0004_env, capsys):
    """Proves verification catches unexpected catalog query errors and fails closed with code 3."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres", "user_name": "postgres", "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())
    mock_conn.fetch.side_effect = RuntimeError("Simulated transient connection timeout during query")

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE
    captured = capsys.readouterr()
    assert "[CRITICAL ALERT] Post-commit security verification encountered an unexpected query error!" in captured.err
    assert "The migration transaction has ALREADY COMMITTED and was NOT rolled back." in captured.err


@pytest.mark.asyncio
async def test_0004_post_commit_fails_if_postgrest_role_missing(valid_0004_args, valid_0004_env, capsys):
    """Proves verification fails closed if any PostgREST role is missing from pg_roles in staging."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres", "user_name": "postgres", "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())

    responses = make_clean_0004_mock_responses()
    # PostgREST role presence: service_role missing!
    responses[1] = [{"rolname": "anon"}, {"rolname": "authenticated"}]
    mock_conn.fetch.side_effect = responses

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE
    captured = capsys.readouterr()
    assert "Catalog inconsistency: expected PostgREST role(s) missing from cluster: ['service_role']" in captured.err


@pytest.mark.asyncio
async def test_0004_post_commit_fails_if_relation_is_view_or_non_ordinary(valid_0004_args, valid_0004_env, capsys):
    """Proves verification fails closed if a relation is not an ordinary table (relkind != 'r')."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres", "user_name": "postgres", "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())

    responses = make_clean_0004_mock_responses()
    # table_presence_query filters by c.relkind = 'r', so a view returns no row for 'users'
    responses[0] = [r for r in responses[0] if r["tablename"] != "users"]
    mock_conn.fetch.side_effect = responses

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_VERIFICATION_FAILURE
    captured = capsys.readouterr()
    assert "Catalog inconsistency: expected table(s) missing from schema public: ['users']" in captured.err


@pytest.mark.asyncio
async def test_0004_post_commit_verification_with_bytes_defaclobjtype(valid_0004_args, valid_0004_env, capsys):
    """Proves 0004 post-commit verification handles asyncpg bytes defaclobjtype (b'r' and b'S') cleanly."""
    ok, msg, config = validate_preflight_config(valid_0004_args, valid_0004_env)
    assert ok

    mock_conn = AsyncMock()
    mock_conn.is_closed = MagicMock(return_value=False)
    mock_conn.fetchrow.return_value = {
        "db_name": "postgres", "user_name": "postgres", "server_ip": "10.0.0.1",
    }
    mock_conn.transaction = MagicMock(return_value=MockAsyncTransaction())

    responses = make_clean_0004_mock_responses()
    # Simulate asyncpg returning raw bytes for pg_default_acl.defaclobjtype
    responses[5] = [
        {**r, "defaclobjtype": r["defaclobjtype"].encode("ascii")}
        for r in responses[5]
    ]
    responses[6] = [
        {**r, "defaclobjtype": r["defaclobjtype"].encode("ascii")}
        for r in responses[6]
    ]
    mock_conn.fetch.side_effect = responses

    exit_code = await execute_and_verify(config, conn_override=mock_conn)
    assert exit_code == EXIT_SUCCESS
    captured = capsys.readouterr()
    assert "[VERIFICATION SUCCESS] All post-commit security invariants verified." in captured.out


# ==============================================================================
# Static Source Code Invariant Tests
# ==============================================================================

def test_runner_queries_defaclobjtype_as_text():
    """Asserts that run_migration.py casts defaclobjtype to text in both default-privilege queries."""
    runner_path = Path(__file__).resolve().parents[3] / "packages" / "database" / "run_migration.py"
    source = runner_path.read_text(encoding="utf-8")

    assert source.count("d.defaclobjtype::text AS defaclobjtype") == 2, (
        "Expected both default ACL queries in run_migration.py to cast d.defaclobjtype::text AS defaclobjtype"
    )

def test_migration_0004_ci_notice_both_branches():
    """Asserts that 0004 contains accurate notices for both superuser and non-superuser in standalone/CI."""
    sql = MIGRATION_0004_FILE.read_text(encoding="utf-8")
    assert "runtime default grants established for role postgres" in sql
    assert "PostgREST table ACL revocations and postgres default grants skipped (insufficient authority for role postgres)" in sql


def test_migration_0004_section_1_requires_base_table():
    """Asserts that Section 1 validates table_type = 'BASE TABLE' so a view cannot pass prerequisite check."""
    sql = MIGRATION_0004_FILE.read_text(encoding="utf-8")
    assert "table_type = 'BASE TABLE'" in sql

def test_runner_source_never_uses_has_table_privilege_public():
    """Asserts that run_migration.py never calls has_table_privilege('public', ...) which crashes in PostgreSQL."""
    runner_path = Path(__file__).resolve().parents[3] / "packages" / "database" / "run_migration.py"
    source = runner_path.read_text(encoding="utf-8")

    assert "has_table_privilege('public'" not in source, "Prohibited call has_table_privilege('public', ...) detected in runner"
    assert 'has_table_privilege("public"' not in source, 'Prohibited call has_table_privilege("public", ...) detected in runner'
    assert "aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner)))" in source, "Expected aclexplode catalog query missing from runner"
    assert "a.grantee = 0" in source, "Expected grantee OID 0 filter for PUBLIC pseudo-role missing from runner"


def test_runner_checks_all_seven_privileges_for_postgrest_roles():
    """Asserts that run_migration.py checks all 7 table privileges for PostgREST roles."""
    runner_path = Path(__file__).resolve().parents[3] / "packages" / "database" / "run_migration.py"
    source = runner_path.read_text(encoding="utf-8")

    for priv in ("can_select", "can_insert", "can_update", "can_delete", "can_truncate", "can_references", "can_trigger"):
        assert priv in source, f"Privilege check '{priv}' missing from run_migration.py"


def test_migration_0003_hash_preserved():
    """Asserts that Migration 0003's pinned SHA-256 digest is strictly preserved."""
    mig_0003 = Path(__file__).resolve().parents[3] / "packages" / "database" / "migrations" / "0003_security_hardening.sql"
    assert mig_0003.is_file()
    actual_hash = hashlib.sha256(mig_0003.read_bytes()).hexdigest()
    assert actual_hash == APPROVED_MIGRATIONS["0003_security_hardening.sql"]
    assert actual_hash == "8ca03f97e7fe21b7ec208ef4b3835ba1af5ca2c9eef2f251a6ba4716b767ef28"
