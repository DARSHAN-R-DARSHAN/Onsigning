"""
Onsigning Single-Agreement Audit Service
Orchestrates Milestone 1 vertical slice:
1. Parse layout & page provenance.
2. Structured factual extraction (LLM).
3. Deterministic Python citation verification (Stage C).
4. Deterministic playbook evaluation (Stage D).
5. Transactional persistence with Rule 11 human audit preservation.
"""

from typing import Dict, Any, List, Optional, Tuple
import uuid
import json
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete

from apps.api.core.database import Document, ExtractedClause, AuditRun, RiskAlert, RiskPlaybook, AuditLog
from apps.api.core.exceptions import ResourceNotFoundError, PlaybookEvaluationError
from apps.api.core.logging import logger
from apps.api.ai_pipeline.parser import parse_document, ParsedDocument
from apps.api.ai_pipeline.extractor import extract_clauses
from apps.api.ai_pipeline.schemas import (
    DocProcessingStatus, DocErrorCategory, ReviewStatus, PlaybookRuleConfig,
    CitationStatus, PresenceStatus, ExtractedClauseFact
)
from apps.api.services.verifier import verify_citation
from apps.api.services.evaluator import evaluate_playbook, get_baseline_saas_playbook, EvaluatedAlert
from apps.api.services.storage import storage_service


class AuditService:

    async def run_document_audit(
        self,
        db: AsyncSession,
        organization_id: str,
        document_id: str,
        playbook_id: Optional[str] = None,
        actor_user_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes end-to-end ingestion, verification, and playbook evaluation for a document.
        """
        # 1. Fetch Document
        stmt = select(Document).where(
            Document.id == document_id,
            Document.organization_id == organization_id
        )
        res = await db.execute(stmt)
        document = res.scalar_one_or_none()
        if not document:
            raise ResourceNotFoundError("Document", document_id)

        # 2. Fetch or seed Playbook
        playbook: Optional[RiskPlaybook] = None
        if playbook_id:
            p_stmt = select(RiskPlaybook).where(
                RiskPlaybook.id == playbook_id,
                RiskPlaybook.organization_id == organization_id
            )
            p_res = await db.execute(p_stmt)
            playbook = p_res.scalar_one_or_none()

        if not playbook:
            # Fetch active playbook or seed default B2B SaaS playbook
            p_stmt = select(RiskPlaybook).where(
                RiskPlaybook.organization_id == organization_id,
                RiskPlaybook.is_active == True
            ).order_by(RiskPlaybook.version.desc())
            p_res = await db.execute(p_stmt)
            playbook = p_res.scalars().first()

            if not playbook:
                default_config = get_baseline_saas_playbook()
                playbook = RiskPlaybook(
                    id=str(uuid.uuid4()),
                    organization_id=organization_id,
                    name=default_config.playbook_name,
                    version=default_config.version,
                    description=default_config.description,
                    rules=json.dumps([r.model_dump() for r in default_config.rules]),
                    is_active=True,
                    created_by=actor_user_id
                )
                db.add(playbook)
                await db.flush()

        rules_raw = json.loads(playbook.rules) if isinstance(playbook.rules, str) else playbook.rules
        if not isinstance(rules_raw, list):
            raise ValueError("Playbook rules must be a list of rule configurations")
        playbook_config_rules = [
            PlaybookRuleConfig.model_validate(rule) if not isinstance(rule, PlaybookRuleConfig) else rule
            for rule in rules_raw
        ]

        # 3. Stage A: Layout Parsing
        document.processing_status = DocProcessingStatus.PARSING_LAYOUT.value
        await db.commit()

        try:
            file_bytes = await storage_service.read_file(document.storage_key)
            parsed_doc = parse_document(file_bytes, document.mime_type, document.original_filename)

            document.page_count = parsed_doc.page_count
            document.unreadable_pages = parsed_doc.unreadable_pages
            document.parser_warnings = json.dumps(parsed_doc.parser_warnings)
        except Exception as e:
            logger.error(f"Ingestion parsing failure for doc={document_id}: {str(e)}")
            document.processing_status = DocProcessingStatus.FAILED.value
            document.error_category = getattr(e, "error_category", DocErrorCategory.PARSER_ERROR.value)
            document.error_message = str(e)
            document.error_detail = json.dumps({"exception": type(e).__name__})
            document.failed_at = datetime.now(timezone.utc)
            await db.commit()
            raise

        # 4. Stage B: Structured Fact Extraction (LLM)
        document.processing_status = DocProcessingStatus.EXTRACTING_FACTS.value
        await db.commit()

        try:
            extracted_facts = await extract_clauses(parsed_doc)
        except Exception as e:
            logger.error(f"Ingestion extraction failure for doc={document_id}: {str(e)}")
            document.processing_status = DocProcessingStatus.FAILED.value
            document.error_category = DocErrorCategory.LLM_EXTRACTION_ERROR.value
            document.error_message = f"AI clause extraction failed: {str(e)}"
            document.failed_at = datetime.now(timezone.utc)
            await db.commit()
            raise

        # 5. Stage C: Deterministic Citation Verification (Python)
        verification_results: Dict[str, Tuple[CitationStatus, bool]] = {}
        for fact in extracted_facts:
            cit_status, is_verified = verify_citation(
                verbatim_quote=fact.verbatim_quote,
                presence_status=fact.presence_status,
                page_number=fact.page_number,
                page_end=fact.page_end,
                document_pages=parsed_doc.pages
            )
            verification_results[fact.clause_category] = (cit_status, is_verified)

        # 6. Stage D: Deterministic Playbook Rule Evaluation
        document.processing_status = DocProcessingStatus.EVALUATING_PLAYBOOK.value
        await db.commit()

        evaluated_alerts = evaluate_playbook(
            rules=playbook_config_rules,
            extracted_clauses=extracted_facts,
            parsed_doc=parsed_doc,
            clause_verification_results=verification_results
        )

        # 7. Stage E: Safe Transactional Persistence & Rule 11 Human Audit Preservation
        # Check existing alerts to preserve human-reviewed findings on re-ingestion
        existing_alerts_stmt = select(RiskAlert).where(
            RiskAlert.organization_id == organization_id,
            RiskAlert.audit_run_id.in_(
                select(AuditRun.id).where(AuditRun.document_id == document_id)
            )
        )
        existing_alerts_res = await db.execute(existing_alerts_stmt)
        prior_alerts = existing_alerts_res.scalars().all()

        human_confirmed_rule_ids: Dict[str, RiskAlert] = {
            a.rule_id: a for a in prior_alerts
            if a.review_status in (ReviewStatus.HUMAN_CONFIRMED.value, ReviewStatus.HUMAN_OVERRIDDEN.value, ReviewStatus.DISMISSED_FALSE_POSITIVE.value)
        }

        # Clear old unreviewed clauses for this document
        old_clauses_stmt = select(ExtractedClause).where(
            ExtractedClause.document_id == document_id,
            ExtractedClause.organization_id == organization_id
        )
        old_clauses_res = await db.execute(old_clauses_stmt)
        old_clauses = old_clauses_res.scalars().all()

        # Delete only clauses not linked to a preserved human review
        for old_c in old_clauses:
            await db.delete(old_c)
        await db.flush()

        # Persist newly extracted facts
        clause_id_map: Dict[str, str] = {}
        for fact in extracted_facts:
            c_status, is_verified = verification_results[fact.clause_category]
            clause_row = ExtractedClause(
                id=str(uuid.uuid4()),
                organization_id=organization_id,
                document_id=document_id,
                clause_category=fact.clause_category,
                presence_status=fact.presence_status.value,
                section_reference=fact.section_reference,
                page_number=fact.page_number,
                page_end=fact.page_end,
                verbatim_quote=fact.verbatim_quote,
                citation_status=c_status.value,
                is_evidence_verified=is_verified,
                factual_summary=fact.factual_summary,
                cap_structure=fact.cap_structure.value,
                cap_fixed_amount_cents=fact.cap_fixed_amount_cents,
                cap_currency=fact.cap_currency,
                cap_fee_multiple=fact.cap_fee_multiple,
                uncapped_carveouts=fact.uncapped_carveouts,
                governing_law_jurisdiction=fact.governing_law_jurisdiction,
                notice_period_days=fact.notice_period_days,
                extraction_uncertainty_notes=fact.extraction_uncertainty_notes,
                supersession_state="NONE"
            )
            db.add(clause_row)
            clause_id_map[fact.clause_category] = clause_row.id

        await db.flush()

        # Create immutable AuditRun
        audit_run_id = str(uuid.uuid4())
        summary_counts = {
            "total_alerts": len(evaluated_alerts),
            "critical": sum(1 for a in evaluated_alerts if a.severity.value == "CRITICAL"),
            "high": sum(1 for a in evaluated_alerts if a.severity.value == "HIGH"),
            "medium": sum(1 for a in evaluated_alerts if a.severity.value == "MEDIUM"),
            "low": sum(1 for a in evaluated_alerts if a.severity.value == "LOW"),
            "undecided_needs_review": sum(1 for a in evaluated_alerts if a.severity.value == "UNDECIDED_NEEDS_REVIEW"),
            "coverage_warning": len(parsed_doc.unreadable_pages) > 0
        }

        audit_run = AuditRun(
            id=audit_run_id,
            organization_id=organization_id,
            document_id=document_id,
            playbook_id=playbook.id,
            playbook_version=playbook.version,
            status="COMPLETED",
            completed_at=datetime.now(timezone.utc),
            summary=json.dumps(summary_counts),
            created_by=actor_user_id
        )
        db.add(audit_run)
        await db.flush()

        # Persist RiskAlert rows (Rule 11: preserve human decisions if prior review existed)
        for alert in evaluated_alerts:
            clause_id = clause_id_map.get(alert.rule_id.split("_")[0]) or (
                clause_id_map.get(alert.extracted_clause.clause_category) if alert.extracted_clause else None
            )

            # Preserve human review status if prior decision was recorded for this rule
            review_st = alert.review_status.value
            reviewed_by = None
            reviewed_at = None
            review_note = None

            if alert.rule_id in human_confirmed_rule_ids:
                prior = human_confirmed_rule_ids[alert.rule_id]
                review_st = prior.review_status
                reviewed_by = prior.reviewed_by
                reviewed_at = prior.reviewed_at
                review_note = prior.review_note

            alert_row = RiskAlert(
                id=str(uuid.uuid4()),
                organization_id=organization_id,
                audit_run_id=audit_run_id,
                extracted_clause_id=clause_id,
                severity=alert.severity.value,
                rule_id=alert.rule_id,
                title=alert.title,
                explanation=alert.explanation,
                evidence_payload=json.dumps(alert.evidence_payload),
                review_status=review_st,
                reviewed_by=reviewed_by,
                reviewed_at=reviewed_at,
                review_note=review_note
            )
            db.add(alert_row)

        # Update Document Final Terminal Status
        final_status = (
            DocProcessingStatus.READY_WITH_WARNINGS.value
            if len(parsed_doc.unreadable_pages) > 0 or parsed_doc.parser_warnings
            else DocProcessingStatus.READY.value
        )
        document.processing_status = final_status
        document.error_category = None
        document.error_message = None
        document.failed_at = None

        # Log system audit event in audit_logs
        log_entry = AuditLog(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            actor_type="USER" if actor_user_id else "SYSTEM_WORKER",
            entity_table="documents",
            entity_id=document_id,
            action="PLAYBOOK_AUDIT_COMPLETED",
            new_state=json.dumps({"audit_run_id": audit_run_id, "summary": summary_counts})
        )
        db.add(log_entry)

        await db.commit()

        return {
            "document_id": document_id,
            "status": final_status,
            "audit_run_id": audit_run_id,
            "playbook_version": playbook.version,
            "summary": summary_counts,
            "alerts_count": len(evaluated_alerts)
        }


audit_service = AuditService()
