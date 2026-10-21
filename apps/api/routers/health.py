"""
Health and System Status Router for Onsigning.
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from apps.api.core.database import get_db

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Standard health check endpoint.
    Verifies service vitality and database connectivity.
    """
    db_status = "connected"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"unreachable: {str(e)}"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "service": "onsigning-core-api",
        "version": "0.1.0",
        "feature_scope": "Feature 1: Single-Agreement Playbook Audit",
        "database": db_status,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
