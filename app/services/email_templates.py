def password_reset_email(code: str) -> tuple[str, str]:
    return "Your Study Friend password reset code", f"Enter this single-use code to reset your password: {code}\n\nThis code expires in 30 minutes. If you did not request this, ignore this email."


def email_verification_email(code: str) -> tuple[str, str]:
    return "Your Study Friend verification code", f"Enter this verification code in Study Friend: {code}\n\nThis code expires in 60 minutes. If you did not create an account, ignore this email."
