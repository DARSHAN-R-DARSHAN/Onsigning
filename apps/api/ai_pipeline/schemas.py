"""
Onsigning Canonical AI Pipeline Schemas & Pydantic v2 Models
Strictly aligns with AI_DATA_SPEC.md and SCHEMA.md:
- Factual clause extraction models (no model-asserted verification).
- Deterministic playbook rule schemas and operators.
- Separation of untrusted LLM citation candidates from server-verified citations.
"""

from datetime import date
from enum import Enum
from typing import Optional, Union, List, Literal
from pydantic import BaseModel, Field, model_validator


# --- Database & Workflow Enumerations ---

class DocProcessingStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PARSING_LAYOUT = "PARSING_LAYOUT"
    EXTRACTING_FACTS = "EXTRACTING_FACTS"
    EVALUATING_PLAYBOOK = "EVALUATING_PLAYBOOK"
    INDEXING_RETRIEVAL = "INDEXING_RETRIEVAL"
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    FAILED = "FAILED"


class DocErrorCategory(str, Enum):
    PARSER_ERROR = "PARSER_ERROR"
    UNREADABLE_SCANNED_PAGE = "UNREADABLE_SCANNED_PAGE"
    CORRUPTED_FILE = "CORRUPTED_FILE"
    FILE_SIZE_EXCEEDED = "FILE_SIZE_EXCEEDED"
    PAGE_COUNT_EXCEEDED = "PAGE_COUNT_EXCEEDED"
    TIMEOUT = "TIMEOUT"
    LLM_EXTRACTION_ERROR = "LLM_EXTRACTION_ERROR"
    SCHEMA_VALIDATION_ERROR = "SCHEMA_VALIDATION_ERROR"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class PresenceStatus(str, Enum):
    EXPLICITLY_STATED = "EXPLICITLY_STATED"
    SILENT_OMISSION = "SILENT_OMISSION"
    AMBIGUOUS_OR_CROSS_REF = "AMBIGUOUS_OR_CROSS_REF"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"


class CitationStatus(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    NORMALIZED_MATCH = "NORMALIZED_MATCH"
    APPROXIMATE_NEEDS_REVIEW = "APPROXIMATE_NEEDS_REVIEW"
    UNVERIFIED_MISMATCH = "UNVERIFIED_MISMATCH"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class LiabilityCapStructure(str, Enum):
    FIXED_AMOUNT = "FIXED_AMOUNT"
    FEE_MULTIPLE = "FEE_MULTIPLE"
    GREATER_OF_FIXED_OR_MULTIPLE = "GREATER_OF_FIXED_OR_MULTIPLE"
    UNCAPPED = "UNCAPPED"
    SILENT = "SILENT"
    AMBIGUOUS = "AMBIGUOUS"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RiskSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    COMPLIANT = "COMPLIANT"
    UNDECIDED_NEEDS_REVIEW = "UNDECIDED_NEEDS_REVIEW"


class ReviewStatus(str, Enum):
    AI_EVALUATED = "AI_EVALUATED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    HUMAN_CONFIRMED = "HUMAN_CONFIRMED"
    HUMAN_OVERRIDDEN = "HUMAN_OVERRIDDEN"
    DISMISSED_FALSE_POSITIVE = "DISMISSED_FALSE_POSITIVE"


class QAAnswerStatus(str, Enum):
    DIRECT_EVIDENCE = "DIRECT_EVIDENCE"
    QUALIFIED_INFERENCE = "QUALIFIED_INFERENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ObligationTriggerType(str, Enum):
    FIXED_DATE = "FIXED_DATE"
    RELATIVE_TO_ANCHOR = "RELATIVE_TO_ANCHOR"
    RECURRING_SCHEDULE = "RECURRING_SCHEDULE"
    EVENT_TRIGGERED = "EVENT_TRIGGERED"


class ObligationAnchorType(str, Enum):
    EXPLICIT_CALENDAR_DATE = "EXPLICIT_CALENDAR_DATE"
    EFFECTIVE_DATE = "EFFECTIVE_DATE"
    EXPIRATION_DATE = "EXPIRATION_DATE"
    RENEWAL_DATE = "RENEWAL_DATE"
    EVENT_OCCURRENCE = "EVENT_OCCURRENCE"
    UNKNOWN_OR_EXTERNAL_ANCHOR = "UNKNOWN_OR_EXTERNAL_ANCHOR"


class ObligationResponsibleParty(str, Enum):
    VENDOR = "VENDOR"
    CUSTOMER = "CUSTOMER"
    MUTUAL = "MUTUAL"


# --- Factual Extraction Schemas (Returned by LLM) ---

class ExtractedClauseFact(BaseModel):
    """
    Factual extraction output from the LLM.
    Strictly prohibits model-asserted verification flags.
    """
    clause_category: str
    presence_status: PresenceStatus
    section_reference: Optional[str] = None
    page_number: Optional[int] = Field(default=None, ge=1)
    page_end: Optional[int] = Field(default=None, ge=1)
    verbatim_quote: Optional[str] = None
    factual_summary: str
    cap_structure: LiabilityCapStructure = LiabilityCapStructure.NOT_APPLICABLE
    cap_fixed_amount_cents: Optional[int] = Field(default=None, ge=0)
    cap_currency: Optional[str] = Field(default=None, pattern=r"^[A-Z]{3}$")
    cap_fee_multiple: Optional[float] = Field(default=None, ge=0)
    uncapped_carveouts: List[str] = Field(default_factory=list)
    governing_law_jurisdiction: Optional[str] = None
    notice_period_days: Optional[int] = Field(default=None, ge=0)
    extraction_uncertainty_notes: Optional[str] = None

    @model_validator(mode="after")
    def quote_matches_presence(self):
        if self.presence_status == PresenceStatus.EXPLICITLY_STATED:
            if not self.verbatim_quote or len(self.verbatim_quote.strip()) < 20:
                raise ValueError("Explicit clauses require a meaningful verbatim quote (>= 20 characters).")
            if self.page_number is None:
                raise ValueError("Explicit clauses require a page number.")
            if self.page_end is not None and self.page_end < self.page_number:
                raise ValueError("page_end must be greater than or equal to page_number.")
        elif self.verbatim_quote is not None:
            raise ValueError("Non-explicit presence states must not claim a source quote.")
        return self


class ExtractedObligationFact(BaseModel):
    obligation_type: str
    responsible_party: ObligationResponsibleParty
    trigger_type: ObligationTriggerType
    anchor_date_type: ObligationAnchorType
    explicit_due_date_iso: Optional[date] = None
    recurrence_interval_months: Optional[int] = Field(default=None, ge=1)
    notice_period_days: Optional[int] = Field(default=None, ge=0)
    is_business_days: bool = False
    relative_window_description: str
    verbatim_quote: str = Field(min_length=15)
    page_number: int = Field(ge=1)
    page_end: Optional[int] = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_dates_and_pages(self):
        if self.anchor_date_type == ObligationAnchorType.EXPLICIT_CALENDAR_DATE:
            if self.explicit_due_date_iso is None:
                raise ValueError("Explicit calendar date requires explicit_due_date_iso.")
        elif self.explicit_due_date_iso is not None:
            raise ValueError("explicit_due_date_iso must only be provided for EXPLICIT_CALENDAR_DATE.")
        if self.page_end is not None and self.page_end < self.page_number:
            raise ValueError("page_end must be greater than or equal to page_number.")
        return self


# --- Deterministic Playbook Rule Schema (AI_DATA_SPEC.md Section 5) ---

class PlaybookRuleOperator(str, Enum):
    LESS_THAN = "LESS_THAN"
    GREATER_THAN = "GREATER_THAN"
    EQUALS = "EQUALS"
    IN_LIST = "IN_LIST"
    NOT_IN_LIST = "NOT_IN_LIST"
    CONTAINS_ANY = "CONTAINS_ANY"
    MUST_BE_EXPLICITLY_STATED = "MUST_BE_EXPLICITLY_STATED"
    DISALLOW_OMISSION = "DISALLOW_OMISSION"


class PlaybookRuleConfig(BaseModel):
    rule_id: str = Field(pattern=r"^[A-Z0-9_]{3,64}$")
    clause_category: str
    target_field: Literal[
        "cap_fixed_amount_cents",
        "cap_fee_multiple",
        "cap_structure",
        "uncapped_carveouts",
        "governing_law_jurisdiction",
        "notice_period_days",
        "presence_status"
    ]
    operator: PlaybookRuleOperator
    target_value: Union[int, float, str, List[str]]
    severity_on_breach: RiskSeverity
    coverage_fallback_status: RiskSeverity = RiskSeverity.UNDECIDED_NEEDS_REVIEW
    alert_title: str
    explanation_template: str


class RiskPlaybookConfig(BaseModel):
    playbook_name: str
    version: int = Field(ge=1)
    description: str
    rules: List[PlaybookRuleConfig]


# --- Feature 3 Q&A Schemas: Raw Candidate vs Server Verified ---

class LLMQACitationCandidate(BaseModel):
    """Raw citation proposed by LLM without verification assertion."""
    document_id: str
    document_title: str
    section_reference: Optional[str] = None
    page_number: int = Field(ge=1)
    page_end: Optional[int] = Field(default=None, ge=1)
    verbatim_quote: str


class LLMQAResponseCandidate(BaseModel):
    """Raw generation from LLM."""
    candidate_answer_markdown: str
    citation_candidates: List[LLMQACitationCandidate]
    uncertainty_or_conflict_notes: Optional[str] = None


class QACitationVerifiedItem(BaseModel):
    """Verified citation item populated exclusively by deterministic server verifier."""
    document_id: str
    document_title: str
    section_reference: Optional[str] = None
    page_number: int = Field(ge=1)
    page_end: Optional[int] = Field(default=None, ge=1)
    verbatim_quote: str
    citation_status: CitationStatus
    is_evidence_verified: bool


class CrossContractQAResponse(BaseModel):
    """Server-validated response sent to the client."""
    answer_status: QAAnswerStatus
    answer_markdown: str
    citations: List[QACitationVerifiedItem]
    uncertainty_or_conflict_notes: Optional[str] = None
