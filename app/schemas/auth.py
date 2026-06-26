# Pydantic models define the shape of API request/response data.
# They provide automatic validation, serialization, and OpenAPI docs.
from pydantic import BaseModel, EmailStr, field_validator


class RegisterRequest(BaseModel):
    # EmailStr validates that input is a properly formatted email address
    email: EmailStr
    password: str

    # Custom validator enforces password strength rules
    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        # Minimum length prevents weak passwords
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        # Require uppercase — increases password complexity
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain an uppercase letter")
        # Require lowercase — increases password complexity
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain a lowercase letter")
        # Require digit — prevents purely alphabetic passwords
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain a digit")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AuthResponse(BaseModel):
    # JWT that expires in 1 hour — sent with every authenticated request
    access_token: str
    # JWT that expires in 7 days — used only to get a new access_token
    refresh_token: str
    # Standard OAuth2 field — tells frontend how to use the token
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    # The refresh token the frontend wants to exchange for a new pair
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    # Only the email is needed — we look up the user and send a link to this address
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    # One-time token from the URL param in the reset email
    token: str
    # New password — same strength rules as registration for consistency
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain an uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain a lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain a digit")
        return v
