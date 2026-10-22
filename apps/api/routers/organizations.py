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
    set_user_context, set_tenant_context
)
from apps.api.core.auth import get_current_user, User

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
    4. For each verified membership, temporarily applies tenant context (set_tenant_context)
       to read organization metadata under RLS without bypass or privilege escalation.
    5. Always restores user-only context in a finally block to prevent tenant context pollution.
    6. Returns an empty list with HTTP 200 if the user has no memberships.
    """
    # 1. Establish user-only context for RLS
    await set_user_context(db, user.id)

    try:
        # 2. Query memberships belonging strictly to this user
        stmt = (
            select(OrganizationMembership)
            .where(OrganizationMembership.user_id == user.id)
            .order_by(OrganizationMembership.created_at.asc())
        )
        result = await db.execute(stmt)
        memberships = result.scalars().all()

        if not memberships:
            return []

        # 3. For each verified membership, retrieve organization metadata under matching tenant context
        organizations: List[OrganizationSummaryResponse] = []
        for m in memberships:
            org_id = str(m.organization_id)
            await set_tenant_context(db, user.id, org_id)

            org_stmt = select(Organization).where(Organization.id == org_id)
            org_res = await db.execute(org_stmt)
            org = org_res.scalar_one_or_none()

            if org:
                role_val = m.role.value if hasattr(m.role, "value") else str(m.role)
                organizations.append(
                    OrganizationSummaryResponse(
                        id=str(org.id),
                        name=org.name,
                        slug=org.slug,
                        role=role_val,
                        created_at=org.created_at
                    )
                )

        return organizations
    finally:
        # 4. Guarantee cleanup back to user-scoped context
        await set_user_context(db, user.id)
