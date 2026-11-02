"""Xác thực JWT dùng chung với Spring Boot.

Spring ký access token bằng HS512 với secret trong `app.jwt.secret`.
Claim: `sub` = user id, `authVersion`, `scope` (chứa `ROLE_TENANT`...), `type` = access.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt
from jwt import InvalidTokenError
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db.session import core_session_factory


@dataclass(frozen=True)
class AuthenticatedUser:
    id: str
    auth_version: int
    scopes: tuple[str, ...]


class AuthError(Exception):
    """Token không hợp lệ, hết hạn, hoặc đã bị thu hồi."""


class AuthVersionCache:
    """Cache ngắn giá trị auth_version/status của user để không query DB mỗi request."""

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, int, str]] = {}

    def get(self, user_id: str) -> tuple[int, str]:
        now = time.time()
        hit = self._cache.get(user_id)
        if hit and now - hit[0] < self._ttl:
            return hit[1], hit[2]

        from app.db.models_core import CoreUser

        with core_session_factory() as session:
            # `ai_svc` chi duoc doc cot id/status/auth_version nen phai
            # chi dinh danh sach cot, khong dung session.get() (select *).
            row = session.execute(
                select(CoreUser.id, CoreUser.status, CoreUser.auth_version).where(
                    CoreUser.id == user_id
                )
            ).one_or_none()
            if row is None:
                raise AuthError("Không tìm thấy người dùng")
            status = row.status
            auth_version = row.auth_version

        self._cache[user_id] = (now, auth_version, status)
        return auth_version, status


class JwtVerifier:
    def __init__(self, settings: Settings) -> None:
        if not settings.jwt_secret:
            raise RuntimeError(
                "Thiếu JWT_SECRET. Copy .env.example sang .env và điền secret khớp với Spring."
            )
        self._settings = settings
        self._cache = AuthVersionCache(settings.auth_version_cache_seconds)

    def verify(self, token: str) -> AuthenticatedUser:
        try:
            payload = jwt.decode(
                token,
                self._settings.jwt_secret,
                algorithms=[self._settings.jwt_alg],
                issuer=self._settings.jwt_issuer,
                options={"require": ["exp", "sub"]},
            )
        except InvalidTokenError as exc:
            raise AuthError(f"Token không hợp lệ: {exc}") from exc

        token_type = payload.get("type")
        if token_type != "access":
            raise AuthError("Chỉ chấp nhận access token")

        user_id = payload.get("sub")
        if not user_id:
            raise AuthError("Token thiếu claim sub")

        auth_version = int(payload.get("authVersion", 0))
        db_auth_version, status = self._cache.get(user_id)
        if status != "ACTIVE":
            raise AuthError("Tài khoản không active")
        if auth_version != db_auth_version:
            raise AuthError("Token đã bị thu hồi")

        scope = payload.get("scope") or ""
        scopes = tuple(s for s in scope.split(" ") if s)
        return AuthenticatedUser(id=user_id, auth_version=auth_version, scopes=scopes)


_verifier: JwtVerifier | None = None


def get_verifier() -> JwtVerifier:
    global _verifier
    if _verifier is None:
        _verifier = JwtVerifier(get_settings())
    return _verifier
