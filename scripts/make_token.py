"""Sinh access token cho user seed, dùng để gọi API AI khi kiểm thử.

CÁCH DÙNG:
    python -m scripts.make_token tenant0@seed.local
"""

from __future__ import annotations

import argparse
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings


def _writable_db_url() -> str:
    import os

    value = os.environ.get("SEED_DB_URL")
    if not value:
        raise RuntimeError(
            "Set SEED_DB_URL explicitly for test tools; runtime credentials are never elevated automatically."
        )
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Sinh JWT access token cho user seed")
    parser.add_argument("email", help="Email user (ví dụ tenant0@seed.local)")
    parser.add_argument("--scope", default="ROLE_TENANT")
    parser.add_argument("--minutes", type=int, default=30)
    args = parser.parse_args()

    settings = get_settings()

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.db.models_core import CoreUser

    engine = create_engine(_writable_db_url(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    with factory() as session:
        user = session.execute(
            select(CoreUser).where(CoreUser.email == args.email)
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"Không tìm thấy user: {args.email}")
        user_id = user.id
        auth_version = user.auth_version
    engine.dispose()

    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": user_id,
            "authVersion": auth_version,
            "scope": args.scope,
            "type": "access",
            "jti": str(uuid.uuid4()),
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(minutes=args.minutes),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_alg,
    )
    print(token)


if __name__ == "__main__":
    main()
