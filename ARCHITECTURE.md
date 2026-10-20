# Onsigning — System Architecture
**Version:** 1.0 · **Status:** Baseline for implementation · **Updated:** 2026-10-19

## 1. Architectural principles
1. Evidence first: extracted facts, evidence validation, interpretation and human decisions are separate stages.
2. Tenant isolation is enforced in both application authorization and PostgreSQL RLS.
3. LLM output is untrusted input; schema-valid output is not necessarily true.
4. No silent amendment supersession and no fabricated dates.
5. Long-running work is asynchronous and idempotent.
6. Build all four features in the design, but implement in sequence: audit → families/amendments → cross-contract Q&A → obligations/deadlines.

## 2. Canonical stack
- **Web:** Next.js 15 App Router, TypeScript 5.x (`strict`), Tailwind CSS, shadcn/ui, PDF.js viewer.
- **API:** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x async, `asyncpg`, Alembic/forward-only SQL migrations.
- **Database:** PostgreSQL 16+, `pgvector` 0.8+, `pg_trgm`, native full-text search.
- **Jobs:** Redis 7+ and Python ARQ for ingestion, audits, indexing and scheduled deadline jobs.
- **Parsing:** PyMuPDF for PDF text/layout/font inspection; `python-docx` for DOCX.
- **Object storage:** private S3-compatible storage (AWS S3 or Cloudflare R2), encryption and short-lived presigned URLs.
- **AI:** provider-abstracted LLM client with Pydantic structured outputs; provider/model selected by configuration. Do not assume zero-retention unless contract and provider settings confirm it.
- **Embeddings:** add a 1536-dimension embedding model for Feature 3; do not make vector retrieval a dependency of Feature 1.

## 3. Logical architecture
```text
Browser (Next.js)
   │ authenticated request
   ▼
FastAPI ── verifies identity, membership, role and tenant context
   │
   ├── PostgreSQL (RLS + composite tenant foreign keys)
   ├── Private object storage (source PDF/DOCX)
   └── Redis/ARQ ──► workers
                      ├─ parse/layout + coverage
                      ├─ structured fact extraction
                      ├─ deterministic citation verification
                      ├─ playbook rule evaluation
                      ├─ (M2) relationship/supersession proposals
                      ├─ (M3) hybrid retrieval + cited synthesis
                      └─ (M4) deadline scheduler + notifications
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
### Web request lifecycle
For each protected HTTP request:
1. Validate session/JWT cryptographically.
2. Resolve user identity `verified_user_uuid`.
3. Open a database transaction using the runtime role `onsigning_app` (`NOBYPASSRLS`).
4. Set transaction-local context:
   ```sql
   SELECT set_config('app.current_user_id', :verified_user_uuid, true);
   SELECT set_config('app.current_organization_id', :verified_org_uuid, true);
   ```
5. Execute tenant-scoped queries within that transaction.
6. Commit/rollback and return connection to pool.

### Workspace discovery & bootstrap (before tenant selection)
- **Workspace Discovery:** When a user logs in before selecting an active organization, `app.current_organization_id` cannot be set. The user queries `organization_memberships` where an RLS policy permits `SELECT` based strictly on `user_id = current_setting('app.current_user_id')::uuid`. This discovers the user's authorized organizations without exposing other tenants.
- **Workspace Bootstrap:** Creating a new workspace executes inside a transaction setting `app.current_user_id = :verified_user_uuid`. The user inserts a row into `organizations` and an initial membership row into `organization_memberships` with role `OWNER`. RLS policies explicitly allow authenticated users to insert new organizations and their own initial owner membership.

### Background worker tenant context
- ARQ worker tasks (`ingest:{org_id}:{doc_id}:{sha256}`) run asynchronously outside HTTP sessions.
- Job payloads are generated internally by the API server and signed with the worker secret. Client-supplied organization IDs are never accepted directly by workers.
- When an ARQ worker dequeues a task, it acquires a database connection, begins a transaction, and explicitly sets:
  ```sql
  SELECT set_config('app.current_organization_id', :job_org_uuid, true);
  SELECT set_config('app.current_user_id', :worker_system_uuid, true);
  ```
- Because workers connect as `onsigning_app` with `NOBYPASSRLS`, worker queries are strictly subject to tenant RLS policies. RLS is never disabled or bypassed for worker execution.

The runtime database role is `onsigning_app` with `NOBYPASSRLS`, not the migration owner/superuser. Apply `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY` to tenant-sensitive tables. Use composite FKs `(organization_id, parent_id)` so a child cannot point at another tenant's parent. Audit logs are append-only to the application role (`SELECT, INSERT` only).

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
To ensure the Next.js PDF.js viewer remains responsive and functional during extended review sessions (> 15 minutes) without exposing object storage credentials:
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
