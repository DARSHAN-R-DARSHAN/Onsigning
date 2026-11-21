"""
PostgreSQL 16+ Row-Level Security (RLS) Live Integration Suite.
Strictly implements the verification requirements of Tasks 5.11.2 - 5.12.

Validates:
1. Direct PostgreSQL Engine RLS Policies:
   - Silent filtering (USING): SELECT, UPDATE, DELETE return 0 rows for foreign tenants.
   - Fail-closed rejections (WITH CHECK): Cross-tenant INSERT and tenant-id mutation raise SQLSTATE 42501.
   - Global identity: users table accessible across tenants without RLS.
   - Bootstrap membership lookup: organization_memberships allows user_id lookup before tenant is set.
   - Append-only audit_logs: Table ACL denies UPDATE, DELETE, TRUNCATE with SQLSTATE 42501.
   - Transaction recovery: Rollback clears INERROR state after an RLS violation.
2. SQLAlchemy 2.0 AsyncSession & Application Integration:
   - after_begin event hook reapplication across commit() and rollback().
   - set_user_context() clearing stale tenant context from session.info and database GUC.
   - Live user and tenant switching (Scenario A).
   - Connection pool reuse preventing GUC leakage.
   - System-worker execution with actor_user_id = NULL.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
import pytest
from fastapi import Request
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.auth import get_tenant_context
from apps.api.core.database import (
    set_user_context, set_tenant_context,
    User, Organization, OrganizationMembership, Document, AuditLog
)
from apps.api.core.exceptions import AuthorizationError

RUN_POSTGRES_RLS = os.getenv("RUN_POSTGRES_RLS", "false").lower() in ("true", "1", "yes")

pytestmark = [
    pytest.mark.postgres_rls,
    pytest.mark.skipif(
        not RUN_POSTGRES_RLS,
        reason="PostgreSQL RLS suite skipped: RUN_POSTGRES_RLS is not enabled. Real PostgreSQL 16+ instance required."
    )
]


# ==============================================================================
# Helper Fixtures & Data Seeds (Executed under onsigning_admin with BYPASSRLS)
# ==============================================================================

async def _seed_tenants_and_users(admin_conn):
    """Provisions Org A, Org B, User A (member of A), User B (member of B), and documents."""
    org_a_id = str(uuid.uuid4())
    org_b_id = str(uuid.uuid4())
    user_a_id = str(uuid.uuid4())
    user_b_id = str(uuid.uuid4())
    doc_a_id = str(uuid.uuid4())
    doc_b_id = str(uuid.uuid4())

    # 1. Organizations
    await admin_conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6);",
        uuid.UUID(org_a_id), "Acme Tenant A", f"acme-a-{uuid.uuid4().hex[:6]}",
        uuid.UUID(org_b_id), "Beta Tenant B", f"beta-b-{uuid.uuid4().hex[:6]}"
    )

    # 2. Users (Global identity, no RLS)
    await admin_conn.execute(
        "INSERT INTO users (id, email, full_name) VALUES ($1, $2, $3), ($4, $5, $6);",
        uuid.UUID(user_a_id), f"user.a.{uuid.uuid4().hex[:6]}@example.com", "Alice Acme",
        uuid.UUID(user_b_id), f"user.b.{uuid.uuid4().hex[:6]}@example.com", "Bob Beta"
    )

    # 3. Memberships
    await admin_conn.execute(
        """
        INSERT INTO organization_memberships (id, organization_id, user_id, role)
        VALUES ($1, $2, $3, 'ADMIN'), ($4, $5, $6, 'ADMIN');
        """,
        uuid.uuid4(), uuid.UUID(org_a_id), uuid.UUID(user_a_id),
        uuid.uuid4(), uuid.UUID(org_b_id), uuid.UUID(user_b_id)
    )

    # 4. Documents
    await admin_conn.execute(
        """
        INSERT INTO documents (
            id, organization_id, title, original_filename, storage_key, sha256, mime_type, byte_size
        ) VALUES
            ($1, $2, 'Doc A Master', 'doc_a.pdf', 'keys/a', 'sha256_dummy_a', 'application/pdf', 1024),
            ($3, $4, 'Doc B Master', 'doc_b.pdf', 'keys/b', 'sha256_dummy_b', 'application/pdf', 2048);
        """,
        uuid.UUID(doc_a_id), uuid.UUID(org_a_id),
        uuid.UUID(doc_b_id), uuid.UUID(org_b_id)
    )

    return {
        "org_a_id": org_a_id,
        "org_b_id": org_b_id,
        "user_a_id": user_a_id,
        "user_b_id": user_b_id,
        "doc_a_id": doc_a_id,
        "doc_b_id": doc_b_id,
    }


# ==============================================================================
# Layer A: Direct PostgreSQL Engine RLS Tests
# ==============================================================================

class TestDirectPostgresRLS:
    """Verifies PostgreSQL engine RLS semantics, SQLSTATEs, and table privileges."""

    @pytest.mark.asyncio
    async def test_runtime_role_privileges_and_attributes(self, pg_app_conn, pg_admin_conn):
        """Proves onsigning_app cannot bypass RLS, owns zero tables, and has no schema DDL powers."""
        # rolbypassrls check
        bypass = await pg_app_conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user;"
        )
        assert bypass is False, "onsigning_app must have rolbypassrls = false."

        # Table ownership check
        owned_tables = await pg_app_conn.fetchval(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user;"
        )
        assert owned_tables == 0, "onsigning_app must not own application tables."

        # Schema CREATE privilege check
        can_create = await pg_app_conn.fetchval(
            "SELECT has_schema_privilege(current_user, 'public', 'CREATE');"
        )
        assert can_create is False, "onsigning_app must not have CREATE privilege on schema public."

    @pytest.mark.asyncio
    async def test_same_tenant_reads(self, pg_app_conn, pg_admin_conn):
        """Proves that a transaction with app.current_organization_id reads tenant documents."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        async with pg_app_conn.transaction():
            await pg_app_conn.execute(
                "SELECT set_config('app.current_organization_id', $1, true);", data["org_a_id"]
            )
            rows = await pg_app_conn.fetch("SELECT id, title FROM documents;")
            assert len(rows) == 1
            assert str(rows[0]["id"]) == data["doc_a_id"]
            assert rows[0]["title"] == "Doc A Master"

    @pytest.mark.asyncio
    async def test_cross_tenant_reads_silent_filtering(self, pg_app_conn, pg_admin_conn):
        """Proves that foreign tenant rows are silently excluded (USING clause) with rowcount = 0."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        async with pg_app_conn.transaction():
            await pg_app_conn.execute(
                "SELECT set_config('app.current_organization_id', $1, true);", data["org_a_id"]
            )
            # Explicitly query for Doc B while in Org A context
            rows = await pg_app_conn.fetch(
                "SELECT * FROM documents WHERE id = $1;", uuid.UUID(data["doc_b_id"])
            )
            assert len(rows) == 0, "Cross-tenant read must return 0 rows without exception."

    @pytest.mark.asyncio
    async def test_cross_tenant_updates_silent_filtering(self, pg_app_conn, pg_admin_conn):
        """Proves that updating a foreign tenant's row silently matches 0 rows."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        async with pg_app_conn.transaction():
            await pg_app_conn.execute(
                "SELECT set_config('app.current_organization_id', $1, true);", data["org_a_id"]
            )
            status = await pg_app_conn.execute(
                "UPDATE documents SET title = 'Tampered' WHERE id = $1;", uuid.UUID(data["doc_b_id"])
            )
            assert status == "UPDATE 0", "Cross-tenant UPDATE must affect 0 rows."

        # Verify Doc B was NOT altered (checked via admin connection)
        title_b = await pg_admin_conn.fetchval(
            "SELECT title FROM documents WHERE id = $1;", uuid.UUID(data["doc_b_id"])
        )
        assert title_b == "Doc B Master"

    @pytest.mark.asyncio
    async def test_cross_tenant_deletes_silent_filtering(self, pg_app_conn, pg_admin_conn):
        """Proves that deleting a foreign tenant's row silently matches 0 rows."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        async with pg_app_conn.transaction():
            await pg_app_conn.execute(
                "SELECT set_config('app.current_organization_id', $1, true);", data["org_a_id"]
            )
            status = await pg_app_conn.execute(
                "DELETE FROM documents WHERE id = $1;", uuid.UUID(data["doc_b_id"])
            )
            assert status == "DELETE 0", "Cross-tenant DELETE must affect 0 rows."

        # Verify Doc B still exists
        exists_b = await pg_admin_conn.fetchval(
            "SELECT count(*) FROM documents WHERE id = $1;", uuid.UUID(data["doc_b_id"])
        )
        assert exists_b == 1

    @pytest.mark.asyncio
    async def test_cross_tenant_inserts_rejected_with_check(self, pg_app_conn, pg_admin_conn):
        """Proves that inserting a row for Org B while context is Org A raises SQLSTATE 42501."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        async with pg_app_conn.transaction():
            await pg_app_conn.execute(
                "SELECT set_config('app.current_organization_id', $1, true);", data["org_a_id"]
            )
            with pytest.raises(Exception) as exc_info:
                await pg_app_conn.execute(
                    """
                    INSERT INTO documents (
                        id, organization_id, title, original_filename, storage_key, sha256, mime_type, byte_size
                    ) VALUES ($1, $2, 'Illegal Insert', 'x.pdf', 'keys/x', 'sha256_x', 'application/pdf', 100);
                    """,
                    uuid.uuid4(), uuid.UUID(data["org_b_id"])
                )

            # Assert PostgreSQL error attributes
            err = exc_info.value
            assert getattr(err, "sqlstate", None) == "42501"
            assert "violates row-level security policy" in str(err)

    @pytest.mark.asyncio
    async def test_tenant_mutability_rejected_with_check(self, pg_app_conn, pg_admin_conn):
        """Proves that re-homing a document (changing organization_id) raises SQLSTATE 42501."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        async with pg_app_conn.transaction():
            await pg_app_conn.execute(
                "SELECT set_config('app.current_organization_id', $1, true);", data["org_a_id"]
            )
            with pytest.raises(Exception) as exc_info:
                await pg_app_conn.execute(
                    "UPDATE documents SET organization_id = $1 WHERE id = $2;",
                    uuid.UUID(data["org_b_id"]), uuid.UUID(data["doc_a_id"])
                )

            err = exc_info.value
            assert getattr(err, "sqlstate", None) == "42501"
            assert "violates row-level security policy" in str(err)

    @pytest.mark.asyncio
    async def test_missing_context_fails_closed(self, pg_app_conn, pg_admin_conn):
        """Proves that an unauthenticated session (empty GUC) sees 0 rows and cannot write."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        async with pg_app_conn.transaction():
            # Reads must return 0 rows
            doc_count = await pg_app_conn.fetchval("SELECT count(*) FROM documents;")
            assert doc_count == 0

            # Writes must fail RLS WITH CHECK
            with pytest.raises(Exception) as exc_info:
                await pg_app_conn.execute(
                    """
                    INSERT INTO documents (
                        id, organization_id, title, original_filename, storage_key, sha256, mime_type, byte_size
                    ) VALUES ($1, $2, 'No Context', 'nc.pdf', 'keys/nc', 'sha256_nc', 'application/pdf', 100);
                    """,
                    uuid.uuid4(), uuid.UUID(data["org_a_id"])
                )
            assert getattr(exc_info.value, "sqlstate", None) == "42501"

    @pytest.mark.asyncio
    async def test_global_users_table_accessible_without_rls(self, pg_app_conn, pg_admin_conn):
        """Proves that the users table has no RLS and is readable across tenants."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        async with pg_app_conn.transaction():
            # Query users without setting any tenant context
            user_count = await pg_app_conn.fetchval("SELECT count(*) FROM users;")
            assert user_count >= 2

            # Query users with Org A context
            await pg_app_conn.execute(
                "SELECT set_config('app.current_organization_id', $1, true);", data["org_a_id"]
            )
            users_in_context = await pg_app_conn.fetchval("SELECT count(*) FROM users;")
            assert users_in_context >= 2

    @pytest.mark.asyncio
    async def test_organization_memberships_dual_predicate_bootstrap(self, pg_app_conn, pg_admin_conn):
        """
        Proves that when only app.current_user_id is set (bootstrap phase),
        the user can query their own membership but cannot see other users' memberships.
        """
        data = await _seed_tenants_and_users(pg_admin_conn)

        async with pg_app_conn.transaction():
            # 1. Establish User A user-only context (organization_id = '')
            await pg_app_conn.execute(
                "SELECT set_config('app.current_user_id', $1, true);", data["user_a_id"]
            )
            await pg_app_conn.execute("SELECT set_config('app.current_organization_id', '', true);")

            # Can read own membership
            my_memberships = await pg_app_conn.fetch(
                "SELECT organization_id, role FROM organization_memberships WHERE user_id = $1;",
                uuid.UUID(data["user_a_id"])
            )
            assert len(my_memberships) == 1
            assert str(my_memberships[0]["organization_id"]) == data["org_a_id"]

            # Cannot read User B's membership
            other_memberships = await pg_app_conn.fetch(
                "SELECT * FROM organization_memberships WHERE user_id = $1;",
                uuid.UUID(data["user_b_id"])
            )
            assert len(other_memberships) == 0

            # Under Migration 0005 (Phase 2): User A discovers their assigned organization (Org A),
            # while foreign organizations (Org B) remain completely invisible.
            orgs_visible = await pg_app_conn.fetch("SELECT id FROM organizations;")
            assert len(orgs_visible) == 1
            assert str(orgs_visible[0]["id"]) == data["org_a_id"]

            # An unaffiliated user (with zero memberships) discovers 0 organizations
            unaffiliated_id = str(uuid.uuid4())
            await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", unaffiliated_id)
            unaffiliated_count = await pg_app_conn.fetchval("SELECT count(*) FROM organizations;")
            assert unaffiliated_count == 0

    @pytest.mark.asyncio
    async def test_phase2_organization_discovery_rls_matrix(self, pg_app_conn, pg_admin_conn):
        """
        Phase 2 Comprehensive Regression Suite for Migration 0005 (Single-Query Discovery RLS).
        Exhaustively tests all 12 required security and behavioral invariants:
        1. No user GUC set (uninitialized): zero organizations.
        2. Empty user GUC ('') set: zero organizations.
        3. Valid user with no memberships: zero organizations.
        4. User A with one membership: exactly Org A.
        5. User A with multiple memberships: exactly the assigned organizations.
        6. User A cannot discover Org B when only User B belongs to Org B.
        7. Deleting a membership removes discovery visibility in the next query.
        8. User context switching across separate transactions returns the correct organization set.
        9. UPDATE and DELETE remain blocked in pre-tenant context.
        10. INSERT remains blocked in pre-tenant context.
        11. Existing tenant-scoped reads and writes continue to work.
        12. Ordering is deterministic when membership timestamps tie.
        """
        import asyncpg
        data = await _seed_tenants_and_users(pg_admin_conn)
        org_a_id = data["org_a_id"]
        org_b_id = data["org_b_id"]
        user_a_id = data["user_a_id"]
        user_b_id = data["user_b_id"]

        # 1. No user GUC set (uninitialized): zero organizations
        async with pg_app_conn.transaction():
            count_uninit = await pg_app_conn.fetchval("SELECT count(*) FROM organizations;")
            assert count_uninit == 0

        # 2. Empty user GUC ('') set: zero organizations
        async with pg_app_conn.transaction():
            await pg_app_conn.execute("SELECT set_config('app.current_user_id', '', true);")
            await pg_app_conn.execute("SELECT set_config('app.current_organization_id', '', true);")
            count_empty = await pg_app_conn.fetchval("SELECT count(*) FROM organizations;")
            assert count_empty == 0

        # 3. Valid user with no memberships: zero organizations
        unaffiliated_id = str(uuid.uuid4())
        async with pg_app_conn.transaction():
            await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", unaffiliated_id)
            await pg_app_conn.execute("SELECT set_config('app.current_organization_id', '', true);")
            count_unaffil = await pg_app_conn.fetchval("SELECT count(*) FROM organizations;")
            assert count_unaffil == 0

        # 4. User A with one membership: exactly Org A
        async with pg_app_conn.transaction():
            await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", user_a_id)
            await pg_app_conn.execute("SELECT set_config('app.current_organization_id', '', true);")
            rows_a = await pg_app_conn.fetch("SELECT id, name FROM organizations;")
            assert len(rows_a) == 1
            assert str(rows_a[0]["id"]) == org_a_id

        # 5. User A with multiple memberships: exactly the assigned organizations
        m_extra_id = uuid.uuid4()
        await pg_admin_conn.execute(
            "INSERT INTO organization_memberships (id, organization_id, user_id, role) VALUES ($1, $2, $3, 'VIEWER');",
            m_extra_id, uuid.UUID(org_b_id), uuid.UUID(user_a_id)
        )
        try:
            async with pg_app_conn.transaction():
                await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", user_a_id)
                await pg_app_conn.execute("SELECT set_config('app.current_organization_id', '', true);")
                rows_multi = await pg_app_conn.fetch("SELECT id FROM organizations ORDER BY id;")
                assert len(rows_multi) == 2
                discovered = {str(r["id"]) for r in rows_multi}
                assert discovered == {org_a_id, org_b_id}
        finally:
            await pg_admin_conn.execute("DELETE FROM organization_memberships WHERE id = $1;", m_extra_id)

        # 6. User A cannot discover Org B when only User B belongs to Org B
        async with pg_app_conn.transaction():
            await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", user_a_id)
            await pg_app_conn.execute("SELECT set_config('app.current_organization_id', '', true);")
            org_b_lookup = await pg_app_conn.fetch(
                "SELECT id FROM organizations WHERE id = $1;", uuid.UUID(org_b_id)
            )
            assert len(org_b_lookup) == 0

        # 7. Deleting a membership removes discovery visibility in the next query
        temp_org_id = uuid.uuid4()
        temp_user_id = uuid.uuid4()
        temp_mem_id = uuid.uuid4()
        await pg_admin_conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3);",
            temp_org_id, "Temp Discovery Org", f"temp-{uuid.uuid4().hex[:6]}"
        )
        await pg_admin_conn.execute(
            "INSERT INTO users (id, email, full_name) VALUES ($1, $2, $3);",
            temp_user_id, f"temp.{uuid.uuid4().hex[:6]}@example.com", "Temp User"
        )
        await pg_admin_conn.execute(
            "INSERT INTO organization_memberships (id, organization_id, user_id, role) VALUES ($1, $2, $3, 'ADMIN');",
            temp_mem_id, temp_org_id, temp_user_id
        )
        try:
            # Query 1: Visible while membership exists
            async with pg_app_conn.transaction():
                await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", str(temp_user_id))
                await pg_app_conn.execute("SELECT set_config('app.current_organization_id', '', true);")
                count_before = await pg_app_conn.fetchval("SELECT count(*) FROM organizations;")
                assert count_before == 1

            # Delete membership
            await pg_admin_conn.execute("DELETE FROM organization_memberships WHERE id = $1;", temp_mem_id)

            # Query 2: Immediately invisible after deletion
            async with pg_app_conn.transaction():
                await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", str(temp_user_id))
                await pg_app_conn.execute("SELECT set_config('app.current_organization_id', '', true);")
                count_after = await pg_app_conn.fetchval("SELECT count(*) FROM organizations;")
                assert count_after == 0
        finally:
            await pg_admin_conn.execute("DELETE FROM organizations WHERE id = $1;", temp_org_id)
            await pg_admin_conn.execute("DELETE FROM users WHERE id = $1;", temp_user_id)

        # 8. User context switching across separate transactions returns the correct organization set
        async with pg_app_conn.transaction():
            await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", user_a_id)
            rows1 = await pg_app_conn.fetch("SELECT id FROM organizations;")
            assert str(rows1[0]["id"]) == org_a_id

        async with pg_app_conn.transaction():
            await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", user_b_id)
            rows2 = await pg_app_conn.fetch("SELECT id FROM organizations;")
            assert str(rows2[0]["id"]) == org_b_id

        # 9. UPDATE and DELETE remain blocked in pre-tenant context
        async with pg_app_conn.transaction():
            await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", user_a_id)
            await pg_app_conn.execute("SELECT set_config('app.current_organization_id', '', true);")
            updated = await pg_app_conn.execute(
                "UPDATE organizations SET name = 'Unauthorized' WHERE id = $1;", uuid.UUID(org_a_id)
            )
            assert updated == "UPDATE 0"
            deleted = await pg_app_conn.execute(
                "DELETE FROM organizations WHERE id = $1;", uuid.UUID(org_a_id)
            )
            assert deleted == "DELETE 0"

        # 10. INSERT remains blocked in pre-tenant context (RLS WITH CHECK fails closed)
        async with pg_app_conn.transaction():
            await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", user_a_id)
            await pg_app_conn.execute("SELECT set_config('app.current_organization_id', '', true);")
            with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
                await pg_app_conn.execute(
                    "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3);",
                    uuid.uuid4(), "Hacked Org", f"hacked-{uuid.uuid4().hex[:6]}"
                )

        # 11. Existing tenant-scoped reads and writes continue to work
        async with pg_app_conn.transaction():
            await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", user_a_id)
            await pg_app_conn.execute("SELECT set_config('app.current_organization_id', $1, true);", org_a_id)
            org_doc_count = await pg_app_conn.fetchval(
                "SELECT count(*) FROM documents WHERE organization_id = $1;", uuid.UUID(org_a_id)
            )
            assert org_doc_count == 1
            updated_tenant = await pg_app_conn.execute(
                "UPDATE organizations SET name = 'Acme Tenant A Updated' WHERE id = $1;", uuid.UUID(org_a_id)
            )
            assert updated_tenant == "UPDATE 1"

        # 12. Ordering is deterministic when membership timestamps tie
        tie_user_id = uuid.uuid4()
        tie_org1_id = uuid.uuid4()
        tie_org2_id = uuid.uuid4()
        tie_time = "2026-11-20 12:00:00+00"
        await pg_admin_conn.execute(
            "INSERT INTO users (id, email, full_name) VALUES ($1, $2, $3);",
            tie_user_id, f"tie.{uuid.uuid4().hex[:6]}@example.com", "Tie User"
        )
        await pg_admin_conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6);",
            tie_org1_id, "Tie Org 1", f"tie1-{uuid.uuid4().hex[:6]}",
            tie_org2_id, "Tie Org 2", f"tie2-{uuid.uuid4().hex[:6]}"
        )
        await pg_admin_conn.execute(
            """
            INSERT INTO organization_memberships (id, organization_id, user_id, role, created_at)
            VALUES ($1, $2, $3, 'VIEWER', $4::timestamptz), ($5, $6, $3, 'VIEWER', $4::timestamptz);
            """,
            uuid.uuid4(), tie_org1_id, tie_user_id, tie_time,
            uuid.uuid4(), tie_org2_id
        )
        try:
            async with pg_app_conn.transaction():
                await pg_app_conn.execute("SELECT set_config('app.current_user_id', $1, true);", str(tie_user_id))
                query = """
                SELECT o.id, o.name, m.role::text AS role, o.created_at
                FROM organization_memberships m
                JOIN organizations o ON o.id = m.organization_id
                WHERE m.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
                ORDER BY m.created_at ASC, o.id ASC;
                """
                res1 = await pg_app_conn.fetch(query)
                res2 = await pg_app_conn.fetch(query)
                assert len(res1) == 2
                assert [str(r["id"]) for r in res1] == [str(r["id"]) for r in res2]
                assert str(res1[0]["id"]) < str(res1[1]["id"])  # strictly sorted by o.id ASC
        finally:
            await pg_admin_conn.execute("DELETE FROM organizations WHERE id IN ($1, $2);", tie_org1_id, tie_org2_id)
            await pg_admin_conn.execute("DELETE FROM users WHERE id = $1;", tie_user_id)

    @pytest.mark.asyncio
    async def test_audit_logs_append_only_privilege_denials(self, pg_app_conn, pg_admin_conn):
        """
        Proves that UPDATE, DELETE, and TRUNCATE on audit_logs fail with table ACL denial
        (SQLSTATE 42501 permission denied), distinct from RLS WITH CHECK violations.
        """
        data = await _seed_tenants_and_users(pg_admin_conn)
        log_id = uuid.uuid4()

        # Seed an audit log via admin
        await pg_admin_conn.execute(
            """
            INSERT INTO audit_logs (id, organization_id, actor_user_id, entity_table, entity_id, action)
            VALUES ($1, $2, $3, 'documents', $4, 'DOCUMENT_INGESTED');
            """,
            log_id, uuid.UUID(data["org_a_id"]), uuid.UUID(data["user_a_id"]), uuid.UUID(data["doc_a_id"])
        )

        # Connect as onsigning_app under Org A context
        async with pg_app_conn.transaction():
            await pg_app_conn.execute(
                "SELECT set_config('app.current_organization_id', $1, true);", data["org_a_id"]
            )

            # SELECT succeeds
            rows = await pg_app_conn.fetch("SELECT id, action FROM audit_logs;")
            assert len(rows) == 1
            assert rows[0]["id"] == log_id

            # INSERT succeeds
            await pg_app_conn.execute(
                """
                INSERT INTO audit_logs (id, organization_id, actor_user_id, entity_table, entity_id, action)
                VALUES ($1, $2, $3, 'documents', $4, 'RULE_EVALUATED');
                """,
                uuid.uuid4(), uuid.UUID(data["org_a_id"]), uuid.UUID(data["user_a_id"]), uuid.UUID(data["doc_a_id"])
            )

            # UPDATE fails with Table ACL permission denied
            with pytest.raises(Exception) as exc_update:
                await pg_app_conn.execute(
                    "UPDATE audit_logs SET action = 'TAMPERED' WHERE id = $1;", log_id
                )
            assert getattr(exc_update.value, "sqlstate", None) == "42501"
            assert "permission denied for table audit_logs" in str(exc_update.value).lower()

        # In a new transaction: DELETE fails with Table ACL permission denied
        async with pg_app_conn.transaction():
            await pg_app_conn.execute(
                "SELECT set_config('app.current_organization_id', $1, true);", data["org_a_id"]
            )
            with pytest.raises(Exception) as exc_delete:
                await pg_app_conn.execute("DELETE FROM audit_logs WHERE id = $1;", log_id)
            assert getattr(exc_delete.value, "sqlstate", None) == "42501"
            assert "permission denied for table audit_logs" in str(exc_delete.value).lower()

        # In a new transaction: TRUNCATE fails with Table ACL permission denied
        async with pg_app_conn.transaction():
            with pytest.raises(Exception) as exc_truncate:
                await pg_app_conn.execute("TRUNCATE TABLE audit_logs;")
            assert getattr(exc_truncate.value, "sqlstate", None) == "42501"
            assert "permission denied for table audit_logs" in str(exc_truncate.value).lower()

    @pytest.mark.asyncio
    async def test_least_privilege_table_allowlist_acl(self, pg_app_conn):
        """
        Verifies that 0002_runtime_grants.sql enforces exact least-privilege table permissions:
        - All 16 standard tables grant SELECT, INSERT, UPDATE, DELETE to onsigning_app, but NOT TRUNCATE.
        - audit_logs grants SELECT and INSERT only; UPDATE, DELETE, and TRUNCATE are revoked.
        """
        standard_tables = [
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
        ]

        for table in standard_tables:
            can_select = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, $1, 'SELECT');", table)
            can_insert = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, $1, 'INSERT');", table)
            can_update = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, $1, 'UPDATE');", table)
            can_delete = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, $1, 'DELETE');", table)
            can_truncate = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, $1, 'TRUNCATE');", table)

            assert can_select is True, f"{table} must be readable by onsigning_app"
            assert can_insert is True, f"{table} must be insertable by onsigning_app"
            assert can_update is True, f"{table} must be updatable by onsigning_app"
            assert can_delete is True, f"{table} must be deletable by onsigning_app"
            assert can_truncate is False, f"{table} must not allow TRUNCATE by onsigning_app"

        # audit_logs table append-only verification
        al_select = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'audit_logs', 'SELECT');")
        al_insert = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'audit_logs', 'INSERT');")
        al_update = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'audit_logs', 'UPDATE');")
        al_delete = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'audit_logs', 'DELETE');")
        al_truncate = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'audit_logs', 'TRUNCATE');")

        assert al_select is True, "audit_logs must be readable by onsigning_app"
        assert al_insert is True, "audit_logs must be insertable by onsigning_app"
        assert al_update is False, "audit_logs must NOT be updatable by onsigning_app"
        assert al_delete is False, "audit_logs must NOT be deletable by onsigning_app"
        assert al_truncate is False, "audit_logs must NOT allow TRUNCATE by onsigning_app"

    @pytest.mark.asyncio
    async def test_runtime_grants_migration_fails_closed_when_app_role_missing(self, pg_admin_conn):
        """
        Proves that 0002_runtime_grants.sql executes a fail-closed pre-validation check
        and raises an explicit exception if the required onsigning_app role is absent.
        """
        migration_file = Path(__file__).resolve().parents[3] / "packages" / "database" / "migrations" / "0002_runtime_grants.sql"
        assert migration_file.is_file(), "0002_runtime_grants.sql must exist"

        migration_sql = migration_file.read_text(encoding="utf-8")
        # Substitute the role with a non-existent role name to test the fail-closed DO block
        missing_role_sql = migration_sql.replace("'onsigning_app'", "'onsigning_nonexistent_dummy_role'")

        with pytest.raises(Exception) as exc_info:
            await pg_admin_conn.execute(missing_role_sql)

        err_msg = str(exc_info.value)
        assert "Required database role" in err_msg
        assert "does not exist in cluster" in err_msg

    @pytest.mark.asyncio
    async def test_security_hardening_migration_fails_closed_when_prerequisites_missing(self, pg_admin_conn):
        """
        Proves that 0003_security_hardening.sql executes a fail-closed pre-validation check
        and raises an explicit exception if the required onsigning_app role or prerequisite tables are absent.
        """
        migration_file = Path(__file__).resolve().parents[3] / "packages" / "database" / "migrations" / "0003_security_hardening.sql"
        assert migration_file.is_file(), "0003_security_hardening.sql must exist"

        migration_sql = migration_file.read_text(encoding="utf-8")

        # 1. Test missing onsigning_app role
        missing_role_sql = migration_sql.replace("'onsigning_app'", "'onsigning_nonexistent_role'")
        with pytest.raises(Exception) as exc_role:
            await pg_admin_conn.execute(missing_role_sql)
        assert "Prerequisite role missing" in str(exc_role.value)

        # 2. Test missing prerequisite table
        missing_table_sql = migration_sql.replace("'audit_logs'", "'nonexistent_audit_logs'")
        with pytest.raises(Exception) as exc_table:
            await pg_admin_conn.execute(missing_table_sql)
        assert "Prerequisite schema missing" in str(exc_table.value)

    @pytest.mark.asyncio
    async def test_security_hardening_migration_idempotent_double_run(self, pg_admin_conn):
        """
        Proves that 0003_security_hardening.sql can be executed multiple times
        without syntax errors, duplicate-key errors, or catalog corruption.
        """
        migration_file = Path(__file__).resolve().parents[3] / "packages" / "database" / "migrations" / "0003_security_hardening.sql"
        migration_sql = migration_file.read_text(encoding="utf-8")

        # Execute migration 0003 a second time against the active test database
        await pg_admin_conn.execute(migration_sql)

    @pytest.mark.asyncio
    async def test_security_hardening_effective_table_privileges(self, pg_app_conn):
        """
        Verifies that after 0003_security_hardening.sql, onsigning_app has exact
        least-privilege permissions on both audit_logs and users:
        - audit_logs: SELECT and INSERT only; UPDATE, DELETE, TRUNCATE denied.
        - users: SELECT, INSERT, UPDATE, DELETE permitted; TRUNCATE denied.
        """
        # audit_logs table append-only verification
        al_select = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'public.audit_logs', 'SELECT');")
        al_insert = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'public.audit_logs', 'INSERT');")
        al_update = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'public.audit_logs', 'UPDATE');")
        al_delete = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'public.audit_logs', 'DELETE');")
        al_truncate = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'public.audit_logs', 'TRUNCATE');")

        assert al_select is True, "audit_logs must be readable by onsigning_app"
        assert al_insert is True, "audit_logs must be insertable by onsigning_app"
        assert al_update is False, "audit_logs must NOT be updatable by onsigning_app"
        assert al_delete is False, "audit_logs must NOT be deletable by onsigning_app"
        assert al_truncate is False, "audit_logs must NOT allow TRUNCATE by onsigning_app"

        # users table verification
        u_select = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'public.users', 'SELECT');")
        u_insert = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'public.users', 'INSERT');")
        u_update = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'public.users', 'UPDATE');")
        u_delete = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'public.users', 'DELETE');")
        u_truncate = await pg_app_conn.fetchval("SELECT has_table_privilege(current_user, 'public.users', 'TRUNCATE');")

        assert u_select is True, "users must be readable by onsigning_app"
        assert u_insert is True, "users must be insertable by onsigning_app"
        assert u_update is True, "users must be updatable by onsigning_app"
        assert u_delete is True, "users must be deletable by onsigning_app"
        assert u_truncate is False, "users must NOT allow TRUNCATE by onsigning_app"

    @pytest.mark.asyncio
    async def test_transaction_rollback_recovers_from_policy_violation(self, pg_app_conn, pg_admin_conn):
        """Proves that after an RLS violation puts transaction in ERROR state, ROLLBACK restores usability."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        # Trigger RLS violation inside transaction
        tr = pg_app_conn.transaction()
        await tr.start()
        try:
            await pg_app_conn.execute(
                "SELECT set_config('app.current_organization_id', $1, true);", data["org_a_id"]
            )
            # Cross-tenant insert violates RLS
            await pg_app_conn.execute(
                """
                INSERT INTO documents (
                    id, organization_id, title, original_filename, storage_key, sha256, mime_type, byte_size
                ) VALUES ($1, $2, 'Bad', 'b.pdf', 'k/b', 's_b', 'application/pdf', 10);
                """,
                uuid.uuid4(), uuid.UUID(data["org_b_id"])
            )
        except Exception:
            # Transaction is in aborted state
            await tr.rollback()

        # Connection should be completely healthy for a subsequent transaction
        async with pg_app_conn.transaction():
            await pg_app_conn.execute(
                "SELECT set_config('app.current_organization_id', $1, true);", data["org_a_id"]
            )
            count = await pg_app_conn.fetchval("SELECT count(*) FROM documents;")
            assert count == 1


# ==============================================================================
# Layer B: SQLAlchemy ORM & Application Tenant Context Integration Tests
# ==============================================================================

class TestSQLAlchemyTenantContextLive:
    """Verifies apps/api/core/database.py and auth.py against real PostgreSQL RLS."""

    @pytest.mark.asyncio
    async def test_context_continuity_after_commit_and_rollback(self, pg_db_session: AsyncSession, pg_admin_conn):
        """Proves Session.after_begin hook automatically reapplies set_config across commit() and rollback()."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        # Apply tenant context
        await set_tenant_context(pg_db_session, data["user_a_id"], data["org_a_id"])

        # Tx 1: Read documents
        stmt = select(Document)
        docs = (await pg_db_session.execute(stmt)).scalars().all()
        assert len(docs) == 1
        assert docs[0].title == "Doc A Master"

        # Commit Tx 1
        await pg_db_session.commit()

        # Tx 2 on same session: after_begin listener should have re-applied GUC
        docs_after_commit = (await pg_db_session.execute(stmt)).scalars().all()
        assert len(docs_after_commit) == 1
        assert str(docs_after_commit[0].id) == data["doc_a_id"]

        # Rollback Tx 2
        await pg_db_session.rollback()

        # Tx 3 on same session: after_begin listener should have re-applied GUC
        docs_after_rollback = (await pg_db_session.execute(stmt)).scalars().all()
        assert len(docs_after_rollback) == 1
        assert str(docs_after_rollback[0].id) == data["doc_a_id"]

    @pytest.mark.asyncio
    async def test_user_only_bootstrap_clears_stale_tenant_context(self, pg_db_session: AsyncSession, pg_admin_conn):
        """Proves that set_user_context() clears session.info and executes GUC reset to ''."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        # 1. Establish Org A context
        await set_tenant_context(pg_db_session, data["user_a_id"], data["org_a_id"])
        docs = (await pg_db_session.execute(select(Document))).scalars().all()
        assert len(docs) == 1

        # 2. Reset via set_user_context()
        await set_user_context(pg_db_session, data["user_b_id"])

        # 3. Assert session.info is cleansed
        assert "current_organization_id" not in pg_db_session.info
        assert pg_db_session.info.get("last_applied_org_id") != data["org_a_id"]

        # 4. Assert database documents are invisible (fails closed)
        docs_after_reset = (await pg_db_session.execute(select(Document))).scalars().all()
        assert len(docs_after_reset) == 0

        # Commit and assert subsequent transaction does NOT reapply Org A
        await pg_db_session.commit()
        docs_after_commit = (await pg_db_session.execute(select(Document))).scalars().all()
        assert len(docs_after_commit) == 0

    @pytest.mark.asyncio
    async def test_live_user_switching_scenario_a(self, pg_db_session: AsyncSession, pg_admin_conn):
        """
        Executes complete Scenario A from Task 5.11.4:
        1. Authorize User A for Org A via get_tenant_context() -> sees Doc A.
        2. Call set_user_context(User B) on same session.
        3. Before authorizing any org for User B, prove Org A's docs are invisible and org lookup fails closed.
        4. Attempt to select Org A (which User B does not belong to) via get_tenant_context() -> fails with AuthorizationError.
        5. Authorize valid Org B for User B via get_tenant_context() -> prove only Org B data is visible.
        """
        data = await _seed_tenants_and_users(pg_admin_conn)
        req = Request({"type": "http", "method": "GET", "path": "/api/v1/documents", "headers": []})
        user_a = User(id=data["user_a_id"], email="alice@acme.com", full_name="Alice Acme")
        user_b = User(id=data["user_b_id"], email="bob@beta.com", full_name="Bob Beta")

        # 1. Authorize User A for Org A via real get_tenant_context() dependency
        ctx_a = await get_tenant_context(request=req, user=user_a, x_organization_id=data["org_a_id"], db=pg_db_session)
        assert str(ctx_a.organization.id) == data["org_a_id"]
        assert ctx_a.role == "ADMIN"

        docs_a = (await pg_db_session.execute(select(Document))).scalars().all()
        assert len(docs_a) == 1
        assert str(docs_a[0].id) == data["doc_a_id"]

        # 2. Call set_user_context(User B) on SAME session (switching identity)
        await set_user_context(pg_db_session, data["user_b_id"])

        # 3. Before authorizing any org, assert fail-closed visibility
        docs_blind = (await pg_db_session.execute(select(Document))).scalars().all()
        assert len(docs_blind) == 0

        org_stmt = select(Organization).where(Organization.id == data["org_a_id"])
        org_res = (await pg_db_session.execute(org_stmt)).scalar_one_or_none()
        assert org_res is None, "Organization lookup must fail closed when tenant context is empty."

        # 4. Attempt to select unauthorized Org A as User B via get_tenant_context()
        with pytest.raises(AuthorizationError) as exc_info:
            await get_tenant_context(request=req, user=user_b, x_organization_id=data["org_a_id"], db=pg_db_session)
        assert "User is not authorized for the requested organization." in str(exc_info.value)

        # 5. Authorize valid Org B for User B via get_tenant_context()
        ctx_b = await get_tenant_context(request=req, user=user_b, x_organization_id=data["org_b_id"], db=pg_db_session)
        assert str(ctx_b.organization.id) == data["org_b_id"]
        assert ctx_b.role == "ADMIN"

        # Assert only Org B data is visible
        docs_b = (await pg_db_session.execute(select(Document))).scalars().all()
        assert len(docs_b) == 1
        assert str(docs_b[0].id) == data["doc_b_id"]
        assert docs_b[0].title == "Doc B Master"

    @pytest.mark.asyncio
    async def test_physical_pool_connection_reuse_leak_prevention(self, pg_sa_engine, pg_admin_conn):
        """Proves that when a physical connection is returned to pool, transaction-local GUCs do not leak."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        # Connection 1: set Org A context, verify, commit
        async with pg_sa_engine.connect() as conn1:
            await conn1.execute(
                text("SELECT set_config('app.current_organization_id', :org_id, true);"),
                {"org_id": data["org_a_id"]}
            )
            count = (await conn1.execute(text("SELECT count(*) FROM documents;"))).scalar()
            assert count == 1
            await conn1.commit()

        # Connection 2 checked out from pool: must NOT have Org A context
        async with pg_sa_engine.connect() as conn2:
            count = (await conn2.execute(text("SELECT count(*) FROM documents;"))).scalar()
            assert count == 0, "Pooled connection checkout must not inherit transaction-local GUC."

    @pytest.mark.asyncio
    async def test_system_worker_context_no_user_uuid(self, pg_db_session: AsyncSession, pg_admin_conn):
        """Proves system-worker context sets app.current_user_id = '' and writes actor_user_id = NULL and previous_state = SQL NULL in audit_logs."""
        data = await _seed_tenants_and_users(pg_admin_conn)

        # Background worker sets tenant context with user_id=None
        await set_tenant_context(pg_db_session, None, data["org_a_id"])

        # Documents for Org A are visible
        docs = (await pg_db_session.execute(select(Document))).scalars().all()
        assert len(docs) == 1

        # Write audit log as SYSTEM_WORKER
        log_id = str(uuid.uuid4())
        log = AuditLog(
            id=log_id,
            organization_id=data["org_a_id"],
            actor_user_id=None,
            actor_type="SYSTEM_WORKER",
            entity_table="documents",
            entity_id=data["doc_a_id"],
            action="BACKGROUND_AUDIT_COMPLETED",
            new_state=json.dumps({"status": "SUCCESS"})
        )
        pg_db_session.add(log)
        await pg_db_session.commit()

        # Query audit log via admin connection to verify actor_user_id is NULL and JSONB state persisted
        row = await pg_admin_conn.fetchrow(
            "SELECT actor_user_id, actor_type, action, previous_state, new_state FROM audit_logs WHERE id = $1;",
            uuid.UUID(log_id)
        )
        assert row["actor_user_id"] is None
        assert row["actor_type"] == "SYSTEM_WORKER"
        assert row["action"] == "BACKGROUND_AUDIT_COMPLETED"
        assert row["previous_state"] is None
        actual_new_state = json.loads(row["new_state"]) if isinstance(row["new_state"], str) else row["new_state"]
        assert actual_new_state == {"status": "SUCCESS"}
