"""Kiểm thử tích hợp các endpoint của AI service.

CÁCH DÙNG:
    python -m scripts.smoke_test

Script tự sinh access token cho user seed rồi gọi `/ai/health`,
`/ai/me`, `/ai/recommendations` và `/ai/chat` (SSE).
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import jwt

from app.config import get_settings

BASE_URL = "http://127.0.0.1:8000"


def _writable_db_url() -> str:
    import os

    value = os.environ.get("SEED_DB_URL")
    if not value:
        raise RuntimeError(
            "Set SEED_DB_URL explicitly for test tools; runtime credentials are never elevated automatically."
        )
    return value


def make_token(email: str, scope: str = "ROLE_TENANT") -> str:
    """Sinh access token giống hệt format Spring Boot cấp."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from app.db.models_core import CoreUser

    settings = get_settings()
    engine = create_engine(_writable_db_url(), pool_pre_ping=True, future=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        user = session.execute(
            select(CoreUser).where(CoreUser.email == email)
        ).scalar_one_or_none()
        if user is None:
            raise SystemExit(f"Không tìm thấy user: {email}")
        user_id, auth_version = user.id, user.auth_version
    engine.dispose()

    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "authVersion": auth_version,
            "scope": scope,
            "type": "access",
            "jti": str(uuid.uuid4()),
            "iss": settings.jwt_issuer,
            "iat": now,
            "exp": now + timedelta(minutes=30),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_alg,
    )


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' - ' + detail) if detail else ''}")
    return ok


def main() -> int:
    token = make_token("tenant0@seed.local")
    headers = {"Authorization": f"Bearer {token}"}
    results: list[bool] = []

    with httpx.Client(base_url=BASE_URL, timeout=60.0) as client:
        # 1. Health (không cần token).
        response = client.get("/ai/health")
        body = response.json() if response.status_code == 200 else {}
        results.append(
            _check(
                "GET /ai/health",
                response.status_code == 200 and body.get("status") == "UP",
                f"status={response.status_code}",
            )
        )

        # 2. /ai/me (token hợp lệ).
        response = client.get("/ai/me", headers=headers)
        results.append(
            _check(
                "GET /ai/me (token hợp lệ)",
                response.status_code == 200
                and "ROLE_TENANT" in response.json().get("scopes", []),
                f"status={response.status_code}",
            )
        )

        # 3. /ai/me với token rác -> 401.
        response = client.get(
            "/ai/me", headers={"Authorization": "Bearer token.rac.abc"}
        )
        results.append(
            _check(
                "GET /ai/me (token rác bị từ chối)",
                response.status_code == 401,
                f"status={response.status_code}",
            )
        )

        # 4. /ai/recommendations.
        response = client.get("/ai/recommendations?limit=5", headers=headers)
        payload = response.json() if response.status_code == 200 else {}
        results.append(
            _check(
                "GET /ai/recommendations",
                response.status_code == 200 and len(payload.get("items", [])) > 0,
                f"status={response.status_code} items={len(payload.get('items', []))}",
            )
        )

        # 5. /ai/chat (SSE).
        response = client.post(
            "/ai/chat",
            headers=headers,
            json={"message": "phòng trọ dưới 3 triệu ở Cầu Giấy có điều hòa"},
        )
        events: dict[str, list] = {}
        if response.status_code == 200:
            for line in response.text.splitlines():
                if line.startswith("event: "):
                    name = line[7:].strip()
                elif line.startswith("data: "):
                    events.setdefault(name, []).append(json.loads(line[6:]))
        results.append(
            _check(
                "POST /ai/chat (SSE)",
                response.status_code == 200 and "done" in events,
                f"status={response.status_code} events={sorted(events)}",
            )
        )

    passed = sum(1 for item in results if item)
    print(f"\n{passed}/{len(results)} kiểm thử pass")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
