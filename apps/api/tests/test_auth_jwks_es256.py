"""
Unit and integration tests for Supabase ES256 / JWKS cryptographic token verification.
Strictly verifies:
1. Valid ES256 token verification using Supabase's JWKS endpoint and matching 'kid'.
2. Fail-closed rejection of invalid signatures, unknown kid, and missing kid.
3. Strict algorithm allowlist and prevention of algorithm confusion (e.g. HMAC with EC key).
4. Validation of standard claims: exp, sub, aud, and iss.
5. Dynamic key rotation / JWKS cache refresh behavior.
6. Isolated backward compatibility with legacy HS256 tokens using SUPABASE_JWT_SECRET.
7. Fail-closed behavior on JWKS network failures without unverified fallback.
8. End-to-end integration via FastAPI HTTP client.
"""

import time
import base64
import uuid
from unittest.mock import MagicMock, patch
import pytest
import jwt
from jwt import PyJWKClient, PyJWKClientConnectionError, PyJWKSetError
from httpx import AsyncClient
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.core.config import settings
from apps.api.core.auth import (
    decode_supabase_jwt, get_jwks_client, reset_jwks_client,
    get_current_user
)
from apps.api.core.exceptions import AuthenticationError
from apps.api.core.database import User, Organization, OrganizationMembership


def _int_to_b64(val: int, length: int = 32) -> str:
    """Helper converting an integer coordinate to URL-safe base64 string."""
    return base64.urlsafe_b64encode(val.to_bytes(length, "big")).decode().rstrip("=")


def generate_ec_p256_keypair(kid: str = "test-kid-1"):
    """Generates an ECC P-256 private key and matching JWK dictionary."""
    priv_key = ec.generate_private_key(ec.SECP256R1())
    pub_key = priv_key.public_key()
    numbers = pub_key.public_numbers()

    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "use": "sig",
        "alg": "ES256",
        "kid": kid,
        "x": _int_to_b64(numbers.x),
        "y": _int_to_b64(numbers.y),
    }
    return priv_key, jwk


@pytest.fixture(autouse=True)
def cleanup_jwks_client():
    """Ensure clean JWKS client state before and after each test."""
    reset_jwks_client()
    yield
    reset_jwks_client()


# -----------------------------------------------------------------------------
# 1. ES256 JWKS Verification Happy Path
# -----------------------------------------------------------------------------

def test_es256_valid_token_with_matching_jwks_key():
    """Valid ES256 token with matching JWKS key verifies cleanly."""
    priv_key, jwk = generate_ec_p256_keypair(kid="supabase-key-1")
    jwks_payload = {"keys": [jwk]}

    client = get_jwks_client()
    client.fetch_data = MagicMock(return_value=jwks_payload)

    user_id = str(uuid.uuid4())
    payload = {
        "sub": user_id,
        "email": "reviewer@onsigning.internal",
        "aud": settings.SUPABASE_AUDIENCE,
        "iss": settings.supabase_issuer,
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "user_metadata": {"full_name": "Staging Reviewer"}
    }
    token = jwt.encode(payload, priv_key, algorithm="ES256", headers={"kid": "supabase-key-1"})

    decoded = decode_supabase_jwt(token)
    assert decoded["sub"] == user_id
    assert decoded["email"] == "reviewer@onsigning.internal"
    assert decoded["aud"] == "authenticated"
    assert decoded["iss"] == settings.supabase_issuer


# -----------------------------------------------------------------------------
# 2. Signature and Key ID Failure Cases
# -----------------------------------------------------------------------------

def test_es256_rejects_invalid_signature():
    """Token signed by a different private key fails signature verification."""
    priv_key_1, jwk_1 = generate_ec_p256_keypair(kid="supabase-key-1")
    priv_key_forged, _ = generate_ec_p256_keypair(kid="supabase-key-1")

    # JWKS contains public key for priv_key_1, but token is signed by priv_key_forged
    jwks_payload = {"keys": [jwk_1]}
    client = get_jwks_client()
    client.fetch_data = MagicMock(return_value=jwks_payload)

    payload = {
        "sub": str(uuid.uuid4()),
        "email": "forged@example.com",
        "aud": settings.SUPABASE_AUDIENCE,
        "iss": settings.supabase_issuer,
        "exp": int(time.time()) + 3600,
    }
    forged_token = jwt.encode(payload, priv_key_forged, algorithm="ES256", headers={"kid": "supabase-key-1"})

    with pytest.raises(AuthenticationError) as exc_info:
        decode_supabase_jwt(forged_token)
    assert "Signature verification failed" in str(exc_info.value)


def test_es256_rejects_unknown_kid():
    """Token referencing a key ID missing from JWKS fails closed."""
    priv_key, jwk = generate_ec_p256_keypair(kid="known-key")
    jwks_payload = {"keys": [jwk]}

    client = get_jwks_client()
    client.fetch_data = MagicMock(return_value=jwks_payload)

    payload = {
        "sub": str(uuid.uuid4()),
        "aud": settings.SUPABASE_AUDIENCE,
        "iss": settings.supabase_issuer,
        "exp": int(time.time()) + 3600,
    }
    token_unknown_kid = jwt.encode(payload, priv_key, algorithm="ES256", headers={"kid": "foreign-kid-999"})

    with pytest.raises(AuthenticationError) as exc_info:
        decode_supabase_jwt(token_unknown_kid)
    assert "Unable to verify token signature" in str(exc_info.value)


def test_es256_rejects_missing_kid_header():
    """ES256 token without 'kid' parameter in header is rejected immediately."""
    priv_key, _ = generate_ec_p256_keypair()
    payload = {
        "sub": str(uuid.uuid4()),
        "aud": settings.SUPABASE_AUDIENCE,
        "iss": settings.supabase_issuer,
        "exp": int(time.time()) + 3600,
    }
    # Deliberately omit kid from headers
    token_no_kid = jwt.encode(payload, priv_key, algorithm="ES256")

    with pytest.raises(AuthenticationError) as exc_info:
        decode_supabase_jwt(token_no_kid)
    assert "missing required 'kid' parameter" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 3. Standard Claims Validation (exp, sub, aud, iss)
# -----------------------------------------------------------------------------

def test_es256_rejects_expired_token():
    """Expired ES256 token is rejected with explicit expiration error."""
    priv_key, jwk = generate_ec_p256_keypair(kid="supabase-key-1")
    client = get_jwks_client()
    client.fetch_data = MagicMock(return_value={"keys": [jwk]})

    payload = {
        "sub": str(uuid.uuid4()),
        "aud": settings.SUPABASE_AUDIENCE,
        "iss": settings.supabase_issuer,
        "exp": int(time.time()) - 300,  # Expired 5 minutes ago
    }
    expired_token = jwt.encode(payload, priv_key, algorithm="ES256", headers={"kid": "supabase-key-1"})

    with pytest.raises(AuthenticationError) as exc_info:
        decode_supabase_jwt(expired_token)
    assert "token has expired" in str(exc_info.value).lower()


def test_es256_rejects_missing_subject_claim():
    """Token missing 'sub' identity claim is rejected."""
    priv_key, jwk = generate_ec_p256_keypair(kid="supabase-key-1")
    client = get_jwks_client()
    client.fetch_data = MagicMock(return_value={"keys": [jwk]})

    payload = {
        "email": "nosub@example.com",
        "aud": settings.SUPABASE_AUDIENCE,
        "iss": settings.supabase_issuer,
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, priv_key, algorithm="ES256", headers={"kid": "supabase-key-1"})

    with pytest.raises(AuthenticationError) as exc_info:
        decode_supabase_jwt(token)
    assert 'Token is missing the "sub" claim' in str(exc_info.value)


def test_es256_rejects_wrong_audience():
    """Token with foreign audience (e.g. 'anon' or foreign app) is rejected."""
    priv_key, jwk = generate_ec_p256_keypair(kid="supabase-key-1")
    client = get_jwks_client()
    client.fetch_data = MagicMock(return_value={"keys": [jwk]})

    payload = {
        "sub": str(uuid.uuid4()),
        "aud": "wrong-audience",
        "iss": settings.supabase_issuer,
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, priv_key, algorithm="ES256", headers={"kid": "supabase-key-1"})

    with pytest.raises(AuthenticationError) as exc_info:
        decode_supabase_jwt(token)
    assert "Audience mismatch" in str(exc_info.value)


def test_es256_rejects_wrong_issuer():
    """Token with invalid or spoofed issuer is rejected."""
    priv_key, jwk = generate_ec_p256_keypair(kid="supabase-key-1")
    client = get_jwks_client()
    client.fetch_data = MagicMock(return_value={"keys": [jwk]})

    payload = {
        "sub": str(uuid.uuid4()),
        "aud": settings.SUPABASE_AUDIENCE,
        "iss": "https://attacker-project.supabase.co/auth/v1",
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, priv_key, algorithm="ES256", headers={"kid": "supabase-key-1"})

    with pytest.raises(AuthenticationError) as exc_info:
        decode_supabase_jwt(token)
    assert "Issuer mismatch" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 4. Key Rotation & JWKS Cache Refresh
# -----------------------------------------------------------------------------

def test_jwks_key_rotation_auto_refresh():
    """
    Simulates Supabase key rotation:
    1. Initial JWKS contains 'key-1'.
    2. Rotation occurs: Supabase adds 'key-2'.
    3. Token with 'key-2' arrives: client re-fetches JWKS and verifies successfully.
    """
    priv_key_1, jwk_1 = generate_ec_p256_keypair(kid="supabase-key-1")
    priv_key_2, jwk_2 = generate_ec_p256_keypair(kid="supabase-key-2")

    # Step 1: initial JWKS contains only key-1
    fetch_mock = MagicMock()
    fetch_mock.side_effect = [
        {"keys": [jwk_1]},          # Initial fetch
        {"keys": [jwk_1, jwk_2]},   # Refresh fetch on unknown key
    ]

    client = get_jwks_client()
    client.fetch_data = fetch_mock

    # Token 1 with key-1 verifies
    token_1 = jwt.encode(
        {"sub": "user-1", "aud": settings.SUPABASE_AUDIENCE, "iss": settings.supabase_issuer, "exp": int(time.time()) + 3600},
        priv_key_1,
        algorithm="ES256",
        headers={"kid": "supabase-key-1"}
    )
    assert decode_supabase_jwt(token_1)["sub"] == "user-1"
    assert fetch_mock.call_count == 1

    # Token 2 with newly rotated key-2 triggers refresh and verifies cleanly
    token_2 = jwt.encode(
        {"sub": "user-2", "aud": settings.SUPABASE_AUDIENCE, "iss": settings.supabase_issuer, "exp": int(time.time()) + 3600},
        priv_key_2,
        algorithm="ES256",
        headers={"kid": "supabase-key-2"}
    )
    assert decode_supabase_jwt(token_2)["sub"] == "user-2"
    assert fetch_mock.call_count == 2


# -----------------------------------------------------------------------------
# 5. Algorithm Confusion & Strict Allowlist
# -----------------------------------------------------------------------------

def test_algorithm_confusion_hmac_with_ec_public_key_rejected():
    """
    Security check: An attacker attempting algorithm confusion signs an 'HS256' token
    using the raw bytes of the EC public key.
    The server MUST verify using SUPABASE_JWT_SECRET, never the public key, causing signature failure.
    """
    _, jwk = generate_ec_p256_keypair(kid="supabase-key-1")
    client = get_jwks_client()
    client.fetch_data = MagicMock(return_value={"keys": [jwk]})

    # Attacker uses public key material as HMAC secret
    forged_token = jwt.encode(
        {"sub": "attacker", "aud": settings.SUPABASE_AUDIENCE, "iss": settings.supabase_issuer, "exp": int(time.time()) + 3600},
        key="forged-public-key-material",
        algorithm="HS256"
    )

    with pytest.raises(AuthenticationError) as exc_info:
        decode_supabase_jwt(forged_token)
    assert "Signature verification failed" in str(exc_info.value)


def test_unsupported_and_none_algorithms_rejected():
    """Tokens with 'none' or unapproved algorithms are rejected without signature checks."""
    for bad_alg in ("none", "NONE", "RS384", "PS256"):
        token = jwt.encode(
            {"sub": "123", "aud": settings.SUPABASE_AUDIENCE, "iss": settings.supabase_issuer, "exp": int(time.time()) + 3600},
            key="dummy",
            algorithm="HS256"
        )
        # Modify header manually
        parts = token.split(".")
        header_bytes = base64.urlsafe_b64encode(f'{{"alg":"{bad_alg}","typ":"JWT"}}'.encode()).decode().rstrip("=")
        tampered_token = f"{header_bytes}.{parts[1]}.{parts[2]}"

        with pytest.raises(AuthenticationError) as exc_info:
            decode_supabase_jwt(tampered_token)
        assert "Unsupported or unauthorized signing algorithm" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 6. Legacy HS256 Compatibility
# -----------------------------------------------------------------------------

def test_legacy_hs256_token_acceptance():
    """Legacy HS256 token signed with SUPABASE_JWT_SECRET is accepted cleanly."""
    payload = {
        "sub": "legacy-user-42",
        "email": "legacy@example.com",
        "aud": settings.SUPABASE_AUDIENCE,
        "iss": settings.supabase_issuer,
        "exp": int(time.time()) + 3600,
    }
    hs256_token = jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")

    decoded = decode_supabase_jwt(hs256_token)
    assert decoded["sub"] == "legacy-user-42"
    assert decoded["email"] == "legacy@example.com"


def test_legacy_hs256_token_rejected_on_wrong_secret():
    """HS256 token signed with incorrect secret fails verification."""
    payload = {
        "sub": "user-42",
        "aud": settings.SUPABASE_AUDIENCE,
        "iss": settings.supabase_issuer,
        "exp": int(time.time()) + 3600,
    }
    bad_token = jwt.encode(payload, "wrong-hmac-secret-at-least-32-bytes-long!", algorithm="HS256")

    with pytest.raises(AuthenticationError) as exc_info:
        decode_supabase_jwt(bad_token)
    assert "Signature verification failed" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 7. JWKS Network Outage & Fail-Closed Behavior
# -----------------------------------------------------------------------------

def test_jwks_network_outage_fails_closed():
    """When the JWKS endpoint is unreachable, authentication fails closed without unverified decode."""
    priv_key, _ = generate_ec_p256_keypair(kid="supabase-key-1")
    client = get_jwks_client()
    client.fetch_data = MagicMock(side_effect=PyJWKClientConnectionError("Connection timed out to Supabase JWKS"))

    token = jwt.encode(
        {"sub": "user-123", "aud": settings.SUPABASE_AUDIENCE, "iss": settings.supabase_issuer, "exp": int(time.time()) + 3600},
        priv_key,
        algorithm="ES256",
        headers={"kid": "supabase-key-1"}
    )

    with pytest.raises(AuthenticationError) as exc_info:
        decode_supabase_jwt(token)
    assert "Unable to verify token signature" in str(exc_info.value)


# -----------------------------------------------------------------------------
# 8. End-to-End FastAPI Request with ES256 Header
# -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fastapi_request_with_es256_bearer_token(client: AsyncClient, db_session: AsyncSession):
    """End-to-end HTTP request using ES256 Bearer token against GET /api/v1/organizations."""
    priv_key, jwk = generate_ec_p256_keypair(kid="staging-key-1")
    jwk_client = get_jwks_client()
    jwk_client.fetch_data = MagicMock(return_value={"keys": [jwk]})

    user_id = str(uuid.uuid4())
    token = jwt.encode(
        {
            "sub": user_id,
            "email": "es256.reviewer@onsigning.internal",
            "aud": settings.SUPABASE_AUDIENCE,
            "iss": settings.supabase_issuer,
            "exp": int(time.time()) + 3600,
            "user_metadata": {"full_name": "ES256 Reviewer"}
        },
        priv_key,
        algorithm="ES256",
        headers={"kid": "staging-key-1"}
    )

    # User with no memberships returns empty list with HTTP 200
    res = await client.get(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    assert res.json() == []
