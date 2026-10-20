# Onsigning — Canonical Data Model
**Version:** 1.0 · **Status:** Logical schema baseline; SQL migration must be validated before execution.

> This document defines the canonical entities, relationships, enums and integrity rules. It is not a claim that a migration has been run successfully. Keep actual DDL in `packages/database/migrations/0001_mvp_schema.sql` and test it against PostgreSQL 16+.

## 1. Database requirements
PostgreSQL 16+, `uuid-ossp`, `pg_trgm`, and `vector` (pgvector 0.8+). Use UUID primary keys, UTC `TIMESTAMPTZ` for event timestamps, `DATE` for contract calendar dates, `JSONB` only for flexible payloads, and integer minor units for money. Every tenant-owned table carries `organization_id`.

## 2. Enumerations
```text
user_role:
  OWNER | ADMIN | LEGAL_REVIEWER | VIEWER

doc_hierarchy_type:
  MSA | SOW | AMENDMENT | ADDENDUM_DPA | SLA | ORDER_FORM | INSURANCE_COI | OTHER

doc_processing_status:
  UPLOADED | PARSING_LAYOUT | EXTRACTING_FACTS | EVALUATING_PLAYBOOK |
  INDEXING_RETRIEVAL | READY | READY_WITH_WARNINGS | FAILED

doc_relationship_type:
  AMENDS | SUPPLEMENTS | SOW_UNDER_MSA | REPLACES | RELATED_EXHIBIT

relationship_confirmation_status:
  PROPOSED_BY_AI | CONFIRMED_BY_HUMAN | REJECTED_BY_HUMAN

clause_presence_status:
  EXPLICITLY_STATED | SILENT_OMISSION | AMBIGUOUS_OR_CROSS_REF | INSUFFICIENT_COVERAGE

citation_verification_status:
  EXACT_MATCH | NORMALIZED_MATCH | APPROXIMATE_NEEDS_REVIEW |
  UNVERIFIED_MISMATCH | NOT_APPLICABLE

supersession_status:
  NONE | PROPOSED_BY_AI | CONFIRMED_SUPERSEDED | REJECTED_BY_HUMAN

liability_cap_structure:
  FIXED_AMOUNT | FEE_MULTIPLE | GREATER_OF_FIXED_OR_MULTIPLE |
  UNCAPPED | SILENT | AMBIGUOUS | NOT_APPLICABLE

risk_severity:
  CRITICAL | HIGH | MEDIUM | LOW | COMPLIANT | UNDECIDED_NEEDS_REVIEW

review_status:
  AI_EVALUATED | NEEDS_HUMAN_REVIEW | HUMAN_CONFIRMED |
  HUMAN_OVERRIDDEN | DISMISSED_FALSE_POSITIVE

qa_answer_status:
  DIRECT_EVIDENCE | QUALIFIED_INFERENCE | CONFLICTING_EVIDENCE | INSUFFICIENT_EVIDENCE

obligation_trigger_type:
  FIXED_DATE | RELATIVE_TO_ANCHOR | RECURRING_SCHEDULE | EVENT_TRIGGERED

obligation_anchor_type:
  EXPLICIT_CALENDAR_DATE | EFFECTIVE_DATE | EXPIRATION_DATE | RENEWAL_DATE |
  EVENT_OCCURRENCE | UNKNOWN_OR_EXTERNAL_ANCHOR

obligation_state:
  PENDING_CONFIRMATION | ACTIVE | COMPLETED | WAIVED | SUPERSEDED

obligation_responsible_party:
  VENDOR | CUSTOMER | MUTUAL

notification_delivery_channel:
  EMAIL | WEBHOOK | SLACK

notification_delivery_status:
  PENDING | SENT | FAILED_RETRYABLE | FAILED_PERMANENT

audit_actor_type:
  USER | SYSTEM_WORKER | API_KEY

doc_error_category:
  PARSER_ERROR | UNREADABLE_SCANNED_PAGE | CORRUPTED_FILE | FILE_SIZE_EXCEEDED |
  PAGE_COUNT_EXCEEDED | TIMEOUT | LLM_EXTRACTION_ERROR | SCHEMA_VALIDATION_ERROR | SYSTEM_ERROR
```

## 3. Entities and columns

### `organizations`
- `id UUID PK`, `name`, `slug UNIQUE`, `default_currency CHAR(3)`, `timezone`, `created_at`.
- Workspace boundary for all tenant-owned data.

### `users`
- `id UUID PK`, `email UNIQUE`, `full_name`, `created_at`.
- Global identity. Access to a workspace comes only from membership.
- **Pending Decision (AUD-002):** The authentication provider architecture is undecided pending owner approval. Minimal schema implications:
  - *Option A (Native FastAPI Auth):* Add `password_hash VARCHAR(255) NOT NULL`, `email_verified BOOL DEFAULT FALSE`, `password_reset_token VARCHAR(255) NULL`.
  - *Option B (Managed IdP, e.g. Supabase Auth / Clerk / Auth0):* Add `external_auth_id VARCHAR(255) UNIQUE NOT NULL`, `auth_provider VARCHAR(50) NOT NULL`.
  No speculative credential columns are added to this baseline until the decision is formally approved.

### `organization_memberships`
- `id UUID PK`, `organization_id`, `user_id`, `role user_role`, `created_at`.
- `UNIQUE(organization_id, user_id)`; indexes on `(user_id, organization_id)`.
- Membership bootstrap must permit a user to discover their organizations without exposing other tenants.

### `vendors`
- `id UUID PK`, `organization_id`, `name`, optional normalized identifiers/contact metadata, timestamps.
- Unique composite key `(organization_id, id)` for tenant-safe foreign keys.

### `risk_playbooks`
- `id UUID PK`, `organization_id`, `name`, `version INT`, `rules JSONB`, `is_active BOOL`, `created_by`, timestamps.
- `UNIQUE(organization_id, id)` and unique `(organization_id, name, version)`.
- Audit runs reference the exact playbook version used; published versions are immutable.

### `contract_families`
- `id UUID PK`, `organization_id`, `vendor_id NULL`, `name`, `created_at`, `updated_at`.
- Composite FK to vendor; unique `(organization_id, id)`.

### `documents`
- `id UUID PK`, `organization_id`, `contract_family_id NULL`, `vendor_id NULL`, `title`, `original_filename`, `storage_key`, `sha256`, `mime_type`, `byte_size`, `hierarchy_type`, `processing_status`, `page_count`, `unreadable_pages INT[]`, `parser_warnings JSONB`, `error_category doc_error_category NULL`, `error_message TEXT NULL`, `error_detail JSONB NULL`, `failed_at TIMESTAMPTZ NULL`, `effective_date NULL`, `expiration_date NULL`, `renewal_date NULL`, `created_by`, timestamps.
- `UNIQUE(organization_id, id)` and tenant-composite FKs to family/vendor.
- Never store a public object URL as the canonical storage key.
- Safe error handling (AUD-004): `error_message` provides a user-safe explanation; `error_detail` contains structured technical diagnostics (e.g. parser exception code, timeout status). Neither field may ever store raw contract excerpts, secrets, or signed URLs.

### `document_relationships`
- `id UUID PK`, `organization_id`, `contract_family_id`, `source_document_id`, `target_document_id`, `relationship_type`, `confirmation_status`, `rationale`, `created_by_ai BOOL`, `confirmed_by NULL`, `confirmed_at NULL`, timestamps.
- Both documents and family use composite tenant-safe FKs.
- Source and target must differ. Add unique key to prevent duplicate directed relationships.

### `document_sections`
- `id UUID PK`, `organization_id`, `document_id`, `section_index`, `heading`, `page_start`, `page_end`, `text_content`, `created_at`.
- `UNIQUE(organization_id, id)`; index `(organization_id, document_id, section_index)`.

### `document_chunks`
- `id UUID PK`, `organization_id`, `document_id`, `section_id NULL`, `chunk_index`, `page_number`, `text_content`, `fts_vector TSVECTOR`, `embedding VECTOR(1536) NULL`, `created_at`.
- Feature 3 retrieval index. `embedding` may be null until indexed.
- GIN index on `fts_vector`; HNSW cosine index where embedding is not null.

### `extracted_clauses`
- `id UUID PK`, `organization_id`, `contract_family_id NULL`, `document_id`, `clause_category`, `presence_status clause_presence_status`, `section_reference NULL`, `page_number INT NULL`, `page_end INT NULL`, `verbatim_quote NULL`, `citation_status citation_verification_status`, `is_evidence_verified BOOL`, `factual_summary`, `cap_structure liability_cap_structure`, `cap_fixed_amount_cents NULL`, `cap_currency NULL`, `cap_fee_multiple NULL`, `uncapped_carveouts TEXT[]`, `governing_law_jurisdiction NULL`, `notice_period_days NULL`, `extraction_uncertainty_notes NULL`, `supersession_state supersession_status DEFAULT 'NONE'`, `overrides_section_reference NULL`, `created_at`.
- Composite FKs to document/family.
- `EXPLICITLY_STATED` requires quote and page. Non-explicit states do not carry a purported source quote.
- Page citation boundaries (AUD-009): `page_number` indicates the starting page. For quotes that span supported contiguous page breaks, `page_end` records the ending page (`page_end >= page_number`). For single-page quotes, `page_end` is NULL (or equal to `page_number`).
- `is_evidence_verified=true` only when `citation_status IN (EXACT_MATCH, NORMALIZED_MATCH)` and a quote exists.
- Index active clauses by organization, family, category and normalized cap fields; exclude confirmed superseded rows from active index.

### `audit_runs`
- `id UUID PK`, `organization_id`, `document_id`, `playbook_id`, `playbook_version`, `status`, `started_at`, `completed_at`, `summary JSONB`, `created_by`.
- Immutable snapshot of one evaluation against one playbook version.

### `risk_alerts`
- `id UUID PK`, `organization_id`, `audit_run_id`, `extracted_clause_id NULL`, `severity risk_severity`, `rule_id`, `title`, `explanation`, `evidence_payload JSONB`, `review_status review_status`, `reviewed_by NULL`, `reviewed_at NULL`, `review_note NULL`, timestamps.
- Findings reference the run and, where applicable, the extracted fact. Human decisions are audit-logged.
- Audit & FK preservation (AUD-015): Composite FK `FOREIGN KEY (organization_id, extracted_clause_id) REFERENCES extracted_clauses(organization_id, id) ON DELETE SET NULL (extracted_clause_id)`. `evidence_payload JSONB` stores an immutable snapshot of the verbatim quote, citation status, and page range at evaluation time, so human-reviewed findings remain fully auditable even if an underlying unconfirmed clause is re-extracted or removed.

### `qa_queries`
- `id UUID PK`, `organization_id`, `user_id NULL`, `contract_family_id NULL`, `question_text`, `answer_status`, `answer_markdown`, `citations_payload JSONB`, `uncertainty_or_conflict_notes NULL`, `created_at`.
- Feature 3. Store citations with source IDs and verified quote/page data.

### `obligations`
- `id UUID PK`, `organization_id`, `contract_family_id NULL`, `document_id`, `extracted_clause_id NULL`, `obligation_type`, `responsible_party obligation_responsible_party`, `trigger_type obligation_trigger_type`, `anchor_date_type obligation_anchor_type`, `anchor_date NULL`, `calculated_due_date NULL`, `recurrence_interval_months NULL`, `notice_period_days NULL`, `is_business_days BOOL`, `relative_window_description`, `verbatim_quote`, `page_number INT`, `page_end INT NULL`, `citation_status citation_verification_status`, `is_evidence_verified BOOL`, `alert_lead_days INT[] DEFAULT {90,60,30,7,0}`, `state obligation_state`, `assigned_user_id NULL`, `confirmed_by NULL`, `confirmed_at NULL`, timestamps.
- Standalone contracts (AUD-001): `contract_family_id` is explicitly nullable (`NULL`) so agreements in Milestone 1 and unfamilied contracts can define obligations.
- Multi-page citation support (AUD-009): `page_end INT NULL` represents the end page for multi-page quotes (`page_end >= page_number`).
- Nullable due date is intentional for event-driven or unknown anchors.
- Never fabricate a date. An unknown/external anchor must have null due date and remain `PENDING_CONFIRMATION`.
- Composite FKs must enforce tenant consistency. Deleting a family sets `contract_family_id` to NULL without affecting `organization_id`.

### `obligation_notifications`
- `id UUID PK`, `organization_id`, `obligation_id`, `lead_days_threshold`, `delivery_channel notification_delivery_channel`, `delivery_status notification_delivery_status`, `attempt_count`, `next_retry_at NULL`, `provider_message_id NULL`, `error_detail NULL`, `dispatched_at`, timestamps.
- Unique `(organization_id, obligation_id, lead_days_threshold, delivery_channel)` is the idempotency constraint. Retry by updating the existing record.

### `audit_logs`
- `id UUID PK`, `organization_id`, `actor_user_id NULL`, `actor_type audit_actor_type`, `entity_table`, `entity_id`, `action`, `previous_state JSONB NULL`, `new_state JSONB NULL`, `created_at`.
- Append-only for application role: grant `SELECT, INSERT`; revoke `UPDATE, DELETE, TRUNCATE`.

## 4. Mandatory database integrity rules
1. Every tenant-owned table has `organization_id NOT NULL` and `UNIQUE(organization_id, id)` where referenced by composite FK.
2. Tenant-owned child-to-parent relationships use composite foreign keys including `organization_id`.
3. Enable and `FORCE` RLS on all tenant-sensitive tables.
4. Runtime role `onsigning_app` must be `NOBYPASSRLS`; migration role is separate.
5. Use transaction-local `app.current_user_id` and `app.current_organization_id`; never rely on connection-persistent settings.
6. Citation checks enforce that verified means exact or strict normalized match; approximate similarity is never verification.
7. `audit_logs` is append-only for the runtime role.
8. `obligation_notifications` uniqueness prevents duplicate logical reminders.
9. A playbook version used by an audit is immutable and traceable.
10. All `ON DELETE SET NULL` actions on composite FKs must explicitly null only the nullable child FK column—not `organization_id` (e.g. `ON DELETE SET NULL (contract_family_id)` or `ON DELETE SET NULL (extracted_clause_id)`).
11. Preservation of reviewed audit findings (AUD-015): Re-running document parsing or AI extraction may delete or replace unreviewed AI-generated records (`review_status = AI_EVALUATED`), but must preserve any `extracted_clauses` linked to human-reviewed `risk_alerts` (`review_status IN (HUMAN_CONFIRMED, HUMAN_OVERRIDDEN, DISMISSED_FALSE_POSITIVE)`) or confirmed supersessions (`supersession_state = CONFIRMED_SUPERSEDED`). `risk_alerts.evidence_payload` retains an immutable copy of the source citation and page range so audit trails remain intact indefinitely.

## 5. Indexing plan
- `organization_memberships(user_id, organization_id)`.
- `documents(organization_id, contract_family_id)`.
- `document_sections(organization_id, document_id, section_index)`.
- `document_chunks(organization_id, document_id, page_number)`.
- `GIN(document_chunks.fts_vector)`.
- HNSW on `document_chunks.embedding vector_cosine_ops`, partial where embedding is not null.
- Active clause lookup on `(organization_id, contract_family_id, clause_category, cap_structure, cap_fixed_amount_cents)` with confirmed superseded clauses excluded.
- `risk_alerts(organization_id, audit_run_id, severity)`.
- `obligations(organization_id, state, calculated_due_date)` for active/pending dated obligations.
- `audit_logs(organization_id, entity_table, entity_id, created_at DESC)`.

## 6. Migration discipline
Use ordered, forward-only migrations. Validate the full DDL on a disposable PostgreSQL 16+ instance with extensions installed. Add automated migration tests for role creation privileges, RLS policies, membership/workspace bootstrap, composite FKs, checks, indexes and deletion behavior. Do not treat this logical model as proof that SQL has executed.
