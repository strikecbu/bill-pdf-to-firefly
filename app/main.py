import logging
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.config import load_config
from app.models.database import get_engine
from app.routers import webhook, statements

logger = structlog.get_logger()

LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_config()
    level = LOG_LEVELS.get(settings.app.log_level.upper(), logging.INFO)
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )
    get_engine()  # Initialize DB
    logger.info("application_started")
    yield
    logger.info("application_shutdown")


app = FastAPI(
    title="Credit Card Statement Processor",
    description="自動化信用卡對帳單處理系統",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(webhook.router)
app.include_router(statements.router)


@app.get("/health")
async def health_check():
    return {"status": "ok"}
