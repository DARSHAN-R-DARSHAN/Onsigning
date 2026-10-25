"""
Onsigning Authentication & Authorization Middleware
Server-side validation of Supabase Auth JWTs and tenant organization membership.
Strictly adheres to ARCHITECTURE.md Section 5 and PRD.md Section 3:
- Never trusts client-supplied organization IDs without server-side database validation.
- Enforces Row-Level Security tenant context on every protected transaction.
"""

from dataclasses import dataclass
from typing import Optional
import uuid
import jwt
from jwt import PyJWKClient, PyJWKClientError, PyJWKError
from fastapi import Request, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from apps.api.core.config import settings
from apps.api.core.database import (
    get_db, set_tenant_context, set_user_context,
    User, Organization, OrganizationMembership
)
from apps.api.core.exceptions import AuthenticationError, AuthorizationError
from apps.api.core.logging import logger


@dataclass
class TenantContext:
    user: User
    organization: Organization
    role: str  # OWNER, ADMIN, LEGAL_REVIEWER, VIEWER


# Module-level cached JWKS client singleton
_jwks_client: Optional[PyJWKClient] = None
_jwks_client_url: Optional[str] = None


def get_jwks_client() -> PyJWKClient:
    """
    Returns a cached PyJWKClient instance configured for Supabase's JWKS endpoint.
    Automatically initializes or refreshes if the target JWKS URL changes.
    """
    global _jwks_client, _jwks_client_url
    target_url = settings.supabase_jwks_url
    if _jwks_client is None or _jwks_client_url != target_url:
        _jwks_client = PyJWKClient(
            uri=target_url,
            cache_jwk_set=True,
            lifespan=float(settings.SUPABASE_JWKS_CACHE_TTL_SECONDS),
            timeout=10.0
        )
        _jwks_client_url = target_url
    return _jwks_client


def reset_jwks_client() -> None:
    """Resets the JWKS client singleton (used for unit testing and key rotation tests)."""
    global _jwks_client, _jwks_client_url
    _jwks_client = None
    _jwks_client_url = None


def decode_supabase_jwt(token: str) -> dict:
    """
    Cryptographically validates a Supabase Auth JWT and returns its claims.

    Security invariants:
    1. Algorithm selection is strictly constrained by settings.allowed_jwt_algorithms.
       Tokens with 'none', unapproved, or missing algorithms fail immediately.
    2. Dynamic ECC P-256 (ES256) verification:
       - Requires 'kid' header parameter.
       - Resolves public key from Supabase's JWKS endpoint using PyJWKClient.
       - Dispatches strictly with algorithms=['ES256'] to prevent algorithm confusion.
    3. Isolated legacy HS256 verification:
       - Verifies strictly with settings.SUPABASE_JWT_SECRET as HMAC secret.
       - Dispatches strictly with algorithms=['HS256'].
       - Never accepts EC public keys as HMAC secrets.
    4. Required claims:
       - exp, sub, aud, and iss are required and validated.
       - Audience must match settings.SUPABASE_AUDIENCE ('authenticated').
       - Issuer must match settings.supabase_issuer ('{SUPABASE_URL}/auth/v1').
    5. Fails closed on signature errors, expired tokens, unknown key IDs,
       issuer/audience mismatch, or JWKS network failures.
    """
    if not token or not isinstance(token, str):
        raise AuthenticationError("Authentication token is missing or malformed.")

    try:
        unverified_header = jwt.get_unverified_header(token)
    except Exception as e:
        raise AuthenticationError(f"Invalid authentication token header: {str(e)}")

    alg = unverified_header.get("alg")
    if not alg:
        raise AuthenticationError("Token header missing required 'alg' parameter.")

    alg_upper = alg.strip().upper()
    allowed_algos = settings.allowed_jwt_algorithms

    if alg_upper not in allowed_algos:
        raise AuthenticationError(f"Unsupported or unauthorized signing algorithm: '{alg}'.")

    expected_issuer = settings.supabase_issuer
    expected_audience = settings.SUPABASE_AUDIENCE

    try:
        if alg_upper == "ES256":
            kid = unverified_header.get("kid")
            if not kid:
                raise AuthenticationError("ES256 token header missing required 'kid' parameter.")

            try:
                signing_key = get_jwks_client().get_signing_key_from_jwt(token)
            except PyJWKClientError as e:
                logger.warning(f"JWKS key resolution failed: {str(e)}")
                raise AuthenticationError(f"Unable to verify token signature: {str(e)}")
            except Exception as e:
                logger.warning(f"Unexpected JWKS error during key resolution: {str(e)}")
                raise AuthenticationError(f"Authentication service unavailable: {str(e)}")

            key = signing_key.key
            verify_algorithms = ["ES256"]

        elif alg_upper == "HS256":
            secret = settings.SUPABASE_JWT_SECRET
            if not secret or not secret.strip():
                raise AuthenticationError("HS256 token verification is not configured on this server.")
            key = secret
            verify_algorithms = ["HS256"]

        else:
            raise AuthenticationError(f"Algorithm '{alg}' is not supported by this verification handler.")

        payload = jwt.decode(
            token,
            key=key,
            algorithms=verify_algorithms,
            audience=expected_audience,
            issuer=expected_issuer,
            options={
                "require": ["exp", "sub", "aud", "iss"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": True,
            }
        )
        return payload

    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Authentication token has expired.")
    except jwt.InvalidIssuerError:
        raise AuthenticationError("Invalid authentication token: Issuer mismatch.")
    except jwt.InvalidAudienceError:
        raise AuthenticationError("Invalid authentication token: Audience mismatch.")
    except jwt.MissingRequiredClaimError as e:
        raise AuthenticationError(f"Invalid authentication token: {str(e)}")
    except jwt.InvalidSignatureError:
        raise AuthenticationError("Invalid authentication token: Signature verification failed.")
    except jwt.InvalidTokenError as e:
        raise AuthenticationError(f"Invalid authentication token: {str(e)}")
    except (PyJWKClientError, PyJWKError) as e:
        raise AuthenticationError(f"Invalid authentication token: {str(e)}")
    except jwt.PyJWTError as e:
        raise AuthenticationError(f"Invalid authentication token: {str(e)}")


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    db: AsyncSession = Depends(get_db)
) -> User:
    """FastAPI dependency extracting and verifying the authenticated user identity."""
    if not authorization:
        raise AuthenticationError("Authorization header is missing.")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationError("Invalid Authorization header format. Expected 'Bearer <token>'.")

    token = parts[1]
    payload = decode_supabase_jwt(token)
    sub = payload.get("sub")
    if not sub:
        raise AuthenticationError("Token payload missing 'sub' identity claim.")

    # Fetch user or just-in-time provision
    stmt = select(User).where(User.id == sub)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        email = payload.get("email", f"{sub}@supabase.user")
        user_metadata = payload.get("user_metadata", {})
        full_name = user_metadata.get("full_name") or user_metadata.get("name")
        user = User(
            id=sub,
            email=email,
            full_name=full_name,
            external_auth_id=sub,
            auth_provider="supabase"
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    return user


async def get_tenant_context(
    request: Request,
    user: User = Depends(get_current_user),
    x_organization_id: Optional[str] = Header(None, alias="X-Organization-Id"),
    db: AsyncSession = Depends(get_db)
) -> TenantContext:
    """
    FastAPI dependency validating organization membership and setting RLS tenant context.
    Client-supplied organization IDs are NEVER trusted without database validation.
    """
    if not x_organization_id:
        raise AuthorizationError("Missing required 'X-Organization-Id' workspace header.")

    try:
        org_uuid = uuid.UUID(x_organization_id)
    except ValueError:
        raise AuthorizationError("Invalid 'X-Organization-Id' UUID format.")

    # 1. Establish minimal user-only RLS context before querying membership
    await set_user_context(db, uuid.UUID(user.id))

    # 2. Verify membership directly without prematurely querying organizations table
    stmt = (
        select(OrganizationMembership)
        .where(
            OrganizationMembership.organization_id == str(org_uuid),
            OrganizationMembership.user_id == user.id
        )
    )
    result = await db.execute(stmt)
    membership = result.scalar_one_or_none()

    if not membership:
        logger.warning(
            f"Unauthorized workspace access attempt: user={user.id} requested org={org_uuid}"
        )
        raise AuthorizationError("User is not authorized for the requested organization.")

    # 3. Establish full tenant context only after membership is verified
    await set_tenant_context(db, uuid.UUID(user.id), org_uuid)

    # 4. Fetch Organization (now permitted under tenant RLS policy)
    org_stmt = select(Organization).where(Organization.id == str(org_uuid))
    org_result = await db.execute(org_stmt)
    organization = org_result.scalar_one_or_none()

    if not organization:
        raise AuthorizationError("Organization not found.")

    return TenantContext(
        user=user,
        organization=organization,
        role=membership.role
    )


def require_role(allowed_roles: list[str]):
    """Decorator / dependency factory enforcing minimum RBAC permissions."""
    def role_checker(context: TenantContext = Depends(get_tenant_context)) -> TenantContext:
        if context.role not in allowed_roles:
            raise AuthorizationError(
                f"Role '{context.role}' does not have permission for this action. Required: {allowed_roles}"
            )
        return context
    return role_checker


# Utility for testing
def create_mock_jwt(user_id: str, email: str, secret: Optional[str] = None) -> str:
    """Generates a valid signed JWT for testing."""
    import time
    payload = {
        "sub": user_id,
        "email": email,
        "aud": settings.SUPABASE_AUDIENCE,
        "iss": settings.supabase_issuer,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "user_metadata": {"full_name": "Test User"}
    }
    key = secret or settings.SUPABASE_JWT_SECRET
    return jwt.encode(payload, key, algorithm="HS256")
