from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.transcript import router as transcript_router
from app.api.summary import router as summary_router
from app.api.chat import router as chat_router
from app.api.config import router as config_router
from app.database.db import Base, engine
from app.jobs.cleanup import clean_expired_revoked_tokens
from app.middleware import TokenInfoMiddleware
from fastapi.middleware.cors import CORSMiddleware

Base.metadata.create_all(bind=engine)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(clean_expired_revoked_tokens, "interval", hours=1)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Audio Intelligence Platform", version="1.0.0", lifespan=lifespan)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Middleware: annotates service API responses with JWT token_info
# Must be added BEFORE routers so it wraps the full handler chain
app.add_middleware(TokenInfoMiddleware)

app.include_router(auth_router)
app.include_router(transcript_router)
app.include_router(summary_router)
app.include_router(chat_router)
app.include_router(config_router)
