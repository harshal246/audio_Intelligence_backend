# Use pydantic-settings to load config from .env file
# This keeps secrets (DB password, JWT secret) out of the codebase
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database connection string for PostgreSQL
    DATABASE_URL: str
    # Secret key used to sign JWT tokens — must be kept secret
    SECRET_KEY: str
    # JWT signing algorithm (HS256 is symmetric, fast, and standard)
    ALGORITHM: str = "HS256"
    # Short-lived access token (1 hour) — limits damage if token is stolen
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    # Longer-lived refresh token (7 days) — avoids forcing frequent re-login
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Tell pydantic to read values from the .env file at project root
    model_config = {"env_file": ".env"}


# Single global instance — imported everywhere else
settings = Settings()
