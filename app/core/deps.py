"""Dependency FastAPI: lấy user hiện tại từ Bearer token."""

from fastapi import Depends, Header, HTTPException, status

from app.core.security import AuthenticatedUser, AuthError, get_verifier


def get_current_user(
    authorization: str | None = Header(default=None),
) -> AuthenticatedUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Thiếu Bearer token",
        )
    token = authorization.split(" ", 1)[1].strip()
    try:
        return get_verifier().verify(token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc


def get_current_tenant(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    """Chỉ role TENANT được gọi các endpoint AI."""
    if "ROLE_TENANT" not in user.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Chỉ tài khoản TENANT được phép dùng AI service",
        )
    return user
