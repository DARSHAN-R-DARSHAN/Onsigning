"""
Onsigning Canonical Database Migration Runner: 0003_security_hardening.sql
PostgreSQL 16+ Transactional Execution & Zero-Trust Verification CLI

Strictly adheres to ARCHITECTURE.md, SCHEMA.md, and AGENTS.md.
Executes packages/database/migrations/0003_security_hardening.sql within an
explicit, atomic transaction and performs exhaustive read-only verification.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import ssl
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Exit Codes
EXIT_SUCCESS = 0
EXIT_PREFLIGHT_FAILURE = 1
EXIT_MIGRATION_FAILURE = 2
EXIT_VERIFICATION_FAILURE = 3

# Dry-Run Status Identifiers
DRY_RUN_STATUS_PENDING = "PENDING"
DRY_RUN_STATUS_ALREADY_APPLIED = "ALREADY_APPLIED"
DRY_RUN_STATUS_DRIFTED = "DRIFTED"
DRY_RUN_STATUS_BLOCKED = "BLOCKED"

DEFAULT_MIGRATION_FILENAME = "0003_security_hardening.sql"
MIGRATION_FILENAME = DEFAULT_MIGRATION_FILENAME
APPROVED_MIGRATION_0003_SHA256 = "8ca03f97e7fe21b7ec208ef4b3835ba1af5ca2c9eef2f251a6ba4716b767ef28"
APPROVED_MIGRATION_0004_SHA256 = "3d4a23a5b27341e1f724a052180c8831b46e544532165bfd3fa68a859696d062"
APPROVED_MIGRATION_0005_SHA256 = "7eff2f1d3ad3252e7351d6c32a2ea664b95d1fc2a1cd906d38563c7dc7a30bbe"

APPROVED_MIGRATIONS: Dict[str, str] = {
    "0003_security_hardening.sql": APPROVED_MIGRATION_0003_SHA256,
    "0004_postgrest_privilege_hardening.sql": APPROVED_MIGRATION_0004_SHA256,
    "0005_organization_discovery_rls.sql": APPROVED_MIGRATION_0005_SHA256,
}


def mask_dsn(dsn: Optional[str]) -> str:
    """Masks password and sensitive tokens from a database connection string."""
    if not dsn:
        return "<empty>"
    # Mask password in standard postgresql://user:password@host:port/db
    masked = re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", dsn)
    return masked


def sanitize_log(msg: Any) -> str:
    """Sanitizes text by removing passwords, tokens, or raw connection credentials."""
    text = str(msg)
    # Mask :password@ patterns
    text = re.sub(r":([^@]+)@", r":***@", text)
    # Mask query parameters like key=..., password=..., secret=...
    text = re.sub(
        r"(password|secret|token|key)=([^\s&]+)",
        r"\1=***",
        text,
        flags=re.IGNORECASE,
    )
    return text


def build_ssl_context(ca_path: Optional[str] = None) -> ssl.SSLContext:
    """
    Constructs a secure SSL context enforcing TLS 1.2+ with certificate and
    hostname verification enabled.
    """
    ctx = ssl.create_default_context(cafile=ca_path)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def parse_args(args: Optional[List[str]] = None) -> argparse.Namespace:
    """Parses command-line arguments for the migration runner."""
    parser = argparse.ArgumentParser(
        description="Onsigning Transactional Migration Runner for 0003_security_hardening.sql",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=["staging"],
        help="Target deployment environment. Must explicitly be 'staging'.",
    )
    parser.add_argument(
        "--confirm-staging",
        action="store_true",
        default=False,
        help="Explicit operator confirmation flag for staging execution.",
    )
    parser.add_argument(
        "--project-ref",
        required=True,
        help="Expected Supabase staging project reference (subdomain).",
    )
    parser.add_argument(
        "--expected-db",
        default="postgres",
        help="Expected target database catalog name (defaults to 'postgres').",
    )
    parser.add_argument(
        "--ca-path",
        default=None,
        help="Optional path to custom CA bundle for TLS verification.",
    )
    parser.add_argument(
        "--migration",
        default=DEFAULT_MIGRATION_FILENAME,
        choices=list(APPROVED_MIGRATIONS.keys()),
        help=f"Migration file to execute (defaults to '{DEFAULT_MIGRATION_FILENAME}').",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Command execution timeout in seconds (default 60s).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Execute in strictly read-only mode to inspect prerequisites and migration state without applying DDL.",
    )
    return parser.parse_args(args)


def validate_preflight_config(
    args: argparse.Namespace,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """
    Exhaustively validates pre-flight security requirements before attempting any connection.
    Fails closed if target identity, operator opt-in, or TLS conditions are unmet.
    """
    if env is None:
        env = os.environ  # type: ignore

    # 1. Reject execution under pytest or live RLS test harness unless explicitly in runner test mode
    is_pytest = "pytest" in sys.modules
    is_rls_suite = env.get("RUN_POSTGRES_RLS", "").lower() in ("true", "1", "yes")
    test_mode = env.get("ONSIGNING_RUNNER_TEST_MODE", "").lower() in ("true", "1", "yes")

    if (is_pytest or is_rls_suite) and not test_mode:
        return (
            False,
            "Execution rejected: migration runner must not be executed within pytest or disposable test harness.",
            None,
        )

    # 2. Operator explicit opt-in
    opt_in = env.get("ONSIGNING_MIGRATION_OPT_IN", "")
    if opt_in != "1":
        return (
            False,
            "Operator opt-in missing: environment variable ONSIGNING_MIGRATION_OPT_IN=1 is required.",
            None,
        )

    # 3. Explicit staging confirmation flag
    if not args.confirm_staging:
        return (
            False,
            "Missing confirmation: flag --confirm-staging is required.",
            None,
        )

    if args.target != "staging":
        return (
            False,
            f"Invalid target environment: '{args.target}'. Only 'staging' is authorized.",
            None,
        )

    # 4. Independent Staging Project Reference Validation
    # The repository requires a trusted, documented staging configuration.
    # We do not accept an arbitrary CLI --project-ref without matching trusted env configuration.
    trusted_project_ref = env.get("ONSIGNING_STAGING_PROJECT_REF", "").strip()
    if not trusted_project_ref:
        return (
            False,
            "Trusted staging project reference is not configured. "
            "Environment variable ONSIGNING_STAGING_PROJECT_REF must be defined in the trusted "
            "deployment environment to prevent targeting an unintended database.",
            None,
        )

    if args.project_ref.strip() != trusted_project_ref:
        return (
            False,
            "Project reference mismatch: CLI argument --project-ref does not match trusted "
            "ONSIGNING_STAGING_PROJECT_REF configuration.",
            None,
        )

    # 5. DSN extraction and structural validation
    dsn = env.get("ONSIGNING_MIGRATION_DSN", "").strip()
    if not dsn:
        return (
            False,
            "Connection configuration missing: environment variable ONSIGNING_MIGRATION_DSN must be set.",
            None,
        )

    try:
        parsed_dsn = urlparse(dsn)
    except Exception as e:
        return False, f"Connection DSN is malformed: {sanitize_log(e)}", None

    if parsed_dsn.scheme not in ("postgresql", "postgres"):
        return (
            False,
            f"Unsupported connection scheme: '{parsed_dsn.scheme}'. Must be 'postgresql' or 'postgres'.",
            None,
        )

    # 6. Enforce Direct Connection Only (Reject Poolers)
    # Supabase connection poolers (transaction or session mode) can alter transaction,
    # connection lifetime, and session semantics. Migration 0003 strictly requires
    # a direct connection to PostgreSQL on port 5432.
    port = parsed_dsn.port or 5432
    if port == 6543:
        return (
            False,
            "Transaction pooler port (6543) detected. Runner strictly requires a direct "
            "connection (port 5432). Connection poolers are prohibited for DDL migrations.",
            None,
        )

    # 7. Endpoint Identity Validation (Direct Connection Only)
    # Verify hostname corresponds to the direct staging project endpoint db.<ref>.supabase.co
    hostname = parsed_dsn.hostname or ""
    expected_direct_host = f"db.{trusted_project_ref}.supabase.co"

    # Allow custom trusted host override strictly if specified in trusted environment
    trusted_host_override = env.get("ONSIGNING_STAGING_HOST_OVERRIDE", "").strip()

    if hostname.endswith(".pooler.supabase.com"):
        return (
            False,
            f"Connection endpoint rejected: hostname '{hostname}' is a Supabase connection pooler. "
            f"The migration runner strictly requires direct connection to '{expected_direct_host}:5432'.",
            None,
        )

    is_valid_host = (
        hostname == expected_direct_host
        or (trusted_host_override and hostname == trusted_host_override)
    )

    if not is_valid_host:
        return (
            False,
            f"Connection endpoint mismatch: DSN hostname does not match direct staging project endpoint "
            f"'{expected_direct_host}'.",
            None,
        )

    # 8. Database name validation
    db_name = parsed_dsn.path.lstrip("/")
    expected_db = env.get("ONSIGNING_STAGING_DB_NAME", args.expected_db).strip()
    if db_name != expected_db:
        return (
            False,
            f"Database name mismatch: DSN targets database '{db_name}', expected '{expected_db}'.",
            None,
        )

    # 9. Ensure username is postgres
    if parsed_dsn.username != "postgres":
        return (
            False,
            f"Invalid DSN role: username is '{parsed_dsn.username}', expected 'postgres'.",
            None,
        )

    # 10. Verify migration file exists and calculate hash
    migration_filename = getattr(args, "migration", DEFAULT_MIGRATION_FILENAME)
    expected_sha256 = APPROVED_MIGRATIONS.get(migration_filename)
    if not expected_sha256:
        return (
            False,
            f"Unsupported or unapproved migration file: '{migration_filename}'.",
            None,
        )

    repo_root = Path(__file__).resolve().parent
    migration_file = repo_root / "migrations" / migration_filename
    if not migration_file.is_file():
        return (
            False,
            f"Migration file missing at resolved path: {migration_file}",
            None,
        )

    try:
        sql_bytes = migration_file.read_bytes().replace(b"\r\n", b"\n")
        sha256 = hashlib.sha256(sql_bytes).hexdigest()
        sql_text = sql_bytes.decode("utf-8")
    except Exception as e:
        return False, f"Failed to read migration file: {sanitize_log(e)}", None

    # Enforce exact approved SHA-256 digest before allowing connection or execution
    if sha256 != expected_sha256:
        return (
            False,
            f"Migration integrity check failed: SHA-256 digest '{sha256}' does not match "
            f"approved digest '{expected_sha256}'. Execution rejected.",
            None,
        )

    allow_insecure_local_tls = (
        test_mode
        and env.get("ONSIGNING_ALLOW_INSECURE_LOCAL_TEST_TLS", "").lower() in ("true", "1", "yes")
        and hostname in ("localhost", "127.0.0.1", "::1")
    )

    config = {
        "dsn": dsn,
        "masked_dsn": mask_dsn(dsn),
        "hostname": hostname,
        "port": port,
        "db_name": db_name,
        "trusted_project_ref": trusted_project_ref,
        "migration_filename": migration_filename,
        "migration_file": migration_file,
        "migration_sha256": sha256,
        "migration_sql": sql_text,
        "ca_path": args.ca_path,
        "timeout": args.timeout,
        "allow_insecure_local_tls": allow_insecure_local_tls,
        "dry_run": getattr(args, "dry_run", False),
    }

    return True, "Pre-flight validation passed.", config


async def execute_and_verify(
    config: Dict[str, Any],
    conn_override: Optional[Any] = None,
) -> int:
    """
    Executes migration 0003 inside an atomic transaction and executes
    read-only catalog verifications post-commit.
    """
    # Deferred asyncpg import
    try:
        import asyncpg
    except ImportError:
        print("[PRE-FLIGHT ERROR] Required driver 'asyncpg' is not installed.", file=sys.stderr)
        return EXIT_PREFLIGHT_FAILURE

    if config.get("allow_insecure_local_tls", False):
        if config["hostname"] not in ("localhost", "127.0.0.1", "::1"):
            print("[PRE-FLIGHT ERROR] Insecure TLS is strictly forbidden for non-localhost targets.", file=sys.stderr)
            return EXIT_PREFLIGHT_FAILURE
        ssl_ctx = None
    else:
        print("[PRE-FLIGHT] Initializing TLS secure context (TLS 1.2+ minimum)...")
        try:
            ssl_ctx = build_ssl_context(config.get("ca_path"))
        except Exception as e:
            print(f"[PRE-FLIGHT ERROR] Failed to construct TLS context: {sanitize_log(e)}", file=sys.stderr)
            return EXIT_PREFLIGHT_FAILURE

    conn = conn_override
    owns_conn = False

    if conn is None:
        print(f"[PRE-FLIGHT] Connecting to endpoint {config['hostname']}:{config['port']}/{config['db_name']}...")
        try:
            # Enforce statement_cache_size=0 and TLS verification
            conn = await asyncpg.connect(
                dsn=config["dsn"],
                ssl=ssl_ctx,
                statement_cache_size=0,
                command_timeout=config["timeout"],
            )
            owns_conn = True
        except Exception as e:
            print(f"[PRE-FLIGHT ERROR] Database connection failed: {sanitize_log(e)}", file=sys.stderr)
            return EXIT_PREFLIGHT_FAILURE

    try:
        # Pre-flight Server Identity Validation
        row = await conn.fetchrow(
            "SELECT current_database() AS db_name, current_user AS user_name, inet_server_addr()::text AS server_ip;"
        )
        if not row:
            print("[PRE-FLIGHT ERROR] Server identity query returned no rows.", file=sys.stderr)
            return EXIT_PREFLIGHT_FAILURE

        server_db = row["db_name"]
        server_user = row["user_name"]

        if server_user != "postgres":
            print(
                f"[PRE-FLIGHT ERROR] Identity check failed: current_user is '{server_user}', expected 'postgres'.",
                file=sys.stderr,
            )
            return EXIT_PREFLIGHT_FAILURE

        if server_db != config["db_name"]:
            print(
                f"[PRE-FLIGHT ERROR] Database check failed: current_database is '{server_db}', expected '{config['db_name']}'.",
                file=sys.stderr,
            )
            return EXIT_PREFLIGHT_FAILURE

        print(
            f"[PRE-FLIGHT] Verified database '{server_db}' and execution role '{server_user}'."
        )
        print(
            f"[MIGRATION] Loaded {config.get('migration_filename', MIGRATION_FILENAME)} (SHA-256: {config['migration_sha256']})."
        )

        # ----------------------------------------------------------------------
        # TRANSACTIONAL EXECUTION
        # ----------------------------------------------------------------------
        print("[MIGRATION] Opening explicit PostgreSQL transaction...")
        try:
            async with conn.transaction():
                print("[MIGRATION] Executing multi-statement migration SQL...")
                # Simple query protocol path (no parameters passed)
                await conn.execute(config["migration_sql"])
                print("[MIGRATION] Multi-statement execution completed; committing transaction...")

            print("[MIGRATION SUCCESS] Transaction committed successfully to catalog.")
        except Exception as exc:
            sqlstate = getattr(exc, "sqlstate", None)
            sqlstate_str = f" [SQLSTATE {sqlstate}]" if sqlstate else ""
            print(
                f"[MIGRATION FAILED] Execution error: {sanitize_log(exc)}{sqlstate_str}",
                file=sys.stderr,
            )

            # Distinguish confirmed server-side rollback from ambiguous connection/driver drops
            # without inspecting exception text for "commit".
            conn_closed = getattr(conn, "is_closed", lambda: True)()
            is_connection_failure = (
                conn_closed
                or isinstance(exc, (ConnectionError, OSError, asyncio.TimeoutError))
                or (hasattr(asyncpg, "InterfaceError") and isinstance(exc, asyncpg.InterfaceError))
                or (hasattr(asyncpg, "ConnectionDoesNotExistError") and isinstance(exc, asyncpg.ConnectionDoesNotExistError))
            )

            if is_connection_failure:
                print(
                    "[MIGRATION AMBIGUOUS] Database connection was lost, closed, or timed out during execution. "
                    "Transaction outcome CANNOT be confirmed and may be AMBIGUOUS. "
                    "The transaction may have committed before the connection dropped, or rollback may have occurred. "
                    "Do NOT assume the database is unchanged. Inspect catalog manually before retrying.",
                    file=sys.stderr,
                )
            else:
                # Connection remained healthy and PostgreSQL rejected the transaction (e.g. prerequisite failure)
                print(
                    "[MIGRATION FAILED] Transaction rolled back by PostgreSQL. Pre-migration state confirmed.",
                    file=sys.stderr,
                )
            return EXIT_MIGRATION_FAILURE

        # ----------------------------------------------------------------------
        # POST-COMMIT SECURITY VERIFICATION
        # ----------------------------------------------------------------------
        print("[VERIFICATION] Running post-commit read-only security assertions...")
        verification_failures: List[str] = []

        is_migration_0004 = config.get("migration_filename") == "0004_postgrest_privilege_hardening.sql"

        if is_migration_0004:
            target_tables = [
                "users", "organizations", "organization_memberships", "vendors",
                "risk_playbooks", "contract_families", "documents", "document_relationships",
                "document_sections", "document_chunks", "extracted_clauses", "audit_runs",
                "risk_alerts", "qa_queries", "obligations", "obligation_notifications", "audit_logs"
            ]
        else:
            target_tables = ["users", "audit_logs"]

        table_values = ", ".join(f"('{t}')" for t in target_tables)
        table_in_clause = ", ".join(f"'{t}'" for t in target_tables)

        try:
            # 1. Verify Catalog Table Presence
            # Ensure every expected target table exists in schema public as an ordinary table (relkind = 'r').
            table_presence_query = f"""
            SELECT c.relname AS tablename
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind = 'r'
              AND c.relname IN ({table_in_clause});
            """
            presence_rows = await conn.fetch(table_presence_query)
            found_tables = {pr["tablename"] for pr in presence_rows}
            missing_tables = set(target_tables) - found_tables
            if missing_tables:
                verification_failures.append(
                    f"Catalog inconsistency: expected table(s) missing from schema public: {sorted(missing_tables)}"
                )

            # 2. Verify Effective Table Privileges on PostgREST Roles
            # anon, authenticated, and service_role must have NO privileges (all 7 checked).
            # When verifying Migration 0004 on staging, fail closed if any PostgREST role is missing from pg_roles.
            if is_migration_0004:
                postgrest_roles_check = await conn.fetch(
                    "SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated', 'service_role')"
                )
                found_roles = {r["rolname"] for r in postgrest_roles_check}
                missing_roles = {"anon", "authenticated", "service_role"} - found_roles
                if missing_roles:
                    verification_failures.append(
                        f"Catalog inconsistency: expected PostgREST role(s) missing from cluster: {sorted(missing_roles)}"
                    )

            roles_query = f"""
            SELECT
                r.rolname,
                t.tablename,
                has_table_privilege(r.rolname, 'public.' || t.tablename, 'SELECT') AS can_select,
                has_table_privilege(r.rolname, 'public.' || t.tablename, 'INSERT') AS can_insert,
                has_table_privilege(r.rolname, 'public.' || t.tablename, 'UPDATE') AS can_update,
                has_table_privilege(r.rolname, 'public.' || t.tablename, 'DELETE') AS can_delete,
                has_table_privilege(r.rolname, 'public.' || t.tablename, 'TRUNCATE') AS can_truncate,
                has_table_privilege(r.rolname, 'public.' || t.tablename, 'REFERENCES') AS can_references,
                has_table_privilege(r.rolname, 'public.' || t.tablename, 'TRIGGER') AS can_trigger
            FROM (VALUES ('anon'), ('authenticated'), ('service_role')) AS r(rolname)
            CROSS JOIN (VALUES {table_values}) AS t(tablename)
            WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.rolname);
            """
            rows = await conn.fetch(roles_query)
            for r in rows:
                rolname = r["rolname"]
                table = r["tablename"]
                for priv in ("select", "insert", "update", "delete", "truncate", "references", "trigger"):
                    if r[f"can_{priv}"]:
                        verification_failures.append(
                            f"PostgREST role '{rolname}' has unexpected effective privilege '{priv.upper()}' on public.{table}."
                        )

            # 3. Verify PUBLIC Pseudo-Role Table Privileges using PostgreSQL acl catalog semantics (aclexplode)
            # In PostgreSQL, PUBLIC is a pseudo-role representing all users, NOT a role in pg_roles / pg_authid.
            # Grantee OID 0 corresponds to PUBLIC.
            # Uses COALESCE(c.relacl, acldefault('r', c.relowner)) to handle NULL relacl by evaluating built-in default ACLs.
            # Checks all 7 privilege types: SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER.
            public_acl_query = f"""
            SELECT
                c.relname AS tablename,
                a.privilege_type,
                a.is_grantable
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            CROSS JOIN LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) a
            WHERE n.nspname = 'public'
              AND c.relname IN ({table_in_clause})
              AND a.grantee = 0;
            """
            public_acl_rows = await conn.fetch(public_acl_query)
            for pr in public_acl_rows:
                table = pr["tablename"]
                priv = pr["privilege_type"]
                grantable_suffix = " (WITH GRANT OPTION)" if pr["is_grantable"] else ""
                verification_failures.append(
                    f"PUBLIC pseudo-role has prohibited privilege '{priv}'{grantable_suffix} on public.{table}."
                )

            # 4. Verify onsigning_app runtime privileges
            # 16 standard tables: CRUD required; TRUNCATE, REFERENCES, TRIGGER prohibited.
            # audit_logs: SELECT, INSERT required; UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER prohibited.
            app_query = f"""
            SELECT
                t.tablename,
                has_table_privilege('onsigning_app', 'public.' || t.tablename, 'SELECT') AS can_select,
                has_table_privilege('onsigning_app', 'public.' || t.tablename, 'INSERT') AS can_insert,
                has_table_privilege('onsigning_app', 'public.' || t.tablename, 'UPDATE') AS can_update,
                has_table_privilege('onsigning_app', 'public.' || t.tablename, 'DELETE') AS can_delete,
                has_table_privilege('onsigning_app', 'public.' || t.tablename, 'TRUNCATE') AS can_truncate,
                has_table_privilege('onsigning_app', 'public.' || t.tablename, 'REFERENCES') AS can_references,
                has_table_privilege('onsigning_app', 'public.' || t.tablename, 'TRIGGER') AS can_trigger
            FROM (VALUES {table_values}) AS t(tablename);
            """
            app_rows = await conn.fetch(app_query)
            for ar in app_rows:
                table = ar["tablename"]
                if table != "audit_logs":
                    if not (ar["can_select"] and ar["can_insert"] and ar["can_update"] and ar["can_delete"]):
                        verification_failures.append(f"onsigning_app missing required CRUD privilege on public.{table}.")
                    if ar["can_truncate"]:
                        verification_failures.append(f"onsigning_app unexpectedly has TRUNCATE privilege on public.{table}.")
                    if ar["can_references"]:
                        verification_failures.append(f"onsigning_app unexpectedly has REFERENCES privilege on public.{table}.")
                    if ar["can_trigger"]:
                        verification_failures.append(f"onsigning_app unexpectedly has TRIGGER privilege on public.{table}.")
                else:
                    if not (ar["can_select"] and ar["can_insert"]):
                        verification_failures.append("onsigning_app missing SELECT/INSERT on append-only public.audit_logs.")
                    if ar["can_update"] or ar["can_delete"] or ar["can_truncate"] or ar["can_references"] or ar["can_trigger"]:
                        verification_failures.append(
                            "onsigning_app has forbidden UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER on append-only public.audit_logs."
                        )

            # 5. Verify Default Privileges for role postgres (Schema public and Global scope)
            # Tracks table defaults ('r') and sequence defaults ('S') independently.
            # Reasserts required grants for onsigning_app and confirms no prohibited grants
            # exist for anon, authenticated, service_role, or PUBLIC across both scopes.

            # 5a. Inspect pg_default_acl entries
            default_acl_entries_query = """
            SELECT
                d.defaclobjtype::text AS defaclobjtype,
                d.defaclnamespace,
                CASE WHEN d.defaclnamespace = 0 THEN 'global' ELSE 'public' END AS scope,
                d.defaclacl::text AS defaclacl_str
            FROM pg_default_acl d
            WHERE d.defaclrole = (SELECT oid FROM pg_roles WHERE rolname = 'postgres')
              AND d.defaclobjtype IN ('r', 'S')
              AND (d.defaclnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public') OR d.defaclnamespace = 0);
            """
            default_entries = await conn.fetch(default_acl_entries_query)
            public_table_default_found = False
            public_sequence_default_found = False
            for entry in default_entries:
                objtype = entry["defaclobjtype"]
                if isinstance(objtype, (bytes, bytearray)):
                    objtype = objtype.decode("ascii", errors="replace")
                scope = entry["scope"]
                if scope == "public":
                    if objtype == "r":
                        public_table_default_found = True
                    elif objtype == "S":
                        public_sequence_default_found = True

            if is_migration_0004:
                if not public_table_default_found:
                    verification_failures.append(
                        "Default ACL for role postgres in schema public (tables) was not found in pg_default_acl."
                    )
                if not public_sequence_default_found:
                    verification_failures.append(
                        "Default ACL for role postgres in schema public (sequences) was not found in pg_default_acl."
                    )

            # 5b. Inspect exploded default ACL privileges via aclexplode
            default_acl_exploded_query = """
            SELECT
                d.defaclobjtype::text AS defaclobjtype,
                CASE WHEN d.defaclnamespace = 0 THEN 'global' ELSE 'public' END AS scope,
                a.grantee,
                COALESCE(gr.rolname, 'PUBLIC') AS grantee_name,
                a.privilege_type,
                a.is_grantable
            FROM pg_default_acl d
            CROSS JOIN LATERAL aclexplode(d.defaclacl) a
            LEFT JOIN pg_roles gr ON gr.oid = a.grantee
            WHERE d.defaclrole = (SELECT oid FROM pg_roles WHERE rolname = 'postgres')
              AND d.defaclobjtype IN ('r', 'S')
              AND (d.defaclnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public') OR d.defaclnamespace = 0);
            """
            default_exploded_rows = await conn.fetch(default_acl_exploded_query)

            app_public_table_privs = set()
            app_public_seq_privs = set()

            for drow in default_exploded_rows:
                objtype = drow["defaclobjtype"]
                if isinstance(objtype, (bytes, bytearray)):
                    objtype = objtype.decode("ascii", errors="replace")
                obj_desc = "tables" if objtype == "r" else "sequences"
                scope = drow["scope"]
                grantee_name = drow["grantee_name"]
                grantee_oid = drow["grantee"]
                priv = drow["privilege_type"]
                is_grantable = drow["is_grantable"]

                # 5c. Check for prohibited grants to PostgREST roles or PUBLIC
                if grantee_name in ("anon", "authenticated", "service_role") or grantee_name == "PUBLIC" or grantee_oid == 0:
                    verification_failures.append(
                        f"Default ACL for role postgres in {scope} ({obj_desc}) contains prohibited grant '{priv}' to '{grantee_name}'."
                    )

                # 5d. Check unexpected WITH GRANT OPTION
                if is_grantable and grantee_name != "postgres":
                    verification_failures.append(
                        f"Default ACL for role postgres in {scope} ({obj_desc}) unexpectedly allows GRANT OPTION for '{grantee_name}' on '{priv}'."
                    )

                # Collect onsigning_app privileges in schema public
                if scope == "public" and grantee_name == "onsigning_app":
                    if objtype == "r":
                        app_public_table_privs.add(priv)
                    elif objtype == "S":
                        app_public_seq_privs.add(priv)

            # 5e. Assert onsigning_app required default grants for Migration 0004
            if is_migration_0004:
                for req in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    if req not in app_public_table_privs:
                        verification_failures.append(
                            f"Default ACL for role postgres in schema public (tables) missing required grant '{req}' to onsigning_app."
                        )
                for forb in ("TRUNCATE", "REFERENCES", "TRIGGER"):
                    if forb in app_public_table_privs:
                        verification_failures.append(
                            f"Default ACL for role postgres in schema public (tables) unexpectedly grants '{forb}' to onsigning_app."
                        )

                for req in ("USAGE", "SELECT"):
                    if req not in app_public_seq_privs:
                        verification_failures.append(
                            f"Default ACL for role postgres in schema public (sequences) missing required grant '{req}' to onsigning_app."
                        )
                for forb in ("UPDATE",):
                    if forb in app_public_seq_privs:
                        verification_failures.append(
                            f"Default ACL for role postgres in schema public (sequences) unexpectedly grants '{forb}' to onsigning_app."
                        )
        except Exception as ver_err:
            sanitized_err = sanitize_log(ver_err)
            print(
                "[CRITICAL ALERT] Post-commit security verification encountered an unexpected query error!",
                file=sys.stderr,
            )
            print(
                f"[CRITICAL ALERT] Verification exception: {sanitized_err}",
                file=sys.stderr,
            )
            print(
                "[CRITICAL ALERT] The migration transaction has ALREADY COMMITTED and was NOT rolled back.",
                file=sys.stderr,
            )
            print(
                "[CRITICAL ALERT] Do NOT attempt automated compensating migrations. Manual DBA inspection is required.",
                file=sys.stderr,
            )
            return EXIT_VERIFICATION_FAILURE

        if verification_failures:
            print(
                "[CRITICAL ALERT] Post-commit security verification FAILED!",
                file=sys.stderr,
            )
            print(
                "[CRITICAL ALERT] The migration transaction has ALREADY COMMITTED and was NOT rolled back.",
                file=sys.stderr,
            )
            print("[CRITICAL ALERT] Invariant violations detected:", file=sys.stderr)
            for f in verification_failures:
                print(f"  - {f}", file=sys.stderr)
            print(
                "[CRITICAL ALERT] Do NOT attempt automated compensating migrations. Manual DBA inspection is required.",
                file=sys.stderr,
            )
            return EXIT_VERIFICATION_FAILURE

        validated_migration_name = config.get("migration_filename", MIGRATION_FILENAME)
        print("[VERIFICATION SUCCESS] All post-commit security invariants verified.")
        print(f"[RUNNER SUCCESS] Migration {validated_migration_name} successfully applied and verified.")
        return EXIT_SUCCESS

    finally:
        if owns_conn and conn is not None:
            await conn.close()


def check_policy_0005_equivalence(policy_row: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Evaluates whether an existing policy on public.organizations strictly matches
    the approved security semantics of 0005_organization_discovery_rls.sql.
    
    Verifies:
    1. Command: Must be SELECT-only (or catalog 'r').
    2. Permissiveness: Must be PERMISSIVE.
    3. Roles: Must apply to 'public' (universal role scope).
    4. Write safety: WITH CHECK must be empty/None.
    5. Structural integrity: Entire USING expression must be an EXISTS (SELECT 1 FROM ...)
       subquery against public.organization_memberships without outer disjunctions (OR).
    6. Correlation: Must correlate organization_id = organizations.id.
    7. User isolation: Must check user_id against NULLIF(current_setting('app.current_user_id', true), '')::uuid.
    """
    cmd = str(policy_row.get("cmd", "")).upper()
    if cmd not in ("SELECT", "R"):
        return False, f"Expected command SELECT, found '{cmd}'"

    permissive = str(policy_row.get("permissive", "")).upper()
    if permissive != "PERMISSIVE":
        return False, f"Expected PERMISSIVE policy, found '{permissive}'"

    roles = policy_row.get("roles")
    if roles is not None:
        if isinstance(roles, str):
            roles_list = [roles.strip().lower()]
        else:
            roles_list = [str(r).strip().lower() for r in roles]
        if "public" not in roles_list:
            return False, f"Expected policy to apply to 'public', found {roles_list}"

    with_check = policy_row.get("with_check")
    if with_check and str(with_check).strip() not in ("", "none", "null"):
        return False, f"Unexpected WITH CHECK on SELECT policy: '{with_check}'"

    qual = str(policy_row.get("qual", "")).strip()
    q = re.sub(r"\s+", " ", qual.lower())

    # Strip outer parentheses if present in deparsed catalog expression
    while q.startswith("(") and q.endswith(")"):
        inner = q[1:-1].strip()
        if inner.startswith("exists"):
            q = inner
        else:
            break

    if not q.startswith("exists (select 1 from "):
        return False, "USING expression must be an EXISTS (SELECT 1 FROM ...) subquery"

    if " or " in q:
        return False, "Disjunction OR is strictly prohibited in discovery policy"

    # Match relation and alias
    match = re.search(r"from\s+(?:public\.)?organization_memberships(?:\s+as)?\s+(\w+)", q)
    if not match:
        return False, "Subquery must select from organization_memberships"

    alias = match.group(1)

    # Check organization_id correlation
    has_org_corr = (
        f"{alias}.organization_id = organizations.id" in q
        or f"organizations.id = {alias}.organization_id" in q
    )
    if not has_org_corr:
        return False, f"Missing correlation: '{alias}.organization_id = organizations.id'"

    # Check user_id filter with fail-closed NULLIF
    user_pattern = (
        rf"{alias}\.user_id\s*=\s*\(?\s*nullif\(\s*current_setting\(\s*'app\.current_user_id'(?:::text)?,\s*true\s*\),\s*''(?:::text)?\s*\)(?:::uuid)?"
    )
    if not re.search(user_pattern, q):
        return False, "Missing fail-closed user_id check against NULLIF(current_setting('app.current_user_id', true), '')::uuid"

    return True, "Policy matches approved 0005 semantics."


async def _dry_run_inspect_0005(conn: Any, config: Dict[str, Any]) -> int:
    """
    Executes read-only catalog queries to inspect prerequisites and policy state
    for 0005_organization_discovery_rls.sql.
    """
    print("[DRY-RUN] Inspecting required tables and RLS enforcement...")

    # 1. Inspect required tables and RLS state
    table_rows = await conn.fetch(
        """
        SELECT
            c.relname AS table_name,
            c.relrowsecurity AS rls_enabled,
            c.relforcerowsecurity AS rls_forced
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relname IN ('organizations', 'organization_memberships');
        """
    )

    found_tables = {r["table_name"]: r for r in table_rows}

    for req_table in ("organizations", "organization_memberships"):
        if req_table not in found_tables:
            print(f"[DRY-RUN BLOCKED] Prerequisite table missing: 'public.{req_table}' does not exist.", file=sys.stderr)
            return EXIT_PREFLIGHT_FAILURE

        r_info = found_tables[req_table]
        if not r_info["rls_enabled"]:
            print(f"[DRY-RUN BLOCKED] RLS disabled on table 'public.{req_table}'. Must have ENABLE ROW LEVEL SECURITY.", file=sys.stderr)
            return EXIT_PREFLIGHT_FAILURE

        if not r_info["rls_forced"]:
            print(f"[DRY-RUN BLOCKED] FORCE RLS disabled on table 'public.{req_table}'. Must have FORCE ROW LEVEL SECURITY.", file=sys.stderr)
            return EXIT_PREFLIGHT_FAILURE

    print("[DRY-RUN] Verified tables 'organizations' and 'organization_memberships' exist with ENABLE and FORCE RLS.")

    # 2. Inspect existing policies
    policy_rows = await conn.fetch(
        """
        SELECT
            tablename,
            policyname,
            cmd,
            permissive,
            roles::text[] AS roles,
            qual,
            with_check
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename IN ('organizations', 'organization_memberships');
        """
    )

    policies_by_table: Dict[str, Dict[str, Any]] = {"organizations": {}, "organization_memberships": {}}
    for p in policy_rows:
        t_name = p["tablename"]
        if t_name in policies_by_table:
            policies_by_table[t_name][p["policyname"]] = p

    # 3. Check prerequisite policies (from migrations 0001-0004)
    org_policies = policies_by_table["organizations"]
    mem_policies = policies_by_table["organization_memberships"]

    if "tenant_isolation_organizations" not in org_policies:
        print("[DRY-RUN BLOCKED] Prerequisite policy 'tenant_isolation_organizations' missing on public.organizations.", file=sys.stderr)
        return EXIT_PREFLIGHT_FAILURE

    if "tenant_isolation_memberships" not in mem_policies:
        print("[DRY-RUN BLOCKED] Prerequisite policy 'tenant_isolation_memberships' missing on public.organization_memberships.", file=sys.stderr)
        return EXIT_PREFLIGHT_FAILURE

    print("[DRY-RUN] Verified prerequisite tenant isolation policies exist.")

    # 4. Check target policy state: user_discovery_organizations
    target_policy = org_policies.get("user_discovery_organizations")
    if target_policy is None:
        print("\n================================================================================")
        print("[DRY-RUN STATUS] Migration 0005_organization_discovery_rls.sql is: PENDING")
        print("================================================================================")
        print("[DRY-RUN] Prerequisite tables:   PRESENT (organizations, organization_memberships)")
        print("[DRY-RUN] RLS state:             ENABLED & FORCED on both tables")
        print("[DRY-RUN] Prerequisite policies: PRESENT (tenant_isolation_organizations, tenant_isolation_memberships)")
        print("[DRY-RUN] Discovery policy:      NOT APPLIED (user_discovery_organizations does not exist)")
        print("[DRY-RUN] Conclusion:            Ready to apply. Zero schema or policy changes made.")
        return EXIT_SUCCESS

    # Target policy exists: perform semantic equivalence check
    is_match, match_reason = check_policy_0005_equivalence(target_policy)
    cmd = str(target_policy.get("cmd", "")).upper()
    with_check = target_policy.get("with_check")

    if is_match:
        print("\n================================================================================")
        print("[DRY-RUN STATUS] Migration 0005_organization_discovery_rls.sql is: ALREADY APPLIED")
        print("================================================================================")
        print("[DRY-RUN] Policy 'user_discovery_organizations' already exists on public.organizations.")
        print("[DRY-RUN] Existing definition matches approved 0005 semantics:")
        print(f"          - Command: {cmd}")
        print(f"          - Permissive: {target_policy.get('permissive')}")
        print(f"          - USING: {target_policy.get('qual')}")
        print("[DRY-RUN] Conclusion:            Migration is already applied. No further action needed.")
        return EXIT_SUCCESS
    else:
        print("\n================================================================================")
        print("[DRY-RUN STATUS] Migration 0005_organization_discovery_rls.sql is: DRIFTED", file=sys.stderr)
        print("================================================================================")
        print(f"[DRY-RUN ALERT] Policy 'user_discovery_organizations' exists but definition differs from approved migration:", file=sys.stderr)
        print(f"          - Discrepancy: {match_reason}", file=sys.stderr)
        print(f"          - Command: {cmd} (expected: SELECT)", file=sys.stderr)
        print(f"          - USING: {target_policy.get('qual')}", file=sys.stderr)
        print(f"          - WITH CHECK: {with_check} (expected: None)", file=sys.stderr)
        return EXIT_VERIFICATION_FAILURE


async def execute_dry_run(
    config: Dict[str, Any],
    conn_override: Optional[Any] = None,
) -> int:
    """
    Executes a strictly read-only dry-run against the target database catalog.
    Enforces that NO migration SQL is executed and NO database state is modified.
    """
    # Deferred asyncpg import
    try:
        import asyncpg
    except ImportError:
        print("[PRE-FLIGHT ERROR] Required driver 'asyncpg' is not installed.", file=sys.stderr)
        return EXIT_PREFLIGHT_FAILURE

    if config.get("allow_insecure_local_tls", False):
        if config["hostname"] not in ("localhost", "127.0.0.1", "::1"):
            print("[PRE-FLIGHT ERROR] Insecure TLS is strictly forbidden for non-localhost targets.", file=sys.stderr)
            return EXIT_PREFLIGHT_FAILURE
        ssl_ctx = None
    else:
        print("[DRY-RUN] Initializing TLS secure context (TLS 1.2+ minimum)...")
        try:
            ssl_ctx = build_ssl_context(config.get("ca_path"))
        except Exception as e:
            print(f"[PRE-FLIGHT ERROR] Failed to construct TLS context: {sanitize_log(e)}", file=sys.stderr)
            return EXIT_PREFLIGHT_FAILURE

    conn = conn_override
    owns_conn = False

    if conn is None:
        print(f"[DRY-RUN] Connecting to endpoint {config['hostname']}:{config['port']}/{config['db_name']} in read-only mode...")
        try:
            conn = await asyncpg.connect(
                dsn=config["dsn"],
                ssl=ssl_ctx,
                statement_cache_size=0,
                command_timeout=config["timeout"],
            )
            owns_conn = True
        except Exception as e:
            print(f"[PRE-FLIGHT ERROR] Database connection failed: {sanitize_log(e)}", file=sys.stderr)
            return EXIT_PREFLIGHT_FAILURE

    try:
        # =========================================================================
        # SAFETY BOUNDARY: STRICTLY READ-ONLY DRY-RUN EXECUTION
        # - This path NEVER calls conn.execute(config["migration_sql"]).
        # - This path NEVER executes DDL, DML, or schema alterations.
        # - Transaction is opened with readonly=True (PostgreSQL enforces no writes).
        # - Inspects catalogs only to determine readiness of target migration.
        # =========================================================================
        print("[DRY-RUN] Establishing read-only transaction for catalog inspection...")
        async with conn.transaction(readonly=True):
            # 1. Server Identity Validation
            row = await conn.fetchrow(
                "SELECT current_database() AS db_name, current_user AS user_name, inet_server_addr()::text AS server_ip;"
            )
            if not row:
                print("[DRY-RUN BLOCKED] Server identity query returned no rows.", file=sys.stderr)
                return EXIT_PREFLIGHT_FAILURE

            server_db = row["db_name"]
            server_user = row["user_name"]

            if server_user != "postgres":
                print(
                    f"[DRY-RUN BLOCKED] Identity check failed: current_user is '{server_user}', expected 'postgres'.",
                    file=sys.stderr,
                )
                return EXIT_PREFLIGHT_FAILURE

            if server_db != config["db_name"]:
                print(
                    f"[DRY-RUN BLOCKED] Database check failed: current_database is '{server_db}', expected '{config['db_name']}'.",
                    file=sys.stderr,
                )
                return EXIT_PREFLIGHT_FAILURE

            print(f"[DRY-RUN] Verified database '{server_db}' and execution role '{server_user}'.")
            print(f"[DRY-RUN] Target migration: {config['migration_filename']} (SHA-256: {config['migration_sha256']})")

            # Dispatch to migration-specific inspection
            if config["migration_filename"] == "0005_organization_discovery_rls.sql":
                return await _dry_run_inspect_0005(conn, config)
            else:
                print(f"[DRY-RUN] Generic pre-flight catalog verification passed for {config['migration_filename']}.")
                return EXIT_SUCCESS

    except Exception as exc:
        print(f"[DRY-RUN ERROR] Catalog inspection failed: {sanitize_log(exc)}", file=sys.stderr)
        return EXIT_PREFLIGHT_FAILURE
    finally:
        if owns_conn and conn is not None:
            await conn.close()


def main(args: Optional[List[str]] = None) -> int:
    """Entry point for the migration runner CLI."""
    parsed_args = parse_args(args)
    ok, msg, config = validate_preflight_config(parsed_args)
    if not ok:
        print(f"[PRE-FLIGHT ERROR] {msg}", file=sys.stderr)
        return EXIT_PREFLIGHT_FAILURE

    assert config is not None

    # Strict architectural boundary: dry-run never enters execute_and_verify
    if parsed_args.dry_run:
        return asyncio.run(execute_dry_run(config))

    return asyncio.run(execute_and_verify(config))


if __name__ == "__main__":
    sys.exit(main())
