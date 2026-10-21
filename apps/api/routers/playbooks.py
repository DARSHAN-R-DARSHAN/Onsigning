"""
Playbook Management Router for Onsigning Single-Agreement Audit.
Allows querying active risk playbooks and rules.
"""

from typing import List, Dict, Any, Optional
import json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from apps.api.core.database import get_db, RiskPlaybook
from apps.api.core.auth import get_tenant_context, TenantContext
from apps.api.services.evaluator import get_baseline_saas_playbook

router = APIRouter(prefix="/api/v1/playbooks", tags=["Playbooks"])


class PlaybookRuleSummary(BaseModel):
    rule_id: str
    target_clause_category: str
    severity: str
    operator: str
    title: str
    description: str


class PlaybookSummaryResponse(BaseModel):
    id: str
    name: str
    version: int
    description: Optional[str] = None
    is_active: bool
    rule_count: int
    created_at: datetime
    updated_at: datetime


class PlaybookDetailResponse(BaseModel):
    id: str
    name: str
    version: int
    description: Optional[str] = None
    is_active: bool
    rules: List[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime


@router.get("", response_model=List[PlaybookSummaryResponse])
async def list_playbooks(
    context: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """
    List all active playbooks for the authenticated user's organization.
    If none exist, automatically seeds the canonical Baseline B2B SaaS Playbook.
    """
    stmt = select(RiskPlaybook).where(
        RiskPlaybook.organization_id == context.organization.id
    ).order_by(RiskPlaybook.created_at.desc())

    res = await db.execute(stmt)
    playbooks = res.scalars().all()

    if not playbooks:
        # Auto-seed baseline SaaS playbook for this organization
        default_config = get_baseline_saas_playbook()
        rules_json = json.dumps([r.model_dump() for r in default_config.rules])
        seeded = RiskPlaybook(
            id=str(uuid.uuid4()),
            organization_id=context.organization.id,
            name=default_config.playbook_name,
            version=default_config.version,
            description=default_config.description,
            rules=rules_json,
            is_active=True,
            created_by=context.user.id
        )
        db.add(seeded)
        await db.commit()
        await db.refresh(seeded)
        playbooks = [seeded]

    result = []
    for p in playbooks:
        try:
            parsed_rules = json.loads(p.rules)
            rule_count = len(parsed_rules) if isinstance(parsed_rules, list) else 0
        except Exception:
            rule_count = 0

        result.append(
            PlaybookSummaryResponse(
                id=p.id,
                name=p.name,
                version=p.version,
                description=p.description,
                is_active=p.is_active,
                rule_count=rule_count,
                created_at=p.created_at,
                updated_at=p.updated_at
            )
        )

    return result


@router.get("/{playbook_id}", response_model=PlaybookDetailResponse)
async def get_playbook_detail(
    playbook_id: str,
    context: TenantContext = Depends(get_tenant_context),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieve full playbook details, including individual deterministic evaluation rules.
    """
    stmt = select(RiskPlaybook).where(
        RiskPlaybook.id == playbook_id,
        RiskPlaybook.organization_id == context.organization.id
    )
    res = await db.execute(stmt)
    playbook = res.scalar_one_or_none()

    if not playbook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Risk playbook with id '{playbook_id}' was not found in your organization."
        )

    try:
        rules = json.loads(playbook.rules)
    except Exception:
        rules = []

    return PlaybookDetailResponse(
        id=playbook.id,
        name=playbook.name,
        version=playbook.version,
        description=playbook.description,
        is_active=playbook.is_active,
        rules=rules,
        created_at=playbook.created_at,
        updated_at=playbook.updated_at
    )
