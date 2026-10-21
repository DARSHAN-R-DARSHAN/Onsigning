-- ============================================================================
-- ONSIGNING CANONICAL DATABASE MIGRATION: 0001_mvp_schema.sql
-- PostgreSQL 16+ Forward-Only DDL Baseline
-- Strictly conforms to SCHEMA.md, ARCHITECTURE.md, and AI_DATA_SPEC.md
-- ============================================================================

-- 1. EXTENSIONS
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
-- vector extension is optional in Milestone 1, enabled when pgvector package is present
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS "vector";
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pgvector extension not installed in environment; skipping vector indexing for Feature 1.';
END $$;

-- 2. ENUMERATIONS
CREATE TYPE user_role AS ENUM (
    'OWNER',
    'ADMIN',
    'LEGAL_REVIEWER',
    'VIEWER'
);

CREATE TYPE doc_hierarchy_type AS ENUM (
    'MSA',
    'SOW',
    'AMENDMENT',
    'ADDENDUM_DPA',
    'SLA',
    'ORDER_FORM',
    'INSURANCE_COI',
    'OTHER'
);

CREATE TYPE doc_processing_status AS ENUM (
    'UPLOADED',
    'PARSING_LAYOUT',
    'EXTRACTING_FACTS',
    'EVALUATING_PLAYBOOK',
    'INDEXING_RETRIEVAL',
    'READY',
    'READY_WITH_WARNINGS',
    'FAILED'
);

CREATE TYPE doc_relationship_type AS ENUM (
    'AMENDS',
    'SUPPLEMENTS',
    'SOW_UNDER_MSA',
    'REPLACES',
    'RELATED_EXHIBIT'
);

CREATE TYPE relationship_confirmation_status AS ENUM (
    'PROPOSED_BY_AI',
    'CONFIRMED_BY_HUMAN',
    'REJECTED_BY_HUMAN'
);

CREATE TYPE clause_presence_status AS ENUM (
    'EXPLICITLY_STATED',
    'SILENT_OMISSION',
    'AMBIGUOUS_OR_CROSS_REF',
    'INSUFFICIENT_COVERAGE'
);

CREATE TYPE citation_verification_status AS ENUM (
    'EXACT_MATCH',
    'NORMALIZED_MATCH',
    'APPROXIMATE_NEEDS_REVIEW',
    'UNVERIFIED_MISMATCH',
    'NOT_APPLICABLE'
);

CREATE TYPE supersession_status AS ENUM (
    'NONE',
    'PROPOSED_BY_AI',
    'CONFIRMED_SUPERSEDED',
    'REJECTED_BY_HUMAN'
);

CREATE TYPE liability_cap_structure AS ENUM (
    'FIXED_AMOUNT',
    'FEE_MULTIPLE',
    'GREATER_OF_FIXED_OR_MULTIPLE',
    'UNCAPPED',
    'SILENT',
    'AMBIGUOUS',
    'NOT_APPLICABLE'
);

CREATE TYPE risk_severity AS ENUM (
    'CRITICAL',
    'HIGH',
    'MEDIUM',
    'LOW',
    'COMPLIANT',
    'UNDECIDED_NEEDS_REVIEW'
);

CREATE TYPE review_status AS ENUM (
    'AI_EVALUATED',
    'NEEDS_HUMAN_REVIEW',
    'HUMAN_CONFIRMED',
    'HUMAN_OVERRIDDEN',
    'DISMISSED_FALSE_POSITIVE'
);

CREATE TYPE qa_answer_status AS ENUM (
    'DIRECT_EVIDENCE',
    'QUALIFIED_INFERENCE',
    'CONFLICTING_EVIDENCE',
    'INSUFFICIENT_EVIDENCE'
);

CREATE TYPE obligation_trigger_type AS ENUM (
    'FIXED_DATE',
    'RELATIVE_TO_ANCHOR',
    'RECURRING_SCHEDULE',
    'EVENT_TRIGGERED'
);

CREATE TYPE obligation_anchor_type AS ENUM (
    'EXPLICIT_CALENDAR_DATE',
    'EFFECTIVE_DATE',
    'EXPIRATION_DATE',
    'RENEWAL_DATE',
    'EVENT_OCCURRENCE',
    'UNKNOWN_OR_EXTERNAL_ANCHOR'
);

CREATE TYPE obligation_state AS ENUM (
    'PENDING_CONFIRMATION',
    'ACTIVE',
    'COMPLETED',
    'WAIVED',
    'SUPERSEDED'
);

CREATE TYPE obligation_responsible_party AS ENUM (
    'VENDOR',
    'CUSTOMER',
    'MUTUAL'
);

CREATE TYPE notification_delivery_channel AS ENUM (
    'EMAIL',
    'WEBHOOK',
    'SLACK'
);

CREATE TYPE notification_delivery_status AS ENUM (
    'PENDING',
    'SENT',
    'FAILED_RETRYABLE',
    'FAILED_PERMANENT'
);

CREATE TYPE audit_actor_type AS ENUM (
    'USER',
    'SYSTEM_WORKER',
    'API_KEY'
);

CREATE TYPE doc_error_category AS ENUM (
    'PARSER_ERROR',
    'UNREADABLE_SCANNED_PAGE',
    'CORRUPTED_FILE',
    'FILE_SIZE_EXCEEDED',
    'PAGE_COUNT_EXCEEDED',
    'TIMEOUT',
    'LLM_EXTRACTION_ERROR',
    'SCHEMA_VALIDATION_ERROR',
    'SYSTEM_ERROR'
);

-- 3. CORE IDENTITY AND MULTI-TENANCY TABLES

-- Users: Maps 1:1 to Supabase Auth UUID (sub claim)
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) NOT NULL UNIQUE,
    full_name VARCHAR(255),
    external_auth_id VARCHAR(255) UNIQUE,
    auth_provider VARCHAR(50) NOT NULL DEFAULT 'supabase',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Organizations: Workspace boundary for all tenant-owned entities
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) NOT NULL UNIQUE,
    default_currency CHAR(3) NOT NULL DEFAULT 'USD',
    timezone VARCHAR(50) NOT NULL DEFAULT 'UTC',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Organization Memberships: Links users to workspaces with assigned RBAC roles
CREATE TABLE IF NOT EXISTS organization_memberships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role user_role NOT NULL DEFAULT 'VIEWER',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_organization_membership UNIQUE (organization_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_memberships_user ON organization_memberships(user_id, organization_id);

-- Vendors: Vendor metadata scoped by organization
CREATE TABLE IF NOT EXISTS vendors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    normalized_identifier VARCHAR(100),
    contact_email VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_vendors_tenant_key UNIQUE (organization_id, id)
);

-- Risk Playbooks: Versioned risk rules for deterministic contract evaluation
CREATE TABLE IF NOT EXISTS risk_playbooks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    version INT NOT NULL DEFAULT 1,
    description TEXT,
    rules JSONB NOT NULL,
    is_active BOOL NOT NULL DEFAULT TRUE,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_playbooks_tenant_key UNIQUE (organization_id, id),
    CONSTRAINT uq_playbook_version UNIQUE (organization_id, name, version)
);

-- Contract Families: Groups master agreements with related amendments and SOWs (Feature 2)
CREATE TABLE IF NOT EXISTS contract_families (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    vendor_id UUID,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_contract_families_tenant_key UNIQUE (organization_id, id),
    CONSTRAINT fk_families_vendor FOREIGN KEY (organization_id, vendor_id)
        REFERENCES vendors(organization_id, id) ON DELETE SET NULL
);

-- Documents: Uploaded contract documents with parser metadata and error tracking
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    contract_family_id UUID,
    vendor_id UUID,
    title VARCHAR(255) NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    storage_key VARCHAR(512) NOT NULL,
    sha256 CHAR(64) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    byte_size BIGINT NOT NULL,
    hierarchy_type doc_hierarchy_type NOT NULL DEFAULT 'OTHER',
    processing_status doc_processing_status NOT NULL DEFAULT 'UPLOADED',
    page_count INT,
    unreadable_pages INT[] DEFAULT '{}',
    parser_warnings JSONB DEFAULT '[]',
    error_category doc_error_category,
    error_message TEXT,
    error_detail JSONB,
    failed_at TIMESTAMPTZ,
    effective_date DATE,
    expiration_date DATE,
    renewal_date DATE,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_documents_tenant_key UNIQUE (organization_id, id),
    CONSTRAINT fk_documents_family FOREIGN KEY (organization_id, contract_family_id)
        REFERENCES contract_families(organization_id, id) ON DELETE SET NULL,
    CONSTRAINT fk_documents_vendor FOREIGN KEY (organization_id, vendor_id)
        REFERENCES vendors(organization_id, id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_tenant_family ON documents(organization_id, contract_family_id);
CREATE INDEX IF NOT EXISTS idx_documents_sha ON documents(organization_id, sha256);

-- Document Relationships: Proposals and confirmations between documents (Feature 2)
CREATE TABLE IF NOT EXISTS document_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    contract_family_id UUID NOT NULL,
    source_document_id UUID NOT NULL,
    target_document_id UUID NOT NULL,
    relationship_type doc_relationship_type NOT NULL,
    confirmation_status relationship_confirmation_status NOT NULL DEFAULT 'PROPOSED_BY_AI',
    rationale TEXT,
    created_by_ai BOOL NOT NULL DEFAULT TRUE,
    confirmed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_doc_rel_tenant_key UNIQUE (organization_id, id),
    CONSTRAINT fk_doc_rel_family FOREIGN KEY (organization_id, contract_family_id)
        REFERENCES contract_families(organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_doc_rel_source FOREIGN KEY (organization_id, source_document_id)
        REFERENCES documents(organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_doc_rel_target FOREIGN KEY (organization_id, target_document_id)
        REFERENCES documents(organization_id, id) ON DELETE CASCADE,
    CONSTRAINT chk_diff_docs CHECK (source_document_id <> target_document_id),
    CONSTRAINT uq_directed_relationship UNIQUE (organization_id, source_document_id, target_document_id, relationship_type)
);

-- Document Sections: Logical structural sections parsed from documents
CREATE TABLE IF NOT EXISTS document_sections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    document_id UUID NOT NULL,
    section_index INT NOT NULL,
    heading VARCHAR(255),
    page_start INT NOT NULL,
    page_end INT NOT NULL,
    text_content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_doc_sections_tenant_key UNIQUE (organization_id, id),
    CONSTRAINT fk_doc_sections_document FOREIGN KEY (organization_id, document_id)
        REFERENCES documents(organization_id, id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_doc_sections_lookup ON document_sections(organization_id, document_id, section_index);

-- Document Chunks: Text chunks with full-text search and embeddings (Feature 3)
CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    document_id UUID NOT NULL,
    section_id UUID,
    chunk_index INT NOT NULL,
    page_number INT NOT NULL,
    text_content TEXT NOT NULL,
    fts_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', text_content)) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_doc_chunks_tenant_key UNIQUE (organization_id, id),
    CONSTRAINT fk_doc_chunks_document FOREIGN KEY (organization_id, document_id)
        REFERENCES documents(organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_doc_chunks_section FOREIGN KEY (organization_id, section_id)
        REFERENCES document_sections(organization_id, id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_page ON document_chunks(organization_id, document_id, page_number);
CREATE INDEX IF NOT EXISTS idx_chunks_fts ON document_chunks USING GIN(fts_vector);

-- Extracted Clauses: Structured factual data extracted from documents with page ranges
CREATE TABLE IF NOT EXISTS extracted_clauses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    contract_family_id UUID,
    document_id UUID NOT NULL,
    clause_category VARCHAR(100) NOT NULL,
    presence_status clause_presence_status NOT NULL,
    section_reference VARCHAR(100),
    page_number INT,
    page_end INT,
    verbatim_quote TEXT,
    citation_status citation_verification_status NOT NULL DEFAULT 'NOT_APPLICABLE',
    is_evidence_verified BOOL NOT NULL DEFAULT FALSE,
    factual_summary TEXT NOT NULL,
    cap_structure liability_cap_structure NOT NULL DEFAULT 'NOT_APPLICABLE',
    cap_fixed_amount_cents BIGINT,
    cap_currency CHAR(3),
    cap_fee_multiple NUMERIC(6, 2),
    uncapped_carveouts TEXT[] DEFAULT '{}',
    governing_law_jurisdiction VARCHAR(100),
    notice_period_days INT,
    extraction_uncertainty_notes TEXT,
    supersession_state supersession_status NOT NULL DEFAULT 'NONE',
    overrides_section_reference VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_extracted_clauses_tenant_key UNIQUE (organization_id, id),
    CONSTRAINT fk_clauses_document FOREIGN KEY (organization_id, document_id)
        REFERENCES documents(organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_clauses_family FOREIGN KEY (organization_id, contract_family_id)
        REFERENCES contract_families(organization_id, id) ON DELETE SET NULL,
    CONSTRAINT chk_verified_match CHECK (
        (is_evidence_verified = FALSE) OR
        (is_evidence_verified = TRUE AND citation_status IN ('EXACT_MATCH', 'NORMALIZED_MATCH') AND verbatim_quote IS NOT NULL)
    ),
    CONSTRAINT chk_page_boundary CHECK (
        (page_end IS NULL) OR (page_number IS NOT NULL AND page_end >= page_number)
    )
);
CREATE INDEX IF NOT EXISTS idx_clauses_lookup ON extracted_clauses(organization_id, document_id, clause_category);

-- Audit Runs: Immutable evaluation snapshot against an exact playbook version
CREATE TABLE IF NOT EXISTS audit_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    document_id UUID NOT NULL,
    playbook_id UUID NOT NULL,
    playbook_version INT NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'COMPLETED',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    summary JSONB NOT NULL DEFAULT '{}',
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT uq_audit_runs_tenant_key UNIQUE (organization_id, id),
    CONSTRAINT fk_audit_runs_document FOREIGN KEY (organization_id, document_id)
        REFERENCES documents(organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_audit_runs_playbook FOREIGN KEY (organization_id, playbook_id)
        REFERENCES risk_playbooks(organization_id, id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_audit_runs_doc ON audit_runs(organization_id, document_id, started_at DESC);

-- Risk Alerts: Individual findings resulting from evaluating playbook rules on clauses
CREATE TABLE IF NOT EXISTS risk_alerts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    audit_run_id UUID NOT NULL,
    extracted_clause_id UUID,
    severity risk_severity NOT NULL,
    rule_id VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    explanation TEXT NOT NULL,
    evidence_payload JSONB NOT NULL,
    review_status review_status NOT NULL DEFAULT 'AI_EVALUATED',
    reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_risk_alerts_tenant_key UNIQUE (organization_id, id),
    CONSTRAINT fk_risk_alerts_run FOREIGN KEY (organization_id, audit_run_id)
        REFERENCES audit_runs(organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_risk_alerts_clause FOREIGN KEY (organization_id, extracted_clause_id)
        REFERENCES extracted_clauses(organization_id, id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_run_severity ON risk_alerts(organization_id, audit_run_id, severity);

-- QA Queries: Cross-contract Q&A history and cited evidence (Feature 3)
CREATE TABLE IF NOT EXISTS qa_queries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    contract_family_id UUID,
    question_text TEXT NOT NULL,
    answer_status qa_answer_status NOT NULL,
    answer_markdown TEXT NOT NULL,
    citations_payload JSONB NOT NULL DEFAULT '[]',
    uncertainty_or_conflict_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_qa_queries_tenant_key UNIQUE (organization_id, id),
    CONSTRAINT fk_qa_family FOREIGN KEY (organization_id, contract_family_id)
        REFERENCES contract_families(organization_id, id) ON DELETE SET NULL
);

-- Obligations: Contractual commitments, dates, assignments, and states (Feature 4)
CREATE TABLE IF NOT EXISTS obligations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    contract_family_id UUID,
    document_id UUID NOT NULL,
    extracted_clause_id UUID,
    obligation_type VARCHAR(100) NOT NULL,
    responsible_party obligation_responsible_party NOT NULL,
    trigger_type obligation_trigger_type NOT NULL,
    anchor_date_type obligation_anchor_type NOT NULL,
    anchor_date DATE,
    calculated_due_date DATE,
    recurrence_interval_months INT,
    notice_period_days INT,
    is_business_days BOOL NOT NULL DEFAULT FALSE,
    relative_window_description TEXT NOT NULL,
    verbatim_quote TEXT NOT NULL,
    page_number INT NOT NULL,
    page_end INT,
    citation_status citation_verification_status NOT NULL,
    is_evidence_verified BOOL NOT NULL DEFAULT FALSE,
    alert_lead_days INT[] NOT NULL DEFAULT '{90,60,30,7,0}',
    state obligation_state NOT NULL DEFAULT 'PENDING_CONFIRMATION',
    assigned_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    confirmed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_obligations_tenant_key UNIQUE (organization_id, id),
    CONSTRAINT fk_obligations_document FOREIGN KEY (organization_id, document_id)
        REFERENCES documents(organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_obligations_family FOREIGN KEY (organization_id, contract_family_id)
        REFERENCES contract_families(organization_id, id) ON DELETE SET NULL,
    CONSTRAINT fk_obligations_clause FOREIGN KEY (organization_id, extracted_clause_id)
        REFERENCES extracted_clauses(organization_id, id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_obligations_lookup ON obligations(organization_id, state, calculated_due_date);

-- Obligation Notifications: Automated reminder history with idempotency keys
CREATE TABLE IF NOT EXISTS obligation_notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    obligation_id UUID NOT NULL,
    lead_days_threshold INT NOT NULL,
    delivery_channel notification_delivery_channel NOT NULL,
    delivery_status notification_delivery_status NOT NULL DEFAULT 'PENDING',
    attempt_count INT NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    provider_message_id VARCHAR(255),
    error_detail TEXT,
    dispatched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ob_notifications_tenant_key UNIQUE (organization_id, id),
    CONSTRAINT fk_notifications_obligation FOREIGN KEY (organization_id, obligation_id)
        REFERENCES obligations(organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_notification_idempotency UNIQUE (organization_id, obligation_id, lead_days_threshold, delivery_channel)
);

-- Audit Logs: Append-only system security and entity mutation audit trail
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    actor_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    actor_type audit_actor_type NOT NULL DEFAULT 'USER',
    entity_table VARCHAR(100) NOT NULL,
    entity_id UUID NOT NULL,
    action VARCHAR(50) NOT NULL,
    previous_state JSONB,
    new_state JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_timeline ON audit_logs(organization_id, entity_table, entity_id, created_at DESC);

-- 4. ROW-LEVEL SECURITY (RLS) POLICIES

-- Enable and FORCE RLS on all tenant-owned tables
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE organizations FORCE ROW LEVEL SECURITY;

ALTER TABLE organization_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE organization_memberships FORCE ROW LEVEL SECURITY;

ALTER TABLE vendors ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendors FORCE ROW LEVEL SECURITY;

ALTER TABLE risk_playbooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_playbooks FORCE ROW LEVEL SECURITY;

ALTER TABLE contract_families ENABLE ROW LEVEL SECURITY;
ALTER TABLE contract_families FORCE ROW LEVEL SECURITY;

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;

ALTER TABLE document_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_relationships FORCE ROW LEVEL SECURITY;

ALTER TABLE document_sections ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_sections FORCE ROW LEVEL SECURITY;

ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_chunks FORCE ROW LEVEL SECURITY;

ALTER TABLE extracted_clauses ENABLE ROW LEVEL SECURITY;
ALTER TABLE extracted_clauses FORCE ROW LEVEL SECURITY;

ALTER TABLE audit_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_runs FORCE ROW LEVEL SECURITY;

ALTER TABLE risk_alerts ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_alerts FORCE ROW LEVEL SECURITY;

ALTER TABLE qa_queries ENABLE ROW LEVEL SECURITY;
ALTER TABLE qa_queries FORCE ROW LEVEL SECURITY;

ALTER TABLE obligations ENABLE ROW LEVEL SECURITY;
ALTER TABLE obligations FORCE ROW LEVEL SECURITY;

ALTER TABLE obligation_notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE obligation_notifications FORCE ROW LEVEL SECURITY;

ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY;

-- Base Tenant Policy Macro: organization_id matches app.current_organization_id
CREATE POLICY tenant_isolation_organizations ON organizations
    FOR ALL USING (id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_memberships ON organization_memberships
    FOR ALL USING (
        organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid OR
        user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
    );

CREATE POLICY tenant_isolation_vendors ON vendors
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_playbooks ON risk_playbooks
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_families ON contract_families
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_documents ON documents
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_relationships ON document_relationships
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_sections ON document_sections
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_chunks ON document_chunks
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_clauses ON extracted_clauses
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_audit_runs ON audit_runs
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_alerts ON risk_alerts
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_qa ON qa_queries
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_obligations ON obligations
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_notifications ON obligation_notifications
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

CREATE POLICY tenant_isolation_audit_logs ON audit_logs
    FOR ALL USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);

-- Append-only constraint on audit_logs: Only SELECT and INSERT permitted
REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM PUBLIC;
