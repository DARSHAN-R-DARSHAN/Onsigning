# Onsigning — Product Requirements Document
**Version:** 1.0 · **Status:** Baseline for implementation · **Updated:** 2026-10-19

## 1. Product vision
Onsigning turns signed vendor, supplier, and commercial agreements into actionable, evidence-grounded contract intelligence. It is a post-signature execution layer—not an e-signature tool or a general-purpose document chatbot.

**Product promise:** Every obligation. Every signal. Every action.

## 2. Problem
After agreements are signed and archived, teams lose track of important terms, exceptions, amendments, and deadlines. The cost appears as overlooked renewal windows, uncapped liability carve-outs, missed security/privacy commitments, and slow answers to operational questions. Existing repositories store documents; Onsigning should help teams understand what the documents require and what they must do next.

## 3. Initial customer and users
Initial discovery segment: B2B SaaS companies with vendor/customer agreements and limited legal-operations capacity. This is a hypothesis to validate, not a proven market claim.

| Role | Typical user | Permissions |
|---|---|---|
| `OWNER` | Founder, General Counsel, VP Legal | Workspace, membership/roles, playbooks, documents, approvals, obligations |
| `ADMIN` | Legal Ops or Procurement lead | Playbooks, vendors, documents, review, assignments |
| `LEGAL_REVIEWER` | In-house/external counsel | Run audits, review/override findings, confirm relationships and dates |
| `VIEWER` | Finance, Security, executive | Read-only access to authorized reports, families, Q&A and calendars |

All authorization is enforced server-side. UI hiding is not an authorization control.

## 4. Four-feature MVP — built sequentially
The product is designed for all four features, but implementation proceeds one milestone at a time. Feature 1 is the first deliverable; later features must not delay a useful, testable Feature 1.

### Feature 1 — Single-Agreement Playbook Audit (Milestone 1)
Analyze one signed agreement against a versioned company Risk Playbook.
- Upload PDF/DOCX; parse text with page provenance and parser warnings.
- Extract factual clause data separately from playbook-specific risk evaluations.
- Compare extracted facts to configurable rules/thresholds.
- Verify each quote against source page text using deterministic code.
- Show evidence, page links, severity, uncertainty, coverage warnings, and human review status.
- Distinguish `SILENT_OMISSION` from `INSUFFICIENT_COVERAGE` and `AMBIGUOUS_OR_CROSS_REF`.
- Preserve immutable audit history and human decisions.

**Acceptance criteria**
1. Only `EXACT_MATCH` and strict `NORMALIZED_MATCH` citations may have `is_evidence_verified=true`.
2. Missing/unreadable pages or referenced-but-unattached exhibits block confident omission findings for affected categories.
3. Human confirmations, overrides, and dismissals are recorded in the audit log.
4. A user can trace every material finding to the source document and page.
5. Re-running ingestion does not erase human-reviewed decisions.

### Feature 2 — Contract Families & Amendment Management (Milestone 2)
Group related MSAs, SOWs, amendments, DPAs and exhibits; identify changes to terms over time.
- Suggest relationships such as `AMENDS`, `SUPPLEMENTS`, `SOW_UNDER_MSA`, `REPLACES`, and `RELATED_EXHIBIT`.
- Compare clauses and propose supersession with old and new evidence.
- Require an authorized human to confirm/reject each proposed supersession.
- Keep historical clauses and rejected proposals queryable.

**Acceptance criteria**
1. AI proposals never silently deactivate older terms.
2. Only a confirmed relationship/supersession changes active-term rollups.
3. The decision, actor, timestamp and evidence are auditable.

### Feature 3 — Cross-Contract Q&A (Milestone 3)
Answer natural-language questions over the authorized repository.
- Combine structured SQL filters, PostgreSQL full-text search and vector similarity.
- Fuse candidates with Reciprocal Rank Fusion (RRF).
- Prefer active clauses but disclose relevant pending amendment proposals.
- Return answer status: `DIRECT_EVIDENCE`, `QUALIFIED_INFERENCE`, `CONFLICTING_EVIDENCE`, or `INSUFFICIENT_EVIDENCE`.
- Cite each factual claim with document, page, and verified quote.

**Acceptance criteria**
1. Every factual claim has source evidence or is explicitly qualified as inference.
2. No unverified quote is presented as verified.
3. Retrieval executes inside the tenant-scoped database transaction.
4. If evidence is inadequate or contradictory, the system says so rather than inventing an answer.

### Feature 4 — Obligation & Deadline Automation (Milestone 4)
Turn contractual commitments into assigned, trackable work.
- Support `FIXED_DATE`, `RELATIVE_TO_ANCHOR`, `RECURRING_SCHEDULE`, and `EVENT_TRIGGERED`.
- Calculate dates only from explicit or deterministically verified anchors.
- Support owner assignment, confirmation, completion, waiver, supersession and reminders.
- Deliver idempotent email/webhook reminders with retry history.

**Acceptance criteria**
1. Unknown/external anchors produce `calculated_due_date=NULL` and `PENDING_CONFIRMATION`.
2. Re-running a scheduler does not create duplicate notifications for the same obligation, threshold and channel.
3. Durable workflow state is separate from time-derived status (`UPCOMING`, `DUE_SOON`, `OVERDUE`, `UNSCHEDULED`).

## 5. Core workflows
1. User creates/joins a workspace and receives a server-validated role.
2. User uploads a contract; API stores it privately and enqueues idempotent processing.
3. Worker parses pages, records coverage warnings, extracts structured facts, independently validates citations, and evaluates the selected playbook.
4. User reviews the report and confirms/overrides findings.
5. In later milestones, the user links documents into a family, confirms supersessions, asks repository questions, and manages obligations.

## 6. Functional requirements
- **FR-1 Identity & tenancy:** workspace membership and role checks on every protected operation.
- **FR-2 Document lifecycle:** private upload (.pdf and modern .docx; binary .doc rejected), checksum, parser status, retryable background jobs, structured error tracking, warnings and terminal status.
- **FR-3 Evidence:** source page range, verbatim quote, citation status, verified flag and uncertainty are stored separately.
- **FR-4 Playbooks:** versioned declarative rules; each audit references the exact playbook version used.
- **FR-5 Audit history:** prior reports and human decisions remain traceable.
- **FR-6 Amendments:** proposed relationships and supersessions require human confirmation.
- **FR-7 Q&A:** scoped retrieval and verified citations on every factual response.
- **FR-8 Obligations:** explicit anchor/date logic, support for standalone and familied contracts, assignment, state transitions and idempotent reminders.
- **FR-9 Observability:** structured, redacted logs; never log raw contract text, quotes, or presigned URLs.

## 7. Non-goals for MVP v1
- Collaborative contract drafting from scratch.
- Native e-signature execution.
- Fine-tuning custom LLMs.
- Native two-way ERP/CRM integrations.
- AI-only final legal decisions or automatic amendment overwrites.
- Character-level OCR bounding boxes for degraded scans; use page-level anchors for scanned pages in v1.
- Claims that the software provides legal advice or guarantees legal compliance.

## 8. Quality attributes and targets
- **Evidence integrity:** 100% of verified citations pass deterministic validation.
- **Tenant isolation:** cross-tenant reads/writes are denied by application authorization and PostgreSQL RLS.
- **Reliability:** idempotent ingestion and notification jobs; bounded retries and visible failures.
- **Privacy:** private object storage, short-lived signed URLs (maximum 900 seconds), encrypted transport/storage where supported, redacted telemetry.
- **Performance goal to benchmark:** born-digital 30-page PDF, approximately 18k tokens: P50 under 25 seconds and P95 under 60 seconds on a 2-vCPU/4-GB worker. This is a target, not a measured result.
- **Accessibility/usability:** findings must be understandable to non-engineering legal/operations users; uncertainty and evidence gaps are visible.

## 9. Risks and mitigations
| Risk | Mitigation |
|---|---|
| Fabricated or altered citations | Independent deterministic verifier; strict verified-state constraints |
| False “missing clause” finding | Coverage tracking and explicit uncertainty states |
| Amendment applied incorrectly | Human confirmation and immutable history |
| Cross-tenant leakage | RLS, composite FKs, tenant-context transaction tests |
| Duplicate or lost reminders | Unique idempotency key, retry state and delivery audit |
| Prompt injection in contract text | Treat source text as untrusted data; isolate instructions and sanitize hidden text |
| Overbuilding before validation | Ship Feature 1 first; validate buyer pain and willingness to pay during discovery |

## 10. Release gates
**Milestone 1 is not complete** until upload-to-report works end-to-end, citation checks and coverage warnings are tested, tenant isolation is tested, human decisions are audited, and failures are observable. Do not claim tests or production readiness until executed and recorded.
