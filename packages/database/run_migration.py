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

MIGRATION_FILENAME = "0003_security_hardening.sql"


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
        "--timeout",
        type=float,
        default=60.0,
        help="Command execution timeout in seconds (default 60s).",
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
    repo_root = Path(__file__).resolve().parent
    migration_file = repo_root / "migrations" / MIGRATION_FILENAME
    if not migration_file.is_file():
        return (
            False,
            f"Migration file missing at resolved path: {migration_file}",
            None,
        )

    try:
        sql_bytes = migration_file.read_bytes()
        sha256 = hashlib.sha256(sql_bytes).hexdigest()
        sql_text = sql_bytes.decode("utf-8")
    except Exception as e:
        return False, f"Failed to read migration file: {sanitize_log(e)}", None

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
        "migration_file": migration_file,
        "migration_sha256": sha256,
        "migration_sql": sql_text,
        "ca_path": args.ca_path,
        "timeout": args.timeout,
        "allow_insecure_local_tls": allow_insecure_local_tls,
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
            f"[MIGRATION] Loaded {MIGRATION_FILENAME} (SHA-256: {config['migration_sha256']})."
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
            print(
                "[MIGRATION FAILED] Transaction automatically rolled back. Database remains in pre-migration state.",
                file=sys.stderr,
            )
            return EXIT_MIGRATION_FAILURE

        # ----------------------------------------------------------------------
        # POST-COMMIT SECURITY VERIFICATION
        # ----------------------------------------------------------------------
        print("[VERIFICATION] Running post-commit read-only security assertions...")
        verification_failures: List[str] = []

        try:
            # 1. Verify Effective Table Privileges on public.users & public.audit_logs
            # anon, authenticated, service_role must have NO privileges
            roles_query = """
            SELECT
                r.rolname,
                t.tablename,
                has_table_privilege(r.rolname, 'public.' || t.tablename, 'SELECT') AS can_select,
                has_table_privilege(r.rolname, 'public.' || t.tablename, 'INSERT') AS can_insert,
                has_table_privilege(r.rolname, 'public.' || t.tablename, 'UPDATE') AS can_update,
                has_table_privilege(r.rolname, 'public.' || t.tablename, 'DELETE') AS can_delete,
                has_table_privilege(r.rolname, 'public.' || t.tablename, 'TRUNCATE') AS can_truncate
            FROM (VALUES ('anon'), ('authenticated'), ('service_role')) AS r(rolname)
            CROSS JOIN (VALUES ('users'), ('audit_logs')) AS t(tablename)
            WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r.rolname);
            """
            rows = await conn.fetch(roles_query)
            for r in rows:
                rolname = r["rolname"]
                table = r["tablename"]
                for priv in ("select", "insert", "update", "delete", "truncate"):
                    if r[f"can_{priv}"]:
                        verification_failures.append(
                            f"PostgREST role '{rolname}' has unexpected effective privilege '{priv.upper()}' on public.{table}."
                        )

            # 2. Verify onsigning_app runtime privileges
            app_query = """
            SELECT
                'users' AS tablename,
                has_table_privilege('onsigning_app', 'public.users', 'SELECT') AS can_select,
                has_table_privilege('onsigning_app', 'public.users', 'INSERT') AS can_insert,
                has_table_privilege('onsigning_app', 'public.users', 'UPDATE') AS can_update,
                has_table_privilege('onsigning_app', 'public.users', 'DELETE') AS can_delete,
                has_table_privilege('onsigning_app', 'public.users', 'TRUNCATE') AS can_truncate
            UNION ALL
            SELECT
                'audit_logs' AS tablename,
                has_table_privilege('onsigning_app', 'public.audit_logs', 'SELECT') AS can_select,
                has_table_privilege('onsigning_app', 'public.audit_logs', 'INSERT') AS can_insert,
                has_table_privilege('onsigning_app', 'public.audit_logs', 'UPDATE') AS can_update,
                has_table_privilege('onsigning_app', 'public.audit_logs', 'DELETE') AS can_delete,
                has_table_privilege('onsigning_app', 'public.audit_logs', 'TRUNCATE') AS can_truncate;
            """
            app_rows = await conn.fetch(app_query)
            for ar in app_rows:
                table = ar["tablename"]
                if table == "users":
                    if not (ar["can_select"] and ar["can_insert"] and ar["can_update"] and ar["can_delete"]):
                        verification_failures.append("onsigning_app missing required CRUD privilege on public.users.")
                    if ar["can_truncate"]:
                        verification_failures.append("onsigning_app unexpectedly has TRUNCATE privilege on public.users.")
                elif table == "audit_logs":
                    if not (ar["can_select"] and ar["can_insert"]):
                        verification_failures.append("onsigning_app missing SELECT/INSERT on append-only public.audit_logs.")
                    if ar["can_update"] or ar["can_delete"] or ar["can_truncate"]:
                        verification_failures.append(
                            "onsigning_app has forbidden UPDATE/DELETE/TRUNCATE on append-only public.audit_logs."
                        )

            # 3. Verify Default Privileges for role postgres in schema public
            # Distinguish absent default-ACL rows from explicit grants:
            # - If no row exists, built-in system defaults apply (no grants to PostgREST roles).
            # - If rows exist (e.g. objtype 'r' or 'S'), ensure defaclacl contains no grants to anon/authenticated/service_role.
            default_acl_query = """
            SELECT
                defaclobjtype,
                defaclacl::text AS defaclacl_str
            FROM pg_default_acl
            WHERE defaclrole = (SELECT oid FROM pg_roles WHERE rolname = 'postgres')
              AND defaclnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public');
            """
            default_acl_rows = await conn.fetch(default_acl_query)
            for drow in default_acl_rows:
                objtype = drow["defaclobjtype"]
                acl_str = drow["defaclacl_str"] or ""
                # Parse aclitem format: grantee=privileges/grantor (e.g. anon=r/postgres)
                # Ensure no PostgREST role appears as a grantee with privileges
                match = re.search(r"(?:^|[,{])(anon|authenticated|service_role)=([^/]+)/", acl_str)
                if match:
                    rname = match.group(1)
                    privs = match.group(2)
                    verification_failures.append(
                        f"Default ACL for role postgres in public (objtype '{objtype}') contains prohibited grant '{privs}' to '{rname}': {acl_str}"
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

        print("[VERIFICATION SUCCESS] All post-commit security invariants verified.")
        print("[RUNNER SUCCESS] Migration 0003 successfully applied and verified.")
        return EXIT_SUCCESS

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
    return asyncio.run(execute_and_verify(config))


if __name__ == "__main__":
    sys.exit(main())
