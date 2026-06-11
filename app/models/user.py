# The User model maps to the "users" table in PostgreSQL
# We store a hash of the password (never the plaintext) for security
import uuid

from sqlalchemy import Column, String, DateTime, func
from sqlalchemy.dialects.postgresql import UUID

from app.database.db import Base


class User(Base):
    __tablename__ = "users"

    # UUID primary key — prevents user ID enumeration via sequential IDs
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Email is unique and indexed — fast lookups on login, prevents duplicates
    email = Column(String, unique=True, index=True, nullable=False)
    # Stores bcrypt hash, not the raw password
    password_hash = Column(String, nullable=False)
    # Auto-set timestamp on user creation — useful for audits
    created_at = Column(DateTime(timezone=True), server_default=func.now())
