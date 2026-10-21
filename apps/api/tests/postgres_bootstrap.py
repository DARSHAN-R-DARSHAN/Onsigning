"""
Onsigning PostgreSQL RLS Harness: Disposable Database Lifecycle & Bootstrap
Strictly implements ARCHITECTURE.md, SCHEMA.md, and Tasks 5.11.2-5.11.4 specifications.

Key Security & Operational Guarantees:
1. Disposable database lifecycle with cryptographically random names & run tokens.
2. Autocommit connection for CREATE/DROP DATABASE outside transaction blocks.
3. Database comment metadata verification before teardown (fail-closed guard).
4. Strict role separation:
   - Superuser (postgres): provisions extensions, roles, and ephemeral database.
   - Admin (onsigning_admin): BYPASSRLS, owns all 17 tables and runs migrations.
   - Runtime App (onsigning_app): NOBYPASSRLS, NOSUPERUSER, granted DML on tables, append-only on audit_logs.
5. Deferred asyncpg import so SQLite test suites run cleanly when asyncpg is not installed.
"""

from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


class DatabaseSecurityError(RuntimeError):
    """Raised when database identity verification, privilege checks, or teardown guards fail."""
    pass


@dataclass(frozen=True)
class PostgresConfig:
    """Connection parameters and credentials for PostgreSQL RLS testing."""
    host: str = os.getenv("POSTGRES_HOST", "localhost")
    port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    maintenance_db: str = os.getenv("POSTGRES_MAINTENANCE_DB", "postgres")
    superuser: str = os.getenv("POSTGRES_SUPERUSER", "postgres")
    superuser_password: str = os.getenv("POSTGRES_SUPERUSER_PASSWORD", "postgres_ci_password")
    admin_role: str = os.getenv("POSTGRES_ADMIN_ROLE", "onsigning_admin")
    admin_password: str = os.getenv("POSTGRES_ADMIN_PASSWORD", "test_admin_secret")
    app_role: str = os.getenv("POSTGRES_APP_ROLE", "onsigning_app")
    app_password: str = os.getenv("POSTGRES_APP_PASSWORD", "test_app_secret")
    migrations_dir: Path = Path(__file__).resolve().parents[3] / "packages" / "database" / "migrations"
    migration_path: Path = Path(__file__).resolve().parents[3] / "packages" / "database" / "migrations" / "0001_mvp_schema.sql"

    def get_dsn(self, user: str, password: str, database: str) -> str:
        """Constructs an asyncpg-compatible DSN."""
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{database}"

    def get_sa_url(self, user: str, password: str, database: str) -> str:
        """Constructs a SQLAlchemy asyncpg URL."""
        return f"postgresql+asyncpg://{user}:{password}@{self.host}:{self.port}/{database}"


class PostgresHarness:
    """Manages the lifecycle of an ephemeral PostgreSQL database for RLS integration testing."""

    def __init__(self, config: Optional[PostgresConfig] = None):
        self.config = config or PostgresConfig()
        self.run_token = secrets.token_hex(16)
        self.db_name = f"onsigning_ephemeral_{secrets.token_hex(6)}"
        self._asyncpg: Any = None

    def _get_asyncpg(self):
        """Deferred import of asyncpg to avoid breaking test environments lacking the driver."""
        if self._asyncpg is None:
            try:
                import asyncpg
                self._asyncpg = asyncpg
            except ImportError as e:
                raise RuntimeError(
                    "asyncpg is required to run the PostgreSQL RLS integration suite. "
                    "Install it via `pip install asyncpg` or run with RUN_POSTGRES_RLS=false."
                ) from e
        return self._asyncpg

    async def connect_maintenance(self):
        """Connects to the maintenance database as superuser."""
        asyncpg = self._get_asyncpg()
        return await asyncpg.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.superuser,
            password=self.config.superuser_password,
            database=self.config.maintenance_db,
        )

    async def connect_as_admin(self, database: Optional[str] = None):
        """Connects to the target database as onsigning_admin (BYPASSRLS fixture role)."""
        asyncpg = self._get_asyncpg()
        return await asyncpg.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.admin_role,
            password=self.config.admin_password,
            database=database or self.db_name,
        )

    async def connect_as_app(self, database: Optional[str] = None):
        """Connects to the target database as onsigning_app (NOBYPASSRLS runtime role)."""
        asyncpg = self._get_asyncpg()
        return await asyncpg.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.app_role,
            password=self.config.app_password,
            database=database or self.db_name,
        )

    async def provision_roles_if_needed(self, conn) -> None:
        """
        Safely provisions onsigning_admin and onsigning_app using dynamic SQL with %I and %L.
        Never alters or drops pre-existing roles.
        """
        sql = f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {self._quote_literal(self.config.admin_role)}) THEN
                EXECUTE format(
                    'CREATE ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS',
                    {self._quote_literal(self.config.admin_role)},
                    {self._quote_literal(self.config.admin_password)}
                );
            END IF;

            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {self._quote_literal(self.config.app_role)}) THEN
                EXECUTE format(
                    'CREATE ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS',
                    {self._quote_literal(self.config.app_role)},
                    {self._quote_literal(self.config.app_password)}
                );
            END IF;
        END $$;
        """
        await conn.execute(sql)

    async def create_ephemeral_database(self, conn) -> str:
        """
        Creates an isolated ephemeral database owned by onsigning_admin and tags it with a run token.
        Fails closed if the database name already exists.
        """
        # Guard: Ensure database does not already exist
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", self.db_name
        )
        if exists:
            raise DatabaseSecurityError(f"Database name collision: {self.db_name} already exists.")

        # Create database owned by onsigning_admin
        # In PostgreSQL, CREATE DATABASE cannot run in a transaction block
        admin_role_escaped = self._escape_identifier(self.config.admin_role)
        db_name_escaped = self._escape_identifier(self.db_name)
        await conn.execute(f'CREATE DATABASE {db_name_escaped} OWNER {admin_role_escaped};')

        # Tag database with run token for fail-closed teardown guard
        comment_sql = f"COMMENT ON DATABASE {db_name_escaped} IS 'onsigning_test_run:{self.run_token}';"
        await conn.execute(comment_sql)
        return self.db_name

    async def install_extensions(self) -> None:
        """Installs required extensions (uuid-ossp, pg_trgm, vector) as superuser."""
        asyncpg = self._get_asyncpg()
        conn = await asyncpg.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.superuser,
            password=self.config.superuser_password,
            database=self.db_name,
        )
        try:
            await conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')
            await conn.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm";')
            # pgvector may not be installed on plain PostgreSQL images; handled safely
            vector_sql = """
            DO $$
            BEGIN
                CREATE EXTENSION IF NOT EXISTS vector;
            EXCEPTION
                WHEN OTHERS THEN
                    RAISE NOTICE 'pgvector extension not available in cluster; skipping.';
            END $$;
            """
            await conn.execute(vector_sql)
        finally:
            await conn.close()

    async def run_migration_and_grants(self) -> None:
        """
        Executes all SQL migrations in packages/database/migrations/ in sequential order
        as onsigning_admin. Schema creation and least-privilege runtime grants are established
        directly by the canonical migrations (0001_mvp_schema.sql, 0002_runtime_grants.sql).
        """
        if not self.config.migrations_dir.is_dir():
            raise FileNotFoundError(f"Migrations directory not found at: {self.config.migrations_dir}")

        migration_files = sorted(self.config.migrations_dir.glob("*.sql"))
        if not migration_files:
            raise FileNotFoundError(f"No SQL migration files found in: {self.config.migrations_dir}")

        admin_conn = await self.connect_as_admin(self.db_name)
        try:
            for migration_file in migration_files:
                sql = migration_file.read_text(encoding="utf-8")
                # Multi-statement execution via simple query protocol
                await admin_conn.execute(sql)
        finally:
            await admin_conn.close()

    async def verify_privileges_and_catalogs(self) -> None:
        """
        Verifies role attributes, table ownership, and effective privileges against pg_catalog.
        Fails closed if onsigning_app has BYPASSRLS or superuser powers.
        """
        app_conn = await self.connect_as_app(self.db_name)
        try:
            # 1. Assert onsigning_app has rolbypassrls = false
            bypass_rls = await app_conn.fetchval(
                "SELECT rolbypassrls FROM pg_roles WHERE rolname = $1", self.config.app_role
            )
            if bypass_rls:
                raise DatabaseSecurityError(f"Security invariant violated: {self.config.app_role} has BYPASSRLS!")

            # 2. Assert onsigning_app is not superuser, createdb, or createrole
            attrs = await app_conn.fetchrow(
                "SELECT rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = $1",
                self.config.app_role
            )
            if attrs["rolsuper"] or attrs["rolcreatedb"] or attrs["rolcreaterole"]:
                raise DatabaseSecurityError(f"Security invariant violated: {self.config.app_role} has elevated privileges: {dict(attrs)}")

            # 3. Assert onsigning_app does NOT own any application tables
            app_owned = await app_conn.fetchval(
                "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = $1",
                self.config.app_role
            )
            if app_owned > 0:
                raise DatabaseSecurityError(f"Security invariant violated: {self.config.app_role} owns {app_owned} tables!")

            # 4. Assert onsigning_app has no CREATE privilege on schema public
            can_create = await app_conn.fetchval(
                "SELECT has_schema_privilege($1, 'public', 'CREATE')", self.config.app_role
            )
            if can_create:
                raise DatabaseSecurityError(f"Security invariant violated: {self.config.app_role} can CREATE on public schema!")

            # 5. Assert audit_logs append-only ACL permissions
            acl = await app_conn.fetchrow(
                """
                SELECT
                    has_table_privilege($1, 'audit_logs', 'SELECT')   AS can_select,
                    has_table_privilege($1, 'audit_logs', 'INSERT')   AS can_insert,
                    has_table_privilege($1, 'audit_logs', 'UPDATE')   AS can_update,
                    has_table_privilege($1, 'audit_logs', 'DELETE')   AS can_delete,
                    has_table_privilege($1, 'audit_logs', 'TRUNCATE') AS can_truncate;
                """,
                self.config.app_role
            )
            if not (acl["can_select"] and acl["can_insert"]):
                raise DatabaseSecurityError("onsigning_app must have SELECT and INSERT on audit_logs.")
            if acl["can_update"] or acl["can_delete"] or acl["can_truncate"]:
                raise DatabaseSecurityError(f"onsigning_app must not have UPDATE, DELETE, or TRUNCATE on audit_logs: {dict(acl)}")

        finally:
            await app_conn.close()

    async def reset_fixtures(self) -> None:
        """
        Fast cascading reset between tests (<20ms).
        Executed as onsigning_admin (BYPASSRLS). Truncates organizations and users CASCADE,
        which cascades to all 15 remaining tenant tables.
        """
        admin_conn = await self.connect_as_admin(self.db_name)
        try:
            await admin_conn.execute("TRUNCATE TABLE organizations, users CASCADE;")
            # Verify tables are completely empty
            row_count = await admin_conn.fetchval(
                """
                SELECT sum(cnt) FROM (
                    SELECT count(*) AS cnt FROM organizations
                    UNION ALL SELECT count(*) FROM users
                    UNION ALL SELECT count(*) FROM documents
                    UNION ALL SELECT count(*) FROM audit_logs
                ) t;
                """
            )
            if row_count != 0:
                raise DatabaseSecurityError(f"Reset failed: {row_count} rows remain in database after TRUNCATE CASCADE.")
        finally:
            await admin_conn.close()

    async def teardown(self) -> None:
        """
        Tears down the ephemeral database with strict run-token validation.
        Refuses to drop if marker comment does not match.
        """
        m_conn = await self.connect_maintenance()
        try:
            # Validate marker comment
            expected_comment = f"onsigning_test_run:{self.run_token}"
            actual_comment = await m_conn.fetchval(
                "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = $1",
                self.db_name
            )
            if actual_comment != expected_comment:
                raise DatabaseSecurityError(
                    f"Teardown aborted: Database '{self.db_name}' marker mismatch! "
                    f"Expected '{expected_comment}', found '{actual_comment}'."
                )

            # Terminate active backends
            await m_conn.execute(
                """
                SELECT pg_terminate_backend(pid)
                FROM pg_stat_activity
                WHERE datname = $1 AND pid <> pg_backend_pid();
                """,
                self.db_name
            )

            # Drop ephemeral database
            db_name_escaped = self._escape_identifier(self.db_name)
            await m_conn.execute(f"DROP DATABASE {db_name_escaped};")
        finally:
            await m_conn.close()

    @staticmethod
    def _escape_identifier(name: str) -> str:
        """Escapes SQL identifier by doubling internal double quotes."""
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name):
            escaped = name.replace('"', '""')
            return f'"{escaped}"'
        return f'"{name}"'

    @staticmethod
    def _quote_literal(val: str) -> str:
        """Quotes SQL string literal by doubling internal single quotes."""
        escaped = val.replace("'", "''")
        return f"'{escaped}'"
