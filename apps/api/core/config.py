"""
Onsigning Core Configuration
Centrally defined, configurable operational settings and environment parameters.
Strictly adheres to approved baseline limits.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator
from typing import Optional

DEV_MOCK_JWT_SECRET = "mock-supabase-jwt-secret-for-development-and-testing-only-at-least-32-chars"
PRODUCTION_ENVIRONMENTS = ("production", "prod", "staging")


class Settings(BaseSettings):
    # Environment
    ENVIRONMENT: str = Field(default="development", description="Runtime environment (development, test, production)")
    DEBUG: bool = Field(default=False)
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Onsigning"
    CORS_ORIGINS: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        description="Comma-separated list of allowed CORS origins"
    )

    # Database & Multi-Tenancy
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./onsigning_dev.db",
        description="Async SQLAlchemy database connection string"
    )
    DB_POOL_SIZE: int = Field(default=10)
    DB_MAX_OVERFLOW: int = Field(default=20)

    # Supabase Authentication
    SUPABASE_URL: str = Field(default="https://mock-supabase.supabase.co")
    SUPABASE_JWT_SECRET: str = Field(
        default="mock-supabase-jwt-secret-for-development-and-testing-only-at-least-32-chars",
        description="JWT secret or public key used to verify Supabase Auth tokens"
    )
    SUPABASE_JWT_ALGORITHM: str = Field(default="HS256")
    SUPABASE_AUDIENCE: str = Field(default="authenticated")

    # Operational Baseline Limits (Approved)
    MAX_UPLOAD_SIZE_BYTES: int = Field(
        default=35 * 1024 * 1024,  # 35 MB
        description="Maximum file upload size in bytes (35 MB approved baseline)"
    )
    MAX_DOCUMENT_PAGES: int = Field(
        default=150,
        description="Maximum document length in pages (150 pages approved baseline)"
    )
    WORKER_TIMEOUT_SECONDS: int = Field(
        default=300,
        description="Background ARQ worker job timeout in seconds (300s approved baseline)"
    )
    LLM_REQUEST_TIMEOUT_SECONDS: int = Field(
        default=45,
        description="Maximum timeout for a single LLM API call in seconds (45s approved baseline)"
    )
    LLM_MAX_RETRIES: int = Field(
        default=2,
        description="Maximum retries for transient LLM API failures (2 retries approved baseline)"
    )
    PRESIGNED_URL_TTL_SECONDS: int = Field(
        default=900,  # 15 minutes
        description="Lifetime of presigned S3 storage URLs in seconds (900s approved baseline)"
    )

    # Storage Settings
    STORAGE_BACKEND: str = Field(default="local", description="'local' or 's3'")
    STORAGE_LOCAL_DIR: str = Field(default="./storage_data")
    S3_BUCKET_NAME: Optional[str] = Field(default="onsigning-documents-private")
    S3_ENDPOINT_URL: Optional[str] = None
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None
    AWS_REGION: str = "us-east-1"

    # AI / LLM Client Settings
    AI_PROVIDER: str = Field(default="mock", description="'openai', 'anthropic', or 'mock' for testing")
    AI_API_KEY: Optional[str] = None
    AI_MODEL_NAME: str = "gpt-4o"

    # Redis / Queue Settings
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        """
        Enforces fail-closed security invariants for production-like environments:
        1. Reject missing, empty, or whitespace-only SUPABASE_JWT_SECRET.
        2. Reject exact default development mock secret.
        3. Enforce algorithm-appropriate key length and format.
        4. Require real PostgreSQL (reject SQLite) for tenant isolation via RLS.
        5. Never leak secret values or database credentials in exception messages.
        """
        env = (self.ENVIRONMENT or "").strip().lower()
        if env in PRODUCTION_ENVIRONMENTS:
            # 1. Require explicit, non-whitespace secret
            secret = self.SUPABASE_JWT_SECRET
            if not secret or not secret.strip():
                raise ValueError(
                    f"Security invariant violated: SUPABASE_JWT_SECRET must be explicitly configured "
                    f"in environment '{self.ENVIRONMENT}'."
                )

            # 2. Reject exact development mock secret (no substring match)
            if secret == DEV_MOCK_JWT_SECRET:
                raise ValueError(
                    f"Security invariant violated: Using the default development mock JWT secret "
                    f"in environment '{self.ENVIRONMENT}' is strictly prohibited."
                )

            # 3. Algorithm-appropriate key validation
            algo = (self.SUPABASE_JWT_ALGORITHM or "").strip().upper()
            if algo in ("HS256", "HS384", "HS512"):
                min_bytes = 32 if algo == "HS256" else (48 if algo == "HS384" else 64)
                secret_bytes_len = len(secret.encode("utf-8"))
                if secret_bytes_len < min_bytes:
                    raise ValueError(
                        f"Security invariant violated: {algo} requires a secret key of at least "
                        f"{min_bytes} bytes ({min_bytes * 8} bits) in environment '{self.ENVIRONMENT}'. "
                        f"Configured key length is {secret_bytes_len} bytes."
                    )
            elif algo in ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512"):
                secret_stripped = secret.strip()
                if not (secret_stripped.startswith("-----BEGIN") and ("PUBLIC KEY" in secret_stripped or "CERTIFICATE" in secret_stripped)):
                    raise ValueError(
                        f"Security invariant violated: Asymmetric algorithm {algo} requires a valid "
                        f"PEM-encoded public key or certificate in environment '{self.ENVIRONMENT}'."
                    )
            else:
                raise ValueError(
                    f"Security invariant violated: Unsupported or insecure JWT algorithm: '{algo}' "
                    f"in environment '{self.ENVIRONMENT}'."
                )

            # 4. Require PostgreSQL (reject SQLite) in production-like environments
            db_url = (self.DATABASE_URL or "").strip().lower()
            if "sqlite" in db_url:
                raise ValueError(
                    f"Security invariant violated: Production-like environment '{self.ENVIRONMENT}' "
                    "cannot use SQLite. Real PostgreSQL 16+ is required for Row-Level Security tenant isolation."
                )

        return self

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()
