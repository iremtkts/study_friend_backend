from pydantic import BaseModel, EmailStr, Field, SecretStr

from app.schemas.user import UserPublic


class Credentials(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=12, max_length=128)


class RegisterRequest(Credentials):
    full_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=128)


class EmailRequest(BaseModel):
    email: EmailStr


class TokenRequest(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)


class EmailVerificationRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class PasswordResetRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    new_password: SecretStr = Field(min_length=12, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserPublic


class MessageResponse(BaseModel):
    message: str


class RegistrationResponse(BaseModel):
    user: UserPublic
    message: str
