import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database.db import get_db
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import RegisterRequest, LoginRequest, AuthResponse, RefreshRequest
from app.utils.auth import hash_password, verify_password, create_access_token, create_refresh_token

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
