"""
Tests for Stage D Deterministic Playbook Rule Evaluator.
Validates the 4 canonical baseline B2B SaaS playbook rules and coverage fallback logic.
"""

import pytest
from apps.api.ai_pipeline.schemas import (
    ExtractedClauseFact, PresenceStatus, LiabilityCapStructure,
    CitationStatus, RiskSeverity
)
from apps.api.ai_pipeline.parser import ParsedDocument, PageText
from apps.api.services.evaluator import evaluate_playbook, get_baseline_saas_playbook


def test_rule_lol_disallowed_carveouts_triggers_critical():
    """Rule flags uncapped consequential or indirect damages carve-out as CRITICAL severity."""
    playbook = get_baseline_saas_playbook()
    doc = ParsedDocument(page_count=2, pages=[])

    extracted = [
        ExtractedClauseFact(
            clause_category="LIMITATION_OF_LIABILITY",
            presence_status=PresenceStatus.EXPLICITLY_STATED,
            page_number=1,
            verbatim_quote="Neither party's liability for consequential damages or loss of profits shall be limited.",
            factual_summary="Consequential damages are excluded from the liability cap.",
            cap_structure=LiabilityCapStructure.FIXED_AMOUNT,
            cap_fixed_amount_cents=200000000,
            uncapped_carveouts=["consequential_damages", "loss_of_profits"]
        )
    ]
    verif = {"LIMITATION_OF_LIABILITY": (CitationStatus.EXACT_MATCH, True)}

    alerts = evaluate_playbook(playbook.rules, extracted, doc, verif)
    rule_alert = next((a for a in alerts if a.rule_id == "RULE_LOL_DISALLOWED_CARVEOUTS"), None)

    assert rule_alert is not None
    assert rule_alert.severity == RiskSeverity.CRITICAL
    assert "Uncapped Consequential Damages Carve-Out" in rule_alert.title


def test_rule_lol_cap_minimum_triggers_high():
    """Rule flags liability cap below $1M as HIGH severity."""
    playbook = get_baseline_saas_playbook()
    doc = ParsedDocument(page_count=2, pages=[])

    extracted = [
        ExtractedClauseFact(
            clause_category="LIMITATION_OF_LIABILITY",
            presence_status=PresenceStatus.EXPLICITLY_STATED,
            page_number=2,
            verbatim_quote="The total liability under this Agreement shall not exceed five hundred thousand dollars.",
            factual_summary="Liability cap is $500,000.",
            cap_structure=LiabilityCapStructure.FIXED_AMOUNT,
            cap_fixed_amount_cents=50000000,  # $500,000 in cents (< $1,000,000)
            uncapped_carveouts=[]
        )
    ]
    verif = {"LIMITATION_OF_LIABILITY": (CitationStatus.EXACT_MATCH, True)}

    alerts = evaluate_playbook(playbook.rules, extracted, doc, verif)
    rule_alert = next((a for a in alerts if a.rule_id == "RULE_LOL_CAP_MINIMUM"), None)

    assert rule_alert is not None
    assert rule_alert.severity == RiskSeverity.HIGH
    assert "Liability Cap Below $1M Policy Minimum" in rule_alert.title


def test_rule_gov_law_approved_triggers_medium_on_foreign_jurisdiction():
    """Rule flags non-standard or foreign jurisdiction as MEDIUM severity."""
    playbook = get_baseline_saas_playbook()
    doc = ParsedDocument(page_count=2, pages=[])

    extracted = [
        ExtractedClauseFact(
            clause_category="GOVERNING_LAW",
            presence_status=PresenceStatus.EXPLICITLY_STATED,
            page_number=3,
            verbatim_quote="This Agreement shall be construed in accordance with the laws of Texas.",
            factual_summary="Texas governing law.",
            governing_law_jurisdiction="Texas"
        )
    ]
    verif = {"GOVERNING_LAW": (CitationStatus.EXACT_MATCH, True)}

    alerts = evaluate_playbook(playbook.rules, extracted, doc, verif)
    rule_alert = next((a for a in alerts if a.rule_id == "RULE_GOV_LAW_APPROVED"), None)

    assert rule_alert is not None
    assert rule_alert.severity == RiskSeverity.MEDIUM
    assert "Non-Standard Governing Law Jurisdiction" in rule_alert.title


def test_rule_termination_notice_min_triggers_low_on_short_notice():
    """Rule flags termination notice period < 30 days as LOW severity."""
    playbook = get_baseline_saas_playbook()
    doc = ParsedDocument(page_count=2, pages=[])

    extracted = [
        ExtractedClauseFact(
            clause_category="TERMINATION_FOR_CONVENIENCE",
            presence_status=PresenceStatus.EXPLICITLY_STATED,
            page_number=1,
            verbatim_quote="Either party may terminate this agreement upon fifteen (15) days written notice.",
            factual_summary="15 days written notice required.",
            notice_period_days=15
        )
    ]
    verif = {"TERMINATION_FOR_CONVENIENCE": (CitationStatus.EXACT_MATCH, True)}

    alerts = evaluate_playbook(playbook.rules, extracted, doc, verif)
    rule_alert = next((a for a in alerts if a.rule_id == "RULE_TERMINATION_NOTICE_MIN"), None)

    assert rule_alert is not None
    assert rule_alert.severity == RiskSeverity.LOW
    assert "Termination Notice Period Under 30 Days" in rule_alert.title


def test_unreadable_pages_trigger_coverage_warning_and_undecided():
    """Unreadable pages in document trigger UNDECIDED_NEEDS_REVIEW with coverage warning."""
    playbook = get_baseline_saas_playbook()
    doc = ParsedDocument(
        page_count=3,
        pages=[],
        unreadable_pages=[2]  # Page 2 was unreadable
    )

    extracted = [
        ExtractedClauseFact(
            clause_category="LIMITATION_OF_LIABILITY",
            presence_status=PresenceStatus.INSUFFICIENT_COVERAGE,
            verbatim_quote=None,
            factual_summary="Could not read page 2 due to scanning artifacts."
        )
    ]
    verif = {"LIMITATION_OF_LIABILITY": (CitationStatus.NOT_APPLICABLE, False)}

    alerts = evaluate_playbook(playbook.rules, extracted, doc, verif)
    rule_alert = next((a for a in alerts if "RULE_LOL" in a.rule_id), None)

    assert rule_alert is not None
    assert rule_alert.severity == RiskSeverity.UNDECIDED_NEEDS_REVIEW
    assert "coverage warning" in rule_alert.title.lower()
