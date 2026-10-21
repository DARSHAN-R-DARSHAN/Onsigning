"""
Unit and integration tests for fail-closed production settings validation.
Strictly verifies that:
1. Production environments reject mock, empty, or whitespace-only JWT secrets.
2. Production environments reject keys with inadequate entropy for the selected algorithm.
3. Production environments reject SQLite and require real PostgreSQL.
4. Production-like aliases ('prod', 'staging') enforce identical protections.
5. Development and test environments remain functional with default mock settings.
6. Exception messages never disclose secret values or database credentials.
"""

import pytest
from pydantic import ValidationError

from apps.api.core.config import Settings, DEV_MOCK_JWT_SECRET


# Sample production-ready test credentials
VALID_PROD_POSTGRES_URL = "postgresql+asyncpg://onsigning_app:ci_secret@localhost:5432/onsigning_prod"
VALID_PROD_HS256_SECRET = "production-super-secret-jwt-key-with-sufficient-entropy-32b"
VALID_PROD_HS384_SECRET = "production-super-secret-jwt-key-with-sufficient-entropy-for-hs384-at-least-48-bytes"
VALID_PROD_HS512_SECRET = "production-super-secret-jwt-key-with-sufficient-entropy-for-hs512-at-least-64-bytes-long-key-string"
VALID_PEM_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0Y3y
-----END PUBLIC KEY-----"""


class TestProductionConfigSecurity:
    """Validates fail-closed security invariants for production-like environments."""

    def test_production_rejects_default_mock_secret(self):
        """Production environment must fail closed if SUPABASE_JWT_SECRET equals the dev mock secret."""
        with pytest.raises(ValidationError) as exc_info:
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL=VALID_PROD_POSTGRES_URL,
                SUPABASE_JWT_SECRET=DEV_MOCK_JWT_SECRET
            )
        err = str(exc_info.value)
        assert "Using the default development mock JWT secret" in err
        assert "strictly prohibited" in err

    def test_production_rejects_empty_or_whitespace_secret(self):
        """Production environment must fail closed if SUPABASE_JWT_SECRET is empty or whitespace."""
        for empty_val in ("", "   ", "\t\n"):
            with pytest.raises(ValidationError) as exc_info:
                Settings(
                    ENVIRONMENT="production",
                    DATABASE_URL=VALID_PROD_POSTGRES_URL,
                    SUPABASE_JWT_SECRET=empty_val
                )
            assert "SUPABASE_JWT_SECRET must be explicitly configured" in str(exc_info.value)

    def test_production_rejects_inadequate_key_length_for_hmac(self):
        """Production must enforce algorithm-specific minimum byte length for HMAC keys."""
        # HS256 requires >= 32 bytes (256 bits)
        with pytest.raises(ValidationError) as exc_info_hs256:
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL=VALID_PROD_POSTGRES_URL,
                SUPABASE_JWT_ALGORITHM="HS256",
                SUPABASE_JWT_SECRET="too-short-key-only-24-bytes"  # 26 bytes
            )
        assert "HS256 requires a secret key of at least 32 bytes" in str(exc_info_hs256.value)

        # HS384 requires >= 48 bytes (384 bits)
        with pytest.raises(ValidationError) as exc_info_hs384:
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL=VALID_PROD_POSTGRES_URL,
                SUPABASE_JWT_ALGORITHM="HS384",
                SUPABASE_JWT_SECRET="exactly-35-bytes-secret-string-here"
            )
        assert "HS384 requires a secret key of at least 48 bytes" in str(exc_info_hs384.value)

        # HS512 requires >= 64 bytes (512 bits)
        with pytest.raises(ValidationError) as exc_info_hs512:
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL=VALID_PROD_POSTGRES_URL,
                SUPABASE_JWT_ALGORITHM="HS512",
                SUPABASE_JWT_SECRET="exactly-50-bytes-secret-string-for-this-test-here!"
            )
        assert "HS512 requires a secret key of at least 64 bytes" in str(exc_info_hs512.value)

    def test_production_rejects_unsupported_or_insecure_algorithms(self):
        """Production must reject insecure algorithms such as 'none' or unknown ciphers."""
        for bad_algo in ("none", "NONE", "DES", "MD5", "custom-cipher"):
            with pytest.raises(ValidationError) as exc_info:
                Settings(
                    ENVIRONMENT="production",
                    DATABASE_URL=VALID_PROD_POSTGRES_URL,
                    SUPABASE_JWT_ALGORITHM=bad_algo,
                    SUPABASE_JWT_SECRET=VALID_PROD_HS256_SECRET
                )
            assert "Unsupported or insecure JWT algorithm" in str(exc_info.value)

    def test_production_validates_asymmetric_public_key_format(self):
        """Production asymmetric algorithms (RS256, ES256) require valid PEM public key format."""
        # Non-PEM string fails
        with pytest.raises(ValidationError) as exc_info:
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL=VALID_PROD_POSTGRES_URL,
                SUPABASE_JWT_ALGORITHM="RS256",
                SUPABASE_JWT_SECRET="not-a-pem-public-key"
            )
        assert "requires a valid PEM-encoded public key" in str(exc_info.value)

        # Valid PEM succeeds
        settings = Settings(
            ENVIRONMENT="production",
            DATABASE_URL=VALID_PROD_POSTGRES_URL,
            SUPABASE_JWT_ALGORITHM="RS256",
            SUPABASE_JWT_SECRET=VALID_PEM_PUBLIC_KEY
        )
        assert settings.SUPABASE_JWT_ALGORITHM == "RS256"

    def test_production_rejects_sqlite_database_url(self):
        """Production-like environments must reject SQLite database URLs."""
        for sqlite_url in (
            "sqlite+aiosqlite:///./test.db",
            "sqlite:///prod.db",
            "sqlite+aiosqlite:///:memory:"
        ):
            with pytest.raises(ValidationError) as exc_info:
                Settings(
                    ENVIRONMENT="production",
                    DATABASE_URL=sqlite_url,
                    SUPABASE_JWT_SECRET=VALID_PROD_HS256_SECRET
                )
            assert "cannot use SQLite" in str(exc_info.value)
            assert "Real PostgreSQL 16+ is required" in str(exc_info.value)

    def test_production_accepts_valid_configuration(self):
        """Production successfully initializes when given secure keys and PostgreSQL URL."""
        settings = Settings(
            ENVIRONMENT="production",
            DATABASE_URL=VALID_PROD_POSTGRES_URL,
            SUPABASE_JWT_SECRET=VALID_PROD_HS256_SECRET,
            SUPABASE_JWT_ALGORITHM="HS256"
        )
        assert settings.ENVIRONMENT == "production"
        assert settings.SUPABASE_JWT_SECRET == VALID_PROD_HS256_SECRET
        assert settings.DATABASE_URL == VALID_PROD_POSTGRES_URL

    def test_prod_and_staging_aliases_enforce_protections(self):
        """Environments 'prod' and 'staging' must enforce identical fail-closed protections."""
        for env_alias in ("prod", "staging", "PROD", "STAGING", "Production"):
            # Mock secret rejected
            with pytest.raises(ValidationError) as exc_info:
                Settings(
                    ENVIRONMENT=env_alias,
                    DATABASE_URL=VALID_PROD_POSTGRES_URL,
                    SUPABASE_JWT_SECRET=DEV_MOCK_JWT_SECRET
                )
            assert "strictly prohibited" in str(exc_info.value)

            # SQLite rejected
            with pytest.raises(ValidationError) as exc_info_sqlite:
                Settings(
                    ENVIRONMENT=env_alias,
                    DATABASE_URL="sqlite+aiosqlite:///./db.sqlite",
                    SUPABASE_JWT_SECRET=VALID_PROD_HS256_SECRET
                )
            assert "cannot use SQLite" in str(exc_info_sqlite.value)

    def test_development_and_test_allow_mock_defaults(self):
        """Development and test environments must continue to allow mock defaults without error."""
        for dev_env in ("development", "test", "dev", "local", ""):
            settings = Settings(ENVIRONMENT=dev_env)
            assert settings.SUPABASE_JWT_SECRET == DEV_MOCK_JWT_SECRET
            assert "sqlite" in settings.DATABASE_URL

    def test_error_messages_do_not_reveal_secret_or_credentials(self):
        """Security validation exceptions must never print raw secret strings or DB passwords."""
        canary_secret = "canary-super-secret-leak-detection-token-42"
        canary_db_pass = "super-secret-postgres-password-leak-canary"
        canary_url = f"postgresql+asyncpg://app:{canary_db_pass}@localhost:5432/db"

        # 1. Short key error
        with pytest.raises(ValidationError) as exc_info_secret:
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL=canary_url,
                SUPABASE_JWT_SECRET="short-canary-secret"
            )
        err_msg_secret = exc_info_secret.value.errors()[0]["msg"]
        assert "short-canary-secret" not in err_msg_secret
        assert canary_db_pass not in err_msg_secret

        # 2. SQLite error
        with pytest.raises(ValidationError) as exc_info_db:
            Settings(
                ENVIRONMENT="production",
                DATABASE_URL="sqlite+aiosqlite:///leaked_path.db",
                SUPABASE_JWT_SECRET=VALID_PROD_HS256_SECRET
            )
        err_msg_db = exc_info_db.value.errors()[0]["msg"]
        assert VALID_PROD_HS256_SECRET not in err_msg_db
        assert "leaked_path.db" not in err_msg_db
