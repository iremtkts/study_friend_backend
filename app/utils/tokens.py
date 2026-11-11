import hashlib
import hmac
import secrets

from app.core.config import get_settings


def generate_opaque_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_email_verification_code(code: str) -> str:
    pepper = get_settings().jwt_secret_key.get_secret_value().encode("utf-8")
    return hmac.new(pepper, b"email-verification:" + code.encode("ascii"), hashlib.sha256).hexdigest()


def hash_password_reset_code(code: str) -> str:
    pepper = get_settings().jwt_secret_key.get_secret_value().encode("utf-8")
    return hmac.new(pepper, b"password-reset:" + code.encode("ascii"), hashlib.sha256).hexdigest()
