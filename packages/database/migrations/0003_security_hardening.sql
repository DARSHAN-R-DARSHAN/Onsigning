-- ============================================================================
-- ONSIGNING CANONICAL DATABASE MIGRATION: 0003_security_hardening.sql
-- PostgreSQL 16+ Security Hardening & PostgREST Default Privilege Remediation
-- Strictly conforms to ARCHITECTURE.md, SCHEMA.md, and AGENTS.md
-- ============================================================================
--
-- Security & Operational Architecture:
-- 1. Forward-Only Migration:
--    Preserves 0001_mvp_schema.sql and 0002_runtime_grants.sql intact.
-- 2. Zero-Trust PostgREST Boundary:
--    - public.audit_logs: Revokes ALL from PUBLIC, anon, authenticated, service_role.
--    - public.users: Revokes ALL from PUBLIC, anon, authenticated, service_role.
-- 3. Restricts Default Privileges:
--    Prevents future tables and sequences created by postgres from inheriting
--    PostgREST grants. Leaves supabase_admin and function defaults untouched.
-- 4. Environment-Aware Fail-Closed Execution:
--    - In Supabase: Executes under role postgres; fails closed if unauthorized.
--    - In Standalone CI: Skips PostgREST hardening when roles are absent with
--      an explicit descriptive notice, while enforcing runtime role grants.
-- 5. Strict Idempotency:
--    Safe to execute repeatedly without schema mutations, locks, or data loss.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- SECTION 1: FAIL-CLOSED PREREQUISITE VALIDATION
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    -- Assert runtime application role exists
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = 'onsigning_app'
    ) THEN
        RAISE EXCEPTION 'Prerequisite role missing: "onsigning_app" does not exist in cluster. Apply bootstrap provisioning before 0003_security_hardening.sql.';
    END IF;

    -- Assert prerequisite tables from 0001_mvp_schema exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'audit_logs'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'users'
    ) THEN
        RAISE EXCEPTION 'Prerequisite schema missing: tables "audit_logs" and "users" must exist in schema "public". Apply 0001_mvp_schema.sql before 0003_security_hardening.sql.';
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- SECTION 2: UNIVERSAL PUBLIC ACCESS REVOCATION
-- ----------------------------------------------------------------------------
-- Strip any residual implicit public access on sensitive application tables.
REVOKE ALL PRIVILEGES ON TABLE public.audit_logs FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE public.users FROM PUBLIC;

-- ----------------------------------------------------------------------------
-- SECTION 3: RUNTIME APPLICATION LEAST-PRIVILEGE REASSERTION
-- ----------------------------------------------------------------------------
-- audit_logs: strictly append-only (SELECT, INSERT only)
GRANT SELECT, INSERT ON TABLE public.audit_logs TO onsigning_app;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_logs FROM onsigning_app;

-- users: application CRUD permitted; TRUNCATE withheld
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.users TO onsigning_app;
REVOKE TRUNCATE ON TABLE public.users FROM onsigning_app;

-- ----------------------------------------------------------------------------
-- SECTION 4: ENVIRONMENT-AWARE POSTGREST & DEFAULT PRIVILEGE HARDENING
-- ----------------------------------------------------------------------------
DO $$
DECLARE
    is_supabase_env boolean := false;
    is_authorized boolean := false;
    r text;
BEGIN
    -- 1. Detect if Supabase PostgREST roles exist in the cluster
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')) THEN
        is_supabase_env := true;
    END IF;

    -- 2. If running in a Supabase environment, assert execution authority
    IF is_supabase_env THEN
        -- Assert caller is postgres or has superuser privileges
        IF current_user = 'postgres' OR (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) THEN
            is_authorized := true;
        END IF;

        IF NOT is_authorized THEN
            RAISE EXCEPTION 'Supabase environment detected (PostgREST roles exist), but current_user "%" lacks authority to alter default privileges for role "postgres". Run migration as postgres or superuser.', current_user;
        END IF;

        -- 3. Revoke direct table privileges on public.audit_logs (anon, authenticated, service_role)
        FOR r IN SELECT unnest(ARRAY['anon', 'authenticated', 'service_role'])
        LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.audit_logs FROM %I', r);
            END IF;
        END LOOP;

        -- 4. Revoke direct table privileges on public.users (anon, authenticated, service_role)
        FOR r IN SELECT unnest(ARRAY['anon', 'authenticated', 'service_role'])
        LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.users FROM %I', r);
            END IF;
        END LOOP;

        -- 5. Restrict default privileges for role postgres in schema public
        --    Applies strictly to future tables and sequences created by postgres.
        --    Preserves supabase_admin and function defaults.
        FOR r IN SELECT unnest(ARRAY['anon', 'authenticated', 'service_role'])
        LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
                EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON TABLES FROM %I', r);
                EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public REVOKE ALL ON SEQUENCES FROM %I', r);
            END IF;
        END LOOP;

        RAISE NOTICE 'Supabase PostgREST table ACLs and default privileges for role postgres successfully hardened.';
    ELSE
        -- In Standalone CI: PostgREST roles do not exist.
        RAISE NOTICE 'Standalone/CI environment detected (migration user: "%"): PostgREST roles (anon, authenticated, service_role) absent from cluster. PostgREST table ACL and default privilege hardening skipped in this environment.', current_user;
    END IF;
END $$;
