import smtplib
from email.message import EmailMessage

from app.core.config import get_settings
from app.services.email_templates import email_verification_email, password_reset_email


class EmailConfigurationError(RuntimeError):
    pass


def _send_email(recipient: str, subject: str, body: str) -> None:
    settings = get_settings()
    if not settings.smtp_host or not settings.email_from:
        raise EmailConfigurationError("SMTP_HOST and EMAIL_FROM must be configured")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.email_from
    message["To"] = recipient
    message.set_content(body)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        smtp.send_message(message)


def send_password_reset_email(recipient: str, code: str) -> None:
    subject, body = password_reset_email(code)
    _send_email(recipient, subject, body)


def send_verification_email(recipient: str, code: str) -> None:
    subject, body = email_verification_email(code)
    _send_email(recipient, subject, body)
