import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password, verify_password
from app.models import EmailVerificationToken, PasswordResetToken, RefreshToken, User
from app.utils.tokens import (
    generate_opaque_token,
    hash_email_verification_code,
    hash_password_reset_code,
    hash_token,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(value: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value <= _now()


def _issue_pair(db: Session, user: User, family_id: uuid.UUID | None = None) -> dict[str, str]:
    settings = get_settings()
    raw_refresh = generate_opaque_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_token(raw_refresh),
            family_id=family_id or uuid.uuid4(),
            expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    return {"access_token": create_access_token(user.id), "refresh_token": raw_refresh, "token_type": "bearer"}


def register_user(db: Session, email: str, password: str, full_name: str | None) -> tuple[User, str]:
    normalized_email = email.strip().lower()
    existing = db.scalar(
        select(User.id).where(func.lower(User.email) == normalized_email, User.is_active.is_(True))
    )
    if existing:
        raise HTTPException(status_code=409, detail="Unable to create account with these details")

    user = User(email=normalized_email, password_hash=hash_password(password), full_name=full_name, auth_provider="local")
    db.add(user)
    try:
        db.flush()
        verification_code = f"{secrets.randbelow(1_000_000):06d}"
        settings = get_settings()
        db.add(
            EmailVerificationToken(
                user_id=user.id,
                token_hash=hash_email_verification_code(verification_code),
                expires_at=_now() + timedelta(minutes=settings.email_verification_expire_minutes),
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Unable to create account with these details") from exc
    db.refresh(user)
    return user, verification_code


def authenticate_user(db: Session, email: str, password: str) -> tuple[User, dict[str, str]]:
    normalized_email = email.strip().lower()
    user = db.scalar(
        select(User).where(func.lower(User.email) == normalized_email, User.is_active.is_(True))
    )
    password_matches = verify_password(password, user.password_hash if user else None)
    if not user or not password_matches or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if user.email_verified_at is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email verification required")
    user.last_login_at = _now()
    pair = _issue_pair(db, user)
    db.commit()
    db.refresh(user)
    return user, pair


def rotate_refresh_token(db: Session, raw_token: str) -> tuple[User, dict[str, str]]:
    digest = hash_token(raw_token)
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == digest).with_for_update())
    if token is None or not hmac.compare_digest(token.token_hash, digest):
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if token.revoked_at is not None:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == token.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    if _is_expired(token.expires_at):
        token.revoked_at = _now()
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        token.revoked_at = _now()
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    token.revoked_at = _now()
    pair = _issue_pair(db, user, token.family_id)
    db.commit()
    return user, pair


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if token and token.revoked_at is None:
        token.revoked_at = _now()
        db.commit()


def revoke_all_refresh_tokens(db: Session, user_id: uuid.UUID) -> None:
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)).values(revoked_at=_now()))
    db.commit()


def create_password_reset(db: Session, email: str) -> tuple[User | None, str | None]:
    user = db.scalar(
        select(User).where(
            func.lower(User.email) == email.strip().lower(),
            User.is_active.is_(True),
        )
    )
    if user is None or user.auth_provider != "local" or user.email_verified_at is None:
        return None, None
    now = _now()
    latest = db.scalar(
        select(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id)
        .order_by(PasswordResetToken.created_at.desc())
        .limit(1)
    )
    if latest is not None:
        created_at = latest.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if now - created_at < timedelta(seconds=60):
            return None, None
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )

    code = f"{secrets.randbelow(1_000_000):06d}"
    settings = get_settings()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_password_reset_code(code),
            expires_at=now + timedelta(minutes=settings.password_reset_expire_minutes),
        )
    )
    db.commit()
    return user, code


def reset_password(db: Session, email: str, code: str, new_password: str) -> None:
    user = db.scalar(
        select(User)
        .where(func.lower(User.email) == email.strip().lower(), User.is_active.is_(True))
        .with_for_update()
    )
    if user is None or user.auth_provider != "local" or user.email_verified_at is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")
    token = db.scalar(
        select(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .order_by(PasswordResetToken.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if token is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")
    if _is_expired(token.expires_at):
        token.used_at = _now()
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")
    expected_hash = hash_password_reset_code(code)
    if not hmac.compare_digest(token.token_hash, expected_hash):
        token.attempt_count += 1
        if token.attempt_count >= 5:
            token.used_at = _now()
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired reset code")

    user.password_hash = hash_password(new_password)
    now = _now()
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))
        .values(used_at=now)
    )
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)).values(revoked_at=now))
    db.commit()


def create_verification_token(db: Session, user: User) -> str | None:
    now = _now()
    latest = db.scalar(
        select(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user.id)
        .order_by(EmailVerificationToken.created_at.desc())
        .limit(1)
    )
    if latest is not None:
        created_at = latest.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if now - created_at < timedelta(seconds=60):
            return None

    db.execute(
        update(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user.id, EmailVerificationToken.used_at.is_(None))
        .values(used_at=now)
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    settings = get_settings()
    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_email_verification_code(code),
            expires_at=now + timedelta(minutes=settings.email_verification_expire_minutes),
        )
    )
    db.commit()
    return code


def verify_email(db: Session, email: str, code: str) -> None:
    user = db.scalar(
        select(User)
        .where(func.lower(User.email) == email.strip().lower(), User.is_active.is_(True))
        .with_for_update()
    )
    if user is None or user.email_verified_at is not None:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")
    token = db.scalar(
        select(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user.id, EmailVerificationToken.used_at.is_(None))
        .order_by(EmailVerificationToken.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if token is None:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")
    if _is_expired(token.expires_at):
        token.used_at = _now()
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    expected_hash = hash_email_verification_code(code)
    if not hmac.compare_digest(token.token_hash, expected_hash):
        token.attempt_count += 1
        if token.attempt_count >= 5:
            token.used_at = _now()
        db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    user.email_verified_at = _now()
    token.used_at = _now()
    db.execute(
        update(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user.id, EmailVerificationToken.used_at.is_(None))
        .values(used_at=_now())
    )
    db.commit()


