-- ============================================================================
-- ONSIGNING CANONICAL DATABASE MIGRATION: 0002_runtime_grants.sql
-- PostgreSQL 16+ Least-Privilege Runtime Grants Baseline
-- Strictly conforms to ARCHITECTURE.md, SCHEMA.md, and AGENTS.md
-- ============================================================================
--
-- Security & Operational Architecture:
-- 1. Execution Identity:
--    Must be executed by onsigning_admin (the table owner and schema migration role).
--    Never executed by superuser or onsigning_app.
-- 2. Role Separation:
--    - Cluster roles (onsigning_admin, onsigning_app) MUST be provisioned beforehand
--      by an authorized DBA/infrastructure process outside of application migrations.
--    - This migration does NOT create, alter, or drop cluster roles.
-- 3. Fail-Closed Role Validation:
--    If onsigning_app is missing from pg_roles, this migration fails immediately
--    with a descriptive exception, preventing partially-configured deployments.
-- 4. Least Privilege Application Allowlist:
--    - Schema public: USAGE granted; CREATE revoked (no DDL powers).
--    - 16 Standard Tables: SELECT, INSERT, UPDATE, DELETE granted (TRUNCATE withheld).
--    - audit_logs Table: SELECT, INSERT granted only. UPDATE, DELETE, TRUNCATE revoked
--      from both onsigning_app and PUBLIC to ensure append-only tamper resistance.
--    - Sequences: USAGE, SELECT granted.
--    - Default Privileges: Future tables created by onsigning_admin grant DML to onsigning_app.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- SECTION 1: FAIL-CLOSED PREREQUISITE VALIDATION
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    -- Assert that onsigning_app role exists in the cluster
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'onsigning_app'
    ) THEN
        RAISE EXCEPTION 'Required database role "onsigning_app" does not exist in cluster. Provision roles via cluster bootstrap before applying 0002_runtime_grants.sql.';
    END IF;

    -- Assert that 0001_mvp_schema has been applied (users table must exist)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'users'
    ) THEN
        RAISE EXCEPTION 'Prerequisite schema missing: table "users" does not exist in schema "public". Apply 0001_mvp_schema.sql before 0002_runtime_grants.sql.';
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- SECTION 2: SCHEMA PRIVILEGES
-- ----------------------------------------------------------------------------
-- Grant lookup and type resolution on schema public, but prevent object creation
GRANT USAGE ON SCHEMA public TO onsigning_app;
REVOKE CREATE ON SCHEMA public FROM onsigning_app;

-- ----------------------------------------------------------------------------
-- SECTION 3: APPLICATION TABLE ALLOWLIST (16 STANDARD TABLES)
-- ----------------------------------------------------------------------------
-- Standard business entity tables allow DML operations, subject to Row-Level Security
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    users,
    organizations,
    organization_memberships,
    vendors,
    risk_playbooks,
    contract_families,
    documents,
    document_relationships,
    document_sections,
    document_chunks,
    extracted_clauses,
    audit_runs,
    risk_alerts,
    qa_queries,
    obligations,
    obligation_notifications
TO onsigning_app;

-- ----------------------------------------------------------------------------
-- SECTION 4: APPEND-ONLY AUDIT TRAIL (audit_logs TABLE)
-- ----------------------------------------------------------------------------
-- Application runtime may read and record audit events, but is strictly prohibited
-- from mutating, overwriting, or purging historical records.
GRANT SELECT, INSERT ON TABLE audit_logs TO onsigning_app;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_logs FROM onsigning_app;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_logs FROM PUBLIC;

-- ----------------------------------------------------------------------------
-- SECTION 5: SEQUENCE PRIVILEGES
-- ----------------------------------------------------------------------------
-- Allow runtime role to read and advance sequence values in schema public
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO onsigning_app;

-- ----------------------------------------------------------------------------
-- SECTION 6: DEFAULT PRIVILEGES FOR FUTURE MIGRATIONS
-- ----------------------------------------------------------------------------
-- Ensures tables and sequences subsequently created by onsigning_admin automatically
-- inherit least-privilege DML grants for onsigning_app without ad-hoc manual grants.
ALTER DEFAULT PRIVILEGES FOR ROLE onsigning_admin IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO onsigning_app;

ALTER DEFAULT PRIVILEGES FOR ROLE onsigning_admin IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO onsigning_app;
