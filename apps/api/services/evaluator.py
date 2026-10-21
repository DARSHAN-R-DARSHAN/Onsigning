"""
Onsigning Deterministic Playbook Rule Evaluator
Implements Stage D evaluation strictly adhering to AI_DATA_SPEC.md Section 5:
- Pure declarative evaluation without arbitrary code execution (no eval() or AST).
- Supports all 8 canonical operators (LESS_THAN, GREATER_THAN, EQUALS, IN_LIST, NOT_IN_LIST, CONTAINS_ANY, MUST_BE_EXPLICITLY_STATED, DISALLOW_OMISSION).
- Handles coverage fallbacks (INSUFFICIENT_COVERAGE, unreadable pages) to UNDECIDED_NEEDS_REVIEW.
- Pre-seeds the approved 4-rule B2B SaaS reference playbook.
"""

from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import json

from apps.api.ai_pipeline.schemas import (
    ExtractedClauseFact, PlaybookRuleConfig, PlaybookRuleOperator,
    RiskSeverity, ReviewStatus, PresenceStatus, CitationStatus, RiskPlaybookConfig
)
from apps.api.ai_pipeline.parser import ParsedDocument


@dataclass
class EvaluatedAlert:
    rule_id: str
    severity: RiskSeverity
    title: str
    explanation: str
    extracted_clause: Optional[ExtractedClauseFact]
    review_status: ReviewStatus
    evidence_payload: Dict[str, Any]


def format_currency_cents(cents: Optional[int], currency: Optional[str] = "USD") -> str:
    if cents is None:
        return "Not specified"
    dollars = cents / 100.0
    return f"${dollars:,.2f} {currency}" if currency == "USD" else f"{dollars:,.2f} {currency}"


def evaluate_rule_predicate(
    rule: PlaybookRuleConfig,
    fact: Optional[ExtractedClauseFact],
    has_coverage_issues: bool
) -> Tuple[bool, Optional[str]]:
    """
    Evaluates a single rule predicate against an extracted clause fact.
    Returns (is_breach, breach_explanation).
    """
    op = rule.operator
    target_field = rule.target_field

    # Check for missing clause
    if fact is None or fact.presence_status == PresenceStatus.SILENT_OMISSION:
        if op in (PlaybookRuleOperator.MUST_BE_EXPLICITLY_STATED, PlaybookRuleOperator.DISALLOW_OMISSION):
            return True, f"Clause category '{rule.clause_category}' is omitted from the agreement."
        return False, None

    # Coverage issues (unreadable pages or insufficient coverage)
    if fact.presence_status == PresenceStatus.INSUFFICIENT_COVERAGE or has_coverage_issues:
        return False, None  # Handled separately by coverage fallback

    if fact.presence_status == PresenceStatus.AMBIGUOUS_OR_CROSS_REF:
        return False, None  # Ambiguous references prompt review, not direct breach

    # Get fact value for target field
    fact_val = getattr(fact, target_field, None)
    target_val = rule.target_value

    if fact_val is None:
        return False, None

    # Evaluate declarative operator
    if op == PlaybookRuleOperator.LESS_THAN:
        if isinstance(fact_val, (int, float)) and isinstance(target_val, (int, float)):
            if fact_val < target_val:
                actual_fmt = format_currency_cents(fact_val) if "amount_cents" in target_field else str(fact_val)
                expl = rule.explanation_template.replace("{actual_value_formatted}", actual_fmt).replace("{actual_value}", str(fact_val))
                return True, expl

    elif op == PlaybookRuleOperator.GREATER_THAN:
        if isinstance(fact_val, (int, float)) and isinstance(target_val, (int, float)):
            if fact_val > target_val:
                actual_fmt = format_currency_cents(fact_val) if "amount_cents" in target_field else str(fact_val)
                expl = rule.explanation_template.replace("{actual_value_formatted}", actual_fmt).replace("{actual_value}", str(fact_val))
                return True, expl

    elif op == PlaybookRuleOperator.EQUALS:
        if str(fact_val).lower() == str(target_val).lower():
            expl = rule.explanation_template.replace("{actual_value}", str(fact_val))
            return True, expl

    elif op == PlaybookRuleOperator.IN_LIST:
        if isinstance(target_val, list):
            target_set = {str(item).lower() for item in target_val}
            if str(fact_val).lower() in target_set:
                expl = rule.explanation_template.replace("{actual_value}", str(fact_val))
                return True, expl

    elif op == PlaybookRuleOperator.NOT_IN_LIST:
        if isinstance(target_val, list):
            target_set = {str(item).lower() for item in target_val}
            if str(fact_val).lower() not in target_set:
                expl = rule.explanation_template.replace("{actual_value}", str(fact_val))
                return True, expl

    elif op == PlaybookRuleOperator.CONTAINS_ANY:
        if isinstance(fact_val, list) and isinstance(target_val, list):
            fact_set = {str(item).lower() for item in fact_val}
            target_set = {str(item).lower() for item in target_val}
            intersection = fact_set.intersection(target_set)
            if intersection:
                expl = rule.explanation_template.replace("{actual_value}", ", ".join(intersection))
                return True, expl

    return False, None


def evaluate_playbook(
    rules: List[PlaybookRuleConfig],
    extracted_clauses: List[ExtractedClauseFact],
    parsed_doc: ParsedDocument,
    clause_verification_results: Dict[str, Tuple[CitationStatus, bool]]  # category -> (status, verified)
) -> List[EvaluatedAlert]:
    """
    Main evaluation pipeline executing deterministic rules against verified facts.
    """
    alerts: List[EvaluatedAlert] = []
    clause_by_category = {c.clause_category: c for c in extracted_clauses}
    has_unreadable_pages = len(parsed_doc.unreadable_pages) > 0

    for rule in rules:
        fact = clause_by_category.get(rule.clause_category)
        cit_status, is_verified = clause_verification_results.get(
            rule.clause_category, (CitationStatus.NOT_APPLICABLE, False)
        )

        # 1. Handle Incomplete Coverage
        if (fact and fact.presence_status == PresenceStatus.INSUFFICIENT_COVERAGE) or (fact is None and has_unreadable_pages):
            alerts.append(EvaluatedAlert(
                rule_id=rule.rule_id,
                severity=rule.coverage_fallback_status,  # Default UNDECIDED_NEEDS_REVIEW
                title=f"Coverage Warning: {rule.alert_title}",
                explanation=f"Cannot confidently evaluate rule '{rule.rule_id}' due to unreadable pages or incomplete document parsing.",
                extracted_clause=fact,
                review_status=ReviewStatus.NEEDS_HUMAN_REVIEW,
                evidence_payload={
                    "clause_category": rule.clause_category,
                    "presence_status": PresenceStatus.INSUFFICIENT_COVERAGE.value,
                    "unreadable_pages": parsed_doc.unreadable_pages,
                    "is_evidence_verified": False,
                    "quote": None
                }
            ))
            continue

        # 2. Handle Ambiguous References
        if fact and fact.presence_status == PresenceStatus.AMBIGUOUS_OR_CROSS_REF:
            alerts.append(EvaluatedAlert(
                rule_id=rule.rule_id,
                severity=RiskSeverity.UNDECIDED_NEEDS_REVIEW,
                title=f"Ambiguous Reference: {rule.alert_title}",
                explanation=f"Clause references external policies or unattached exhibits: {fact.factual_summary}",
                extracted_clause=fact,
                review_status=ReviewStatus.NEEDS_HUMAN_REVIEW,
                evidence_payload={
                    "clause_category": rule.clause_category,
                    "presence_status": PresenceStatus.AMBIGUOUS_OR_CROSS_REF.value,
                    "section_reference": fact.section_reference,
                    "page_number": fact.page_number,
                    "quote": fact.verbatim_quote,
                    "is_evidence_verified": is_verified
                }
            ))
            continue

        # 3. Evaluate Rule Predicate
        is_breach, breach_explanation = evaluate_rule_predicate(rule, fact, has_unreadable_pages)

        if is_breach:
            # If citation was unverified, quarantine for human review
            review_st = ReviewStatus.AI_EVALUATED
            if fact and fact.presence_status == PresenceStatus.EXPLICITLY_STATED and not is_verified:
                review_st = ReviewStatus.NEEDS_HUMAN_REVIEW

            alerts.append(EvaluatedAlert(
                rule_id=rule.rule_id,
                severity=rule.severity_on_breach,
                title=rule.alert_title,
                explanation=breach_explanation or rule.alert_title,
                extracted_clause=fact,
                review_status=review_st,
                evidence_payload={
                    "clause_category": rule.clause_category,
                    "presence_status": fact.presence_status.value if fact else PresenceStatus.SILENT_OMISSION.value,
                    "section_reference": fact.section_reference if fact else None,
                    "page_number": fact.page_number if fact else None,
                    "page_end": fact.page_end if fact else None,
                    "quote": fact.verbatim_quote if fact else None,
                    "citation_status": cit_status.value,
                    "is_evidence_verified": is_verified
                }
            ))

    return alerts


def get_baseline_saas_playbook() -> RiskPlaybookConfig:
    """Returns the approved 4-rule baseline reference playbook specified in AI_DATA_SPEC.md Section 5.4."""
    return RiskPlaybookConfig(
        playbook_name="Standard B2B SaaS Playbook",
        version=1,
        description="Approved baseline vendor risk rules for single-agreement audit",
        rules=[
            PlaybookRuleConfig(
                rule_id="RULE_LOL_CAP_MINIMUM",
                clause_category="LIMITATION_OF_LIABILITY",
                target_field="cap_fixed_amount_cents",
                operator=PlaybookRuleOperator.LESS_THAN,
                target_value=100000000,  # $1,000,000 in cents
                severity_on_breach=RiskSeverity.HIGH,
                coverage_fallback_status=RiskSeverity.UNDECIDED_NEEDS_REVIEW,
                alert_title="Liability Cap Below $1M Policy Minimum",
                explanation_template="The agreement caps total liability at {actual_value_formatted}, which is below the organizational policy minimum of $1,000,000."
            ),
            PlaybookRuleConfig(
                rule_id="RULE_LOL_DISALLOWED_CARVEOUTS",
                clause_category="LIMITATION_OF_LIABILITY",
                target_field="uncapped_carveouts",
                operator=PlaybookRuleOperator.CONTAINS_ANY,
                target_value=["consequential_damages", "indirect_damages", "loss_of_profits"],
                severity_on_breach=RiskSeverity.CRITICAL,
                coverage_fallback_status=RiskSeverity.UNDECIDED_NEEDS_REVIEW,
                alert_title="Uncapped Consequential Damages Carve-Out",
                explanation_template="Agreement contains uncapped liability carve-outs for indirect or consequential damages: {actual_value}."
            ),
            PlaybookRuleConfig(
                rule_id="RULE_GOV_LAW_APPROVED",
                clause_category="GOVERNING_LAW",
                target_field="governing_law_jurisdiction",
                operator=PlaybookRuleOperator.NOT_IN_LIST,
                target_value=["Delaware", "New York", "California", "England and Wales"],
                severity_on_breach=RiskSeverity.MEDIUM,
                coverage_fallback_status=RiskSeverity.UNDECIDED_NEEDS_REVIEW,
                alert_title="Non-Standard Governing Law Jurisdiction",
                explanation_template="Governing law is specified as {actual_value}, which is outside the approved core jurisdictions."
            ),
            PlaybookRuleConfig(
                rule_id="RULE_TERMINATION_NOTICE_MIN",
                clause_category="TERMINATION_FOR_CONVENIENCE",
                target_field="notice_period_days",
                operator=PlaybookRuleOperator.LESS_THAN,
                target_value=30,
                severity_on_breach=RiskSeverity.LOW,
                coverage_fallback_status=RiskSeverity.UNDECIDED_NEEDS_REVIEW,
                alert_title="Termination Notice Period Under 30 Days",
                explanation_template="Notice period for termination is {actual_value} days, which is less than the standard 30-day window."
            )
        ]
    )
