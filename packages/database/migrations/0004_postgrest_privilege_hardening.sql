-- ============================================================================
-- ONSIGNING CANONICAL DATABASE MIGRATION: 0004_postgrest_privilege_hardening.sql
-- PostgreSQL 16+ Zero-Trust Privilege Hardening & Supabase Model Reconciliation
-- Strictly conforms to ARCHITECTURE.md, SCHEMA.md, and AGENTS.md
-- ============================================================================
--
-- Security & Operational Architecture:
-- 1. Forward-Only & Transactional:
--    Preserves 0001, 0002, and 0003 intact. Idempotent and forward-only.
-- 2. Zero-Trust PostgREST Boundary Across All 17 Application Tables:
--    - Revokes ALL direct privileges from PUBLIC, anon, authenticated, and service_role
--      on all 16 tenant tables, users, and audit_logs.
--    - Closes the service_role BYPASSRLS vulnerability: because service_role bypasses
--      RLS, direct table grants previously exposed all tenant data across tenants.
-- 3. Runtime Least-Privilege Reassertion:
--    - onsigning_app: Granted SELECT, INSERT, UPDATE, DELETE on 16 tables (TRUNCATE withheld).
--    - public.audit_logs: Strictly append-only (SELECT, INSERT only; UPDATE, DELETE, TRUNCATE withheld).
-- 4. Supabase Role Model Reconciliation & Global Default Privilege Hardening:
--    - Hardens default privileges for role postgres at BOTH global scope and schema public scope:
--      * Revokes ALL default table and sequence privileges globally from anon, authenticated, service_role, and PUBLIC.
--      * Revokes ALL default table and sequence privileges in schema public from anon, authenticated, service_role, and PUBLIC.
--      * Grants SELECT, INSERT, UPDATE, DELETE on future tables in schema public to onsigning_app.
--      * Grants USAGE, SELECT on future sequences in schema public to onsigning_app.
-- 5. Safe Execution Context:
--    - Executes under role postgres or superuser in Supabase.
--    - In standalone CI where PostgREST roles do not exist, safely skips PostgREST
--      revocations with a descriptive notice while enforcing onsigning_app grants.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- SECTION 1: FAIL-CLOSED PREREQUISITE VALIDATION
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    expected_tables text[] := ARRAY[
        'users',
        'organizations',
        'organization_memberships',
        'vendors',
        'risk_playbooks',
        'contract_families',
        'documents',
        'document_relationships',
        'document_sections',
        'document_chunks',
        'extracted_clauses',
        'audit_runs',
        'risk_alerts',
        'qa_queries',
        'obligations',
        'obligation_notifications',
        'audit_logs'
    ];
    t text;
BEGIN
    -- Assert runtime application role exists
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'onsigning_app'
    ) THEN
        RAISE EXCEPTION 'Prerequisite role missing: "onsigning_app" does not exist in cluster. Provision roles via bootstrap before applying 0004_postgrest_privilege_hardening.sql.';
    END IF;

    -- Assert all 17 application tables exist in schema public
    FOREACH t IN ARRAY expected_tables
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = t AND table_type = 'BASE TABLE'
        ) THEN
            RAISE EXCEPTION 'Prerequisite schema missing: table "public.%" does not exist. Apply prior migrations before 0004_postgrest_privilege_hardening.sql.', t;
        END IF;
    END LOOP;
END $$;

-- ----------------------------------------------------------------------------
-- SECTION 2: UNIVERSAL PUBLIC ACCESS REVOCATION
-- ----------------------------------------------------------------------------
-- Strip any residual implicit public access on all 17 application tables
REVOKE ALL PRIVILEGES ON TABLE
    public.users,
    public.organizations,
    public.organization_memberships,
    public.vendors,
    public.risk_playbooks,
    public.contract_families,
    public.documents,
    public.document_relationships,
    public.document_sections,
    public.document_chunks,
    public.extracted_clauses,
    public.audit_runs,
    public.risk_alerts,
    public.qa_queries,
    public.obligations,
    public.obligation_notifications,
    public.audit_logs
FROM PUBLIC;

-- ----------------------------------------------------------------------------
-- SECTION 3: RUNTIME APPLICATION LEAST-PRIVILEGE REASSERTION
-- ----------------------------------------------------------------------------
-- Schema public: Lookup and type resolution permitted; creation prohibited
GRANT USAGE ON SCHEMA public TO onsigning_app;
REVOKE CREATE ON SCHEMA public FROM onsigning_app;

-- 16 Standard application tables: CRUD permitted; TRUNCATE withheld
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE
    public.users,
    public.organizations,
    public.organization_memberships,
    public.vendors,
    public.risk_playbooks,
    public.contract_families,
    public.documents,
    public.document_relationships,
    public.document_sections,
    public.document_chunks,
    public.extracted_clauses,
    public.audit_runs,
    public.risk_alerts,
    public.qa_queries,
    public.obligations,
    public.obligation_notifications
TO onsigning_app;

REVOKE TRUNCATE ON TABLE
    public.users,
    public.organizations,
    public.organization_memberships,
    public.vendors,
    public.risk_playbooks,
    public.contract_families,
    public.documents,
    public.document_relationships,
    public.document_sections,
    public.document_chunks,
    public.extracted_clauses,
    public.audit_runs,
    public.risk_alerts,
    public.qa_queries,
    public.obligations,
    public.obligation_notifications
FROM onsigning_app;

-- Append-only audit trail: strictly SELECT and INSERT only
GRANT SELECT, INSERT ON TABLE public.audit_logs TO onsigning_app;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_logs FROM onsigning_app;

-- Sequences: Allow runtime role to read and advance sequences in schema public
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO onsigning_app;

-- ----------------------------------------------------------------------------
-- SECTION 4: POSTGREST ZERO-TRUST BOUNDARY & DEFAULT PRIVILEGES
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    is_supabase_env boolean := false;
    is_authorized boolean := false;
    postgrest_roles text[] := ARRAY['anon', 'authenticated', 'service_role'];
    r text;
    t text;
    app_tables text[] := ARRAY[
        'users',
        'organizations',
        'organization_memberships',
        'vendors',
        'risk_playbooks',
        'contract_families',
        'documents',
        'document_relationships',
        'document_sections',
        'document_chunks',
        'extracted_clauses',
        'audit_runs',
        'risk_alerts',
        'qa_queries',
        'obligations',
        'obligation_notifications',
        'audit_logs'
    ];
BEGIN
    -- 1. Detect if Supabase PostgREST roles exist in cluster
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ANY(postgrest_roles)) THEN
        is_supabase_env := true;
    END IF;

    -- 2. Verify caller execution authority if in Supabase environment
    IF is_supabase_env THEN
        IF current_user = 'postgres' OR (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
            is_authorized := true;
        END IF;

        IF NOT is_authorized THEN
            RAISE EXCEPTION 'Supabase environment detected (PostgREST roles exist), but current_user "%" lacks authority to alter default privileges for role "postgres". Run migration as postgres or superuser.', current_user;
        END IF;

        -- 3. Revoke all direct privileges on all 17 application tables from PostgREST roles
        FOREACH r IN ARRAY postgrest_roles
        LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                FOREACH t IN ARRAY app_tables
                LOOP
                    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.%I FROM %I', t, r);
                END LOOP;
            END IF;
        END LOOP;

        -- 4. Restrict default privileges for role postgres:
        --    Revoke table and sequence defaults from PostgREST roles at BOTH global scope and schema public scope
        --    Neutralizes PostgreSQL's additive default-privilege behavior between global and schema-specific scopes
        FOREACH r IN ARRAY postgrest_roles
        LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                -- Global scope (defaclnamespace = 0): prevents additive inheritance across all schemas
                EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE ALL ON TABLES FROM %I', r);
                EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE ALL ON SEQUENCES FROM %I', r);

                -- Schema public scope (defaclnamespace = public): defense-in-depth in application schema
                EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM %I', r);
                EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', r);
            END IF;
        END LOOP;

        -- 5. Revoke default privileges for role postgres from PUBLIC (global and schema public)
        --    PUBLIC is a PostgreSQL pseudo-role, not an ordinary entry in pg_roles
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE ALL ON TABLES FROM PUBLIC';
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE ALL ON SEQUENCES FROM PUBLIC';
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC';
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC';

        -- 6. Establish default privileges for role postgres in schema public for onsigning_app:
        --    Ensures future tables and sequences created by postgres automatically grant DML to onsigning_app
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO onsigning_app';
        EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO onsigning_app';

        RAISE NOTICE 'Supabase PostgREST table ACLs and default privileges for role postgres successfully hardened across all 17 tables (global and schema public).';
    ELSE
        -- In Standalone CI: PostgREST roles do not exist.
        -- If caller is postgres or superuser, revoke default privileges from PUBLIC and establish onsigning_app defaults
        IF current_user = 'postgres' OR (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
            EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE ALL ON TABLES FROM PUBLIC';
            EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres REVOKE ALL ON SEQUENCES FROM PUBLIC';
            EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC';
            EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC';
            EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO onsigning_app';
            EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO onsigning_app';

            RAISE NOTICE 'Standalone/CI environment detected (migration user: "%"): PostgREST roles absent from cluster. PostgREST table ACL revocations skipped; runtime default grants established for role postgres.', current_user;
        ELSE
            RAISE NOTICE 'Standalone/CI environment detected (migration user: "%"): PostgREST roles absent from cluster. PostgREST table ACL revocations and postgres default grants skipped (insufficient authority for role postgres).', current_user;
        END IF;
    END IF;
END $$;
