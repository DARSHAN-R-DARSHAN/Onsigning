-- ============================================================================
-- ONSIGNING CANONICAL DATABASE MIGRATION: 0005_organization_discovery_rls.sql
-- PostgreSQL 16+ Single-Query Organization Discovery RLS Policy
-- Strictly conforms to ARCHITECTURE.md, SCHEMA.md, and AGENTS.md
-- ============================================================================
--
-- Security & Operational Architecture:
-- 1. Forward-Only & Non-Destructive:
--    Preserves 0001_mvp_schema, 0002_runtime_grants, 0003_security_hardening,
--    and 0004_postgrest_privilege_hardening intact.
-- 2. Preserves Existing Tenant Isolation:
--    Leaves tenant_isolation_organizations (FOR ALL) in place. Mutations
--    (INSERT, UPDATE, DELETE) remain strictly scoped to app.current_organization_id.
-- 3. Pre-Tenant User Discovery:
--    Adds a dedicated SELECT-only policy permitting authenticated users to read
--    metadata for workspaces where they hold a verified membership, without
--    requiring premature establishment of app.current_organization_id.
-- 4. Fail-Closed Prerequisite Validation:
--    Verifies tables organizations and organization_memberships exist and have
--    active Row-Level Security before applying the policy.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- SECTION 1: FAIL-CLOSED PREREQUISITE VALIDATION
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    -- Assert prerequisite tables exist in schema public
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'organizations'
    ) OR NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'organization_memberships'
    ) THEN
        RAISE EXCEPTION 'Prerequisite schema missing: tables "organizations" and "organization_memberships" must exist in schema "public". Apply prior migrations before 0005_organization_discovery_rls.sql.';
    END IF;

    -- Assert RLS is enabled and forced on organizations
    IF NOT EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'organizations'
          AND c.relrowsecurity = true AND c.relforcerowsecurity = true
    ) THEN
        RAISE EXCEPTION 'Security invariant violation: table "public.organizations" must have ENABLE and FORCE ROW LEVEL SECURITY.';
    END IF;

    -- Assert RLS is enabled and forced on organization_memberships
    IF NOT EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'organization_memberships'
          AND c.relrowsecurity = true AND c.relforcerowsecurity = true
    ) THEN
        RAISE EXCEPTION 'Security invariant violation: table "public.organization_memberships" must have ENABLE and FORCE ROW LEVEL SECURITY.';
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- SECTION 2: IDEMPOTENT POLICY ESTABLISHMENT
-- ----------------------------------------------------------------------------
-- Safely drop existing discovery policy if re-running
DROP POLICY IF EXISTS user_discovery_organizations ON public.organizations;

-- Establish dedicated SELECT-only discovery policy
CREATE POLICY user_discovery_organizations ON public.organizations
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1
            FROM public.organization_memberships AS m
            WHERE m.organization_id = organizations.id
              AND m.user_id =
                  NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

-- Record completion notice
DO $$
BEGIN
    RAISE NOTICE 'Migration 0005_organization_discovery_rls applied: dedicated SELECT policy established on public.organizations.';
END $$;
