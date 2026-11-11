import smtplib

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.models import User
from app.schemas.auth import (
    LoginRequest,
    EmailRequest,
    EmailVerificationRequest,
    MessageResponse,
    PasswordResetRequest,
    RegisterRequest,
    RegistrationResponse,
    TokenPair,
    TokenRequest,
)
from app.schemas.user import UserPublic
from app.services.auth_service import (
    authenticate_user,
    create_password_reset,
    create_verification_token,
    register_user,
    reset_password,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
    verify_email,
)
from app.services.email_service import EmailConfigurationError, send_password_reset_email, send_verification_email
from app.services.google_auth_service import STATE_COOKIE, authenticate_google_callback, build_authorization_url

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


@router.post("/register", response_model=RegistrationResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> RegistrationResponse:
    user, verification_code = register_user(
        db, str(payload.email), payload.password.get_secret_value(), payload.full_name
    )
    try:
        send_verification_email(user.email, verification_code)
    except (EmailConfigurationError, OSError, smtplib.SMTPException):
        # Account creation remains successful if SMTP is not configured or temporarily unavailable.
        pass
    return RegistrationResponse(
        user=user,
        message="Account created. A verification code is required before login. Request a new code if you do not receive it.",
    )


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> TokenPair:
    user, pair = authenticate_user(db, str(payload.email), payload.password.get_secret_value())
    return TokenPair(**pair, user=user)


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: TokenRequest, db: Session = Depends(get_db)) -> TokenPair:
    user, pair = rotate_refresh_token(db, payload.refresh_token)
    return TokenPair(**pair, user=user)


@router.post("/logout", response_model=MessageResponse)
def logout(payload: TokenRequest, db: Session = Depends(get_db)) -> MessageResponse:
    revoke_refresh_token(db, payload.refresh_token)
    return MessageResponse(message="Logged out")


@router.post("/logout-all", response_model=MessageResponse)
def logout_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> MessageResponse:
    revoke_all_refresh_tokens(db, user.id)
    return MessageResponse(message="Logged out from all devices")


@router.post("/password/forgot", response_model=MessageResponse)
def forgot_password(payload: EmailRequest, db: Session = Depends(get_db)) -> MessageResponse:
    user, reset_code = create_password_reset(db, str(payload.email))
    if user and reset_code:
        try:
            send_password_reset_email(user.email, reset_code)
        except (EmailConfigurationError, OSError, smtplib.SMTPException):
            # Keep the response generic even if delivery cannot be completed.
            pass
    return MessageResponse(message="If the address is registered, password reset instructions will be sent.")


@router.post("/password/reset", response_model=MessageResponse)
def password_reset(payload: PasswordResetRequest, db: Session = Depends(get_db)) -> MessageResponse:
    reset_password(db, str(payload.email), payload.code, payload.new_password.get_secret_value())
    return MessageResponse(message="Password reset successfully")


@router.post("/email/verify", response_model=MessageResponse)
def email_verify(payload: EmailVerificationRequest, db: Session = Depends(get_db)) -> MessageResponse:
    verify_email(db, str(payload.email), payload.code)
    return MessageResponse(message="Email verified")


@router.post("/email/resend-verification", response_model=MessageResponse)
def resend_verification(payload: EmailRequest, db: Session = Depends(get_db)) -> MessageResponse:
    email = str(payload.email).strip().lower()
    user = db.scalar(
        select(User).where(func.lower(User.email) == email, User.is_active.is_(True))
    )
    if user and user.email_verified_at is None:
        verification_code = create_verification_token(db, user)
        if verification_code:
            try:
                send_verification_email(user.email, verification_code)
            except (EmailConfigurationError, OSError, smtplib.SMTPException):
                pass
    return MessageResponse(message="If the address needs verification, instructions will be sent.")


@router.get("/google")
def google_login() -> RedirectResponse:
    authorization_url, state_cookie = build_authorization_url()
    settings = get_settings()
    response = RedirectResponse(authorization_url, status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        state_cookie,
        max_age=600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/v1/auth/google/callback",
    )
    return response


@router.get("/google/callback", response_model=TokenPair)
def google_callback(
    request: Request,
    response: Response,
    code: str = Query(min_length=1),
    state: str = Query(min_length=1),
    db: Session = Depends(get_db),
) -> TokenPair:
    state_cookie = request.cookies.get(STATE_COOKIE)
    if not state_cookie:
        raise HTTPException(status_code=401, detail="Google sign-in could not be validated")
    user, pair = authenticate_google_callback(db, code, state, state_cookie)
    response.delete_cookie(STATE_COOKIE, path="/api/v1/auth/google/callback")
    return TokenPair(**pair, user=user)
