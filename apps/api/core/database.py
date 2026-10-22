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
    TypeDecorator, JSON, event, Enum
)
from sqlalchemy.exc import UnboundExecutionError
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


class PortableUUID(TypeDecorator):
    """
    Portable UUID Type.
    Emits native PostgreSQL UUID, and VARCHAR(36) on SQLite.
    Accepts both str and uuid.UUID on bind; always yields str on read.
    """
    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            try:
                value = uuid.UUID(str(value))
            except (ValueError, TypeError, AttributeError) as e:
                raise ValueError(f"Invalid UUID value: {value}") from e
        if dialect.name == "postgresql":
            return value
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return str(value)


class PortableJSON(TypeDecorator):
    """
    Portable JSON/JSONB Type.
    Emits native PostgreSQL JSONB, and JSON on SQLite.
    Transparently handles pre-serialized JSON strings, native dicts, lists,
    primitives, and None on bind. Rejects malformed JSON strings with ValueError.
    Binds Python None as SQL NULL (none_as_null=True), while explicit serialized
    JSON "null" text binds as JSON null (JSON.NULL).
    """
    impl = JSON(none_as_null=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB(none_as_null=True))
        return dialect.type_descriptor(JSON(none_as_null=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            is_valid = True
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                is_valid = False

            if not is_valid:
                raise ValueError("Invalid JSON string payload")
            if parsed is None:
                return JSON.NULL
            return parsed
        return value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return value


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


def _get_session_dialect_name(session: AsyncSession) -> str:
    """
    Resolves the database dialect name for the given SQLAlchemy session.

    Resolution Order:
    1. Direct session bind or get_bind():
       Inspects session.bind or session.get_bind() for a concrete engine/connection dialect.
    2. Documented Fallback:
       If the session is unbound (raises UnboundExecutionError or AttributeError on mock),
       falls back to the dialect configured via settings.DATABASE_URL
       ("sqlite" if is_sqlite else "postgresql").
    """
    bind = getattr(session, "bind", None)
    if bind is None:
        try:
            bind = session.get_bind()
        except (UnboundExecutionError, AttributeError):
            bind = None

    if bind is not None and hasattr(bind, "dialect"):
        dialect_name = getattr(bind.dialect, "name", None)
        if isinstance(dialect_name, str):
            return dialect_name

    return "sqlite" if is_sqlite else "postgresql"


def _is_sqlite_session(session: AsyncSession) -> bool:
    """
    Determines whether the given session is backed by an SQLite dialect.
    Prefers the session's actual engine/connection bind; falls back to the
    default application database configuration when the session is unbound or a generic mock.
    """
    return _get_session_dialect_name(session) == "sqlite"


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

    if not _is_sqlite_session(session):
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
    session_is_sqlite = _is_sqlite_session(session)
    if not session_is_sqlite and session.in_transaction():
        await session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true);"),
            {"uid": uid_str}
        )
        await session.execute(
            text("SELECT set_config('app.current_organization_id', :org_id, true);"),
            {"org_id": str(org_uuid)}
        )
    elif session_is_sqlite:
        session.info["last_applied_org_id"] = str(org_uuid)
        session.info["last_applied_user_id"] = uid_str


# --- Canonical PostgreSQL Enum Types (Strictly Conforming to 0001_mvp_schema.sql) ---

class AliasedDocHierarchyTypeEnum(Enum):
    """
    Subclass of SQLAlchemy Enum for doc_hierarchy_type that transparently coerces
    legacy application/UI aliases ('MASTER_SERVICES_AGREEMENT' -> 'MSA', 'STATEMENT_OF_WORK' -> 'SOW')
    to preserve backward compatibility with existing tests and UI payloads while
    compiling natively to the PostgreSQL 'doc_hierarchy_type' enum.
    """
    ALIASES = {
        "MASTER_SERVICES_AGREEMENT": "MSA",
        "STATEMENT_OF_WORK": "SOW",
    }

    def bind_processor(self, dialect):
        parent_proc = super().bind_processor(dialect)

        def process(value):
            if value is not None and isinstance(value, str):
                value = self.ALIASES.get(value, value)
            if parent_proc:
                return parent_proc(value)
            return value

        return process

    def result_processor(self, dialect, coltype):
        parent_proc = super().result_processor(dialect, coltype)

        def process(value):
            if value is not None and isinstance(value, str):
                value = self.ALIASES.get(value, value)
            if parent_proc:
                return parent_proc(value)
            return value

        return process


UserRoleEnum = Enum(
    "OWNER", "ADMIN", "LEGAL_REVIEWER", "VIEWER",
    name="user_role",
    create_type=False,
    length=50
)

DocHierarchyTypeEnum = AliasedDocHierarchyTypeEnum(
    "MSA", "SOW", "AMENDMENT", "ADDENDUM_DPA", "SLA", "ORDER_FORM", "INSURANCE_COI", "OTHER",
    name="doc_hierarchy_type",
    create_type=False,
    length=50
)

DocProcessingStatusEnum = Enum(
    "UPLOADED", "PARSING_LAYOUT", "EXTRACTING_FACTS", "EVALUATING_PLAYBOOK", "INDEXING_RETRIEVAL",
    "READY", "READY_WITH_WARNINGS", "FAILED",
    name="doc_processing_status",
    create_type=False,
    length=50
)

DocErrorCategoryEnum = Enum(
    "PARSER_ERROR", "UNREADABLE_SCANNED_PAGE", "CORRUPTED_FILE", "FILE_SIZE_EXCEEDED",
    "PAGE_COUNT_EXCEEDED", "TIMEOUT", "LLM_EXTRACTION_ERROR", "SCHEMA_VALIDATION_ERROR", "SYSTEM_ERROR",
    name="doc_error_category",
    create_type=False,
    length=50
)

ClausePresenceStatusEnum = Enum(
    "EXPLICITLY_STATED", "SILENT_OMISSION", "AMBIGUOUS_OR_CROSS_REF", "INSUFFICIENT_COVERAGE",
    name="clause_presence_status",
    create_type=False,
    length=50
)

CitationVerificationStatusEnum = Enum(
    "EXACT_MATCH", "NORMALIZED_MATCH", "APPROXIMATE_NEEDS_REVIEW", "UNVERIFIED_MISMATCH", "NOT_APPLICABLE",
    name="citation_verification_status",
    create_type=False,
    length=50
)

LiabilityCapStructureEnum = Enum(
    "FIXED_AMOUNT", "FEE_MULTIPLE", "GREATER_OF_FIXED_OR_MULTIPLE", "UNCAPPED", "SILENT", "AMBIGUOUS", "NOT_APPLICABLE",
    name="liability_cap_structure",
    create_type=False,
    length=50
)

SupersessionStatusEnum = Enum(
    "NONE", "PROPOSED_BY_AI", "CONFIRMED_SUPERSEDED", "REJECTED_BY_HUMAN",
    name="supersession_status",
    create_type=False,
    length=50
)

RiskSeverityEnum = Enum(
    "CRITICAL", "HIGH", "MEDIUM", "LOW", "COMPLIANT", "UNDECIDED_NEEDS_REVIEW",
    name="risk_severity",
    create_type=False,
    length=50
)

ReviewStatusEnum = Enum(
    "AI_EVALUATED", "NEEDS_HUMAN_REVIEW", "HUMAN_CONFIRMED", "HUMAN_OVERRIDDEN", "DISMISSED_FALSE_POSITIVE",
    name="review_status",
    create_type=False,
    length=50
)

AuditActorTypeEnum = Enum(
    "USER", "SYSTEM_WORKER", "API_KEY",
    name="audit_actor_type",
    create_type=False,
    length=50
)


# --- ORM Entity Definitions (Strictly Conforming to SCHEMA.md) ---

class User(Base):
    __tablename__ = "users"
    id = Column(PortableUUID, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False, index=True)
    full_name = Column(String(255), nullable=True)
    external_auth_id = Column(String(255), unique=True, nullable=True)
    auth_provider = Column(String(50), nullable=False, default="supabase")
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    memberships = relationship("OrganizationMembership", back_populates="user", cascade="all, delete-orphan")


class Organization(Base):
    __tablename__ = "organizations"
    id = Column(PortableUUID, primary_key=True, default=lambda: str(uuid.uuid4()))
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
    id = Column(PortableUUID, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(PortableUUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(PortableUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(UserRoleEnum, nullable=False, default="VIEWER")  # OWNER, ADMIN, LEGAL_REVIEWER, VIEWER
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_org_membership"),
        Index("idx_org_memberships_user_org", "user_id", "organization_id"),
    )

    user = relationship("User", back_populates="memberships")
    organization = relationship("Organization", back_populates="memberships")


class RiskPlaybook(Base):
    __tablename__ = "risk_playbooks"
    id = Column(PortableUUID, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(PortableUUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    description = Column(Text, nullable=True)
    rules = Column(PortableJSON, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(PortableUUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("organization_id", "name", "version", name="uq_playbook_name_version"),
        Index("idx_playbooks_tenant_active", "organization_id", "is_active"),
    )

    organization = relationship("Organization", back_populates="playbooks")


class Document(Base):
    __tablename__ = "documents"
    id = Column(PortableUUID, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(PortableUUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    contract_family_id = Column(PortableUUID, nullable=True)
    vendor_id = Column(PortableUUID, nullable=True)
    title = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    storage_key = Column(String(512), nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    mime_type = Column(String(100), nullable=False)
    byte_size = Column(BigInteger, nullable=False)
    hierarchy_type = Column(DocHierarchyTypeEnum, nullable=False, default="OTHER")
    processing_status = Column(DocProcessingStatusEnum, nullable=False, default="UPLOADED")  # doc_processing_status
    page_count = Column(Integer, nullable=True)
    unreadable_pages = Column(PortableIntArray, nullable=True, default=list)  # INT[] in PostgreSQL, JSON list in SQLite
    parser_warnings = Column(PortableJSON, nullable=True, default=list)
    error_category = Column(DocErrorCategoryEnum, nullable=True)            # doc_error_category
    error_message = Column(Text, nullable=True)
    error_detail = Column(PortableJSON, nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=True)
    effective_date = Column(Date, nullable=True)
    expiration_date = Column(Date, nullable=True)
    renewal_date = Column(Date, nullable=True)
    created_by = Column(PortableUUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
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
    id = Column(PortableUUID, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(PortableUUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    contract_family_id = Column(PortableUUID, nullable=True)
    document_id = Column(PortableUUID, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    clause_category = Column(String(100), nullable=False)
    presence_status = Column(ClausePresenceStatusEnum, nullable=False)  # clause_presence_status
    section_reference = Column(String(100), nullable=True)
    page_number = Column(Integer, nullable=True)
    page_end = Column(Integer, nullable=True)
    verbatim_quote = Column(Text, nullable=True)
    citation_status = Column(CitationVerificationStatusEnum, nullable=False, default="NOT_APPLICABLE")
    is_evidence_verified = Column(Boolean, nullable=False, default=False)
    factual_summary = Column(Text, nullable=False)
    cap_structure = Column(LiabilityCapStructureEnum, nullable=False, default="NOT_APPLICABLE")
    cap_fixed_amount_cents = Column(BigInteger, nullable=True)
    cap_currency = Column(String(3), nullable=True)
    cap_fee_multiple = Column(Numeric(6, 2), nullable=True)
    uncapped_carveouts = Column(PortableStringArray, nullable=True, default=list)  # TEXT[] in PostgreSQL, JSON list in SQLite
    governing_law_jurisdiction = Column(String(100), nullable=True)
    notice_period_days = Column(Integer, nullable=True)
    extraction_uncertainty_notes = Column(Text, nullable=True)
    supersession_state = Column(SupersessionStatusEnum, nullable=False, default="NONE")
    overrides_section_reference = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("idx_clauses_tenant_doc", "organization_id", "document_id", "clause_category"),
    )

    document = relationship("Document", back_populates="clauses")
    alerts = relationship("RiskAlert", back_populates="clause")


class AuditRun(Base):
    __tablename__ = "audit_runs"
    id = Column(PortableUUID, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(PortableUUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    document_id = Column(PortableUUID, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    playbook_id = Column(PortableUUID, ForeignKey("risk_playbooks.id", ondelete="RESTRICT"), nullable=False)
    playbook_version = Column(Integer, nullable=False)
    status = Column(String(50), nullable=False, default="COMPLETED")
    started_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    summary = Column(PortableJSON, nullable=False, default=dict)
    created_by = Column(PortableUUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        Index("idx_audit_runs_tenant_doc", "organization_id", "document_id", "started_at"),
    )

    document = relationship("Document", back_populates="audit_runs")
    alerts = relationship("RiskAlert", back_populates="audit_run", cascade="all, delete-orphan")


class RiskAlert(Base):
    __tablename__ = "risk_alerts"
    id = Column(PortableUUID, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(PortableUUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    audit_run_id = Column(PortableUUID, ForeignKey("audit_runs.id", ondelete="CASCADE"), nullable=False)
    extracted_clause_id = Column(PortableUUID, ForeignKey("extracted_clauses.id", ondelete="SET NULL"), nullable=True)
    severity = Column(RiskSeverityEnum, nullable=False)  # risk_severity
    rule_id = Column(String(64), nullable=False)
    title = Column(String(255), nullable=False)
    explanation = Column(Text, nullable=False)
    evidence_payload = Column(PortableJSON, nullable=False)
    review_status = Column(ReviewStatusEnum, nullable=False, default="AI_EVALUATED")  # review_status
    reviewed_by = Column(PortableUUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
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
    id = Column(PortableUUID, primary_key=True, default=lambda: str(uuid.uuid4()))
    organization_id = Column(PortableUUID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    actor_user_id = Column(PortableUUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_type = Column(AuditActorTypeEnum, nullable=False, default="USER")
    entity_table = Column(String(100), nullable=False)
    entity_id = Column(PortableUUID, nullable=False)
    action = Column(String(50), nullable=False)
    previous_state = Column(PortableJSON, nullable=True)
    new_state = Column(PortableJSON, nullable=True)
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
