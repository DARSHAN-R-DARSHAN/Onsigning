"""
Tests for Stage C Deterministic Citation Verification.
Validates the exact 5-step normalization algorithm, semantic invariants (negation, numbers),
and adjacent-page boundary verification.
"""

import pytest
from apps.api.ai_pipeline.schemas import CitationStatus, PresenceStatus
from apps.api.ai_pipeline.parser import PageText
from apps.api.services.verifier import verify_citation, normalize_text


def test_exact_citation_match():
    """Exact verbatim match returns EXACT_MATCH with is_verified=True."""
    doc_pages = [
        PageText(
            page_number=1,
            text="Neither party's total aggregate liability under this Agreement shall exceed twelve (12) months of fees paid."
        )
    ]
    status, is_verified = verify_citation(
        verbatim_quote="Neither party's total aggregate liability under this Agreement shall exceed twelve (12) months of fees paid.",
        presence_status=PresenceStatus.EXPLICITLY_STATED,
        page_number=1,
        page_end=1,
        document_pages=doc_pages
    )
    assert status == CitationStatus.EXACT_MATCH
    assert is_verified is True


def test_normalized_match_typographic_quotes_and_hyphenation():
    """Normalizes smart quotes, line-break hyphens, and whitespace collapses."""
    doc_pages = [
        PageText(
            page_number=1,
            text='The “Service Provider” shall main-\ntain comprehensive general liability in-\nsurance.'
        )
    ]
    quote = 'The "Service Provider" shall maintain comprehensive general liability insurance.'

    status, is_verified = verify_citation(
        verbatim_quote=quote,
        presence_status=PresenceStatus.EXPLICITLY_STATED,
        page_number=1,
        page_end=1,
        document_pages=doc_pages
    )
    assert status in (CitationStatus.EXACT_MATCH, CitationStatus.NORMALIZED_MATCH)
    assert is_verified is True


def test_altered_negation_strictly_fails_verification():
    """
    CRITICAL SAFETY INVARIANT: If the LLM alters negation
    (e.g., dropping 'not' or changing 'in no event' to 'in every event'),
    the citation verifier strictly rejects it with is_verified=False.
    """
    doc_pages = [
        PageText(
            page_number=2,
            text="In no event shall either party be liable for any indirect, special, or consequential damages."
        )
    ]
    altered_quote = "In every event shall either party be liable for any indirect, special, or consequential damages."

    status, is_verified = verify_citation(
        verbatim_quote=altered_quote,
        presence_status=PresenceStatus.EXPLICITLY_STATED,
        page_number=2,
        page_end=2,
        document_pages=doc_pages
    )
    assert is_verified is False
    assert status in (CitationStatus.UNVERIFIED_MISMATCH, CitationStatus.APPROXIMATE_NEEDS_REVIEW)


def test_number_mismatch_strictly_fails_verification():
    """
    CRITICAL FINANCIAL INVARIANT: If the LLM alters a dollar amount or number
    (e.g. $5,000,000 vs $50,000,000), citation verifier strictly rejects it.
    """
    doc_pages = [
        PageText(
            page_number=3,
            text="The aggregate liability cap shall be fixed at $5,000,000 USD."
        )
    ]
    altered_number_quote = "The aggregate liability cap shall be fixed at $50,000,000 USD."

    status, is_verified = verify_citation(
        verbatim_quote=altered_number_quote,
        presence_status=PresenceStatus.EXPLICITLY_STATED,
        page_number=3,
        page_end=3,
        document_pages=doc_pages
    )
    assert is_verified is False
    assert status == CitationStatus.UNVERIFIED_MISMATCH


def test_adjacent_page_boundary_verification():
    """
    Clauses spanning page breaks (page_number=1, page_end=2)
    verify cleanly against the joined page boundary text.
    """
    doc_pages = [
        PageText(
            page_number=1,
            text="Section 8.2: Either party may terminate this agreement upon"
        ),
        PageText(
            page_number=2,
            text="thirty (30) days prior written notice for material breach."
        )
    ]
    quote = "Either party may terminate this agreement upon thirty (30) days prior written notice for material breach."

    status, is_verified = verify_citation(
        verbatim_quote=quote,
        presence_status=PresenceStatus.EXPLICITLY_STATED,
        page_number=1,
        page_end=2,
        document_pages=doc_pages
    )
    assert status in (CitationStatus.EXACT_MATCH, CitationStatus.NORMALIZED_MATCH)
    assert is_verified is True


def test_non_explicit_clause_returns_not_applicable():
    """Omitted or non-explicit presence status returns NOT_APPLICABLE with is_verified=False."""
    doc_pages = [PageText(page_number=1, text="Standard NDA text.")]
    status, is_verified = verify_citation(
        verbatim_quote=None,
        presence_status=PresenceStatus.SILENT_OMISSION,
        page_number=None,
        page_end=None,
        document_pages=doc_pages
    )
    assert status == CitationStatus.NOT_APPLICABLE
    assert is_verified is False
