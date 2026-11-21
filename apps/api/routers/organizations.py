"""
Onsigning Organization Discovery Router
Allows authenticated users to discover organizations they belong to before selecting an active workspace.
Conforms strictly to ARCHITECTURE.md Section 5 and PRD.md Section 3.
"""

from typing import List
from datetime import datetime
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from apps.api.core.database import (
    get_db, Organization, OrganizationMembership,
    set_user_context
)
from apps.api.core.auth import get_current_user, User
from apps.api.core.logging import logger

router = APIRouter(prefix="/api/v1/organizations", tags=["Organizations"])


class OrganizationSummaryResponse(BaseModel):
    id: str
    name: str
    slug: str
    role: str
    created_at: datetime


@router.get("", response_model=List[OrganizationSummaryResponse])
async def list_user_organizations(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Discovers all workspaces where the authenticated user holds an active membership.

    Security controls:
    1. Authenticates strictly via Supabase JWT (get_current_user); does NOT accept client-supplied user IDs.
    2. Does NOT require X-Organization-Id; operates in pre-tenant discovery mode.
    3. Sets user-only RLS context (set_user_context) so dual-predicate RLS restricts
       membership queries strictly to user_id = current_setting('app.current_user_id').
    4. Executes a single JOIN between organization_memberships and organizations under
       RLS user discovery policy, eliminating sequential N+1 database roundtrips.
    5. Operates strictly in user discovery mode without altering app.current_organization_id,
       preventing tenant context pollution.
    6. Returns an empty list with HTTP 200 if the user has no memberships.
    """
    # 1. Establish user-only context for RLS
    await set_user_context(db, user.id)

    # 2. Query memberships joined with organizations under user-only RLS context
    # Single aggregated query eliminating per-membership sequential roundtrips (Phase 2 optimization)
    stmt = (
        select(
            Organization.id,
            Organization.name,
            Organization.slug,
            OrganizationMembership.role,
            Organization.created_at,
        )
        .join(
            Organization,
            Organization.id == OrganizationMembership.organization_id
        )
        .where(OrganizationMembership.user_id == user.id)
        .order_by(
            OrganizationMembership.created_at.asc(),
            Organization.id.asc()
        )
    )
    result = await db.execute(stmt)
    rows = result.all()

    organizations: List[OrganizationSummaryResponse] = []
    for r in rows:
        role_val = r.role.value if hasattr(r.role, "value") else str(r.role)
        organizations.append(
            OrganizationSummaryResponse(
                id=str(r.id),
                name=r.name,
                slug=r.slug,
                role=role_val,
                created_at=r.created_at
            )
        )

    return organizations
