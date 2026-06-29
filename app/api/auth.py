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
    RefreshRequest, ForgotPasswordRequest, VerifyOtpRequest, ResetPasswordRequest,
)
from app.utils.auth import hash_password, verify_password, create_access_token, create_refresh_token
from app.utils.email import send_otp_email

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
    Send a 6-digit OTP to the user's email for password reset.

    Security note: we always return 200 regardless of whether the email
    exists in our DB — this prevents email enumeration attacks.
    """
    user = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()

    if user:
        # Invalidate any outstanding unused OTPs for this user
        db.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used == False,
            )
            .values(used=True)
        )

        # Generate a 6-digit OTP
        otp = f"{secrets.randbelow(1000000):06d}"
        otp_hash = hashlib.sha256(otp.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.RESET_TOKEN_EXPIRE_MINUTES)

        reset_record = PasswordResetToken(
            user_id=user.id,
            otp_hash=otp_hash,
            expires_at=expires_at,
        )
        db.add(reset_record)
        db.commit()

        try:
            send_otp_email(to_email=user.email, otp=otp)
        except Exception:
            import traceback
            traceback.print_exc()
            # Don't re-raise — returning 500 would reveal that the email exists.

    return {"detail": "If that email is registered, a reset code has been sent."}


@router.post("/verify-otp", status_code=status.HTTP_200_OK)
def verify_otp(body: VerifyOtpRequest, db: Session = Depends(get_db)):
    """
    Verify the 6-digit OTP and return a short-lived reset_session_token.
    The reset_session_token is used in the next step to actually reset the password.
    """
    user = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid code. Please request a new one.",
        )

    otp_hash = hashlib.sha256(body.otp.encode()).hexdigest()
    record = db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.otp_hash == otp_hash,
            PasswordResetToken.used == False,
            PasswordResetToken.expires_at > datetime.now(timezone.utc),
        )
    ).scalar_one_or_none()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired code. Please request a new one.",
        )

    # OTP is valid — generate a short-lived reset session token
    raw_session_token = secrets.token_urlsafe(32)
    record.reset_session_hash = hashlib.sha256(raw_session_token.encode()).hexdigest()
    record.otp_verified = True
    db.commit()

    return {"reset_session_token": raw_session_token}


@router.post("/reset-password", status_code=status.HTTP_200_OK)
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    """
    Consume the reset_session_token (issued after OTP verification) and update the password.
    """
    session_hash = hashlib.sha256(body.reset_session_token.encode()).hexdigest()

    record = db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.reset_session_hash == session_hash,
            PasswordResetToken.otp_verified == True,
            PasswordResetToken.used == False,
            PasswordResetToken.expires_at > datetime.now(timezone.utc),
        )
    ).scalar_one_or_none()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session expired or invalid. Please start the reset process again.",
        )

    user = db.get(User, record.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Update the password hash
    user.password_hash = hash_password(body.new_password)

    # Mark token as consumed so it can't be reused
    record.used = True

    # Revoke all active refresh tokens — forces re-login on all devices
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked == False)
        .values(revoked=True)
    )

    db.commit()
    return {"detail": "Password has been reset successfully. Please log in with your new password."}

