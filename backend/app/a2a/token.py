"""A2A 短时 JWT。签名密钥用 A2A_JWT_SECRET，缺省回落到 A2A_TOKEN。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import get_settings


def _secret() -> str:
    s = get_settings()
    return (s.a2a_jwt_secret or s.a2a_token or "").strip()


def issue_a2a_token(subject: str = "forge") -> str:
    secret = _secret()
    if not secret:
        raise RuntimeError("A2A_TOKEN / A2A_JWT_SECRET is empty")
    now = datetime.now(timezone.utc)
    ttl = max(30, int(get_settings().a2a_jwt_ttl_seconds or 300))
    return jwt.encode(
        {
            "sub": subject,
            "iss": "forge-a2a",
            "aud": "a2a",
            "iat": now,
            "nbf": now - timedelta(seconds=5),
            "exp": now + timedelta(seconds=ttl),
            "scope": "a2a",
        },
        secret,
        algorithm="HS256",
    )


def bearer_header(subject: str = "forge") -> str:
    return f"Bearer {issue_a2a_token(subject)}"


def verify_a2a_authorization(header: str) -> bool:
    s = get_settings()
    secret = _secret()
    if not secret:
        return False
    raw = (header or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        return False
    if s.a2a_allow_legacy_token and raw == s.a2a_token.strip() and raw.count(".") < 2:
        return True
    try:
        jwt.decode(raw, secret, algorithms=["HS256"], audience="a2a", issuer="forge-a2a")
        return True
    except jwt.PyJWTError:
        return False
