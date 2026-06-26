import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database.db import get_db
from app.models.password_reset_token import PasswordResetToken
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    RegisterRequest, LoginRequest, AuthResponse,
    RefreshRequest, ForgotPasswordRequest, ResetPasswordRequest,
)
from app.utils.auth import hash_password, verify_password, create_access_token, create_refresh_token
from app.utils.email import send_reset_email

router = APIRouter(prefix="/auth", tags=["auth"])


def _build_tokens(user: User) -> AuthResponse:
    access_token = create_access_token({"sub": str(user.id), "email": user.email})
    refresh_token = create_refresh_token({"sub": str(user.id)})
    return AuthResponse(access_token=access_token, refresh_token=refresh_token)


def _store_refresh_token(user_id: uuid.UUID, token: str, db: Session):
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    db_refresh = RefreshToken(
        user_id=user_id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=expires_at,
    )
    db.add(db_refresh)


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    try:
        existing = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="Email already registered")

        user = User(email=body.email, password_hash=hash_password(body.password))
        db.add(user)
        db.flush()

        db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked == False)
            .values(revoked=True)
        )

        tokens = _build_tokens(user)
        _store_refresh_token(user.id, tokens.refresh_token, db)
        db.commit()
        return tokens
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Registration failed")


@router.post("/login", response_model=AuthResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    try:
        user = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
        if not user or not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")

        db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.revoked == False)
            .values(revoked=True)
        )

        tokens = _build_tokens(user)
        _store_refresh_token(user.id, tokens.refresh_token, db)
        db.commit()
        return tokens
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        db.rollback()
        raise HTTPException(status_code=500, detail="Login failed")


@router.post("/refresh", response_model=AuthResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(body.refresh_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        user_id = uuid.UUID(payload.get("sub"))
    except (JWTError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    token_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    stored = db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked == False,
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    ).scalar_one_or_none()

    if not stored:
        raise HTTPException(status_code=401, detail="Refresh token revoked or not found")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    stored.revoked = True
    db.flush()

    tokens = _build_tokens(user)
    _store_refresh_token(user.id, tokens.refresh_token, db)
    db.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(body: RefreshRequest, db: Session = Depends(get_db)):
    token_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    stored = db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).scalar_one_or_none()
    if stored:
        stored.revoked = True
        db.commit()
    return {"detail": "Logged out"}


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Request a password-reset email.

    Security note: we always return 200 regardless of whether the email
    exists in our DB — this prevents email enumeration attacks where an
    attacker probes which addresses are registered.
    """
    user = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()

    if user:
        # Invalidate any outstanding unused reset tokens for this user
        # so only the most recent link is valid (prevents token accumulation).
        db.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used == False,
            )
            .values(used=True)
        )

        # Generate a cryptographically secure random token
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.RESET_TOKEN_EXPIRE_MINUTES)

        reset_record = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.add(reset_record)
        db.commit()

        # Send email — if SMTP is misconfigured we log but don't expose the
        # error to the caller (the user sees a generic success message).
        try:
            send_reset_email(to_email=user.email, raw_token=raw_token)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            # Don't re-raise — returning 500 here would reveal that the email exists.

    # Always return the same response to prevent email-enumeration
    return {"detail": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    """
    Consume a password-reset token and update the user's password.

    The token is:
      - Looked up by its SHA-256 hash (raw token is never stored)
      - Checked for expiry
      - Checked it hasn't been used already
    After a successful reset the token is marked used and ALL active
    refresh tokens for that user are revoked so any stolen sessions are
    immediately invalidated.
    """
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()

    record = db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used == False,
            PasswordResetToken.expires_at > datetime.now(timezone.utc),
        )
    ).scalar_one_or_none()

    if not record:
        # Generic message — don't reveal whether token was valid but expired
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset link is invalid or has expired. Please request a new one.",
        )

    user = db.get(User, record.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Update the password hash
    user.password_hash = hash_password(body.new_password)

    # Mark token as consumed so it can't be reused
    record.used = True

    # Revoke all active refresh tokens — forces re-login on all devices
    # This protects against an attacker who had a valid refresh token
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked == False)
        .values(revoked=True)
    )

    db.commit()
    return {"detail": "Password has been reset successfully. Please log in with your new password."}
