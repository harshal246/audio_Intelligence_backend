# SQLAlchemy is the ORM — it maps Python classes to DB tables
# and handles connection pooling, query building, etc.
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Import settings so the DB URL comes from .env
from app.config import settings

# Create the database engine (manages connection pool to PostgreSQL)
engine = create_engine(settings.DATABASE_URL)
# SessionLocal is a factory that creates new DB sessions
# autocommit=False means we explicitly commit/rollback transactions
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Base class for all SQLAlchemy models — every table model inherits from this
class Base(DeclarativeBase):
    pass


# FastAPI dependency that provides a DB session per request
# Using yield + finally ensures the session is always closed
# even if an exception occurs
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
