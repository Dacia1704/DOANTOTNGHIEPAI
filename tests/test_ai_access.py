import inspect
import time
import unittest
from unittest.mock import patch

import jwt
from fastapi import HTTPException

from app.api.chat import chat
from app.api.events import record_event, record_feedback
from app.api.recommendations import recommendations
from app.config import Settings
from app.core.deps import get_current_user
from app.core.security import JwtVerifier


class AiAccessTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(_env_file=None, jwt_secret="test-secret-" * 8)
        self.verifier = JwtVerifier(self.settings)
        self.verifier_patch = patch("app.core.deps.get_verifier", return_value=self.verifier)
        self.verifier_patch.start()
        self.addCleanup(self.verifier_patch.stop)

    def token(self, role, **overrides):
        claims = {
            "sub": "test-user",
            "iss": self.settings.jwt_issuer,
            "exp": int(time.time()) + 60,
            "type": "access",
            "authVersion": 1,
            "scope": f"ROLE_{role}",
        }
        claims.update(overrides)
        return jwt.encode(claims, self.settings.jwt_secret, algorithm="HS512")

    def test_all_roles_can_authenticate_for_ai(self):
        with patch.object(self.verifier._cache, "get", return_value=(1, "ACTIVE")):
            for role in ("TENANT", "LANDLORD", "ADMIN"):
                with self.subTest(role=role):
                    user = get_current_user(f"Bearer {self.token(role)}")
                    self.assertEqual(user.id, "test-user")
                    self.assertIn(f"ROLE_{role}", user.scopes)

    def test_missing_and_invalid_tokens_remain_unauthorized(self):
        for authorization in (None, "Bearer invalid"):
            with self.subTest(authorization=authorization):
                with self.assertRaises(HTTPException) as raised:
                    get_current_user(authorization)
                self.assertEqual(raised.exception.status_code, 401)

    def test_inactive_revoked_expired_and_refresh_tokens_are_rejected(self):
        cases = (
            ((1, "INACTIVE"), {}),
            ((2, "ACTIVE"), {}),
            ((1, "ACTIVE"), {"exp": int(time.time()) - 60}),
            ((1, "ACTIVE"), {"type": "refresh"}),
        )
        for cached, overrides in cases:
            with (
                self.subTest(cached=cached, overrides=overrides),
                patch.object(self.verifier._cache, "get", return_value=cached),
            ):
                with self.assertRaises(HTTPException) as raised:
                    get_current_user(f"Bearer {self.token('ADMIN', **overrides)}")
                self.assertEqual(raised.exception.status_code, 401)

    def test_ai_endpoints_require_role_independent_authentication(self):
        for endpoint in (chat, record_event, record_feedback, recommendations):
            with self.subTest(endpoint=endpoint.__name__):
                dependency = inspect.signature(endpoint).parameters["user"].default
                self.assertIs(dependency.dependency, get_current_user)
