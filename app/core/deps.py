"""Dependency FastAPI: lấy user hiện tại từ Bearer token."""

from fastapi import Header, HTTPException, status

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
