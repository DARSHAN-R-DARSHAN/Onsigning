"""
Onsigning Independent Deterministic Citation Verifier
Implements Stage C citation verification strictly adhering to AI_DATA_SPEC.md:
- 5-step deterministic normalization algorithm (Unicode NFKC, curly quotes, dashes, whitespace, hyphenation).
- Strict number and negation invariance (zero tolerance for altered amounts or negations).
- Support for contiguous adjacent-page boundary quotations ([page_number, page_end]).
- Populates citation_status and is_evidence_verified.
"""

from typing import Tuple, List, Optional
import unicodedata
import re

from apps.api.ai_pipeline.schemas import CitationStatus, PresenceStatus
from apps.api.ai_pipeline.parser import PageText

# Critical negations requiring 100% invariant preservation
NEGATION_TOKENS = {
    "not", "never", "no", "neither", "nor", "none",
    "except", "unless", "without", "excluding"
}

# Regex patterns for normalization
SMART_SINGLE_QUOTES = re.compile(r"[‘’‚‛]")
SMART_DOUBLE_QUOTES = re.compile(r"[“”„‟]")
DASHES = re.compile(r"[–—―]")
WHITESPACE_RUNS = re.compile(r"[\s\u00A0\u2007\u202F]+")
LINE_BREAK_HYPHEN = re.compile(r"([A-Za-z]+)-\s+([A-Za-z]+)")
NUMBER_TOKEN = re.compile(r"^[\$€£]?\d+(?:[,\.]\d+)*(?:[kKmMbB]|million|thousand|billion)?$")


def normalize_text(text: str) -> str:
    """
    Applies the conservative deterministic normalization pipeline:
    1. Unicode NFKC normalization
    2. Smart quote & dash replacement to ASCII equivalents
    3. Line-break de-hyphenation (e.g. 'obli-\\n gation' -> 'obligation')
    4. Whitespace sequence collapse to single ASCII space
    """
    if not text:
        return ""

    # Step 1: Unicode NFKC
    norm = unicodedata.normalize("NFKC", text)

    # Step 2: Typographic punctuation replacement
    norm = SMART_SINGLE_QUOTES.sub("'", norm)
    norm = SMART_DOUBLE_QUOTES.sub('"', norm)
    norm = DASHES.sub("--", norm)

    # Step 3: De-hyphenate line breaks (preserving numbers and compound words)
    norm = LINE_BREAK_HYPHEN.sub(r"\1\2", norm)

    # Step 4: Collapse all whitespace runs and non-breaking spaces
    norm = WHITESPACE_RUNS.sub(" ", norm).strip()

    return norm


def extract_invariants(tokens: List[str]) -> Tuple[List[str], List[str]]:
    """Extracts ordered negation and numeric tokens for strict invariance verification."""
    negations = []
    numbers = []
    for t in tokens:
        clean_t = t.strip(".,;:()[]{}\"'").lower()
        if clean_t in NEGATION_TOKENS:
            negations.append(clean_t)
        if NUMBER_TOKEN.match(t.strip(".,;:()[]{}")):
            numbers.append(t.strip(".,;:()[]{}").lower())
    return negations, numbers


def verify_citation(
    verbatim_quote: Optional[str],
    presence_status: PresenceStatus,
    page_number: Optional[int],
    page_end: Optional[int],
    document_pages: List[PageText]
) -> Tuple[CitationStatus, bool]:
    """
    Deterministically verifies an extracted quote against source page text.
    Returns (citation_status, is_evidence_verified).
    """
    # Non-explicit presence states do not carry source quotes
    if presence_status != PresenceStatus.EXPLICITLY_STATED:
        return CitationStatus.NOT_APPLICABLE, False

    if not verbatim_quote or not page_number:
        return CitationStatus.UNVERIFIED_MISMATCH, False

    # Build source text candidate across claimed page(s)
    page_map = {p.page_number: p.text for p in document_pages}
    if page_number not in page_map:
        return CitationStatus.UNVERIFIED_MISMATCH, False

    source_text = page_map[page_number]

    # Support adjacent-page boundary if page_end == page_number + 1
    if page_end and page_end > page_number:
        if page_end == page_number + 1 and page_end in page_map:
            source_text = source_text + " " + page_map[page_end]
        else:
            # Citations spanning non-adjacent pages (> 2 pages) fail verified match
            return CitationStatus.APPROXIMATE_NEEDS_REVIEW, False

    # 1. Check EXACT_MATCH
    if verbatim_quote in source_text:
        return CitationStatus.EXACT_MATCH, True

    # 2. Check NORMALIZED_MATCH
    norm_quote = normalize_text(verbatim_quote)
    norm_source = normalize_text(source_text)

    if norm_quote in norm_source:
        return CitationStatus.NORMALIZED_MATCH, True

    # 3. Check for Token and Invariant Discrepancies
    quote_tokens = norm_quote.split()
    source_tokens = norm_source.split()

    quote_negations, quote_numbers = extract_invariants(quote_tokens)

    # If quote tokens have high lexical overlap with a window in source text
    if len(quote_tokens) >= 5:
        # Check rolling window in source text
        q_len = len(quote_tokens)
        for i in range(len(source_tokens) - q_len + 1):
            window = source_tokens[i : i + q_len]
            # Calculate match ratio
            matches = sum(1 for w, q in zip(window, quote_tokens) if w == q)
            if matches / q_len >= 0.70:
                # We found an approximate candidate. Check number/negation invariance!
                window_negations, window_numbers = extract_invariants(window)
                if quote_negations != window_negations or quote_numbers != window_numbers:
                    # Altered negation or number -> UNVERIFIED_MISMATCH
                    return CitationStatus.UNVERIFIED_MISMATCH, False
                else:
                    return CitationStatus.APPROXIMATE_NEEDS_REVIEW, False

    return CitationStatus.UNVERIFIED_MISMATCH, False
