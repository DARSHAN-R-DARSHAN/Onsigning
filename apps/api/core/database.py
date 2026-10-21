"""
Onsigning Database Layer & SQLAlchemy Async ORM
Manages database sessions, connection pooling, and tenant-scoped RLS context.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional, Union, List
import uuid
import json
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Integer, BigInteger, Boolean, DateTime, Date,
    Text, ForeignKey, Index, UniqueConstraint, text, Numeric,
    TypeDecorator, JSON, event
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB, ARRAY
from sqlalchemy.ext.asyncio import (
    AsyncSession, create_async_engine, async_sessionmaker
)
from sqlalchemy.orm import declarative_base, relationship, Session

from apps.api.core.config import settings
from apps.api.core.logging import logger

# Base Model
Base = declarative_base()

# Engine creation with connection pooling
is_sqlite = settings.DATABASE_URL.startswith("sqlite")

engine_kwargs = {
    "echo": settings.DEBUG,
    "future": True,
}

if not is_sqlite:
    engine_kwargs.update({
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
    })

engine = create_async_engine(settings.DATABASE_URL, **engine_kwargs)
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)


class PortableIntArray(TypeDecorator):
    """
    Portable Integer Array Type.
    Emits native INTEGER[] on PostgreSQL and JSON array on SQLite.
    Always yields Python list[int] upon reading and writing.
    """
    impl = ARRAY(Integer)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(JSON())
        return dialect.type_descriptor(ARRAY(Integer))

    def process_bind_param(self, value, dialect):
        if value is None:
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return [int(x) for x in parsed] if isinstance(parsed, list) else []
            except Exception:
                return []
        return [int(x) for x in value]

    def process_result_value(self, value, dialect):
        if value is None:
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return [int(x) for x in parsed] if isinstance(parsed, list) else []
            except Exception:
                return []
        return list(value)


class PortableStringArray(TypeDecorator):
    """
    Portable Text Array Type.
    Emits native TEXT[] on PostgreSQL and JSON array on SQLite.
    Always yields Python list[str] upon reading and writing.
    """
    impl = ARRAY(Text)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(JSON())
        return dialect.type_descriptor(ARRAY(Text))

    def process_bind_param(self, value, dialect):
        if value is None:
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return [str(x) for x in parsed] if isinstance(parsed, list) else []
            except Exception:
                return []
        return [str(x) for x in value]

    def process_result_value(self, value, dialect):
        if value is None:
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return [str(x) for x in parsed] if isinstance(parsed, list) else []
            except Exception:
                return []
        return list(value)


@event.listens_for(Session, "after_begin")
def _reapply_tenant_context_on_transaction_begin(session: Session, transaction, connection):
    """
    Ensures PostgreSQL transaction-local RLS configuration persists across transactions.
    Fires on every new transaction started by the session (including after commit() or rollback()).
    Uses the transaction's own connection to execute set_config with is_local=true.
    Does not touch unconfigured sessions.
    """
    org_id = session.info.get("current_organization_id")
    if not org_id:
        return

    user_id = session.info.get("current_user_id") or ""
    session.info["tenant_context_reapplied_count"] = session.info.get("tenant_context_reapplied_count", 0) + 1

    if connection.dialect.name != "sqlite":
        connection.execute(
            text("SELECT set_config('app.current_user_id', :uid, true);"),
            {"uid": user_id}
        )
        connection.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true);"),
            {"org_id": org_id}
        )
    else:
        # SQLite verification tracking
        session.info["last_applied_org_id"] = org_id
        session.info["last_applied_user_id"] = user_id


async def set_user_context(
    session: AsyncSession,
    user_id: Union[uuid.UUID, str]
):
    """
    Sets transaction-local user configuration for PostgreSQL Row Level Security (RLS).
    Used exclusively during authentication bootstrap to allow querying organization_memberships
    under the user_id RLS policy predicate before organization context is verified.
    Does NOT establish organization context.

    Hardening (Task 5.10):
    - Clears any previous organization context from session.info BEFORE executing SQL
      so that Session.after_begin cannot reapply an old tenant if autobegin is triggered.
    - Explicitly clears PostgreSQL transaction-local organization setting via
      set_config('app.current_organization_id', '', true).
    - Sets PostgreSQL transaction-local user setting via
      set_config('app.current_user_id', :uid, true).
    """
    if not user_id:
        raise ValueError("user_id must be provided to set user context.")
    try:
        user_uuid = uuid.UUID(str(user_id))
    except (ValueError, TypeError, AttributeError) as e:
        raise ValueError(f"Invalid user_id UUID: {user_id}") from e

    uid_str = str(user_uuid)

    # 1. Reset stale organization context from session.info BEFORE any SQL execution
    # This prevents Session.after_begin from reapplying an old tenant if SQL autobegins a transaction.
    session.info.pop("current_organization_id", None)
    session.info.pop("current_user_id", None)
    session.info.pop("last_applied_org_id", None)
    session.info.pop("tenant_context_reapplied_count", None)

    if not is_sqlite:
        await session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true);"),
            {"uid": uid_str}
        )
        await session.execute(
            text("SELECT set_config('app.current_organization_id', '', true);")
        )
    else:
        # SQLite verification tracking
        session.info["last_applied_user_id"] = uid_str
        session.info["last_applied_org_id"] = ""


async def set_tenant_context(
    session: AsyncSession,
    user_id: Optional[Union[uuid.UUID, str]],
    organization_id: Union[uuid.UUID, str]
):
    """
    Sets transaction-local configuration for PostgreSQL Row Level Security (RLS).
    Evaluated by RLS policies:
    - app.current_organization_id: NULLIF(current_setting('app.current_organization_id', true), '')::uuid
    - app.current_user_id: NULLIF(current_setting('app.current_user_id', true), '')::uuid

    System-Actor Policy:
    When user_id is None or empty (worker automated background tasks, system jobs):
    - No fake user UUID or human impersonation is performed.
    - 'app.current_user_id' is set to '' so NULLIF('', '')::uuid evaluates to NULL in RLS policies.
    - 'app.current_organization_id' is strictly set to the validated organization UUID.

    Transaction Continuity:
    Preserves tenant identity in session.info so that subsequent transactions
    (e.g., following commit() or rollback()) automatically reapply context via the
    after_begin event hook without losing tenant isolation.
    """
    if not organization_id:
        raise ValueError("organization_id must be provided to set tenant context.")
    try:
        org_uuid = uuid.UUID(str(organization_id))
    except (ValueError, TypeError, AttributeError) as e:
        raise ValueError(f"Invalid organization_id UUID: {organization_id}") from e

    uid_str = ""
    if user_id is not None and str(user_id).strip() != "":
        try:
            user_uuid = uuid.UUID(str(user_id))
            uid_str = str(user_uuid)
        except (ValueError, TypeError, AttributeError) as e:
            raise ValueError(f"Invalid user_id UUID: {user_id}") from e

    # 1. Store in session.info for transaction continuity across commits & rollbacks
    session.info["current_organization_id"] = str(org_uuid)
    session.info["current_user_id"] = uid_str if uid_str else None

    # 2. If the current session already has an active transaction open, apply immediately
    if not is_sqlite and session.in_transaction():
        await session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true);"),
            {"uid": uid_str}
        )
        await session.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true);"),
            {"org_id": str(org_uuid)}
        )
    elif is_sqlite:
        session.info["last_applied_org_id"] = str(org_uuid)
        session.info["last_applied_user_id"] = uid_str


# --- ORM Entity Definitions (Strictly Conforming to SCHEMA.md) ---

class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=True)
    external_auth_id = Column(String(255), unique=True, nullable=True)
    auth_provider = Column(String(50), nullable=False, default="supabase")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    memberships = relationship("OrganizationMembership", back_populates="user", cascade="all, delete-orphan")


class Organization(Base):
    __tablename__ = "organizations"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    default_currency = Column(String(3), nullable=False, default="USD")
    timezone = Column(String(50), nullable=False, default="UTC")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    memberships = relationship("OrganizationMembership", back_populates="organization", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="organization", cascade="all, delete-orphan")
    playbooks = relationship("RiskPlaybook", back_populates="organization", cascade="all, delete-orphan")


class OrganizationMembership(Base):
    __tablename__ = "organization_memberships"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(50), nullable=False, default="VIEWER")  # OWNER, ADMIN, LEGAL_REVIEWER, VIEWER
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_membership"),
        Index("idx_org_memberships_user_org", "user_id", "organization_id"),
    )

    user = relationship("User", back_populates="memberships")
    organization = relationship("Organization", back_populates="memberships")


class RiskPlaybook(Base):
    __tablename__ = "risk_playbooks"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    description = Column(Text, nullable=True)
    rules = Column(Text, nullable=False)  # Stored as JSON string
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "name", "version", name="uq_playbook_name_version"),
        Index("idx_playbooks_tenant_active", "organization_id", "is_active"),
    )

    organization = relationship("Organization", back_populates="playbooks")


class Document(Base):
    __tablename__ = "documents"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    contract_family_id = Column(String(36), nullable=True)
    vendor_id = Column(String(36), nullable=True)
    title = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    storage_key = Column(String(512), nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    mime_type = Column(String(100), nullable=False)
    byte_size = Column(BigInteger, nullable=False)
    hierarchy_type = Column(String(50), nullable=False, default="OTHER")
    processing_status = Column(String(50), nullable=False, default="UPLOADED")  # doc_processing_status
    page_count = Column(Integer, nullable=True)
    unreadable_pages = Column(PortableIntArray, nullable=True, default=list)  # INT[] in PostgreSQL, JSON list in SQLite
    parser_warnings = Column(Text, nullable=True, default="[]")   # JSON list of warnings
    error_category = Column(String(50), nullable=True)            # doc_error_category
    error_message = Column(Text, nullable=True)
    error_detail = Column(Text, nullable=True)                    # JSON diagnostics
    failed_at = Column(DateTime(timezone=True), nullable=True)
    effective_date = Column(Date, nullable=True)
    expiration_date = Column(Date, nullable=True)
    renewal_date = Column(Date, nullable=True)
    created_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_docs_tenant_status", "organization_id", "processing_status"),
    )

    organization = relationship("Organization", back_populates="documents")
    clauses = relationship("ExtractedClause", back_populates="document", cascade="all, delete-orphan")
    audit_runs = relationship("AuditRun", back_populates="document", cascade="all, delete-orphan")


class ExtractedClause(Base):
    __tablename__ = "extracted_clauses"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    contract_family_id = Column(String(36), nullable=True)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    clause_category = Column(String(100), nullable=False)
    presence_status = Column(String(50), nullable=False)  # clause_presence_status
    section_reference = Column(String(100), nullable=True)
    page_number = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    verbatim_quote = Column(Text, nullable=True)
    citation_status = Column(String(50), nullable=False, default="NOT_APPLICABLE")
    is_evidence_verified = Column(Boolean, nullable=False, default=False)
    factual_summary = Column(Text, nullable=False)
    cap_structure = Column(String(50), nullable=False, default="NOT_APPLICABLE")
    cap_fixed_amount_cents = Column(BigInteger, nullable=True)
    cap_currency = Column(String(3), nullable=True)
    cap_fee_multiple = Column(Numeric(6, 2), nullable=True)
    uncapped_carveouts = Column(PortableStringArray, nullable=True, default=list)  # TEXT[] in PostgreSQL, JSON list in SQLite
    governing_law_jurisdiction = Column(String(100), nullable=True)
    notice_period_days = Column(Integer, nullable=True)
    extraction_uncertainty_notes = Column(Text, nullable=True)
    supersession_state = Column(String(50), nullable=False, default="NONE")
    overrides_section_reference = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_clauses_tenant_doc", "organization_id", "document_id", "clause_category"),
    )

    document = relationship("Document", back_populates="clauses")
    alerts = relationship("RiskAlert", back_populates="clause")


class AuditRun(Base):
    __tablename__ = "audit_runs"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    document_id = Column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    playbook_id = Column(String(36), ForeignKey("risk_playbooks.id", ondelete="RESTRICT"), nullable=False)
    playbook_version = Column(Integer, nullable=False)
    status = Column(String(50), nullable=False, default="COMPLETED")
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    summary = Column(Text, nullable=False, default="{}")  # JSON summary
    created_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        Index("idx_audit_runs_tenant_doc", "organization_id", "document_id", "started_at"),
    )

    document = relationship("Document", back_populates="audit_runs")
    alerts = relationship("RiskAlert", back_populates="audit_run", cascade="all, delete-orphan")


class RiskAlert(Base):
    __tablename__ = "risk_alerts"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    audit_run_id = Column(String(36), ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False)
    extracted_clause_id = Column(String(36), ForeignKey("extracted_clauses.id", ondelete="SET NULL"), nullable=True)
    severity = Column(String(50), nullable=False)  # risk_severity
    rule_id = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    explanation = Column(Text, nullable=False)
    evidence_payload = Column(Text, nullable=False)  # JSON snapshot of evidence
    review_status = Column(String(50), nullable=False, default="AI_EVALUATED")  # review_status
    reviewed_by = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_alerts_tenant_run_severity", "organization_id", "audit_run_id", "severity"),
    )

    audit_run = relationship("AuditRun", back_populates="alerts")
    clause = relationship("ExtractedClause", back_populates="alerts")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    actor_user_id = Column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_type = Column(String(50), nullable=False, default="USER")
    entity_table = Column(String(100), nullable=False)
    entity_id = Column(String(36), nullable=False)
    action = Column(String(50), nullable=False)
    previous_state = Column(Text, nullable=True)
    new_state = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_audit_logs_tenant_time", "organization_id", "entity_table", "created_at"),
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
