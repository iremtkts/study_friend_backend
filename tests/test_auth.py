import jwt
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import unittest
from unittest.mock import Mock, patch

from app.core.config import get_settings
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.db.session import Base, get_db
from app.main import app
from app.models import EmailVerificationToken, PasswordResetToken, RefreshToken, User
from app.services.auth_service import create_password_reset, reset_password
from app.services.google_auth_service import authenticate_google_callback, build_authorization_url
from app.utils.tokens import hash_email_verification_code, hash_password_reset_code, hash_token


class AuthInfrastructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)

        def override_get_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        self.client = TestClient(app)
        self.sent_codes: dict[str, str] = {}

    def tearDown(self) -> None:
        self.client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def register(self, email: str = "learner@example.com") -> dict:
        def capture_code(recipient: str, code: str) -> None:
            self.sent_codes[recipient] = code

        with patch("app.api.v1.endpoints.auth.send_verification_email", side_effect=capture_code):
            response = self.client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": "a-long-test-password-123", "full_name": "Learner"},
            )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def verify(self, email: str = "learner@example.com", code: str | None = None):
        response = self.client.post(
            "/api/v1/auth/email/verify",
            json={"email": email, "code": code or self.sent_codes[email]},
        )
        return response

    def test_argon2_password_hash_and_jwt_validation(self) -> None:
        password_hash = hash_password("a-long-test-password-123")
        self.assertNotEqual(password_hash, "a-long-test-password-123")
        self.assertTrue(password_hash.startswith("$argon2id$"))
        self.assertTrue(verify_password("a-long-test-password-123", password_hash))
        self.assertFalse(verify_password("wrong-password", password_hash))
        self.assertFalse(verify_password("anything", None))

        token = create_access_token("fca77a5a-3d08-4a44-b207-9ba5d20b91d3")
        claims = decode_access_token(token)
        self.assertEqual(claims["type"], "access")
        self.assertIn("jti", claims)
        with self.assertRaises(jwt.InvalidTokenError):
            decode_access_token(token + "tampered")
        settings = get_settings()
        wrong_type = jwt.encode(
            {
                "sub": "fca77a5a-3d08-4a44-b207-9ba5d20b91d3",
                "iat": datetime.now(timezone.utc),
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
                "jti": "test-jti",
                "type": "refresh",
            },
            settings.jwt_secret_key.get_secret_value(),
            algorithm=settings.jwt_algorithm,
        )
        with self.assertRaises(jwt.InvalidTokenError):
            decode_access_token(wrong_type)

    def test_register_duplicate_and_login(self) -> None:
        registered = self.register("Learner@Example.com")
        self.assertEqual(registered["user"]["email"], "learner@example.com")
        self.assertNotIn("password_hash", registered["user"])
        self.assertNotIn("access_token", registered)
        self.assertEqual(len(self.sent_codes["learner@example.com"]), 6)

        duplicate = self.client.post(
            "/api/v1/auth/register",
            json={"email": "learner@example.com", "password": "a-long-test-password-123"},
        )
        self.assertEqual(duplicate.status_code, 409)

        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "LEARNER@example.com", "password": "a-long-test-password-123"},
        )
        self.assertEqual(login.status_code, 403, login.text)
        self.assertEqual(self.verify().status_code, 200)
        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "LEARNER@example.com", "password": "a-long-test-password-123"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        self.assertNotIn("password_hash", login.text)
        invalid = self.client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": "wrong-password-value"},
        )
        self.assertEqual(invalid.status_code, 401)

    def test_refresh_rotation_reuse_detection_and_logout_all(self) -> None:
        self.register()
        self.verify()
        tokens = self.client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": "a-long-test-password-123"},
        ).json()
        old_refresh = tokens["refresh_token"]
        rotated = self.client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
        self.assertEqual(rotated.status_code, 200, rotated.text)
        new_refresh = rotated.json()["refresh_token"]

        reused = self.client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
        self.assertEqual(reused.status_code, 401)
        after_reuse = self.client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})
        self.assertEqual(after_reuse.status_code, 401)

        fresh_login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": "a-long-test-password-123"},
        ).json()
        logged_out = self.client.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {fresh_login['access_token']}"},
        )
        self.assertEqual(logged_out.status_code, 200, logged_out.text)
        revoked = self.client.post(
            "/api/v1/auth/refresh", json={"refresh_token": fresh_login["refresh_token"]}
        )
        self.assertEqual(revoked.status_code, 401)

        with self.Session() as db:
            records = db.scalars(select(RefreshToken)).all()
            self.assertTrue(all(len(record.token_hash) == 64 for record in records))
            self.assertTrue(all(record.token_hash not in {old_refresh, new_refresh} for record in records))

    def test_logout_revokes_refresh_token(self) -> None:
        self.register()
        self.verify()
        tokens = self.client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": "a-long-test-password-123"},
        ).json()
        response = self.client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
        self.assertEqual(response.status_code, 200, response.text)
        replay = self.client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        self.assertEqual(replay.status_code, 401)

    def test_delete_account_deactivates_without_deleting_user_data(self) -> None:
        self.register()
        self.verify()
        tokens = self.client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": "a-long-test-password-123"},
        ).json()
        access_token = tokens["access_token"]

        deleted = self.client.delete(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)

        with self.Session() as db:
            user = db.scalar(select(User).where(User.email == "learner@example.com"))
            self.assertIsNotNone(user)
            self.assertFalse(user.is_active)
            self.assertTrue(user.password_hash.startswith("$argon2id$"))
            refresh_row = db.scalar(select(RefreshToken).where(RefreshToken.user_id == user.id))
            self.assertIsNotNone(refresh_row.revoked_at)

        refresh_attempt = self.client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
        self.assertEqual(refresh_attempt.status_code, 401)
        access_attempt = self.client.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        self.assertEqual(access_attempt.status_code, 401)

    def test_re_register_same_email_after_deactivation_preserves_old_record(self) -> None:
        first = self.register("learner@example.com")
        self.verify()
        with self.Session() as db:
            first_user = db.scalar(select(User).where(User.email == "learner@example.com"))
            first_user_id = first_user.id
            first_hash = first_user.password_hash

        tokens = self.client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": "a-long-test-password-123"},
        ).json()
        deactivation = self.client.delete(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        self.assertEqual(deactivation.status_code, 200)

        second = self.register("LEARNER@example.com")
        self.assertEqual(second["user"]["email"], "learner@example.com")
        self.assertEqual(self.verify().status_code, 200)

        with self.Session() as db:
            users = db.scalars(select(User).where(User.email == "learner@example.com")).all()
            self.assertEqual(len(users), 2)
            old_user = next(user for user in users if user.id == first_user_id)
            new_user = next(user for user in users if user.id != first_user_id)
            self.assertFalse(old_user.is_active)
            self.assertEqual(old_user.password_hash, first_hash)
            self.assertTrue(new_user.is_active)

        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": "a-long-test-password-123"},
        )
        self.assertEqual(login.status_code, 200, login.text)

    def test_forgot_password_response_does_not_reveal_account_existence(self) -> None:
        self.register()
        with patch("app.api.v1.endpoints.auth.send_password_reset_email") as send_email:
            existing = self.client.post("/api/v1/auth/password/forgot", json={"email": "learner@example.com"})
            send_email.assert_not_called()

        self.verify()
        delivered_codes: list[str] = []
        with patch(
            "app.api.v1.endpoints.auth.send_password_reset_email",
            side_effect=lambda _email, code: delivered_codes.append(code),
        ):
            existing = self.client.post("/api/v1/auth/password/forgot", json={"email": "learner@example.com"})
            missing = self.client.post("/api/v1/auth/password/forgot", json={"email": "missing@example.com"})
        self.assertEqual(existing.status_code, 200)
        self.assertEqual(existing.json(), missing.json())
        self.assertEqual(len(delivered_codes), 1)
        self.assertEqual(len(delivered_codes[0]), 6)
        reset = self.client.post(
            "/api/v1/auth/password/reset",
            json={
                "email": "learner@example.com",
                "code": delivered_codes[0],
                "new_password": "replacement-password-456",
            },
        )
        self.assertEqual(reset.status_code, 200, reset.text)

    def test_google_route_reports_missing_external_credentials(self) -> None:
        response = self.client.get("/api/v1/auth/google", follow_redirects=False)
        self.assertEqual(response.status_code, 503)

    def test_password_reset_is_single_use_and_revokes_sessions(self) -> None:
        self.register()
        self.verify()
        self.client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": "a-long-test-password-123"},
        )
        with self.Session() as db:
            user = db.scalar(select(User).where(User.email == "learner@example.com"))
            _, older_code = create_password_reset(db, user.email)
            first_row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_password_reset_code(older_code)))
            self.assertEqual(first_row.token_hash, hash_password_reset_code(older_code))
            first_row.created_at = datetime.now(timezone.utc) - timedelta(seconds=61)
            db.commit()
            _, code = create_password_reset(db, user.email)
            reset_row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_password_reset_code(code)))
            self.assertEqual(reset_row.token_hash, hash_password_reset_code(code))
            self.assertNotEqual(reset_row.token_hash, code)
            reset_password(db, user.email, code, "replacement-password-456")
            self.assertIsNotNone(reset_row.used_at)
            self.assertIsNotNone(db.scalar(select(RefreshToken)).revoked_at)
            with self.assertRaises(Exception):
                reset_password(db, user.email, code, "another-password-789")
            with self.assertRaises(Exception):
                reset_password(db, user.email, older_code, "another-password-789")

        good_login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "learner@example.com", "password": "replacement-password-456"},
        )
        self.assertEqual(good_login.status_code, 200, good_login.text)

        with self.Session() as db:
            latest = db.scalar(select(PasswordResetToken).order_by(PasswordResetToken.created_at.desc()))
            latest.created_at = datetime.now(timezone.utc) - timedelta(seconds=61)
            db.commit()
            _, expired_code = create_password_reset(db, "learner@example.com")
            expired_row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_password_reset_code(expired_code)))
            expired_row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
            with self.assertRaises(Exception):
                reset_password(db, "learner@example.com", expired_code, "replacement-password-789")

    def test_password_reset_code_has_five_attempt_limit(self) -> None:
        self.register()
        self.verify()
        with self.Session() as db:
            _, correct_code = create_password_reset(db, "learner@example.com")

        wrong_code = "000000" if correct_code != "000000" else "000001"
        for _ in range(5):
            response = self.client.post(
                "/api/v1/auth/password/reset",
                json={
                    "email": "learner@example.com",
                    "code": wrong_code,
                    "new_password": "replacement-password-456",
                },
            )
            self.assertEqual(response.status_code, 400)
        blocked = self.client.post(
            "/api/v1/auth/password/reset",
            json={
                "email": "learner@example.com",
                "code": correct_code,
                "new_password": "replacement-password-456",
            },
        )
        self.assertEqual(blocked.status_code, 400)
        with self.Session() as db:
            reset_row = db.scalar(select(PasswordResetToken))
            self.assertEqual(reset_row.attempt_count, 5)
            self.assertIsNotNone(reset_row.used_at)

    def test_email_verification_single_use(self) -> None:
        self.register()
        with self.Session() as db:
            user = db.scalar(select(User).where(User.email == "learner@example.com"))
            token_row = db.scalar(select(EmailVerificationToken).where(EmailVerificationToken.user_id == user.id))
            self.assertIsNotNone(token_row)
            code = self.sent_codes[user.email]
            self.assertNotEqual(token_row.token_hash, code)
            self.assertEqual(token_row.token_hash, hash_email_verification_code(code))

        verified = self.verify()
        self.assertEqual(verified.status_code, 200, verified.text)
        with self.Session() as db:
            user = db.scalar(select(User).where(User.email == "learner@example.com"))
            verification_row = db.scalar(select(EmailVerificationToken).where(EmailVerificationToken.user_id == user.id))
            self.assertIsNotNone(user.email_verified_at)
            self.assertIsNotNone(verification_row.used_at)
        second_use = self.verify()
        self.assertEqual(second_use.status_code, 400)

    def test_verification_code_attempt_limit_and_resend_throttle(self) -> None:
        self.register()
        correct_code = self.sent_codes["learner@example.com"]
        wrong_code = "000000" if correct_code != "000000" else "000001"
        for _ in range(5):
            response = self.verify(code=wrong_code)
            self.assertEqual(response.status_code, 400)

        locked_out = self.verify(code=correct_code)
        self.assertEqual(locked_out.status_code, 400)
        with self.Session() as db:
            user = db.scalar(select(User).where(User.email == "learner@example.com"))
            token_row = db.scalar(select(EmailVerificationToken).where(EmailVerificationToken.user_id == user.id))
            self.assertEqual(token_row.attempt_count, 5)
            self.assertIsNotNone(token_row.used_at)

        with patch("app.api.v1.endpoints.auth.send_verification_email") as send_email:
            response = self.client.post(
                "/api/v1/auth/email/resend-verification", json={"email": "learner@example.com"}
            )
        self.assertEqual(response.status_code, 200)
        send_email.assert_not_called()

    def test_google_oidc_callback_checks_state_nonce_and_uses_sub(self) -> None:
        with patch("app.services.google_auth_service._require_google_config"):
            _, state_cookie = build_authorization_url()
        settings = get_settings()
        state_claims = jwt.decode(
            state_cookie,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
        http_response = Mock()
        http_response.json.return_value = {"id_token": "mock-google-id-token"}
        http_response.raise_for_status.return_value = None
        claims = {
            "sub": "google-stable-sub-123",
            "email": "google@example.com",
            "email_verified": True,
            "name": "Google Learner",
            "nonce": state_claims["nonce"],
            "iss": "https://accounts.google.com",
        }
        with patch("app.services.google_auth_service._require_google_config"), patch(
            "app.services.google_auth_service.httpx.post", return_value=http_response
        ), patch(
            "app.services.google_auth_service.id_token.verify_oauth2_token", return_value=claims
        ):
            with self.Session() as db:
                user, _ = authenticate_google_callback(db, "auth-code", state_claims["state"], state_cookie)
                self.assertEqual(user.google_sub, "google-stable-sub-123")
                self.assertEqual(user.auth_provider, "google")
                self.assertIsNone(user.password_hash)

        with patch("app.services.google_auth_service._require_google_config"), patch(
            "app.services.google_auth_service.httpx.post", return_value=http_response
        ):
            with self.Session() as db, self.assertRaises(Exception):
                authenticate_google_callback(db, "auth-code", "wrong-state", state_cookie)

        with patch("app.services.google_auth_service._require_google_config"), patch(
            "app.services.google_auth_service.httpx.post", return_value=http_response
        ), patch(
            "app.services.google_auth_service.id_token.verify_oauth2_token", side_effect=ValueError("bad signature")
        ):
            from fastapi import HTTPException

            with self.Session() as db, self.assertRaises(HTTPException) as raised:
                authenticate_google_callback(db, "auth-code", state_claims["state"], state_cookie)
            self.assertEqual(raised.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
