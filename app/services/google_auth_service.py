import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import User
from app.services.auth_service import _issue_pair

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
STATE_COOKIE = "study_friend_google_state"


def build_authorization_url() -> tuple[str, str]:
    settings = get_settings()
    _require_google_config()
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = hashlib.sha256(verifier.encode()).digest()
    challenge_text = base64.urlsafe_b64encode(challenge).rstrip(b"=").decode()
    state_cookie = jwt.encode(
        {
            "type": "google_state",
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "iat": datetime.now(timezone.utc),
            "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
        },
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge_text,
        "code_challenge_method": "S256",
    }
    return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(params)}", state_cookie


def _require_google_config() -> None:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret.get_secret_value():
        raise HTTPException(status_code=503, detail="Google sign-in is not configured")


def authenticate_google_callback(db: Session, code: str, state: str, state_cookie: str) -> tuple[User, dict[str, str]]:
    settings = get_settings()
    _require_google_config()
    try:
        state_claims = jwt.decode(
            state_cookie,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["type", "state", "nonce", "verifier"]},
        )
        if state_claims.get("type") != "google_state" or not secrets.compare_digest(str(state_claims["state"]), state):
            raise ValueError("state mismatch")
        response = httpx.post(
            GOOGLE_TOKEN_ENDPOINT,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret.get_secret_value(),
                "redirect_uri": settings.google_redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": state_claims["verifier"],
            },
            timeout=10.0,
        )
        response.raise_for_status()
        google_id_token = response.json()["id_token"]
        claims = id_token.verify_oauth2_token(google_id_token, Request(), settings.google_client_id)
        if not secrets.compare_digest(str(claims.get("nonce", "")), str(state_claims["nonce"])):
            raise ValueError("nonce mismatch")
        if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("invalid issuer")
        if claims.get("email_verified") is not True:
            raise ValueError("email is not verified")
        google_sub = str(claims["sub"])
        email = str(claims["email"]).strip().lower()
    except (jwt.InvalidTokenError, KeyError, ValueError, httpx.HTTPError, GoogleAuthError) as exc:
        raise HTTPException(status_code=401, detail="Google sign-in could not be validated") from exc

    user = db.scalar(select(User).where(User.google_sub == google_sub))
    if user is None:
        email_owner = db.scalar(
            select(User.id).where(func.lower(User.email) == email, User.is_active.is_(True))
        )
        if email_owner:
            raise HTTPException(status_code=409, detail="This email already uses another sign-in method")
        user = User(
            email=email,
            full_name=claims.get("name"),
            auth_provider="google",
            google_sub=google_sub,
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Unable to create account") from exc
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")
    user.last_login_at = datetime.now(timezone.utc)
    pair = _issue_pair(db, user)
    db.commit()
    db.refresh(user)
    return user, pair
