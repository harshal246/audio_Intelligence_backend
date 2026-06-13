# Utility functions for password hashing and JWT token management.
# Separating these from the API layer keeps concerns clean and testable.
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database.db import get_db
from app.models.user import User

# CryptContext manages bcrypt hashing — bcrypt is chosen because:
# - It's deliberately slow (resists brute-force)
# - It includes a unique salt per password (resists rainbow tables)
# - It's widely audited and trusted
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    # Takes a plaintext password, returns a bcrypt hash string
    # The hash includes the salt, so two identical passwords hash differently
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    # Compares a plaintext password against a stored bcrypt hash
    # Uses constant-time comparison to prevent timing attacks
    return pwd_context.verify(plain, hashed)


def create_access_token(data: dict) -> str:
    # Creates a short-lived JWT for API authorization
    # Short expiry (1 hour) limits damage if the token is compromised
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    # "type": "access" lets the refresh endpoint reject this token type
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(data: dict) -> str:
    # Creates a longer-lived JWT (7 days) used only to obtain new access tokens
    # Longer expiry reduces friction (fewer logins) while still being revocable
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    # "type": "refresh" prevents this token being used for API authorization
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """
    Dependency to get the current authenticated user from JWT token.
    
    Args:
        credentials: HTTP Bearer credentials
        db: Database session
    
    Returns:
        Current authenticated User object
    
    Raises:
        HTTPException: If token is invalid or user not found
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = db.execute(select(User).where(User.id == uuid.UUID(user_id))).scalar_one_or_none()
    if user is None:
        raise credentials_exception
    
    return user
