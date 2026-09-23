"""Application entrypoint: FastAPI app factory, lifespan wiring, router registration."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.apis.v1.attendance_api import router as attendance_router
from app.apis.v1.auth_api import router as auth_router
from app.apis.v1.dashboard_api import router as dashboard_router
from app.apis.v1.day_off_api import router as day_off_router
from app.apis.v1.deal_api import router as deal_router
from app.apis.v1.internal_api import router as internal_router
from app.apis.v1.lead_api import router as lead_router
from app.apis.v1.notification_api import router as notification_router
from app.apis.v1.opportunity_api import router as opportunity_router
from app.apis.v1.property_api import router as property_router
from app.apis.v1.signup_api import router as signup_router
from app.apis.v1.site_visit_api import router as site_visit_router
from app.core.config import get_settings
from app.core.error_handlers import register_error_handlers
from app.core.lock_sweeper import LockSweeper
from app.core.rate_limit import RateLimiter
from app.persistence.db_persistence import Database
from app.persistence.lock_persistence import LockPersistence
from app.persistence.opportunity_persistence import OpportunityPersistence
from app.persistence.signup_persistence import SignupPersistence

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # The pool connects lazily on first use — never at startup — so a database
    # outage degrades to per-request 500s instead of the app failing to boot.
    db = Database(settings)
    app.state.db = db
    app.state.rate_limiter = RateLimiter(settings)

    lock_sweeper = LockSweeper(LockPersistence(db), OpportunityPersistence(db), SignupPersistence(db))
    if settings.enable_background_lock_sweeper:
        lock_sweeper.start()

    try:
        yield
    finally:
        await lock_sweeper.stop()
        await db.disconnect()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Divine Vision Infra — Employee Site Visit Portal API",
        version="1.5.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    prefix = settings.api_v1_prefix
    app.include_router(auth_router, prefix=prefix)
    app.include_router(signup_router, prefix=prefix)
    app.include_router(dashboard_router, prefix=prefix)
    app.include_router(lead_router, prefix=prefix)
    app.include_router(site_visit_router, prefix=prefix)
    app.include_router(property_router, prefix=prefix)
    app.include_router(attendance_router, prefix=prefix)
    app.include_router(day_off_router, prefix=prefix)
    app.include_router(notification_router, prefix=prefix)
    app.include_router(opportunity_router, prefix=prefix)
    app.include_router(deal_router, prefix=prefix)
    app.include_router(internal_router, prefix=prefix)

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
