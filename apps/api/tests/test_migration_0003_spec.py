"""
Unit Tests for Database Migration 0003: Security Hardening & PostgREST Default Privilege Remediation.
Validates migration file ordering, SQL structure, fail-closed authorization semantics,
and least-privilege invariants without requiring a live PostgreSQL instance.
"""

from pathlib import Path
import re
import pytest

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "packages" / "database" / "migrations"


def test_migration_chain_ordering_and_discovery():
    """Validates that migrations 0001, 0002, and 0003 exist in strict sequential lexical order."""
    assert MIGRATIONS_DIR.is_dir(), f"Migrations directory missing at: {MIGRATIONS_DIR}"

    sql_files = sorted(f.name for f in MIGRATIONS_DIR.glob("*.sql"))
    expected_files = [
        "0001_mvp_schema.sql",
        "0002_runtime_grants.sql",
        "0003_security_hardening.sql"
    ]
    assert sql_files == expected_files, f"Unexpected migration sequence: {sql_files}"


def test_migration_0003_prerequisite_validation_block():
    """Asserts that 0003 enforces fail-closed prerequisite checks for onsigning_app, audit_logs, and users."""
    file_path = MIGRATIONS_DIR / "0003_security_hardening.sql"
    sql = file_path.read_text(encoding="utf-8")

    # Check for fail-closed checks on onsigning_app
    assert "SELECT 1 FROM pg_roles WHERE rolname = 'onsigning_app'" in sql
    assert "Prerequisite role missing: \"onsigning_app\" does not exist in cluster" in sql

    # Check for fail-closed checks on audit_logs and users tables
    assert "table_name = 'audit_logs'" in sql
    assert "table_name = 'users'" in sql
    assert "Prerequisite schema missing: tables \"audit_logs\" and \"users\" must exist" in sql


def test_migration_0003_public_and_runtime_role_specifications():
    """Asserts that universal PUBLIC revocations and runtime role least-privilege grants are strictly specified."""
    file_path = MIGRATIONS_DIR / "0003_security_hardening.sql"
    sql = file_path.read_text(encoding="utf-8")

    # Universal PUBLIC revocations
    assert re.search(r"REVOKE\s+ALL\s+PRIVILEGES\s+ON\s+TABLE\s+public\.audit_logs\s+FROM\s+PUBLIC\s*;", sql, re.IGNORECASE)
    assert re.search(r"REVOKE\s+ALL\s+PRIVILEGES\s+ON\s+TABLE\s+public\.users\s+FROM\s+PUBLIC\s*;", sql, re.IGNORECASE)

    # onsigning_app on audit_logs: SELECT, INSERT only (append-only)
    assert re.search(r"GRANT\s+SELECT,\s+INSERT\s+ON\s+TABLE\s+public\.audit_logs\s+TO\s+onsigning_app\s*;", sql, re.IGNORECASE)
    assert re.search(r"REVOKE\s+UPDATE,\s+DELETE,\s+TRUNCATE\s+ON\s+TABLE\s+public\.audit_logs\s+FROM\s+onsigning_app\s*;", sql, re.IGNORECASE)

    # onsigning_app on users: SELECT, INSERT, UPDATE, DELETE (TRUNCATE withheld)
    assert re.search(r"GRANT\s+SELECT,\s+INSERT,\s+UPDATE,\s+DELETE\s+ON\s+TABLE\s+public\.users\s+TO\s+onsigning_app\s*;", sql, re.IGNORECASE)
    assert re.search(r"REVOKE\s+TRUNCATE\s+ON\s+TABLE\s+public\.users\s+FROM\s+onsigning_app\s*;", sql, re.IGNORECASE)


def test_migration_0003_postgrest_zero_trust_and_default_privilege_scope():
    """
    Asserts that Section 4 revokes PostgREST access from anon, authenticated, and service_role,
    and alters default privileges strictly for role postgres in schema public.
    """
    file_path = MIGRATIONS_DIR / "0003_security_hardening.sql"
    sql = file_path.read_text(encoding="utf-8")

    # Authoritative check requires postgres or superuser (does NOT rely on ambiguous pg_has_role USAGE)
    assert "current_user = 'postgres' OR (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)" in sql
    assert "pg_has_role" not in sql, "Must not rely on pg_has_role(..., 'USAGE') for ALTER DEFAULT PRIVILEGES authority"

    # Both audit_logs and users revoke from anon, authenticated, and service_role
    assert "REVOKE ALL PRIVILEGES ON TABLE public.audit_logs FROM %I" in sql
    assert "REVOKE ALL PRIVILEGES ON TABLE public.users FROM %I" in sql

    # Default privileges strictly scoped to role postgres and schema public
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM %I" in sql
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I" in sql

    # Ensures supabase_admin and function defaults are untouched
    assert "FOR ROLE supabase_admin" not in sql, "Must not alter defaults for platform superuser supabase_admin"
    assert "ON FUNCTIONS" not in sql and "ON ROUTINES" not in sql, "Must not alter default function execution privileges"

    # Ensures standalone CI branch explicitly documents skipping rather than claiming false verification
    assert "PostgREST table ACL and default privilege hardening skipped in this environment" in sql
