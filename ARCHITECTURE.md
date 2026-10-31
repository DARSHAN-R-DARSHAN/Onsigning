# Onsigning — System Architecture
**Version:** 1.1 · **Status:** Hardened Baseline (Migrations 0001–0004 Verified) · **Updated:** 2026-10-31

## 1. Architectural principles
1. Evidence first: extracted facts, evidence validation, interpretation and human decisions are separate stages.
2. Tenant isolation is enforced in both application authorization and PostgreSQL RLS.
3. LLM output is untrusted input; schema-valid output is not necessarily true.
4. No silent amendment supersession and no fabricated dates.
5. Long-running work is asynchronous and idempotent.
6. Build all four features in the design, but implement in sequence: audit → families/amendments → cross-contract Q&A → obligations/deadlines.

## 2. Canonical stack
- **Web:** Next.js 15 App Router, TypeScript 5.x (`strict`), Tailwind CSS, shadcn/ui, native browser PDF viewer via HTML `<object>` element and streamed document content, `@supabase/supabase-js` (used exclusively for user authentication and session tokens; direct database querying is strictly prohibited).
- **Authentication:** Supabase Auth (managed identity provider issuing signed JWTs); FastAPI validates JWT signatures cryptographically via cached JWKS.
- **API:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x async, `asyncpg` (TLS-encrypted database connections via `DATABASE_URL`; strict TLS 1.2+ and certificate verification are enforced by the migration runner), forward-only SQL migrations.
- **Database:** PostgreSQL 16+ (Supabase Staging / Cloud or Dedicated PostgreSQL), `pgvector` 0.8+, `pg_trgm`, native full-text search. Runtime connections strictly use `onsigning_app` with `NOBYPASSRLS`.
- **Jobs:** Redis 7+ and Python ARQ for ingestion, audits, indexing and scheduled deadline jobs.
- **Parsing:** PyMuPDF for PDF text/layout/font inspection; `python-docx` for DOCX.
- **Object storage:** private S3-compatible storage (AWS S3 or Cloudflare R2), encryption and short-lived presigned URLs.
- **AI:** provider-abstracted LLM client with Pydantic structured outputs; provider/model selected by configuration. Do not assume zero-retention unless contract and provider settings confirm it.
- **Embeddings:** add a 1536-dimension embedding model for Feature 3; do not make vector retrieval a dependency of Feature 1.

## 3. Logical architecture
```text
Browser (Next.js)
   │ 1. Sign-in / session refresh
   ▼
Supabase Auth (Managed IdP)
   │ 2. Signed Bearer JWT (ES256 / HS256)
   ▼
FastAPI Application Gateway (Exclusive Data Access Layer)
   ├── Cryptographic JWT signature verification via Supabase JWKS (.well-known/jwks.json)
   ├── Identity resolution (`sub` -> `users.id` / `external_auth_id`)
   ├── Workspace membership verification (`organization_memberships`)
   ├── Establishes transaction-local RLS context:
   │   app.current_user_id = <user_uuid>
   │   app.current_organization_id = <org_uuid>
   │
   ├── Direct PostgreSQL (Port 5432, role: onsigning_app, NOBYPASSRLS, TLS encrypted)
   ├── Private object storage (source PDF/DOCX)
   └── Redis/ARQ ──► workers (role: onsigning_app, strict tenant context)

[BLOCKED / ZERO PRIVILEGES BOUNDARY]
Supabase PostgREST (/rest/v1/*) ──X  (anon, authenticated, service_role: 0 table grants)
```

## 4. Monorepo and workspace layout (AUD-010)
All six canonical specification files are maintained directly in the workspace root to ensure visibility and prevent drift.

```text
onsigning/
├── PRD.md                       # Product Requirements Document
├── ARCHITECTURE.md              # System Architecture & Technical Specifications
├── SCHEMA.md                    # Canonical Data Model & Integrity Rules
├── AI_DATA_SPEC.md              # AI Pipeline, Normalization & Playbook Rule Specs
├── DESIGN_SYSTEM.md             # UI Design Tokens, Workflows & Accessibility
├── AGENTS.md                    # Agent Governance & Engineering Rules
├── apps/
│   ├── web/                     # Next.js 15 App Router (Shadcn/UI, Tailwind)
│   └── api/
│       ├── core/                # config, auth, DB sessions, RLS context, logging
│       ├── routers/             # audits, families, qa, obligations, documents
│       ├── services/             # domain rules, verifier, playbook evaluator
│       ├── ai_pipeline/          # parsing (PyMuPDF/docx), prompts, Pydantic schemas
│       ├── workers/              # ARQ worker tasks and deadline schedules
│       └── tests/                # unit, integration, RLS tenant isolation tests
└── packages/
    └── database/
        └── migrations/           # forward-only SQL migrations (PostgreSQL 16+)
```

## 5. Request and transaction security (AUD-007)
### Web request lifecycle & authentication architecture
For each protected HTTP request:
1. **Authentication Token Extraction:** Client authenticates with Supabase Auth (managed identity provider) and includes the issued JWT in the `Authorization: Bearer <token>` header, alongside the target workspace in `X-Organization-Id: <uuid>`.
2. **Cryptographic JWT Validation (`decode_supabase_jwt`):**
   - **Algorithm Constraints:** Constrained strictly to `settings.allowed_jwt_algorithms` (`ES256`, `HS256`). Unapproved, missing, or 'none' algorithms fail immediately.
   - **Asymmetric ES256 (Primary):** Resolves the public key dynamically from Supabase Auth's JWKS endpoint (`{SUPABASE_URL}/auth/v1/.well-known/jwks.json`) via `PyJWKClient` using the header's `kid`. Keys are cached in-memory with a 1-hour TTL (`SUPABASE_JWKS_CACHE_TTL_SECONDS = 3600`).
   - **Symmetric HS256 (Fallback):** Verifies using `settings.SUPABASE_JWT_SECRET` strictly as HMAC secret; never accepts asymmetric public keys as HMAC secrets.
   - **Claims Enforcement:** Validates `exp` (not expired), `sub` (user UUID), `aud` (matches `settings.SUPABASE_AUDIENCE = 'authenticated'`), and `iss` (matches `settings.supabase_issuer = '{SUPABASE_URL}/auth/v1'`).
3. **User Resolution & Just-In-Time Provisioning (`get_current_user`):**
   - Maps `sub` to `users.id` and `users.external_auth_id` (`auth_provider = 'supabase'`).
   - If the user does not exist in local PostgreSQL storage (e.g. initial login after Supabase signup), provisions the `users` row automatically using `email` and `full_name` from token metadata.
4. **Workspace Membership & Tenant Context (`get_tenant_context`):**
   - Client-supplied organization IDs are **never** trusted without server-side validation.
   - FastAPI acquires a connection from the pool and opens a transaction under runtime role `onsigning_app` (`NOBYPASSRLS`).
   - Sets minimal user context: `SELECT set_config('app.current_user_id', :verified_user_uuid, true); SELECT set_config('app.current_organization_id', '', true);`.
   - Queries `organization_memberships` to verify `(organization_id, user_id)`. If unauthorized, aborts immediately with HTTP 403.
   - Upon membership validation, sets the full tenant context for the transaction:
     ```sql
     SELECT set_config('app.current_user_id', :verified_user_uuid, true);
     SELECT set_config('app.current_organization_id', :verified_org_uuid, true);
     ```
5. **Tenant-Scoped Execution:** Executes application queries strictly within that transaction, governed by PostgreSQL RLS.
6. **Clean Session Teardown:** Commits or rolls back the transaction and resets context before releasing the connection back to the connection pool.

### Workspace discovery & bootstrap (before tenant selection)
- **Workspace Discovery:** When a user logs in before selecting an active organization, `app.current_organization_id` cannot be set. The user queries `organization_memberships` where an RLS policy permits `SELECT` based strictly on `user_id = current_setting('app.current_user_id')::uuid`. This discovers the user's authorized organizations without exposing other tenants.
- **Workspace Bootstrap:** Creating a new workspace executes inside a transaction setting `app.current_user_id = :verified_user_uuid`. The user inserts a row into `organizations` and an initial membership row into `organization_memberships` with role `OWNER`. RLS policies explicitly allow authenticated users to insert new organizations and their own initial owner membership.

### Background worker tenant context
- ARQ worker tasks (`ingest:{org_id}:{doc_id}:{sha256}`) run asynchronously outside HTTP sessions.
- Job payloads are generated internally by the API server and signed with the worker secret. Client-supplied organization IDs are never accepted directly by workers.
- When an ARQ worker dequeues a task, it acquires a database connection, begins a transaction, and sets tenant context via `set_tenant_context()`:
  - If a human actor initiated the task (`actor_user_id` is present), the worker verifies their membership in `organization_id` and sets:
    ```sql
    SELECT set_config('app.current_organization_id', :job_org_uuid, true);
    SELECT set_config('app.current_user_id', :actor_user_uuid, true);
    ```
  - For automated tasks without a human actor (`actor_user_id` is `None`), the worker executes under the `SYSTEM_WORKER` policy: `app.current_organization_id` is set to the validated organization UUID and `app.current_user_id` is set to an empty string (`''`):
    ```sql
    SELECT set_config('app.current_organization_id', :job_org_uuid, true);
    SELECT set_config('app.current_user_id', '', true);
    ```
    The empty string is converted to `NULL` by the documented RLS policy expression `NULLIF(current_setting('app.current_user_id', true), '')::uuid`. No synthetic system-user UUID is fabricated.
- Because workers connect as `onsigning_app` with `NOBYPASSRLS`, worker queries are strictly subject to tenant RLS policies. RLS is never disabled or bypassed for worker execution.

### Database migration and role architecture
- **FastAPI Exclusive Data Boundary**: FastAPI is the single, exclusive data access layer for all application data. Application clients never connect directly to PostgreSQL or PostgREST. Runtime web requests and background workers connect strictly as cluster role `onsigning_app` with `NOBYPASSRLS` and `NOSUPERUSER` over TLS-encrypted connections negotiated via `DATABASE_URL` (`ssl=require`). In contrast, administrative migrations executed via `packages/database/run_migration.py` explicitly enforce a minimum of TLS 1.2, certificate-chain validation, hostname verification (`ssl.CERT_REQUIRED`), and optional project CA bundle validation (`--ca-path`).
- **Runtime Least Privilege**:
  - `onsigning_app` possesses `USAGE` on schema `public` (`CREATE` is revoked, completely preventing runtime DDL).
  - `onsigning_app` possesses `SELECT, INSERT, UPDATE, DELETE` on 16 application tables (`TRUNCATE` is withheld).
  - `onsigning_app` possesses append-only `SELECT, INSERT` on `public.audit_logs` (`UPDATE, DELETE, TRUNCATE` are withheld from `onsigning_app` and `PUBLIC`).
  - `onsigning_app` possesses `USAGE, SELECT` on all sequences in schema `public`.
- **Zero-Trust PostgREST Boundary**: Supabase PostgREST roles (`anon`, `authenticated`, `service_role`) and `PUBLIC` have all direct table privileges revoked across all 17 application tables via forward-only migrations `0003_security_hardening.sql` and `0004_postgrest_privilege_hardening.sql`. Direct PostgREST endpoints (`/rest/v1/*`) cannot access application data, failing closed with SQL permission denied errors.
- **`service_role` Privileges & `BYPASSRLS` Scope**: In Supabase Cloud, `service_role` retains its cluster-level `BYPASSRLS` attribute (`rolbypassrls = true`). RLS policies alone do NOT protect against `service_role`. Migration 0004 revokes all effective privileges (`SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER`) from `service_role` on the 17 audited application tables, blocking direct access through those audited table-permission paths. Because PostgreSQL's `BYPASSRLS` attribute only bypasses Row-Level Security on relations where a role already holds permission, it does not grant access to tables where privileges have been revoked.
- **`users` Table RLS Architecture**: The `users` table intentionally has RLS disabled. Because user authentication, identity resolution, and just-in-time user creation must occur before tenant organization context (`app.current_organization_id`) exists, applying organization RLS to `users` would cause login deadlocks. Protection of user records is strictly enforced server-side by FastAPI authentication and authorization dependencies (`get_current_user`). All 16 tenant tables enforce `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`.
- **Default Privilege Hardening for Role `postgres`**:
  - In Supabase Cloud, migrations execute under role `postgres` (the table owner).
  - Migration 0004 hardened default privileges for `postgres` both globally and in schema `public`:
    * Revokes all default table and sequence privileges from `anon`, `authenticated`, `service_role`, and `PUBLIC`.
    * Grants `SELECT, INSERT, UPDATE, DELETE` on future tables in schema `public` to `onsigning_app`.
    * Grants `USAGE, SELECT` on future sequences in schema `public` to `onsigning_app`.
- **CRITICAL INVARIANT: Future Tables RLS Provisioning**:
  PostgreSQL default privileges apply **only** to object-level DML grants (`SELECT, INSERT, UPDATE, DELETE`) and sequence usage. PostgreSQL provides **no** default privilege mechanism to automatically enable Row-Level Security, force RLS, or generate tenant policies on newly created tables. Therefore, whenever a future migration introduces a table, the migration author MUST explicitly:
  1. Execute `ALTER TABLE <new_table> ENABLE ROW LEVEL SECURITY;`
  2. Execute `ALTER TABLE <new_table> FORCE ROW LEVEL SECURITY;`
  3. Define tenant isolation policies using `current_setting('app.current_organization_id', true)::uuid`.
  4. Ensure composite foreign keys `(organization_id, parent_id)` are enforced.
- **Canonical Migration Lineage**:
  1. `0001_mvp_schema.sql`: 17 base tables, enums, indexes, composite FKs, RLS policies.
  2. `0002_runtime_grants.sql`: Runtime least-privilege grants to `onsigning_app`.
  3. `0003_security_hardening.sql`: PostgREST hardening on `users` and `audit_logs`; postgres schema defaults.
  4. `0004_postgrest_privilege_hardening.sql`: Full 17-table zero-trust PostgREST boundary and global default privilege hardening.
- **Standalone CI Model**: Automated ephemeral testing (`apps/api/tests/postgres_bootstrap.py`) provisions an ephemeral database owned by `onsigning_admin` (`BYPASSRLS`) to run migrations in isolated CI environments where PostgREST roles do not exist.

## 6. Ingestion and audit sequence
### Supported document formats & boundary validation (AUD-017)
- **Supported Formats:** Strictly `application/pdf` (`.pdf`) and `application/vnd.openxmlformats-officedocument.wordprocessingml.document` (`.docx`).
- **Rejected Formats:** Legacy binary `.doc` (Word 97–2003), image-only formats without PDF wrapper, RTF, and plain text are explicitly rejected at the API upload boundary with HTTP 415 (Unsupported Media Type). Upload validation verifies both MIME type and file magic bytes.

### Ingestion flow
1. API validates file format, magic bytes, byte size, and user authorization; computes SHA-256; stores file in private object storage.
2. Insert `documents` row (`processing_status = UPLOADED`) and enqueue deterministic ARQ job key `ingest:{org_id}:{doc_id}:{sha256}`.
3. Worker sets tenant RLS context and transitions `processing_status = PARSING_LAYOUT`. Parses document page by page using PyMuPDF (PDF) or `python-docx` (DOCX). Records `page_count`, `unreadable_pages`, and `parser_warnings`. Strips zero-point or invisible text.
4. If permanent parsing failure occurs, record `processing_status = FAILED`, populate `error_category`, user-safe `error_message`, technical `error_detail`, and `failed_at`, and terminate gracefully.
5. Transition to `EXTRACTING_FACTS`. Convert parsed pages to structured context and prompt LLM with untrusted delimiters. LLM outputs `ExtractedClauseFact` candidates.
6. Independent Python verifier executes Stage C normalization and citation verification against parsed page text. Evaluates contiguous quotes and cross-page boundaries (`page_number` to `page_end`). Sets `citation_status` and `is_evidence_verified`.
7. Transition to `EVALUATING_PLAYBOOK`. Deterministic evaluator applies active `risk_playbooks.rules` to verified facts and coverage metadata. Emits `risk_alerts`.
8. Persist results in a single transaction; transition status to `READY` (or `READY_WITH_WARNINGS` if unreadable pages or ambiguous references exist).
9. Safe retry (AUD-015): Re-running ingestion replaces unreviewed records (`AI_EVALUATED`), but retains clauses linked to human-confirmed findings (`HUMAN_CONFIRMED`) or confirmed supersessions (`CONFIRMED_SUPERSEDED`).

### Document viewing architecture (Secure PDF Streaming)
To ensure the Next.js native browser PDF viewer (rendered via the HTML `<object>` element) remains responsive and functional during extended review sessions (> 15 minutes) without exposing object storage credentials:
- The web application loads document content through an authenticated API route: `GET /api/documents/{id}/content`.
- The endpoint cryptographically validates user authentication and organization membership on every request, then streams the document from private object storage.
- Presigned S3 URLs are restricted to short-lived worker transfers or explicit user download actions, never exposed directly in client application state or browser navigation history.

### Document processing state
`UPLOADED → PARSING_LAYOUT → EXTRACTING_FACTS → EVALUATING_PLAYBOOK → READY`
Optional intermediate state: `INDEXING_RETRIEVAL` when later feature indexing is enabled. Terminal/alternate states: `READY_WITH_WARNINGS`, `FAILED`. Partial coverage is represented explicitly; never infer a clean document from a successful parser exit alone.

## 7. Feature-specific architecture
### M1: Single-agreement audit
Full-document structured extraction → citation verifier → deterministic playbook evaluator → immutable audit run/report. Separate facts (`extracted_clauses`) from rule evaluations (`risk_alerts`).

### M2: Families and amendments
Relationship detector creates proposals only. Authorized human confirms/rejects. Clause supersession transitions from `PROPOSED_BY_AI` to `CONFIRMED_SUPERSEDED` only after confirmation. Historical records remain queryable.

### M3: Cross-contract Q&A
Within tenant-scoped transaction, union candidates from:
- structured SQL filters over normalized fields,
- `tsvector` full-text search,
- `pgvector` cosine similarity.
Merge ranks with RRF. Exclude confirmed-superseded clauses from active-term answers, but surface historical or pending changes when relevant. Synthesis must emit claim-level citations and uncertainty status.

### M4: Obligation automation
Obligations have durable workflow state and separately computed temporal status. Scheduler finds due thresholds and inserts a unique notification record before dispatch. Retries update the same record; they do not create duplicate logical alerts.

## 8. Security, privacy and operations
- Never log raw contract text, verbatim quotes, access tokens, or presigned URLs.
- Use opaque entity IDs and correlation IDs in logs; capture status, latency and token counters.
- Store uploads privately; presigned URLs have a maximum 900-second TTL.
- Treat embedded instructions in documents as prompt injection. Delimit contract text as untrusted and never follow instructions found inside it.
- Rate-limit uploads, expensive AI requests and Q&A.
- Use least-privilege credentials and separate migration/runtime roles.
- Retain explicit audit trails for human decisions and external notification attempts.

### Operational and resource limits (AUD-011)
The following limits define the operational boundary for Feature 1. Limits marked **[Pending Approval]** represent recommended baseline parameters requiring owner sign-off:
- **Maximum file upload size:** 35 MB [Pending Approval]. Prevents worker OOM errors on large multipart uploads.
- **Maximum document page count:** 150 pages [Pending Approval]. Documents exceeding this threshold are rejected at parse time with `PAGE_COUNT_EXCEEDED`.
- **Ingestion worker task timeout:** 300 seconds [Pending Approval]. Bounded execution preventing stalled worker threads.
- **LLM request timeout:** 45 seconds with maximum 2 retries [Pending Approval]. Protects against provider latency spikes.
- **Presigned storage URL TTL:** 900 seconds (15 minutes) [Approved Baseline]. Restricts exposure of object storage links.

## 9. Reliability and failure handling
- ARQ jobs are idempotent and bounded-retry.
- Persist processing stage, error category, user-safe message, technical detail, and timestamp; never leave a document indefinitely “processing.”
- Distinguish transient provider/network errors from permanent parse/schema errors. Permanent errors transition directly to `FAILED`.
- Validate LLM output against strict Pydantic schemas; invalid output is retried only within a bounded policy.
- Outbound notifications use idempotency constraints and delivery status (`PENDING`, `SENT`, `FAILED_RETRYABLE`, `FAILED_PERMANENT`).
- Use transactional writes for state transitions and audit events.

## 10. Testing gates
- Unit tests: quote normalization, number/negation preservation, playbook thresholds, date calculations and state transitions.
- Integration tests: upload → parse → extract → verify → audit report.
- Security tests: cross-tenant reads/writes, guessed IDs, membership revocation, RLS context reset and worker tenant context.
- Reliability tests: duplicate job delivery, retry, worker crash, duplicate scheduler execution and notification provider failure.
- Evaluation tests: curated contract fixtures with expected clauses, citations, omission/coverage cases and known risk outcomes.

## 11. Explicit implementation cautions
- Validate the SQL migration on a real disposable PostgreSQL instance before calling it executable.
- RLS helper policies and workspace bootstrap need dedicated tests to avoid login/workspace-creation deadlocks.
- Composite `ON DELETE SET NULL` must null only the child foreign-key column, not `organization_id`.
- Use one queue technology (ARQ + Redis); do not add a second queue stack.
- Do not build vector search for M1.
