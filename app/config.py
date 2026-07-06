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
    # Password reset token expiry — short window reduces exposure if email is intercepted
    RESET_TOKEN_EXPIRE_MINUTES: int = 15

    # SMTP email settings for sending password-reset emails
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""              # Sender email address (e.g. you@gmail.com)
    SMTP_PASSWORD: str = ""          # Gmail App Password (not your real Google password)
    SMTP_FROM: str = ""              # "From" display name/email in reset emails

    # Base URL of your frontend — used to build the reset link inside the email
    FRONTEND_URL: str = "http://localhost:3000"

    # Audio processing settings
    HF_TOKEN: str  # Hugging Face token for PyAnnote
    MODEL_SIZE: str = "tiny"  # WhisperX model size
    UPLOAD_DIR: str = "uploads"  # Directory for uploaded audio files
    CHUNK_DIR: str = "chunks"  # Directory for audio chunks

    # Google Gemini API for AI-powered summaries
    GEMINI_API_KEY: str  # Google Gemini API key
    GEMINI_MODEL: str = "gemini-3.1-flash-lite"  # Model: gemini-2.5-flash (fast) or gemini-2.5-pro (quality)

    # Cloudinary settings
    # Set USE_CLOUDINARY=false in .env to skip cloud upload and keep files local
    USE_CLOUDINARY: bool = False
    CLOUDINARY_CLOUD_NAME: str = ""
    CLOUDINARY_API_KEY: str = ""
    CLOUDINARY_API_SECRET: str = ""

    # Redis connection URL for Celery broker/backend
    REDIS_URL: str = "redis://localhost:6379/0"

    # Tell pydantic to read values from the .env file at project root
    model_config = {"env_file": ".env"}


# Single global instance — imported everywhere else
settings = Settings()
