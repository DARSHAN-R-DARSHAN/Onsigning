# Onsigning Database Migrations & Security Hardening

This directory contains the canonical forward-only SQL migrations and the transactional migration runner CLI for Onsigning.

---

## 1. Canonical Migration Lineage

Migrations must be executed in strict forward-only chronological order:

| Migration File | Description | Security Scope |
|---|---|---|
| `0001_mvp_schema.sql` | Base DDL: 17 core tables, custom enums, composite foreign keys `(organization_id, id)`, and tenant RLS policies. | Initializes 16 tenant tables with `ENABLE ROW LEVEL SECURITY` and `FORCE ROW LEVEL SECURITY`. `users` intentionally has RLS disabled for pre-tenant authentication resolution. |
| `0002_runtime_grants.sql` | Runtime role least-privilege grants. | Grants schema `public` `USAGE` (revokes `CREATE`) to `onsigning_app`. Grants CRUD to 16 application tables. Enforces append-only `audit_logs` (`SELECT, INSERT` only). Grants sequence `USAGE, SELECT`. |
| `0003_security_hardening.sql` | Targeted PostgREST remediation and table owner default privileges. | Revokes `ALL` privileges on `public.audit_logs` and `public.users` from `anon`, `authenticated`, `service_role`, and `PUBLIC`. Configures `postgres` schema default privileges for `onsigning_app`. |
| `0004_postgrest_privilege_hardening.sql` | Full 17-table zero-trust PostgREST boundary and global default privilege hardening. | Revokes `ALL` privileges on all 17 tables from `anon`, `authenticated`, `service_role`, and `PUBLIC`. Blocks direct access for `service_role` (which retains `BYPASSRLS`) across audited application tables. Hardens `postgres` default privileges globally and in schema `public`. |

---

## 2. Approved SHA-256 Checksums

The migration runner strictly pins and validates the SHA-256 hash of each approved migration before execution:

- `0003_security_hardening.sql`:
  `8ca03f97e7fe21b7ec208ef4b3835ba1af5ca2c9eef2f251a6ba4716b767ef28`

- `0004_postgrest_privilege_hardening.sql`:
  `3d4a23a5b27341e1f724a052180c8831b46e544532165bfd3fa68a859696d062`

---

## 3. Migration Runner Workflow (`run_migration.py`)

Migrations are executed via `packages/database/run_migration.py`. The runner operates within an explicit atomic transaction with fail-closed preflight validation and automated post-commit catalog verification.

### 3.1 Required Environment Variables

Before invoking the runner, three environment variables must be configured in the operator's private session:

1. `ONSIGNING_MIGRATION_DSN`:
   Direct PostgreSQL connection URI connecting as cloud administrator `postgres` to port **5432**:
   ```text
   postgresql://postgres:<PASSWORD>@db.<PROJECT_REF>.supabase.co:5432/postgres?ssl=require
   ```
   *Warning:* Supabase transaction/session poolers (port 6543) alter transaction states and are rejected during preflight.
2. `ONSIGNING_STAGING_PROJECT_REF`:
   Trusted target Supabase project reference (subdomain). For staging, this must match the expected project ref (e.g. `ixbmyzrzwmlaltmxgmbo`). The runner rejects execution if `--project-ref` does not match this trusted variable.
3. `ONSIGNING_MIGRATION_OPT_IN`:
   Explicit operator confirmation variable. Must be strictly set to `1`.

### 3.2 Preflight Security Requirements

The runner validates the following conditions before connecting or executing any SQL:
- Rejects execution within `pytest` or disposable test suites unless `ONSIGNING_RUNNER_TEST_MODE=1`.
- Requires `ONSIGNING_MIGRATION_OPT_IN=1`.
- Requires CLI flag `--confirm-staging`.
- Requires CLI flag `--target staging`.
- Validates that `--project-ref` strictly matches `ONSIGNING_STAGING_PROJECT_REF`.
- Verifies that target port is 5432 (rejects pooler port 6543).
- Enforces TLS 1.2+ with certificate and hostname validation (`ssl.CERT_REQUIRED`). An optional custom CA certificate bundle can be supplied via `--ca-path <path>` (e.g., Supabase `prod-ca-2021.crt`). If omitted, the system default trusted CA store is used.
- Validates the migration file SHA-256 against approved pins.

### 3.3 Execution Command

Execute from the repository root using the project virtual environment:

```powershell
python packages/database/run_migration.py `
  --target staging `
  --confirm-staging `
  --project-ref ixbmyzrzwmlaltmxgmbo `
  --migration 0004_postgrest_privilege_hardening.sql
```

---

## 4. Runner Exit Codes & Failure Semantics

The runner returns distinct exit codes depending on the failure stage:

| Exit Code | Constant | Meaning & Transaction State |
|---|---|---|
| `0` | `EXIT_SUCCESS` | **Success:** Migration transaction committed, and all post-commit catalog assertions passed. |
| `1` | `EXIT_PREFLIGHT_FAILURE` | **Preflight Error:** Preflight validation failed before connecting or before executing SQL. The database is untouched. |
| `2` | `EXIT_MIGRATION_FAILURE` | **SQL/Transaction Error:** An error occurred during DDL execution or commit. The runner attempts to roll back; however, if the connection is lost or drops during commit, the final transaction outcome may be ambiguous. Operators must inspect the database migration and catalog state before retrying. |
| `3` | `EXIT_VERIFICATION_FAILURE` | **Post-Commit Invariant Violation:** **CRITICAL ALERT:** The migration transaction has **ALREADY COMMITTED** and was **NOT** rolled back. One or more post-commit catalog assertions failed (e.g., unexpected grants detected or default ACL mismatch). |

### OPERATOR GUIDANCE FOR EXIT CODES 2 & 3
- **Exit Code 2 (`EXIT_MIGRATION_FAILURE`):** When PostgreSQL explicitly rejects a statement over a healthy connection, the transaction is rolled back. However, if the connection dropped or timed out during commit, the transaction may have already committed on the server. Operators must inspect the database migration/catalog state before retrying; do not assume every exit-code-2 failure guarantees rollback.
- **Exit Code 3 (`EXIT_VERIFICATION_FAILURE`):** The migration transaction has already committed to the database catalog. **DO NOT** attempt automated retries or automated compensating migrations. Immediate manual DBA inspection of the catalog is required to diagnose invariant violations.

---

## 5. Security Model & Future Tables Invariant

### 5.1 Zero-Trust PostgREST Boundary & `service_role` Privileges Scope
Supabase PostgREST roles (`anon`, `authenticated`, `service_role`) and `PUBLIC` have zero effective privileges across all 17 application tables. Direct PostgREST API access (`/rest/v1/*`) cannot read or mutate application data through those relation paths. The `service_role` retains its cluster-level `BYPASSRLS` attribute (`rolbypassrls = true`), but revoking all direct table privileges blocks direct access through those audited table-permission paths, as `BYPASSRLS` does not grant permissions on tables where privileges have been revoked.

### 5.2 Runtime Application Role (`onsigning_app`)
Application traffic and background workers connect exclusively via FastAPI as role `onsigning_app` with `NOBYPASSRLS` and `NOSUPERUSER`. `onsigning_app` possesses:
- `USAGE` on schema `public` (`CREATE` revoked).
- `SELECT, INSERT, UPDATE, DELETE` on 16 application tables (`TRUNCATE` withheld).
- Append-only `SELECT, INSERT` on `public.audit_logs` (`UPDATE, DELETE, TRUNCATE` withheld).
- `USAGE, SELECT` on all sequences in schema `public`.

### 5.3 CRITICAL INVARIANT: Provisioning RLS on Future Tables
Default privileges configured by Migration 0004 automatically grant table DML and sequence usage to `onsigning_app` for future objects created by role `postgres`.

However, **PostgreSQL default privileges do not and cannot automatically enable Row-Level Security, force RLS, or create tenant policies.**

Whenever a future migration creates a new table, the migration author **MUST** explicitly include:
```sql
ALTER TABLE public.<new_table> ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.<new_table> FORCE ROW LEVEL SECURITY;

CREATE POLICY <new_table>_tenant_isolation ON public.<new_table>
    AS RESTRICTIVE
    FOR ALL
    TO onsigning_app
    USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid)
    WITH CHECK (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);
```
Failure to explicitly enable and force RLS on future tenant tables will result in complete data exposure across tenants for the `onsigning_app` role.
