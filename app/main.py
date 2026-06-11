from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.database.db import Base, engine
from app.jobs.cleanup import clean_expired_revoked_tokens

Base.metadata.create_all(bind=engine)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(clean_expired_revoked_tokens, "interval", hours=1)
    scheduler.start()
    yield
    scheduler.shutdown()


app = FastAPI(title="Audio Intelligence Platform", version="1.0.0", lifespan=lifespan)
app.include_router(auth_router)
